"""Atomic SQLite build and source-validation helpers."""

import json
import math
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path


REQUIRED_TABLES = {"basic_info", "detail_info", "translations"}
TRANSLATIONS_PRIMARY_KEY = ("course_id", "field", "lang")


class _AtomicConnection(sqlite3.Connection):
    validated_expected_rows = None


def _remove_database_files(path):
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _prepare_replacement(path):
    with path.open("rb") as database_file:
        os.fsync(database_file.fileno())


@contextmanager
def atomic_database(target: Path, schema: str):
    """Build a SQLite file beside target and replace target only after validation."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = None
    temp_path = None
    conn = None

    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temp_path = Path(raw_path)
        os.close(descriptor)
        descriptor = None

        conn = sqlite3.connect(temp_path, factory=_AtomicConnection)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema)
        yield conn

        expected_rows = conn.validated_expected_rows
        if expected_rows is None:
            raise RuntimeError(
                "validate_built_database must succeed before atomic replacement"
            )
        validate_built_database(conn, expected_rows=expected_rows)
        conn.commit()
        validate_built_database(conn, expected_rows=expected_rows)
        conn.close()
        conn = None

        _prepare_replacement(temp_path)
        os.replace(temp_path, target)
        temp_path = None
    except BaseException:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if conn is not None:
            with suppress(BaseException):
                conn.close()
        if temp_path is not None:
            _remove_database_files(temp_path)
        raise


def validate_built_database(
    conn: sqlite3.Connection, expected_rows: int
) -> None:
    """Validate the shared schema contract and exact 1:1 course row counts."""
    if isinstance(expected_rows, bool) or not isinstance(expected_rows, int):
        raise ValueError("expected_rows must be a non-negative integer")
    if expected_rows < 0:
        raise ValueError("expected_rows must be a non-negative integer")

    objects = {
        (row[0], row[1])
        for row in conn.execute("SELECT name, type FROM sqlite_master")
    }
    missing_tables = sorted(
        table for table in REQUIRED_TABLES if (table, "table") not in objects
    )
    if missing_tables:
        raise ValueError(f"required tables missing: {', '.join(missing_tables)}")
    if ("courses_view", "view") not in objects:
        raise ValueError("required view missing: courses_view")

    translation_columns = conn.execute(
        "PRAGMA table_info(translations)"
    ).fetchall()
    translation_pk = tuple(
        row[1]
        for row in sorted(
            (row for row in translation_columns if row[5]), key=lambda row: row[5]
        )
    )
    if translation_pk != TRANSLATIONS_PRIMARY_KEY:
        raise ValueError(
            "translations primary key must be (course_id, field, lang); "
            f"got {translation_pk}"
        )

    foreign_key_failures = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_failures:
        raise ValueError(
            f"database validation failed: foreign key violations={foreign_key_failures}"
        )

    integrity = conn.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise ValueError(f"database validation failed: integrity={integrity}")

    basic_rows = conn.execute("SELECT COUNT(*) FROM basic_info").fetchone()[0]
    detail_rows = conn.execute("SELECT COUNT(*) FROM detail_info").fetchone()[0]
    if basic_rows != expected_rows or detail_rows != expected_rows:
        raise ValueError(
            "database validation failed: row count mismatch "
            f"expected={expected_rows} basic={basic_rows} detail={detail_rows}"
        )

    if isinstance(conn, _AtomicConnection):
        conn.validated_expected_rows = expected_rows


@dataclass(frozen=True)
class CourseSourceRow:
    source: Path
    index: int
    item: Mapping
    basic: Mapping
    detail: Mapping

    @property
    def context(self):
        return f"{self.source} row {self.index}"


def load_course_rows(source):
    """Load and validate the common JSON container/object contract."""
    source = Path(source)
    with source.open(encoding="utf-8") as source_file:
        data = json.load(source_file)
    if not isinstance(data, list):
        raise ValueError(f"{source}: top-level JSON value must be a list")

    rows = []
    for index, item in enumerate(data):
        context = f"{source} row {index}"
        if not isinstance(item, Mapping):
            raise ValueError(f"{context}: row must be a mapping")

        basic = item.get("基本信息")
        if not isinstance(basic, Mapping):
            raise ValueError(f"{context}: 基本信息 must be a mapping")

        detail = item.get("详细信息")
        if detail is None:
            detail = {}
        elif not isinstance(detail, Mapping):
            raise ValueError(f"{context}: 详细信息 must be a mapping or null")

        rows.append(CourseSourceRow(source, index, item, basic, detail))
    return rows


def required_text(mapping, key, context):
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{context}: {key} must be text and cannot be blank")
    value = value.strip()
    if not value:
        raise ValueError(f"{context}: {key} cannot be blank")
    return value


def optional_text(mapping, key, context, *, strip=False):
    value = mapping.get(key, "")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{context}: {key} must be text or null")
    if strip:
        return value.strip()
    return value


def strict_credit(mapping, context):
    value = mapping.get("学分")
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{context}: invalid 学分 value {value!r}")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{context}: invalid 学分 value {value!r}")
    try:
        credit = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{context}: invalid 学分 value {value!r}") from None
    if not math.isfinite(credit):
        raise ValueError(f"{context}: invalid 学分 value {value!r}")
    return credit


def deduplicate_rows(rows):
    """Deduplicate equal shaped rows and reject conflicting UNIQUE keys."""
    prepared = []
    seen = {}
    duplicates = 0
    for key, semantic_content, database_row, context in rows:
        previous = seen.get(key)
        if previous is None:
            seen[key] = (semantic_content, context)
            prepared.append(database_row)
            continue
        previous_content, previous_context = previous
        if semantic_content != previous_content:
            raise ValueError(
                f"{context}: conflicting duplicate for UNIQUE key {key!r}; "
                f"first seen at {previous_context}"
            )
        duplicates += 1
    return prepared, duplicates
