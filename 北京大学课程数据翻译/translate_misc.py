#!/usr/bin/env python3
"""Translate ALL remaining Chinese-containing fields to 7 languages.

Strategy:
  - Dedupe by source value (many notes/categories repeat). Cache by source text.
  - Batch all 7 langs in one API call per unique source.
  - Store in translations(course_id, field, lang, text) for every (course, field)
    pair so the lookup is simple at request time.

Fields covered:
  Short (Phase A):
    UG basic_info  : course_name, notes, pnp, classroom, major
    UG detail_info : prerequisites, ge_series, textbook
    GR basic_info  : course_name, notes, classroom, major
    GR detail_info : audience, term, reference_book(short)
  Long (Phase B):
    UG detail_info : syllabus, evaluation
    GR detail_info : reference_book(long), extra_notes (already done)

Already done elsewhere:
  intro_cn (UG), intro (GR), extra_notes (GR) — handled by translate_courses.py

For English: re-use UG english_name when source is course_name. Skip API call.
"""
import argparse
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import certifi
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

API_KEY = os.environ.get("DEEPSEEK_API_KEY") or sys.exit(
    "Set DEEPSEEK_API_KEY env var (e.g. export DEEPSEEK_API_KEY=sk-...)"
)
API_URL = "https://api.qnaigc.com/v1/chat/completions"
MODEL = "deepseek/deepseek-v4-flash"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
UG_DB = str(_PROJECT_ROOT / "数据库" / "2026春季学期本科生课程.db")
GR_DB = str(_PROJECT_ROOT / "数据库" / "2026春季学期研究生课程.db")
SUMMER_DB = str(_PROJECT_ROOT / "数据库" / "2026暑期本科生课程.db")

LANGS = ["en", "ja", "ko", "fr", "de", "es", "ru"]
LANG_NAMES = {
    "en": "English", "ja": "Japanese (日本語)", "ko": "Korean (한국어)",
    "fr": "French (français)", "de": "German (Deutsch)",
    "es": "Spanish (español)", "ru": "Russian (русский)",
}

# (db_path, source_field, source_table, store_field, hint, is_long)
# 'hint' guides the translator (e.g. department names use official forms).
SHORT_JOBS = [
    (UG_DB, "course_name",   "basic_info",  "course_name",
     "Chinese university course title", False),
    (UG_DB, "notes",         "basic_info",  "notes",
     "Short course-registration note (may contain abbreviations, room numbers)", False),
    (UG_DB, "pnp",           "basic_info",  "pnp",
     "Whether P/NP grading can be elected", False),
    (UG_DB, "classroom",     "basic_info",  "classroom",
     "Classroom or building+room (e.g. 二教403 = Building 2 Room 403)", False),
    (UG_DB, "major",         "basic_info",  "major",
     "Major / specialization restriction", False),
    (UG_DB, "prerequisites", "detail_info", "prerequisites",
     "Course prerequisites (often a list of other course names)", False),
    (UG_DB, "ge_series",     "detail_info", "ge_series",
     "General Education core/elective series name", False),
    (UG_DB, "textbook",      "detail_info", "textbook",
     "Textbook citation(s)", False),
    (GR_DB, "course_name",   "basic_info",  "course_name",
     "Chinese university course title", False),
    (GR_DB, "notes",         "basic_info",  "notes",
     "Short course-registration note", False),
    (GR_DB, "classroom",     "basic_info",  "classroom",
     "Classroom or building+room", False),
    (GR_DB, "major",         "basic_info",  "major",
     "Major / specialization restriction", False),
    (GR_DB, "audience",      "detail_info", "audience",
     "Target audience (e.g. 硕博 = Master & PhD)", False),
    (GR_DB, "term",          "detail_info", "term",
     "Academic term / semester", False),

    # Summer undergrad (same schema as spring UG)
    (SUMMER_DB, "course_name",   "basic_info",  "course_name",
     "Chinese university course title", False),
    (SUMMER_DB, "notes",         "basic_info",  "notes",
     "Short course-registration note (may contain abbreviations, room numbers)", False),
    (SUMMER_DB, "pnp",           "basic_info",  "pnp",
     "Whether P/NP grading can be elected", False),
    (SUMMER_DB, "classroom",     "basic_info",  "classroom",
     "Classroom or building+room (e.g. 二教403 = Building 2 Room 403)", False),
    (SUMMER_DB, "major",         "basic_info",  "major",
     "Major / specialization restriction", False),
    (SUMMER_DB, "prerequisites", "detail_info", "prerequisites",
     "Course prerequisites (often a list of other course names)", False),
    (SUMMER_DB, "ge_series",     "detail_info", "ge_series",
     "General Education core/elective series name", False),
    (SUMMER_DB, "textbook",      "detail_info", "textbook",
     "Textbook citation(s)", False),
]
LONG_JOBS = [
    (UG_DB, "syllabus",     "detail_info", "syllabus",
     "Long-form syllabus / weekly schedule (preserve newlines and structure)", True),
    (UG_DB, "evaluation",   "detail_info", "evaluation",
     "Long-form teacher/student course evaluations (preserve structure)", True),
    (UG_DB, "reference_book","detail_info","reference_book",
     "Reference book list", True),
    (GR_DB, "reference_book","detail_info","reference_book",
     "Reference book list (citations, may be long)", True),

    # Summer undergrad
    (SUMMER_DB, "syllabus",     "detail_info", "syllabus",
     "Long-form syllabus / weekly schedule (preserve newlines and structure)", True),
    (SUMMER_DB, "evaluation",   "detail_info", "evaluation",
     "Long-form teacher/student course evaluations (preserve structure)", True),
    (SUMMER_DB, "reference_book","detail_info","reference_book",
     "Reference book list", True),
]


def has_cn(s: str) -> bool:
    return bool(s) and bool(re.search(r"[一-鿿]", s))


def setup_db(db_path: str):
    conn = sqlite3.connect(db_path)
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
    conn.commit()
    conn.close()


def call_api(text: str, langs, hint: str, max_retries: int = 3):
    """Batch-translate one Chinese text to multiple langs in one JSON call."""
    lang_spec = ", ".join(f"{l} ({LANG_NAMES[l]})" for l in langs)
    user_prompt = (
        f"Translate the following Chinese text ({hint}) to: {lang_spec}.\n\n"
        f"Return strict JSON with exactly these keys: {langs}. "
        f"No markdown, no commentary.\n\n"
        f"Guidelines:\n"
        f"- DO NOT translate proper nouns of people (人名).\n"
        f"- DO translate institutional names (院系/部门) using the OFFICIAL Peking "
        f"University English translation (e.g. 光华管理学院 → Guanghua School of "
        f"Management; 国家发展研究院 → National School of Development).\n"
        f"- Preserve numbers, codes, dates, room numbers verbatim.\n"
        f"- For room numbers like '二教403', expand to readable form in each target "
        f"language (e.g. EN: 'Building 2 Room 403', JA: '第2教学楼403教室').\n"
        f"- Preserve newlines and bullet structures.\n"
        f"- Use standard academic terminology.\n\n"
        f"Source (Chinese):\n{text}\n\nOutput JSON:"
    )
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system",
             "content": "You are a professional academic translator. Output only valid JSON."},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    last = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=300, context=SSL_CTX) as resp:
                r = json.loads(resp.read())
            content = r["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            missing = [l for l in langs if l not in parsed or not parsed[l]]
            if missing:
                raise ValueError(f"missing langs in response: {missing}")
            return parsed
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise last


def fetch_jobs(jobs, allow_non_cn=False):
    """Return list of (db_path, store_field, hint, course_id, source) needing work.

    allow_non_cn=True keeps rows whose source is pure English/Latin (no CJK).
    Use this to translate English-only course names into the other 6 langs.
    """
    out = []
    for db_path, src_field, src_table, store_field, hint, is_long in jobs:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            f"SELECT course_id AS cid, {src_field} AS src FROM {src_table} "
            f"WHERE {src_field} IS NOT NULL AND {src_field} != ''"
        ).fetchall() if src_table == "detail_info" else conn.execute(
            f"SELECT id AS cid, {src_field} AS src FROM {src_table} "
            f"WHERE {src_field} IS NOT NULL AND {src_field} != ''"
        ).fetchall()
        for cid, src in rows:
            if not allow_non_cn and not has_cn(src):
                continue
            have = {
                r[0]
                for r in conn.execute(
                    "SELECT lang FROM translations "
                    "WHERE course_id=? AND field=?",
                    (cid, store_field),
                )
            }
            missing = [l for l in LANGS if l not in have]
            if missing:
                out.append((db_path, store_field, hint, cid, src, missing))
        conn.close()
    return out


def reuse_english_for_course_names():
    """Copy english_name into translations as (course_id, 'course_name', 'en')."""
    for db_path in (UG_DB, GR_DB, SUMMER_DB):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT course_id, english_name FROM detail_info "
            "WHERE english_name IS NOT NULL AND english_name != ''"
        ).fetchall()
        n = 0
        for cid, en in rows:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO translations VALUES (?,?,?,?)",
                    (cid, "course_name", "en", en),
                )
                if conn.total_changes:
                    n += 1
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()
        print(f"[reuse en] {db_path}: {n} course_name 'en' rows seeded from english_name")


def write_row(db_path, cid, field, lang, text):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute(
        "INSERT OR REPLACE INTO translations VALUES (?,?,?,?)",
        (cid, field, lang, text),
    )
    conn.commit()
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["short", "long", "all"], default="short")
    ap.add_argument("--workers", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--db", choices=["ug", "gr", "summer", "all"], default="all",
                    help="Restrict jobs to one DB (ug = spring undergrad, gr = spring graduate, summer = summer undergrad). Default: all.")
    ap.add_argument("--allow-non-cn", action="store_true",
                    help="Also process rows whose source has no Chinese characters "
                         "(e.g. course_name is English-only). Use to translate "
                         "purely English course names into the other 6 target langs.")
    args = ap.parse_args()

    setup_db(UG_DB)
    setup_db(GR_DB)
    setup_db(SUMMER_DB)
    reuse_english_for_course_names()

    DB_FILTER = {
        "ug":     {UG_DB},
        "gr":     {GR_DB},
        "summer": {SUMMER_DB},
        "all":    {UG_DB, GR_DB, SUMMER_DB},
    }[args.db]

    jobs = []
    if args.phase in ("short", "all"):
        jobs.extend(SHORT_JOBS)
    if args.phase in ("long", "all"):
        jobs.extend(LONG_JOBS)
    jobs = [j for j in jobs if j[0] in DB_FILTER]

    pending = fetch_jobs(jobs, allow_non_cn=args.allow_non_cn)
    if args.limit:
        pending = pending[: args.limit]

    if not pending:
        print("Nothing pending.")
        return

    # Dedup cache: source -> {lang: translation}
    # Each unique source is translated at most once.
    cache: dict = {}
    cache_lock = __import__("threading").Lock()

    # Group pending items by source text. For each source, find the union of
    # missing langs across all rows that use it.
    by_source: dict = {}
    for db_path, store_field, hint, cid, src, missing in pending:
        key = (src, hint)
        by_source.setdefault(key, {"missing": set(), "rows": []})
        by_source[key]["missing"].update(missing)
        by_source[key]["rows"].append((db_path, store_field, cid, missing))

    total_unique = len(by_source)
    total_rows = len(pending)
    print(f"Unique sources : {total_unique}")
    print(f"Total (cid,fld): {total_rows}")

    completed_sources = 0
    completed_rows = 0
    errors = []
    t0 = time.time()

    def process_one(item):
        nonlocal completed_sources, completed_rows
        (src, hint), info = item
        missing_langs = sorted(info["missing"])
        try:
            translations = call_api(src, missing_langs, hint)
        except Exception as e:
            with cache_lock:
                errors.append((src[:60], str(e)[:200]))
            return
        with cache_lock:
            cache[src] = translations
        # Now write to DB for each row that needs this source
        for db_path, store_field, cid, want_langs in info["rows"]:
            for lang in want_langs:
                if lang in translations:
                    try:
                        write_row(db_path, cid, store_field, lang, translations[lang])
                    except Exception as e:
                        with cache_lock:
                            errors.append((f"write cid={cid} lang={lang}", str(e)))
            with cache_lock:
                completed_rows += 1
        with cache_lock:
            completed_sources += 1

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(process_one, item) for item in by_source.items()]
        last_print = 0
        for i, f in enumerate(futs):
            f.result()
            if completed_sources - last_print >= 25 or completed_sources == total_unique:
                elapsed = time.time() - t0
                rate = completed_sources / max(elapsed, 0.01)
                eta = (total_unique - completed_sources) / max(rate, 0.01)
                print(
                    f"  [{completed_sources}/{total_unique} unique  "
                    f"{completed_rows}/{total_rows} rows] "
                    f"elapsed={elapsed:.0f}s rate={rate:.1f}/s eta={eta:.0f}s "
                    f"errors={len(errors)}"
                )
                last_print = completed_sources

    elapsed = time.time() - t0
    print(f"\n== Done in {elapsed:.0f}s ==")
    print(f"  unique sources translated: {completed_sources}/{total_unique}")
    print(f"  rows updated             : {completed_rows}/{total_rows}")
    print(f"  errors                   : {len(errors)}")
    for src, err in errors[:10]:
        print(f"    {src!r}: {err}")


if __name__ == "__main__":
    main()
