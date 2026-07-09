#!/usr/bin/env python3
"""Translate remaining stubborn rows one language at a time."""
import argparse
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import certifi
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

API_KEY = os.environ.get("DEEPSEEK_API_KEY") or sys.exit(
    "Set DEEPSEEK_API_KEY env var (e.g. export DEEPSEEK_API_KEY=sk-...)"
)
API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.qnaigc.com/v1/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")
ENABLE_THINKING = os.environ.get("DEEPSEEK_ENABLE_THINKING")
THINKING = os.environ.get("DEEPSEEK_THINKING")

LANGS = ["en", "ja", "ko", "fr", "de", "es", "ru"]
LANG_NAMES = {
    "en": "English", "ja": "Japanese (日本語)", "ko": "Korean (한국어)",
    "fr": "French (français)", "de": "German (Deutsch)",
    "es": "Spanish (español)", "ru": "Russian (русский)",
}


def translate_one(text: str, target: str, retries: int = 3) -> str:
    prompt = (
        f"Translate the following Chinese university course content to "
        f"{LANG_NAMES[target]} ({target}). Output the translation directly, "
        f"no JSON wrapper, no commentary, no markdown.\n\n"
        f"Preserve newlines and bullet-list structure. Keep Latin abbreviations, "
        f"numbers, dates verbatim. Use standard academic terminology in the target "
        f"language. Translate institutional names to their official forms.\n\n"
        f"Source (Chinese):\n{text}"
    )
    body_obj = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional academic translator."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    if ENABLE_THINKING is not None:
        body_obj["enable_thinking"] = ENABLE_THINKING.lower() in {"1", "true", "yes"}
    elif "qnaigc.com" in API_URL:
        body_obj["enable_thinking"] = False
    if "api.deepseek.com" in API_URL:
        body_obj["thinking"] = {"type": THINKING or "disabled"}
    body = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300, context=SSL_CTX) as resp:
                r = json.loads(resp.read())
            return r["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise last


def gather_missing(db_path: str, src_field: str, store_field: str):
    """Return list[(course_id, source_text, [missing_langs])]."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        f"SELECT course_id, {src_field} FROM detail_info "
        f"WHERE {src_field} IS NOT NULL AND {src_field} != ''"
    ).fetchall()
    missing = []
    for cid, src in rows:
        have = {
            r[0] for r in conn.execute(
                "SELECT lang FROM translations WHERE course_id=? AND field=?",
                (cid, store_field),
            )
        }
        miss = [l for l in LANGS if l not in have]
        if miss:
            missing.append((cid, src, miss))
    conn.close()
    return missing


def write(db_path: str, cid: int, field: str, lang: str, text: str):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute(
        "INSERT OR REPLACE INTO translations VALUES (?,?,?,?)",
        (cid, field, lang, text),
    )
    conn.commit()
    conn.close()


def process(db_path, src_field, store_field, label, workers=10, limit=0):
    missing = gather_missing(db_path, src_field, store_field)
    if not missing:
        print(f"[{label}] all done")
        return 0, 0
    tasks = []
    for cid, src, langs in missing:
        for lang in langs:
            tasks.append((cid, src, lang))
    if limit:
        tasks = tasks[:limit]
    total = len(tasks)
    print(f"[{label}] {len(missing)} rows / {sum(len(m[2]) for m in missing)} pending pairs; running {total}")
    ok = 0
    fail = 0
    t0 = time.time()

    def work(item):
        cid, src, lang = item
        try:
            text = translate_one(src, lang)
            write(db_path, cid, store_field, lang, text)
            return True, cid, lang, len(text), None
        except Exception as e:
            return False, cid, lang, 0, f"{type(e).__name__}: {str(e)[:160]}"

    errors = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, task) for task in tasks]
        for done, fut in enumerate(as_completed(futs), 1):
            success, cid, lang, chars, err = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
                errors.append((cid, lang, err))
            if done % 50 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / max(elapsed, 0.01)
                eta = (total - done) / max(rate, 0.01)
                print(
                    f"  [{label}] {done}/{total} ok={ok} fail={fail} "
                    f"elapsed={elapsed:.0f}s rate={rate:.2f}/s eta={eta:.0f}s"
                )
    for cid, lang, err in errors[:10]:
        print(f"    [{label}] cid={cid} {lang}: {err}")

    return ok, fail


if __name__ == "__main__":
    from pathlib import Path
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _UG_DB = str(_PROJECT_ROOT / "数据库" / "2026春季学期本科生课程.db")
    _GR_DB = str(_PROJECT_ROOT / "数据库" / "2026春季学期研究生课程.db")
    _SUMMER_DB = str(_PROJECT_ROOT / "数据库" / "2026暑期本科生课程.db")
    _FALL_DB = str(_PROJECT_ROOT / "数据库" / "2026秋季学期本科生课程.db")

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", choices=["gr", "ug", "summer", "fall", "all"], default="all")
    ap.add_argument("--field", choices=["intro", "extra_notes", "syllabus", "evaluation", "reference_book", "all"], default="all")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    all_tasks = [
        ("gr", "intro", _GR_DB, "intro", "intro_cn", "GR intro"),
        ("gr", "extra_notes", _GR_DB, "extra_notes", "extra_notes", "GR extra"),
        ("ug", "intro", _UG_DB, "intro_cn", "intro_cn", "UG intro"),
        ("summer", "intro", _SUMMER_DB, "intro_cn", "intro_cn", "Summer intro"),
        ("summer", "syllabus", _SUMMER_DB, "syllabus", "syllabus", "Summer syllabus"),
        ("summer", "evaluation", _SUMMER_DB, "evaluation", "evaluation", "Summer evaluation"),
        ("fall", "intro", _FALL_DB, "intro_cn", "intro_cn", "Fall intro"),
        ("fall", "syllabus", _FALL_DB, "syllabus", "syllabus", "Fall syllabus"),
        ("fall", "evaluation", _FALL_DB, "evaluation", "evaluation", "Fall evaluation"),
        ("fall", "reference_book", _FALL_DB, "reference_book", "reference_book", "Fall reference_book"),
    ]
    selected = [
        task for task in all_tasks
        if args.db in ("all", task[0]) and args.field in ("all", task[1])
    ]
    print("== per-lang retranslate ==")
    total_ok = 0
    total_fail = 0
    for _, _, db_path, src_field, store_field, label in selected:
        ok, fail = process(db_path, src_field, store_field, label, workers=args.workers, limit=args.limit)
        total_ok += ok
        total_fail += fail
    print(f"\nTOTAL: OK={total_ok} FAIL={total_fail}")
