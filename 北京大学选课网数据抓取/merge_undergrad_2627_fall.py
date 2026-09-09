#!/usr/bin/env python3
"""Merge the September fall snapshot, retaining older-only classes and IDs."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import build_undergrad_2627_fall_db as builder  # noqa: E402

BASELINE = ROOT / "课程数据/北大本科课程_26-27第1学期.json"
LATEST = ROOT / "课程数据/20260907-黄庭逸选课网数据.json"
OUTPUT = ROOT / "课程数据/北大本科课程_26-27第1学期_20260907合并.json"
COVERED_TYPES = frozenset({"专业课", "通识课", "公选课"})


def record_key(row):
    return row["课程类型"], row["课程序号"]


def index_rows(rows):
    index = {}
    for row in rows:
        for field in ("课程类型", "课程序号", "数据学期"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"Missing {field}")
        if not isinstance(row.get("基本信息"), dict) or not isinstance(row.get("详细信息"), dict):
            raise ValueError("Missing course information")
        key = record_key(row)
        if key in index:
            raise ValueError(f"Duplicate snapshot key: {key}")
        index[key] = row
    return index


def merge_rows(baseline, latest):
    old_index, new_index = index_rows(baseline), index_rows(latest)
    if not baseline or not latest:
        raise ValueError("Both snapshots must contain courses")
    terms = {r["数据学期"] for r in [*baseline, *latest]}
    if len(terms) != 1:
        raise ValueError("Snapshots must describe the same semester")
    if {r["课程类型"] for r in latest} - COVERED_TYPES:
        raise ValueError("Unexpected latest-snapshot category")
    by_sequence = defaultdict(list)
    for row in latest:
        by_sequence[row["课程序号"]].append(row)

    merged, changes, used = [], [], set()
    counts = Counter()
    for local_id, old in enumerate(baseline, 1):
        key = record_key(old)
        variants = by_sequence.get(old["课程序号"], [])
        source_type = ""
        if key in new_index:
            replacement = deepcopy(new_index[key])
            used.add(key)
            action = "updated" if replacement != old else "unchanged_latest"
        elif variants:
            if old["课程类型"] in COVERED_TYPES:
                # A known class moving between covered categories is an update,
                # not an older-only class. Reuse its original ID.
                destinations = [r for r in variants if record_key(r) not in old_index and record_key(r) not in used]
                if len(destinations) != 1:
                    raise ValueError(f"Ambiguous category move: {key}")
                replacement = deepcopy(destinations[0])
                used.add(record_key(replacement))
                action = "reclassified"
            else:
                # Keep older-only classifications (PE/labor/etc.), while their
                # shared course information comes from the new snapshot.
                replacement = deepcopy(variants[0])
                source_type = replacement["课程类型"]
                replacement["课程类型"] = old["课程类型"]
                replacement["基本信息"]["课程类别"] = old["基本信息"].get("课程类别", "")
                action = "refreshed_other_category"
        else:
            replacement = deepcopy(old)
            action = "retained_old_only"
        for field in ("课程号", "班号"):
            if replacement["基本信息"][field] != old["基本信息"][field]:
                raise ValueError(f"Course identity changed for {key}: {field}")
        merged.append(replacement)
        counts[action] += 1
        changes.append({"id": local_id, "sequence": old["课程序号"], "action": action,
                        "oldType": old["课程类型"], "newType": replacement["课程类型"],
                        "sharedFieldsSourceType": source_type})
    for row in latest:
        if record_key(row) not in used:
            merged.append(deepcopy(row))
            used.add(record_key(row))
            counts["added"] += 1
            changes.append({"id": len(merged), "sequence": row["课程序号"], "action": "added",
                            "oldType": "", "newType": row["课程类型"], "sharedFieldsSourceType": ""})
    merged_index = index_rows(merged)
    if any(merged_index[key] != row for key, row in new_index.items()):
        raise ValueError("Merged data must preserve every latest-snapshot record exactly")
    return merged, {"counts": dict(counts), "records": len(merged),
                    "categories": dict(Counter(r["课程类型"] for r in merged)), "changes": changes}


def write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class PublicationRollbackError(RuntimeError):
    """Keep staged backups if a failed publication cannot be fully restored."""


def validate_output_paths(sources, targets):
    """Reject aliases before creating files, including existing hard links."""
    seen = list(sources)
    for target in targets:
        for other in seen:
            if target.resolve() == other.resolve() or (
                target.exists() and other.exists() and target.samefile(other)
            ):
                raise ValueError(f"Output paths must be distinct from sources and each other: {target}")
        if target.exists() and not target.is_file():
            raise ValueError(f"Output must be a file: {target}")
        seen.append(target)


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_files(replacements):
    """Publish validated candidates, restoring all previous files on failure."""
    backups = {}
    published = []
    directories = {target.parent for _candidate, target in replacements}
    # Prepare every backup before replacing any destination.
    for candidate, target in replacements:
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
        candidate.chmod(mode)
        with candidate.open("rb") as stream:
            os.fsync(stream.fileno())
        backup = candidate.parent / "previous"
        if target.exists():
            os.link(target, backup)
            backups[target] = backup
        else:
            backups[target] = None
    try:
        for candidate, target in replacements:
            os.replace(candidate, target)
            published.append(target)
        for directory in directories:
            sync_directory(directory)
    except BaseException:
        failures = []
        for target in reversed(published):
            try:
                backup = backups[target]
                if backup is None:
                    target.unlink()
                else:
                    os.replace(backup, target)
            except OSError as exc:
                failures.append(f"{target}: {exc}")
        for directory in directories:
            try:
                sync_directory(directory)
            except OSError as exc:
                failures.append(f"{directory}: {exc}")
        if failures:
            locations = ", ".join(str(candidate.parent) for candidate, _target in replacements)
            raise PublicationRollbackError(
                f"Publication rollback incomplete: {'; '.join(failures)}. "
                f"Staged files retained for recovery: {locations}"
            )
        raise


def build(baseline=BASELINE, latest=LATEST, output=OUTPUT, database=builder.DB_PATH, report=None):
    baseline, latest, output, database = map(Path, (baseline, latest, output, database))
    report = Path(report) if report is not None else None
    targets = [output, database, *([report] if report is not None else [])]
    validate_output_paths([baseline, latest], targets)
    old_rows = json.loads(baseline.read_text(encoding="utf-8"))
    new_rows = json.loads(latest.read_text(encoding="utf-8"))
    merged, audit = merge_rows(old_rows, new_rows)
    if database.exists():
        with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            actual = conn.execute("SELECT id, course_code, class_no FROM basic_info ORDER BY id LIMIT ?", (len(old_rows),)).fetchall()
        expected = [(i, r["基本信息"]["课程号"], r["基本信息"]["班号"]) for i, r in enumerate(old_rows, 1)]
        if actual != expected:
            raise ValueError("Existing course IDs do not match baseline order; refusing to remap links")
    staged = {}
    preserve_staging = False
    try:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix=f".{target.name}.merge-", dir=target.parent))
            staged[target] = directory / "candidate"
        write_json_atomic(staged[output], merged)
        prepared, duplicates = builder._prepare_rows(staged[output])
        if duplicates or len(prepared) != len(merged):
            raise ValueError("Merge would collapse database IDs")
        builder.build(source=staged[output], target=staged[database])
        audit["sources"] = {side: {"file": path.name, "sha256": hashlib.sha256(data_path.read_bytes()).hexdigest()}
                            for side, path, data_path in (("baseline", baseline, baseline), ("latest", latest, latest),
                                                         ("merged", output, staged[output]))}
        audit["database"] = {"file": database.name, "sha256": hashlib.sha256(staged[database].read_bytes()).hexdigest(),
                             "preservedBaselineIds": len(old_rows), "translations": 0}
        if report is not None:
            write_json_atomic(staged[report], audit)
        publish_files([(staged[target], target) for target in targets])
    except PublicationRollbackError:
        preserve_staging = True
        raise
    finally:
        if not preserve_staging:
            for candidate in staged.values():
                try:
                    shutil.rmtree(candidate.parent)
                except OSError as exc:
                    print(f"Temporary-file cleanup failed for {candidate.parent}: {exc}", file=sys.stderr)
    print(json.dumps({k: v for k, v in audit.items() if k != "changes"}, ensure_ascii=False, indent=2))
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--latest", type=Path, default=LATEST)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--database", type=Path, default=builder.DB_PATH)
    parser.add_argument("--report", type=Path)
    build(**vars(parser.parse_args()))
