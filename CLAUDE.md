# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

拼好课 V2 — single-page PKU course search web app. Backend = FastAPI + multiple SQLite DBs attached as one connection. Frontend = a single `index.html` that fetches from `/api/*`. Deployed at `https://www.pinhaoke.love` behind Nginx + systemd on Alibaba Cloud Ubuntu 24.04.

The README is for users; this file is for what isn't obvious from reading individual files.

## Project layout

```
.
├── app.py                          # FastAPI backend (the only server-side file)
├── index.html                      # SPA frontend (no build step)
├── requirements.txt
├── Images/                         # served at /Images/ by Nginx in prod (do not rename — public URL)
├── deploy/                         # nginx.conf · pinhaoke.service · update.sh
├── 数据库/                          # SQLite files
│   ├── 2026春季学期本科生课程.db    # ← attached by app.py as `main`
│   ├── 2026春季学期研究生课程.db    # ← attached by app.py as `gr`
│   └── 2026暑期本科生课程.db        # ← read by app.py as `main` when ?term=summer
├── 课程数据/                        # source JSONs (also where 数据说明.md lives)
├── 数据库构建脚本/                  # JSON → SQLite (build_common.py is shared)
├── 北京大学课程数据翻译/            # DeepSeek 7-lang translation pipeline — see its README
├── 北京大学选课网数据抓取/          # Playwright-based summer-term scraper — see its README
├── 访问统计/                        # site-visit PDFs
├── 文档/                            # design notes / plans
└── 归档/                            # pre-V2 monolithic build artefacts (read-only reference)
```

## Run locally

The repo `venv/` symlinks point at `/opt/pinhaoke/...` (deploy paths) and **don't work on macOS**. Use a scratch venv:

```bash
python3 -m venv /tmp/pinhaoke-dev
/tmp/pinhaoke-dev/bin/pip install -r requirements.txt
/tmp/pinhaoke-dev/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Then open `http://127.0.0.1:8000/` — do NOT double-click `index.html`; the page needs the API. With `--reload` the server picks up `app.py` edits automatically; `index.html` is static so just refresh the browser. **There is no test suite** — verify changes by hitting endpoints manually.

## API surface

`app.py` exposes three JSON endpoints; `index.html` is the only consumer.

| Endpoint | Notes |
|---|---|
| `GET /api/filters` | Returns the dropdown universes: `course_types`, `categories`, `departments`, `credits`, `gradings`, `weekdays`. Accepts `?term=spring\|summer` (default `spring`). |
| `GET /api/courses` | Cards list. Query params: `term` (`spring\|summer`, default `spring`), `q` (LIKEs `course_name / teacher / classroom`), `type`, `category`, `credits`, `department`, `weekday`, `grading`, `sort` (`pinyin\|pinyin_desc\|credits_asc\|credits_desc\|time_asc\|random`), `random_seed` (int, used by `sort=random` to keep paging deterministic — frontend re-rolls on each click of 随机), `lang`, `page` (≥1), `page_size` (1–200, default 50). Returns `{total, courses[]}` with each row carrying string `id` like `u123` / `g456` / `s789`. |
| `GET /api/courses/{id}` | Single course detail (full field set). `id` is the prefixed string — the prefix alone determines which DB to read, so **no `?term=` needed**. Accepts `?lang=`. |

`lang` accepts `zh` (default — no translation lookup) or one of `en, ja, ko, fr, de, es, ru` (swaps fields from the relevant DB's `translations` table — see i18n section below).

## Rebuild databases from source JSONs

```bash
python3 数据库构建脚本/build_undergrad_db.py     # 课程数据/北大本科*_25-26第2学期.json → 数据库/2026春季学期本科生课程.db (~2465 courses)
python3 数据库构建脚本/build_graduate_db.py      # 课程数据/北大研究生课程_25-26第2学期.json → 数据库/2026春季学期研究生课程.db (~1379 courses)
python3 北京大学选课网数据抓取/build_summer_db.py # 课程数据/北大暑期课程_25-26第3学期.json   → 数据库/2026暑期本科生课程.db (~194 courses)
```

`build_common.py` (in `数据库构建脚本/`) holds shared parsing. The summer build script imports it via `sys.path` injection — there is only one copy of `build_common.py`, do not duplicate. **Rebuilding wipes the `translations` table** — back up the DBs before running, or re-run translation scripts after.

## Translate course content

Pipeline lives in `北京大学课程数据翻译/` — its README has the full story. TL;DR:

```bash
export DEEPSEEK_API_KEY=sk-...
python3 北京大学课程数据翻译/translate_courses.py           # intro_cn (UG+GR) + extra_notes (GR)
python3 北京大学课程数据翻译/translate_misc.py --phase short
python3 北京大学课程数据翻译/translate_misc.py --phase long
python3 北京大学课程数据翻译/translate_stubborn.py          # single-lang-per-call fallback
```

All resumable (`INSERT OR REPLACE` per `(course_id, field, lang)`). Dedupes by source text in-memory so identical Chinese values hit the API once.

## Scrape summer-term courses

Pipeline lives in `北京大学选课网数据抓取/` — its README has the full story. TL;DR: log into elective.pku.edu.cn in a browser, start `receive_pku_summer_payload.py`, paste the in-page JS into devtools, wait for `[done]`, then run `build_summer_db.py`.

## Deploy

```bash
ssh root@8.140.215.49
bash /opt/pinhaoke/deploy/update.sh    # git pull + venv check + systemd reload + smoke test
```

`deploy/update.sh` is the only sanctioned deploy path. systemd unit `pinhaoke.service` runs `uvicorn app:app --host 127.0.0.1 --port 8000 --workers 2` as `www-data`. Nginx (`deploy/nginx.conf`) proxies `/` to `:8000` and serves `/Images/` directly.

If `index.html` deployment looks reverted to the old glassmorphism design, it's **always browser/CDN cache** — verify with `curl -s http://127.0.0.1:8000/ | grep "CLEAN MODERN"` on the server.

## Architecture you need to know before editing

### Term switch (spring / summer) and DB attach

`get_db(term)` attaches a different DB set per term, governed by `TERM_DBS`:
- `term="spring"` opens `2026春季学期本科生课程.db` as `main` and `ATTACH`es `2026春季学期研究生课程.db` as `gr`.
- `term="summer"` opens `2026暑期本科生课程.db` as `main` only (no `gr`).

`/api/filters` and `/api/courses` accept `?term=`. `/api/courses/{id}` does NOT — the id's single-char prefix (`u`/`g`/`s`) is enough to pick the right DB. Adding a new term means: build its `.db` under `数据库/`, add a `LIST_SELECT_*` with a new prefix, register it in `TERM_DBS` + `TERM_UNION_SQL`, and wire the prefix into `_parse_id`.

### Two DBs, one connection (ATTACH) — spring layout

`get_db()` in `app.py` opens `数据库/2026春季学期本科生课程.db` and `ATTACH`es the graduate DB as `gr`. All cross-DB queries use `UNION ALL` of `LIST_SELECT_UG` + `LIST_SELECT_GR` from one cursor. **Both SELECTs must produce the same column shape** for UNION — if you add a field to UG only, alias `''` (or `0`) on the GR side (or vice-versa).

### Course IDs are strings, not ints

DB ids are namespaced by single-char prefix so the three DBs can coexist in URL space:
- `u<id>` — spring undergrad (DB attached as `main` when `term=spring`)
- `g<id>` — spring graduate (DB attached as `gr` when `term=spring`)
- `s<id>` — summer undergrad (DB attached as `main` when `term=summer`)

`_parse_id` splits the prefix into `(term, level, local_id)`. `_apply_translations` uses `ns = "gr" if level == "g" else "main"` to pick which attached DB's `translations` table to read.

**Gotcha**: in `createCard()`, the detail-button onclick must quote `course.id`:
```js
onclick="showDetail('${course.id}')"   // correct — 'u123' is a string
onclick="showDetail(${course.id})"     // BROKEN — JS parses u123 as a variable
```

### i18n is hybrid (two stores)

1. **Finite dictionaries in `index.html`** (`dataI18n` near line 1493): 8 maps — `departments`, `categories`, `gradings`, `languages`, `audiences`, `notes`, `titles`, `weekdayShort`, `scheduleTerms`. Each value is a 7-tuple `[en, ja, ko, fr, de, es, ru]`. Use `tr('departments', '物理学院')` to look up. Add new short-value translations here when a closed set is known.
2. **Per-row `translations` table in each DB** (UG + GR + Summer): for free text where every course is different. Schema: `(course_id, field, lang, text)` primary key. Loaded by `app.py` `_apply_translations` for fields in `TRANSLATABLE_FIELDS`. Populated by the scripts in `北京大学课程数据翻译/` (summer was filled to 100% across 10 fields × 7 langs ≈ 10010 rows in May 2026).

Both endpoints accept `?lang=xx`; when `lang != 'zh'`, the backend swaps Chinese values for translations from the table. The list endpoint translates only card-visible fields (`course_name`, `classroom`, `notes`) to keep the query small; the detail endpoint translates everything in `TRANSLATABLE_FIELDS`.

On the frontend, `setLang()` re-fetches the list AND any open modal so visible content updates immediately.

### Schedule parsing is fragile

Schedule strings look like `1~15周 每周周一10~11节 二教5111~15周 每周周四10~11节 二教511` — slot delimiters are missing and room numbers can merge into the next week range. `build_common.parse_schedule` handles it via regex with a 3-digit cap on room numbers; the JS `trSchedule()` localises the cleaned form. Don't naively split on whitespace.

`build_common.parse_first_period` extracts the smallest `节` start across all slots of one course and stores it in `basic_info.first_period`. The `time_asc` sort uses it via `(first_period IS NULL), first_period, course_name COLLATE NOCASE` so blanks land at the bottom. If you rebuild a DB, the column is populated by the build scripts; if you change `parse_first_period` logic later, run a one-off `UPDATE basic_info SET first_period = ...` rather than wiping the DB (which would drop `translations`).

### sort=random uses a seeded deterministic shuffle

The frontend re-rolls `randomSeed` whenever the user clicks 随机 and includes it in every subsequent `/api/courses` call so pagination stays stable. The backend builds `((CAST(SUBSTR(t.id, 2) AS INTEGER) * mul + level_offset + add) % 999983)` where `mul/add` are derived from the seed via two large primes and `level_offset` differs per `u/g/s` prefix to avoid hash collisions between e.g. `u123` and `g123`. Don't replace this with `ORDER BY RANDOM()` — that breaks pagination because each request gets a fresh order.

### 归档/ folder

Holds the pre-V2 monolithic `courses.db` plus the old `build_db.py` / `build_data.py` / `courses.json` / `courses_data.js`. Kept for reference only — nothing in the live codebase reads from it.
