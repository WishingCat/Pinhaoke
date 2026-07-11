#!/usr/bin/env python3
"""Batch-translate course introductions and graduate extra notes."""

import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing

try:
    from .translation_common import (
        DATABASES,
        LANGUAGE_NAMES,
        LANGUAGES,
        SSL_CONTEXT,
        clean_translation,
        get_api_key,
        nonnegative_int,
        positive_int,
        setup_translation_db,
        write_translation_with_retry,
    )
except ImportError:
    from translation_common import (
        DATABASES,
        LANGUAGE_NAMES,
        LANGUAGES,
        SSL_CONTEXT,
        clean_translation,
        get_api_key,
        nonnegative_int,
        positive_int,
        setup_translation_db,
        write_translation_with_retry,
    )


API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.qnaigc.com/v1/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")
ENABLE_THINKING = os.environ.get("DEEPSEEK_ENABLE_THINKING")
THINKING = os.environ.get("DEEPSEEK_THINKING")

LANGS = LANGUAGES
LANG_NAMES = LANGUAGE_NAMES
UG_DB = DATABASES["ug"]
GR_DB = DATABASES["gr"]
SUMMER_DB = DATABASES["summer"]
FALL_DB = DATABASES["fall"]
FALL_GR_DB = DATABASES["fall_gr"]

# (CLI name, DB key, schema shape, source field, stored field, display label)
JOBS = (
    ("ug_intro", "ug", "undergrad", "intro_cn", "intro_cn", "undergrad intro_cn"),
    ("gr_intro", "gr", "graduate", "intro", "intro_cn", "graduate intro"),
    ("gr_extra", "gr", "graduate", "extra_notes", "extra_notes", "graduate extra"),
    ("summer_intro", "summer", "undergrad", "intro_cn", "intro_cn", "summer intro_cn"),
    ("fall_intro", "fall", "undergrad", "intro_cn", "intro_cn", "fall intro_cn"),
    ("fall_gr_intro", "fall_gr", "graduate", "intro", "intro_cn", "fall grad intro"),
    ("fall_gr_extra", "fall_gr", "graduate", "extra_notes", "extra_notes", "fall grad extra"),
)


def setup_db(db_path):
    setup_translation_db(db_path)


def fetch_pending_undergrad(db_path=UG_DB):
    """Return pending intro rows, seeding available English text first."""
    pending = []
    english_reuse = []
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT course_id, intro_cn, intro_en FROM detail_info "
            "WHERE intro_cn IS NOT NULL AND TRIM(intro_cn) != ''"
        ).fetchall()
        for cid, source, intro_en in rows:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT lang FROM translations WHERE course_id=? AND field='intro_cn'",
                    (cid,),
                )
            }
            missing = [lang for lang in LANGS if lang not in existing]
            if "en" in missing and isinstance(intro_en, str) and intro_en.strip():
                english_reuse.append((cid, clean_translation(intro_en)))
                missing.remove("en")
            if missing:
                pending.append((cid, source, missing))
    for cid, english in english_reuse:
        write_translation_with_retry(db_path, cid, "intro_cn", "en", english)
    return pending


def fetch_pending_grad(field_src, field_store, db_path=GR_DB):
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT course_id, {field_src} FROM detail_info "
            f"WHERE {field_src} IS NOT NULL AND TRIM({field_src}) != ''"
        ).fetchall()
        pending = []
        for cid, source in rows:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT lang FROM translations WHERE course_id=? AND field=?",
                    (cid, field_store),
                )
            }
            missing = [lang for lang in LANGS if lang not in existing]
            if missing:
                pending.append((cid, source, missing))
    return pending


def call_api(text, langs, max_retries=3):
    lang_spec = ", ".join(f"{lang} ({LANG_NAMES[lang]})" for lang in langs)
    user_prompt = (
        "Translate the following Chinese university course content to these "
        f"languages: {lang_spec}.\n\n"
        f"Return strict JSON with exactly these keys: {langs}. "
        "No markdown wrappers, no commentary, no extra fields.\n\n"
        "Guidelines:\n"
        "- Preserve academic terminology and proper-noun translations standard "
        "in each language (e.g. 量子力学→Quantum Mechanics, 北京大学→Peking University).\n"
        "- Preserve newlines and bullet-list structure from source.\n"
        "- Keep Latin abbreviations / numbers / dates verbatim.\n"
        "- Translate naturally; do not transliterate Chinese names of academic "
        "departments — use the official institutional translation when known.\n\n"
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
    }
    if ENABLE_THINKING is not None:
        body["enable_thinking"] = ENABLE_THINKING.lower() in {"1", "true", "yes"}
    elif "qnaigc.com" in API_URL:
        body["enable_thinking"] = False
    if "api.deepseek.com" in API_URL:
        body["thinking"] = {"type": THINKING or "disabled"}

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {get_api_key()}",
            "Content-Type": "application/json",
        },
    )
    last_error = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(request, timeout=240, context=SSL_CONTEXT) as response:
                result = json.loads(response.read())
            parsed = json.loads(result["choices"][0]["message"]["content"])
            cleaned = {lang: clean_translation(parsed.get(lang)) for lang in langs}
            return cleaned, result.get("usage", {})
        except urllib.error.HTTPError as exc:
            last_error = RuntimeError(f"HTTP {exc.code}: {exc.read()[:200]!r}")
        except Exception as exc:
            last_error = exc
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    raise last_error


def translate_one(item, db_path, field):
    cid, source, missing_langs = item
    try:
        translations, usage = call_api(source, missing_langs)
    except Exception as exc:
        return cid, False, f"{type(exc).__name__}: {exc}", {}

    try:
        for lang in missing_langs:
            write_translation_with_retry(db_path, cid, field, lang, translations[lang])
    except Exception as exc:
        return cid, False, f"{type(exc).__name__}: {exc}", usage
    return cid, True, None, usage


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit",
        type=nonnegative_int,
        default=0,
        help="Translate at most N rows total (for testing).",
    )
    parser.add_argument(
        "--workers", type=positive_int, default=10, help="Parallel API workers."
    )
    parser.add_argument(
        "--only",
        choices=[job[0] for job in JOBS],
        help="Restrict to one translation job.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    selected = [job for job in JOBS if args.only is None or job[0] == args.only]

    try:
        selected_paths = list(dict.fromkeys(DATABASES[job[1]] for job in selected))
        for db_path in selected_paths:
            setup_db(db_path)

        all_items = []
        print("== Pending lists ==")
        for _name, db_key, shape, source_field, store_field, label in selected:
            db_path = DATABASES[db_key]
            if shape == "undergrad":
                pending = fetch_pending_undergrad(db_path)
            else:
                pending = fetch_pending_grad(source_field, store_field, db_path)
            print(f"  {label:20s}: {len(pending):4d} rows")
            all_items.extend((db_path, store_field, item) for item in pending)
    except Exception as exc:
        print(f"Setup or pending scan failed: {type(exc).__name__}: {exc}")
        return 1

    if args.limit:
        all_items = all_items[: args.limit]
    total = len(all_items)
    if total == 0:
        print("Nothing to translate. All up to date.")
        return 0

    print(f"== Starting {total} API calls with {args.workers} workers ==")
    started = time.time()
    done = 0
    errors = []
    input_tokens = 0
    output_tokens = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(translate_one, item, db_path, field): (db_path, field, item[0])
            for db_path, field, item in all_items
        }
        for future in as_completed(futures):
            db_path, field, cid = futures[future]
            try:
                _cid, ok, error, usage = future.result()
            except Exception as exc:
                ok = False
                error = f"future error: {type(exc).__name__}: {exc}"
                usage = {}
            input_tokens += usage.get("prompt_tokens", 0)
            output_tokens += usage.get("completion_tokens", 0)
            done += 1
            if not ok:
                errors.append((db_path, field, cid, error))
            if done % 10 == 0 or done == total:
                elapsed = time.time() - started
                rate = done / max(elapsed, 0.01)
                eta = (total - done) / max(rate, 0.01)
                print(
                    f"  [{done}/{total}] elapsed={elapsed:5.0f}s rate={rate:4.1f}/s "
                    f"eta={eta:5.0f}s in={input_tokens} out={output_tokens} "
                    f"errors={len(errors)}"
                )

    elapsed = time.time() - started
    print(f"\n== Done in {elapsed:.0f}s ==")
    print(f"  OK     : {total - len(errors)}")
    print(f"  FAILED : {len(errors)}")
    print(f"  tokens : input={input_tokens} output={output_tokens}")
    for db_path, field, cid, error in errors[:10]:
        print(f"  {os.path.basename(db_path)} {field} cid={cid}: {error[:200]}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
