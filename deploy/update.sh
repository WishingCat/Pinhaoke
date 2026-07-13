#!/usr/bin/env bash
# Update /opt/pinhaoke to the exact origin/main commit after all preflight work succeeds.

set -Eeuo pipefail

APP_DIR=${APP_DIR:-/opt/pinhaoke}
LIVE_VENV=${LIVE_VENV:-"$APP_DIR/venv"}
SERVICE=${SERVICE:-pinhaoke}
UNIT_PATH=${UNIT_PATH:-"/etc/systemd/system/$SERVICE.service"}
LOCK_FILE=${LOCK_FILE:-/run/lock/pinhaoke-update.lock}
SMOKE_ATTEMPTS=${SMOKE_ATTEMPTS:-12}
SMOKE_DELAY_SECONDS=${SMOKE_DELAY_SECONDS:-2}
SMOKE_MAX_SECONDS=${SMOKE_MAX_SECONDS:-15}

STAGE_DIR=""
TARGET_TREE=""
TARGET_INDEX=""
PREVIOUS_TREE=""
PREVIOUS_INDEX=""
CANDIDATE_VENV=""
TARGET_COMMIT=""
TARGET_USES_LFS=0
PREVIOUS_COMMIT=""
PREVIOUS_USES_LFS=0
PREVIOUS_SERVICE_ACTIVE=0
PREVIOUS_VENV_EXISTED=0
VENV_SWAPPED=0
UNIT_BACKUP=""
UNIT_PREVIOUSLY_EXISTED=0
BACKUP_VENV=""
FAILED_VENV=""
ACTIVATION_STARTED=0
SERVICE_STOPPED=0
DEPLOY_SUCCEEDED=0
ROLLBACK_IN_PROGRESS=0

verify_materialized_lfs() {
    local root=$1
    local commit=$2
    local relative path first_line

    while IFS= read -r relative; do
        [[ -z "$relative" ]] && continue
        path="$root/$relative"
        if [[ ! -f "$path" ]]; then
            echo "ERROR: LFS path is missing after checkout: $relative" >&2
            return 1
        fi
        first_line=""
        IFS= read -r first_line <"$path" || true
        if [[ "$first_line" == "version https://git-lfs.github.com/spec/v1" ]]; then
            echo "ERROR: LFS pointer was not materialized: $relative" >&2
            return 1
        fi
    done < <(git lfs ls-files --name-only "$commit")
}

materialize_release_tree() {
    local commit=$1
    local tree=$2
    local index=$3

    mkdir -p "$tree"
    GIT_INDEX_FILE="$index" git read-tree "$commit"
    # Attribute rules must come from the release being expanded, not the live checkout.
    GIT_ATTR_SOURCE="$commit" GIT_INDEX_FILE="$index" \
        git checkout-index --all --force --prefix="$tree/"
}

preflight_previous_lfs_release() {
    local commit=$1
    local tree=${2:-$PREVIOUS_TREE}
    local index=${3:-$PREVIOUS_INDEX}

    if [[ "$PREVIOUS_USES_LFS" -ne 1 ]]; then
        return 0
    fi
    if ! command -v git-lfs >/dev/null 2>&1; then
        echo "ERROR: previous commit uses Git LFS but git-lfs is unavailable" >&2
        return 1
    fi

    git lfs install --local
    git lfs fetch origin "$commit"
    git lfs fsck --objects "$commit"
    materialize_release_tree "$commit" "$tree" "$index"
    git lfs fsck --objects "$commit"
    verify_materialized_lfs "$tree" "$commit"
}

swap_candidate_venv() {
    BACKUP_VENV="$APP_DIR/.venv-backup-${TARGET_COMMIT:0:12}-$$"
    if [[ -e "$BACKUP_VENV" || -L "$BACKUP_VENV" ]]; then
        echo "ERROR: rollback path already exists: $BACKUP_VENV" >&2
        return 1
    fi

    if [[ -e "$LIVE_VENV" || -L "$LIVE_VENV" ]]; then
        PREVIOUS_VENV_EXISTED=1
        mv "$LIVE_VENV" "$BACKUP_VENV"
    fi

    if ! mv "$CANDIDATE_VENV" "$LIVE_VENV"; then
        echo "ERROR: candidate environment rename failed; restoring previous environment" >&2
        if [[ -e "$BACKUP_VENV" || -L "$BACKUP_VENV" ]]; then
            mv "$BACKUP_VENV" "$LIVE_VENV"
            BACKUP_VENV=""
        fi
        return 1
    fi
    VENV_SWAPPED=1
}

apply_release_permissions() {
    # Keep Git metadata, staging trees, and rollback environments out of release chmods.
    chown root:www-data "$APP_DIR"
    find "$APP_DIR" -xdev \
        \( -path "$APP_DIR/.git" \
           -o -path "$APP_DIR/.deploy-stage.*" \
           -o -path "$APP_DIR/.venv-backup-*" \
           -o -path "$APP_DIR/.venv-failed-*" \
           -o -path "$LIVE_VENV" \) -prune \
        -o -type d -exec chown -h root:www-data {} + -exec chmod 0750 {} +
    find "$APP_DIR" -xdev \
        \( -path "$APP_DIR/.git" \
           -o -path "$APP_DIR/.deploy-stage.*" \
           -o -path "$APP_DIR/.venv-backup-*" \
           -o -path "$APP_DIR/.venv-failed-*" \
           -o -path "$LIVE_VENV" \) -prune \
        -o -type f -exec chown -h root:www-data {} + -exec chmod 0640 {} +
    find "$APP_DIR" -xdev \
        \( -path "$APP_DIR/.git" \
           -o -path "$APP_DIR/.deploy-stage.*" \
           -o -path "$APP_DIR/.venv-backup-*" \
           -o -path "$APP_DIR/.venv-failed-*" \
           -o -path "$LIVE_VENV" \) -prune \
        -o -type l -exec chown -h root:www-data {} +

    if [[ -d "$LIVE_VENV" ]]; then
        chown -h root:root "$LIVE_VENV"
        find "$LIVE_VENV" -xdev -type d -exec chown -h root:root {} + -exec chmod 0755 {} +
        find "$LIVE_VENV" -xdev -type f -exec chown -h root:root {} + -exec chmod 0644 {} +
        find "$LIVE_VENV" -xdev -type l -exec chown -h root:root {} +
        find "$LIVE_VENV/bin" -xdev -type f -exec chmod 0755 {} +
    fi

    chmod 0750 "$APP_DIR" "$APP_DIR/Images" "$APP_DIR/数据库" "$APP_DIR/deploy/update.sh"
}

rollback_activation() {
    local reason=${1:-unknown}
    local rollback_failed=0

    if [[ "$ACTIVATION_STARTED" -ne 1 || "$ROLLBACK_IN_PROGRESS" -eq 1 ]]; then
        return 0
    fi
    ROLLBACK_IN_PROGRESS=1
    trap - ERR INT TERM HUP
    set +e
    echo "==> Rolling back failed activation: $reason" >&2

    systemctl stop "$SERVICE" || rollback_failed=1

    if [[ -n "$PREVIOUS_COMMIT" ]]; then
        git reset --hard "$PREVIOUS_COMMIT" || rollback_failed=1
        if [[ "$PREVIOUS_USES_LFS" -eq 1 ]]; then
            git lfs checkout || rollback_failed=1
            git lfs fsck --objects "$PREVIOUS_COMMIT" || rollback_failed=1
        fi
    fi

    if [[ "$VENV_SWAPPED" -eq 1 && ( -e "$BACKUP_VENV" || -L "$BACKUP_VENV" ) ]]; then
        if [[ -e "$LIVE_VENV" || -L "$LIVE_VENV" ]]; then
            FAILED_VENV="$APP_DIR/.venv-failed-${TARGET_COMMIT:0:12}-$$"
            mv "$LIVE_VENV" "$FAILED_VENV" || rollback_failed=1
        fi
        mv "$BACKUP_VENV" "$LIVE_VENV" || rollback_failed=1
        BACKUP_VENV=""
    elif [[ "$VENV_SWAPPED" -eq 1 && "$PREVIOUS_VENV_EXISTED" -eq 0 && \
            ( -e "$LIVE_VENV" || -L "$LIVE_VENV" ) ]]; then
        FAILED_VENV="$APP_DIR/.venv-failed-${TARGET_COMMIT:0:12}-$$"
        mv "$LIVE_VENV" "$FAILED_VENV" || rollback_failed=1
    fi

    if [[ "$UNIT_PREVIOUSLY_EXISTED" -eq 1 ]]; then
        cp -p "$UNIT_BACKUP" "$UNIT_PATH" || rollback_failed=1
    else
        rm -f -- "$UNIT_PATH" || rollback_failed=1
    fi

    apply_release_permissions || rollback_failed=1
    systemctl daemon-reload || rollback_failed=1
    if [[ "$PREVIOUS_SERVICE_ACTIVE" -eq 1 ]]; then
        systemctl start "$SERVICE" || rollback_failed=1
        systemctl is-active --quiet "$SERVICE" || rollback_failed=1
    fi

    ACTIVATION_STARTED=0
    ROLLBACK_IN_PROGRESS=0
    if [[ "$rollback_failed" -ne 0 ]]; then
        echo "ERROR: automatic rollback was incomplete; manual recovery is required" >&2
        return 1
    fi
    echo "Previous release restored." >&2
    return 0
}

cleanup() {
    local status=$?
    set +e
    if [[ "$ACTIVATION_STARTED" -eq 1 && "$DEPLOY_SUCCEEDED" -ne 1 ]]; then
        rollback_activation "unexpected exit $status" || status=1
    fi
    if [[ -n "$STAGE_DIR" && -d "$STAGE_DIR" ]]; then
        rm -rf -- "$STAGE_DIR"
    fi
    if [[ "$status" -eq 0 && "$DEPLOY_SUCCEEDED" -eq 1 && -n "$BACKUP_VENV" && -d "$BACKUP_VENV" ]]; then
        rm -rf -- "$BACKUP_VENV"
    elif [[ -n "$BACKUP_VENV" && -d "$BACKUP_VENV" ]]; then
        echo "Previous environment preserved for manual recovery: $BACKUP_VENV" >&2
    fi
    return "$status"
}

on_error() {
    local status=$?
    local line=${1:-unknown}
    set +e
    echo "ERROR: update failed at line $line" >&2
    rollback_activation "error at line $line" || status=1
    journalctl -u "$SERVICE" -n 80 --no-pager >&2
    exit "$status"
}

on_signal() {
    local signal=$1
    local status=130
    [[ "$signal" == "HUP" ]] && status=129
    [[ "$signal" == "TERM" ]] && status=143
    set +e
    echo "ERROR: update interrupted by $signal" >&2
    rollback_activation "signal $signal" || status=1
    exit "$status"
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
    reviews = data.get("reviews")
    assert data.get("status") == "ok"
    assert isinstance(databases, list) and len(databases) == 5
    assert {item.get("prefix") for item in databases} == {"a", "r", "u", "g", "s"}
    assert all(item.get("integrity") == "ok" for item in databases)
    assert isinstance(reviews, dict) and reviews.get("integrity") == "ok"
    assert isinstance(reviews.get("threads"), int) and reviews["threads"] > 0
    assert isinstance(reviews.get("entries"), int) and reviews["entries"] > 0
    assert isinstance(reviews.get("snapshot_replies"), int) and reviews["snapshot_replies"] > 0
    assert isinstance(reviews.get("highlights"), int) and reviews["highlights"] > 0
elif contract == "filters":
    assert isinstance(data, dict)
    for key in ("course_types", "categories", "departments", "credits", "gradings", "weekdays"):
        assert isinstance(data.get(key), list)
elif contract == "courses":
    assert isinstance(data.get("total"), int) and data["total"] > 0
    courses = data.get("courses")
    assert isinstance(courses, list) and len(courses) == 1
    assert isinstance(courses[0], dict) and courses[0].get("id")
elif contract == "reviews":
    assert isinstance(data.get("total"), int) and data["total"] > 0
    threads = data.get("threads")
    assert isinstance(threads, list) and len(threads) == 1
    assert isinstance(threads[0], dict) and threads[0].get("pid")
    assert isinstance(threads[0].get("entries"), list) and threads[0]["entries"]
elif contract == "review-thread":
    assert isinstance(data.get("pid"), int) and data["pid"] > 0
    assert isinstance(data.get("content"), str) and data["content"]
    assert isinstance(data.get("reply_count"), int) and data["reply_count"] >= 0
    replies = data.get("replies")
    assert isinstance(replies, list) and len(replies) == data["reply_count"]
    assert all(
        isinstance(reply, dict)
        and set(reply) == {"cid", "floor", "posted_at", "content"}
        for reply in replies
    )
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

cleanup_abandoned_artifacts() {
    find "$APP_DIR" -xdev -maxdepth 1 -type d -name '.deploy-stage.*' -mtime +1 \
        -exec rm -rf -- {} +
    find "$APP_DIR" -xdev -maxdepth 1 -type d -name '.venv-failed-*' -mtime +7 \
        -exec rm -rf -- {} +
}

main() {
    local tool

    if [[ "$EUID" -ne 0 ]]; then
        echo "ERROR: this update must run as root" >&2
        exit 1
    fi

    for tool in awk chmod chown cp curl find flock git grep install journalctl mktemp mv python3 rm \
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
    trap cleanup EXIT
    trap 'on_error "$LINENO"' ERR
    trap 'on_signal INT' INT
    trap 'on_signal TERM' TERM
    trap 'on_signal HUP' HUP

    cleanup_abandoned_artifacts
    STAGE_DIR=$(mktemp -d "$APP_DIR/.deploy-stage.XXXXXXXX")
    TARGET_TREE="$STAGE_DIR/target-tree"
    TARGET_INDEX="$STAGE_DIR/index"
    PREVIOUS_TREE="$STAGE_DIR/previous-tree"
    PREVIOUS_INDEX="$STAGE_DIR/previous-index"
    CANDIDATE_VENV="$STAGE_DIR/venv"
    mkdir -p "$TARGET_TREE"

    echo "==> Fetching and resolving origin/main"
    git fetch --prune origin "+refs/heads/main:refs/remotes/origin/main"
    TARGET_COMMIT=$(git rev-parse --verify "refs/remotes/origin/main^{commit}")
    git cat-file -e "$TARGET_COMMIT^{commit}"
    PREVIOUS_COMMIT=$(git rev-parse --verify "HEAD^{commit}")

    if systemctl is-active --quiet "$SERVICE"; then
        PREVIOUS_SERVICE_ACTIVE=1
    fi
    if git cat-file -e "$PREVIOUS_COMMIT:.gitattributes" 2>/dev/null && \
        git show "$PREVIOUS_COMMIT:.gitattributes" | grep -q 'filter=lfs'; then
        PREVIOUS_USES_LFS=1
    fi
    if [[ -e "$UNIT_PATH" || -L "$UNIT_PATH" ]]; then
        UNIT_PREVIOUSLY_EXISTED=1
        UNIT_BACKUP="$STAGE_DIR/previous.service"
        cp -p "$UNIT_PATH" "$UNIT_BACKUP"
    fi

    echo "==> Verifying previous release rollback data before downtime"
    preflight_previous_lfs_release "$PREVIOUS_COMMIT" "$PREVIOUS_TREE" "$PREVIOUS_INDEX"

    if git cat-file -e "$TARGET_COMMIT:.gitattributes" 2>/dev/null; then
        git show "$TARGET_COMMIT:.gitattributes" >"$STAGE_DIR/gitattributes"
        if grep -q 'filter=lfs' "$STAGE_DIR/gitattributes"; then
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

    echo "==> Materializing target release before downtime"
    materialize_release_tree "$TARGET_COMMIT" "$TARGET_TREE" "$TARGET_INDEX"
    if [[ "$TARGET_USES_LFS" -eq 1 ]]; then
        git lfs fsck --objects "$TARGET_COMMIT"
        verify_materialized_lfs "$TARGET_TREE" "$TARGET_COMMIT"
    fi

    echo "==> Preparing target dependencies"
    TARGET_REQUIREMENTS="$TARGET_TREE/requirements.txt"
    TARGET_REQUIREMENTS_SHA=$(sha256sum "$TARGET_REQUIREMENTS" | awk '{print $1}')
    CURRENT_REQUIREMENTS_SHA=""
    if [[ -r "$LIVE_VENV/.requirements.sha256" ]]; then
        IFS= read -r CURRENT_REQUIREMENTS_SHA <"$LIVE_VENV/.requirements.sha256"
    fi

    NEED_VENV_SWAP=0
    if [[ ! -x "$LIVE_VENV/bin/python" || "$CURRENT_REQUIREMENTS_SHA" != "$TARGET_REQUIREMENTS_SHA" ]]; then
        NEED_VENV_SWAP=1
        python3 -m venv "$CANDIDATE_VENV"
        "$CANDIDATE_VENV/bin/python" -m pip install -r "$TARGET_REQUIREMENTS"
        "$CANDIDATE_VENV/bin/python" -m pip check
        "$CANDIDATE_VENV/bin/python" -c \
            'import fastapi, starlette, uvicorn; print(fastapi.__version__, starlette.__version__, uvicorn.__version__)'
        printf '%s\n' "$TARGET_REQUIREMENTS_SHA" >"$CANDIDATE_VENV/.requirements.sha256"
    else
        echo "    Live environment already matches target requirements"
    fi

    echo "==> Stopping service and activating $TARGET_COMMIT"
    ACTIVATION_STARTED=1
    systemctl stop "$SERVICE"
    SERVICE_STOPPED=1

    git reset --hard "$TARGET_COMMIT"
    if [[ "$TARGET_USES_LFS" -eq 1 ]]; then
        git lfs checkout
        git lfs fsck --objects "$TARGET_COMMIT"
        verify_materialized_lfs "$APP_DIR" "$TARGET_COMMIT"
    fi

    install -o root -g root -m 0644 "$APP_DIR/deploy/pinhaoke.service" "$UNIT_PATH"
    if [[ "$NEED_VENV_SWAP" -eq 1 ]]; then
        swap_candidate_venv
    fi
    "$LIVE_VENV/bin/python" -m uvicorn --version
    apply_release_permissions

    systemctl daemon-reload
    systemctl start "$SERVICE"
    systemctl is-active --quiet "$SERVICE"

    smoke_request health "http://127.0.0.1:8000/api/health"
    smoke_request filters "http://127.0.0.1:8000/api/filters?term=fall"
    smoke_request courses "http://127.0.0.1:8000/api/courses?term=fall&page_size=1"
    smoke_request reviews "http://127.0.0.1:8000/api/reviews?page_size=1"
    local review_pid
    review_pid=$(python3 - "$STAGE_DIR/smoke-reviews.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as response:
    print(json.load(response)["threads"][0]["pid"])
PY
    )
    [[ "$review_pid" =~ ^[1-9][0-9]*$ ]]
    smoke_request review-thread "http://127.0.0.1:8000/api/reviews/$review_pid"

    DEPLOY_SUCCEEDED=1
    ACTIVATION_STARTED=0
    SERVICE_STOPPED=0
    echo "Update complete: $TARGET_COMMIT"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
