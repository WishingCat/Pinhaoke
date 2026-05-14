#!/usr/bin/env python3
"""Translate remaining stubborn (long) rows one language at a time."""
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import certifi
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

API_KEY = os.environ.get("DEEPSEEK_API_KEY") or sys.exit(
    "Set DEEPSEEK_API_KEY env var (e.g. export DEEPSEEK_API_KEY=sk-...)"
)
API_URL = "https://api.qnaigc.com/v1/chat/completions"
MODEL = "deepseek/deepseek-v4-flash"

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
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional academic translator."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "enable_thinking": False,
    }).encode("utf-8")
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


def process(db_path, src_field, store_field, label):
    missing = gather_missing(db_path, src_field, store_field)
    if not missing:
        print(f"[{label}] all done")
        return 0, 0
    total = sum(len(m[2]) for m in missing)
    print(f"[{label}] {len(missing)} rows / {total} (cid,lang) pairs to translate")
    ok = 0
    fail = 0

    def work(item):
        nonlocal ok, fail
        cid, src, langs = item
        for lang in langs:
            try:
                t = translate_one(src, lang)
                write(db_path, cid, store_field, lang, t)
                ok += 1
                print(f"  [{label}] cid={cid} {lang}: OK ({len(t)} chars)")
            except Exception as e:
                fail += 1
                print(f"  [{label}] cid={cid} {lang}: FAIL {type(e).__name__}: {str(e)[:120]}")

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(work, missing))

    return ok, fail


if __name__ == "__main__":
    from pathlib import Path
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _UG_DB = str(_PROJECT_ROOT / "数据库" / "2026春季学期本科生课程.db")
    _GR_DB = str(_PROJECT_ROOT / "数据库" / "2026春季学期研究生课程.db")
    _SUMMER_DB = str(_PROJECT_ROOT / "数据库" / "2026暑期本科生课程.db")
    print("== sequential per-lang retranslate ==")
    o1, f1 = process(_GR_DB, "intro", "intro_cn", "GR intro")
    o2, f2 = process(_GR_DB, "extra_notes", "extra_notes", "GR extra")
    o3, f3 = process(_UG_DB, "intro_cn", "intro_cn", "UG intro")
    o4, f4 = process(_SUMMER_DB, "intro_cn", "intro_cn", "Summer intro")
    o5, f5 = process(_SUMMER_DB, "syllabus", "syllabus", "Summer syllabus")
    o6, f6 = process(_SUMMER_DB, "evaluation", "evaluation", "Summer evaluation")
    print(f"\nTOTAL: OK={o1+o2+o3+o4+o5+o6} FAIL={f1+f2+f3+f4+f5+f6}")
