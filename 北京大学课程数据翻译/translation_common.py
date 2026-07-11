"""Shared configuration and safe SQLite helpers for translation jobs."""

import argparse
import os
import sqlite3
import ssl
import time
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASES = {
    "ug": PROJECT_ROOT / "数据库" / "2026春季学期本科生课程.db",
    "gr": PROJECT_ROOT / "数据库" / "2026春季学期研究生课程.db",
    "summer": PROJECT_ROOT / "数据库" / "2026暑期本科生课程.db",
    "fall": PROJECT_ROOT / "数据库" / "2026秋季学期本科生课程.db",
    "fall_gr": PROJECT_ROOT / "数据库" / "2026秋季学期研究生课程.db",
}

LANGUAGES = ["en", "ja", "ko", "fr", "de", "es", "ru"]
LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "fr": "French (français)",
    "de": "German (Deutsch)",
    "es": "Spanish (español)",
    "ru": "Russian (русский)",
}


def create_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


SSL_CONTEXT = create_ssl_context()


def get_api_key() -> str:
    value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not value:
        raise RuntimeError("Set DEEPSEEK_API_KEY before starting translation")
    return value


def clean_translation(text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("translation must be a nonblank string")
    return text.strip()


def setup_translation_db(db_path: str | Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS translations (
                    course_id INTEGER NOT NULL,
                    field     TEXT NOT NULL,
                    lang      TEXT NOT NULL,
                    text      TEXT NOT NULL,
                    PRIMARY KEY (course_id, field, lang)
                );
                CREATE INDEX IF NOT EXISTS idx_trans_cid_field
                    ON translations(course_id, field);
                """
            )


def write_translation_with_retry(
    db_path: str | Path,
    course_id: int,
    field: str,
    lang: str,
    text: object,
    *,
    attempts: int = 5,
    base_delay: float = 0.25,
) -> None:
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    cleaned = clean_translation(text)
    for attempt in range(attempts):
        try:
            with closing(sqlite3.connect(db_path, timeout=30)) as conn:
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO translations VALUES (?,?,?,?)",
                        (course_id, field, lang, cleaned),
                    )
            return
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            retryable = "locked" in message or "busy" in message
            if not retryable or attempt == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return parsed
