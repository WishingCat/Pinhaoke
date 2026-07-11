#!/usr/bin/env bash
# Update /opt/pinhaoke to the exact origin/main commit after all preflight work succeeds.

set -Eeuo pipefail

APP_DIR=/opt/pinhaoke
LIVE_VENV="$APP_DIR/venv"
SERVICE=pinhaoke
LOCK_FILE=/run/lock/pinhaoke-update.lock
SMOKE_ATTEMPTS=12
SMOKE_DELAY_SECONDS=2
SMOKE_MAX_SECONDS=15

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: this update must run as root" >&2
    exit 1
fi

for tool in awk chmod chown curl find flock git grep install journalctl mktemp mv python3 rm \
    sha256sum sleep systemctl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: required tool is missing: $tool" >&2
        exit 1
    fi
done

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "ERROR: another Pinhaoke update is already running" >&2
    exit 1
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
    echo "ERROR: $APP_DIR is not the expected Git checkout" >&2
    exit 1
fi

cd "$APP_DIR"

STAGE_DIR=""
BACKUP_VENV=""
SERVICE_STOPPED=0
DEPLOY_SUCCEEDED=0

cleanup() {
    local status=$?
    if [[ -n "$STAGE_DIR" && -d "$STAGE_DIR" ]]; then
        rm -rf -- "$STAGE_DIR"
    fi
    if [[ "$status" -eq 0 && "$DEPLOY_SUCCEEDED" -eq 1 && -n "$BACKUP_VENV" && -d "$BACKUP_VENV" ]]; then
        rm -rf -- "$BACKUP_VENV"
    elif [[ "$status" -ne 0 && -n "$BACKUP_VENV" && -d "$BACKUP_VENV" ]]; then
        echo "Previous environment preserved for rollback: $BACKUP_VENV" >&2
    fi
    return "$status"
}

on_error() {
    local status=$?
    local line=${1:-unknown}
    set +e
    echo "ERROR: update failed at line $line" >&2
    if [[ "$SERVICE_STOPPED" -eq 1 ]]; then
        journalctl -u "$SERVICE" -n 80 --no-pager >&2
    fi
    exit "$status"
}

trap cleanup EXIT
trap 'on_error "$LINENO"' ERR

# The lock makes it safe to remove abandoned staging directories from older runs.
find "$APP_DIR" -xdev -maxdepth 1 -type d -name '.deploy-stage.*' -mtime +1 \
    -exec rm -rf -- {} +
STAGE_DIR=$(mktemp -d "$APP_DIR/.deploy-stage.XXXXXXXX")
TARGET_REQUIREMENTS="$STAGE_DIR/requirements.txt"
CANDIDATE_VENV="$STAGE_DIR/venv"

echo "==> Fetching and resolving origin/main"
git fetch --prune origin "+refs/heads/main:refs/remotes/origin/main"
TARGET_COMMIT=$(git rev-parse --verify "refs/remotes/origin/main^{commit}")
git cat-file -e "$TARGET_COMMIT^{commit}"

TARGET_USES_LFS=0
TARGET_ATTRIBUTES="$STAGE_DIR/gitattributes"
if git cat-file -e "$TARGET_COMMIT:.gitattributes" 2>/dev/null; then
    git show "$TARGET_COMMIT:.gitattributes" >"$TARGET_ATTRIBUTES"
    if grep -q 'filter=lfs' "$TARGET_ATTRIBUTES"; then
        TARGET_USES_LFS=1
        if ! command -v git-lfs >/dev/null 2>&1; then
            echo "ERROR: target commit uses Git LFS but git-lfs is unavailable" >&2
            exit 1
        fi
        git lfs install --local
        git lfs fetch origin "$TARGET_COMMIT"
        git lfs fsck --objects "$TARGET_COMMIT"
    fi
fi

echo "==> Preparing target dependencies"
git show "$TARGET_COMMIT:requirements.txt" >"$TARGET_REQUIREMENTS"
TARGET_REQUIREMENTS_SHA=$(sha256sum "$TARGET_REQUIREMENTS" | awk '{print $1}')
CURRENT_REQUIREMENTS_SHA=""
if [[ -r "$LIVE_VENV/.requirements.sha256" ]]; then
    IFS= read -r CURRENT_REQUIREMENTS_SHA <"$LIVE_VENV/.requirements.sha256"
fi

NEED_VENV_SWAP=0
if [[ ! -x "$LIVE_VENV/bin/python" || ! -x "$LIVE_VENV/bin/uvicorn" || \
      "$CURRENT_REQUIREMENTS_SHA" != "$TARGET_REQUIREMENTS_SHA" ]]; then
    NEED_VENV_SWAP=1
    python3 -m venv "$CANDIDATE_VENV"
    "$CANDIDATE_VENV/bin/pip" install -r "$TARGET_REQUIREMENTS"
    "$CANDIDATE_VENV/bin/pip" check
    "$CANDIDATE_VENV/bin/python" -c \
        'import fastapi, starlette, uvicorn; print(fastapi.__version__, starlette.__version__, uvicorn.__version__)'
    printf '%s\n' "$TARGET_REQUIREMENTS_SHA" >"$CANDIDATE_VENV/.requirements.sha256"
else
    echo "    Live environment already matches target requirements"
fi

swap_candidate_venv() {
    BACKUP_VENV="$APP_DIR/.venv-backup-${TARGET_COMMIT:0:12}-$$"
    if [[ -e "$BACKUP_VENV" || -L "$BACKUP_VENV" ]]; then
        echo "ERROR: rollback path already exists: $BACKUP_VENV" >&2
        return 1
    fi

    if [[ -e "$LIVE_VENV" || -L "$LIVE_VENV" ]]; then
        if ! mv "$LIVE_VENV" "$BACKUP_VENV"; then
            echo "ERROR: could not preserve live environment" >&2
            return 1
        fi
    fi

    if ! mv "$CANDIDATE_VENV" "$LIVE_VENV"; then
        echo "ERROR: candidate environment rename failed; restoring previous environment" >&2
        if [[ -e "$BACKUP_VENV" || -L "$BACKUP_VENV" ]]; then
            mv "$BACKUP_VENV" "$LIVE_VENV"
            BACKUP_VENV=""
        fi
        return 1
    fi
}

verify_materialized_lfs() {
    local path first_line
    while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        if [[ ! -f "$path" ]]; then
            echo "ERROR: LFS path is missing after checkout: $path" >&2
            return 1
        fi
        first_line=""
        IFS= read -r first_line <"$path" || true
        if [[ "$first_line" == "version https://git-lfs.github.com/spec/v1" ]]; then
            echo "ERROR: LFS pointer was not materialized: $path" >&2
            return 1
        fi
    done < <(git lfs ls-files --name-only "$TARGET_COMMIT")
}

fail_with_journal() {
    local message=$1
    set +e
    echo "ERROR: $message" >&2
    journalctl -u "$SERVICE" -n 80 --no-pager >&2
    exit 1
}

validate_smoke_json() {
    local contract=$1
    local response_file=$2
    python3 - "$contract" "$response_file" <<'PY'
import json
import sys

contract, response_path = sys.argv[1:]
with open(response_path, encoding="utf-8") as response:
    data = json.load(response)

if contract == "health":
    databases = data.get("databases")
    assert data.get("status") == "ok"
    assert isinstance(databases, list) and len(databases) == 5
    assert {item.get("prefix") for item in databases} == {"a", "r", "u", "g", "s"}
    assert all(item.get("integrity") == "ok" for item in databases)
elif contract == "filters":
    assert isinstance(data, dict)
    for key in ("course_types", "categories", "departments", "credits", "gradings", "weekdays"):
        assert isinstance(data.get(key), list)
elif contract == "courses":
    assert data.get("total") == 4421
    courses = data.get("courses")
    assert isinstance(courses, list) and len(courses) == 1
    assert isinstance(courses[0], dict) and courses[0].get("id")
else:
    raise AssertionError(f"unknown smoke contract: {contract}")
PY
}

smoke_request() {
    local contract=$1
    local url=$2
    local response_file="$STAGE_DIR/smoke-$contract.json"
    local http_code attempt

    for ((attempt = 1; attempt <= SMOKE_ATTEMPTS; attempt++)); do
        http_code="000"
        if http_code=$(curl --silent --show-error --max-time "$SMOKE_MAX_SECONDS" \
            --output "$response_file" --write-out '%{http_code}' "$url") && \
            [[ "$http_code" == "200" ]] && validate_smoke_json "$contract" "$response_file"; then
            echo "    $contract smoke check passed"
            return 0
        fi
        echo "    $contract attempt $attempt/$SMOKE_ATTEMPTS failed (HTTP $http_code)" >&2
        sleep "$SMOKE_DELAY_SECONDS"
    done
    return 1
}

echo "==> Stopping service and activating $TARGET_COMMIT"
if ! systemctl stop "$SERVICE"; then
    fail_with_journal "could not stop $SERVICE"
fi
SERVICE_STOPPED=1

git reset --hard "$TARGET_COMMIT"
if [[ "$TARGET_USES_LFS" -eq 1 ]]; then
    git lfs checkout
    git lfs fsck --objects "$TARGET_COMMIT"
    verify_materialized_lfs
fi

install -o root -g root -m 0644 \
    "$APP_DIR/deploy/pinhaoke.service" "/etc/systemd/system/$SERVICE.service"

if [[ "$NEED_VENV_SWAP" -eq 1 ]]; then
    swap_candidate_venv
fi

# Root owns the release. www-data receives only traversal/read access, plus venv execution.
find "$APP_DIR" -xdev -exec chown root:www-data {} +
find "$APP_DIR" -xdev -type d -exec chmod 0750 {} +
find "$APP_DIR" -xdev -type f -exec chmod 0640 {} +
find "$LIVE_VENV/bin" -xdev -type f -exec chmod 0750 {} +
chmod 0750 "$APP_DIR" "$APP_DIR/Images" "$APP_DIR/数据库" "$APP_DIR/deploy/update.sh"

systemctl daemon-reload
if ! systemctl start "$SERVICE"; then
    fail_with_journal "could not start $SERVICE"
fi
if ! systemctl is-active --quiet "$SERVICE"; then
    fail_with_journal "$SERVICE did not become active"
fi

if ! smoke_request health "http://127.0.0.1:8000/api/health"; then
    fail_with_journal "health smoke check failed"
fi
if ! smoke_request filters "http://127.0.0.1:8000/api/filters?term=fall"; then
    fail_with_journal "filters smoke check failed"
fi
if ! smoke_request courses "http://127.0.0.1:8000/api/courses?term=fall&page_size=1"; then
    fail_with_journal "courses smoke check failed"
fi

DEPLOY_SUCCEEDED=1
SERVICE_STOPPED=0
echo "Update complete: $TARGET_COMMIT"
