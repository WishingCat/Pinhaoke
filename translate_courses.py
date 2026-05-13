#!/usr/bin/env python3
"""Batch-translate course intro and extra_notes via DeepSeek v4-pro into 7 languages.

Stores results in a `translations` table per DB:
    translations(course_id INTEGER, field TEXT, lang TEXT, text TEXT, PRIMARY KEY(...))

Scope (per user request 2026-05-13):
  - undergrad detail_info.intro_cn      → field 'intro_cn'
  - graduate  detail_info.intro          → field 'intro_cn' (same UI slot)
  - graduate  detail_info.extra_notes    → field 'extra_notes'

For English, reuse undergrad detail_info.intro_en when present (no API call needed).

Resumable: re-running skips (course_id, field, lang) triples already in translations.
"""
import argparse
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

API_KEY = os.environ.get("DEEPSEEK_API_KEY") or sys.exit(
    "Set DEEPSEEK_API_KEY env var (e.g. export DEEPSEEK_API_KEY=sk-...)"
)
API_URL = "https://api.qnaigc.com/v1/chat/completions"
MODEL = "deepseek/deepseek-v4-flash"

UG_DB = "2026春季学期本科生课程.db"
GR_DB = "2026春季学期研究生课程.db"

LANGS = ["en", "ja", "ko", "fr", "de", "es", "ru"]
LANG_NAMES = {
    "en": "English",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "fr": "French (français)",
    "de": "German (Deutsch)",
    "es": "Spanish (español)",
    "ru": "Russian (русский)",
}


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


def fetch_pending_undergrad():
    """Returns list[(course_id, source_text, missing_langs)]."""
    conn = sqlite3.connect(UG_DB)
    rows = conn.execute(
        "SELECT course_id, intro_cn, intro_en FROM detail_info "
        "WHERE intro_cn IS NOT NULL AND intro_cn != ''"
    ).fetchall()
    pending = []
    for cid, src, intro_en in rows:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT lang FROM translations WHERE course_id=? AND field='intro_cn'",
                (cid,),
            )
        }
        missing = [l for l in LANGS if l not in existing]
        if "en" in missing and intro_en and intro_en.strip():
            conn.execute(
                "INSERT OR REPLACE INTO translations VALUES (?,?,?,?)",
                (cid, "intro_cn", "en", intro_en),
            )
            missing.remove("en")
        if missing:
            pending.append((cid, src, missing))
    conn.commit()
    conn.close()
    return pending


def fetch_pending_grad(field_src: str, field_store: str):
    conn = sqlite3.connect(GR_DB)
    rows = conn.execute(
        f"SELECT course_id, {field_src} FROM detail_info "
        f"WHERE {field_src} IS NOT NULL AND {field_src} != ''"
    ).fetchall()
    pending = []
    for cid, src in rows:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT lang FROM translations WHERE course_id=? AND field=?",
                (cid, field_store),
            )
        }
        missing = [l for l in LANGS if l not in existing]
        if missing:
            pending.append((cid, src, missing))
    conn.close()
    return pending


def call_api(text: str, langs):
    lang_spec = ", ".join(f"{l} ({LANG_NAMES[l]})" for l in langs)
    user_prompt = (
        f"Translate the following Chinese university course content to these "
        f"languages: {lang_spec}.\n\n"
        f"Return strict JSON with exactly these keys: {langs}. "
        f"No markdown wrappers, no commentary, no extra fields.\n\n"
        f"Guidelines:\n"
        f"- Preserve academic terminology and proper-noun translations standard "
        f"in each language (e.g. 量子力学→Quantum Mechanics, "
        f"北京大学→Peking University).\n"
        f"- Preserve newlines and bullet-list structure from source.\n"
        f"- Keep Latin abbreviations / numbers / dates verbatim.\n"
        f"- Translate naturally; do not transliterate Chinese names of academic "
        f"departments — use the official institutional translation when known.\n\n"
        f"Source (Chinese):\n{text}\n\nOutput JSON:"
    )
    body = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional academic translator. "
                    "Output only valid JSON with the requested language keys."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=240, context=SSL_CTX) as resp:
        result = json.loads(resp.read())
    content = result["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    missing_in_resp = [l for l in langs if l not in parsed or not parsed[l]]
    if missing_in_resp:
        raise ValueError(f"API response missing langs: {missing_in_resp}")
    return parsed, result.get("usage", {})


def translate_one(item, db_path, field):
    cid, src, missing_langs = item
    last_err = None
    for attempt in range(3):
        try:
            translations, usage = call_api(src, missing_langs)
            conn = sqlite3.connect(db_path, timeout=30)
            for lang, txt in translations.items():
                if lang in missing_langs:
                    conn.execute(
                        "INSERT OR REPLACE INTO translations VALUES (?,?,?,?)",
                        (cid, field, lang, txt),
                    )
            conn.commit()
            conn.close()
            return (cid, True, None, usage)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read()[:200]!r}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(2 ** attempt)
    return (cid, False, last_err, {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="Translate at most N rows total (for testing).")
    ap.add_argument("--workers", type=int, default=10,
                    help="Parallel API workers.")
    ap.add_argument("--only", choices=["ug_intro", "gr_intro", "gr_extra"],
                    help="Restrict to one job for testing.")
    args = ap.parse_args()

    print("== Setup ==")
    setup_db(UG_DB)
    setup_db(GR_DB)

    print("== Pending lists ==")
    ug = fetch_pending_undergrad()
    gr_intro = fetch_pending_grad("intro", "intro_cn")
    gr_extra = fetch_pending_grad("extra_notes", "extra_notes")
    print(f"  undergrad intro_cn : {len(ug):4d} rows")
    print(f"  graduate  intro    : {len(gr_intro):4d} rows")
    print(f"  graduate  extra    : {len(gr_extra):4d} rows")

    jobs = []
    if args.only in (None, "ug_intro"):
        jobs.append((UG_DB, "intro_cn", ug))
    if args.only in (None, "gr_intro"):
        jobs.append((GR_DB, "intro_cn", gr_intro))
    if args.only in (None, "gr_extra"):
        jobs.append((GR_DB, "extra_notes", gr_extra))

    all_items = []
    for db, field, items in jobs:
        for it in items:
            all_items.append((db, field, it))

    if args.limit:
        all_items = all_items[: args.limit]

    total = len(all_items)
    if total == 0:
        print("Nothing to translate. All up to date.")
        return

    print(f"== Starting {total} API calls with {args.workers} workers ==")
    t0 = time.time()
    done = 0
    errors = []
    total_input_tokens = 0
    total_output_tokens = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(translate_one, item, db, field): (db, field, item[0])
            for db, field, item in all_items
        }
        for fut in as_completed(futs):
            db, field, cid = futs[fut]
            try:
                _cid, ok, err, usage = fut.result()
                total_input_tokens += usage.get("prompt_tokens", 0)
                total_output_tokens += usage.get("completion_tokens", 0)
            except Exception as e:
                ok, err = False, f"future error: {e}"
            done += 1
            if not ok:
                errors.append((db, field, cid, err))
            if done % 10 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / max(elapsed, 0.01)
                eta = (total - done) / max(rate, 0.01)
                print(
                    f"  [{done}/{total}] elapsed={elapsed:5.0f}s "
                    f"rate={rate:4.1f}/s eta={eta:5.0f}s "
                    f"in={total_input_tokens} out={total_output_tokens} "
                    f"errors={len(errors)}"
                )

    elapsed = time.time() - t0
    print(f"\n== Done in {elapsed:.0f}s ==")
    print(f"  OK     : {total - len(errors)}")
    print(f"  FAILED : {len(errors)}")
    print(f"  tokens : input={total_input_tokens} output={total_output_tokens}")
    if errors:
        print(f"\nFirst 10 errors:")
        for db, field, cid, err in errors[:10]:
            print(f"  {os.path.basename(db)} {field} cid={cid}: {err[:200]}")


if __name__ == "__main__":
    main()
