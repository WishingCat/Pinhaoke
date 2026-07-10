"""Pinhaoke Course Search API.

Reads standalone SQLite databases (term-switched at request time):
  spring →
    - 2026春季学期本科生课程.db  (undergraduate, AS main)
    - 2026春季学期研究生课程.db  (graduate,      AS gr)
  summer →
    - 2026暑期本科生课程.db      (undergraduate, AS main)
  fall →
    - 2026秋季学期本科生课程.db  (undergraduate, AS main)
    - 2026秋季学期研究生课程.db  (graduate,      AS gr)

The DBs share the basic_info columns but have different detail schemas. Cross-DB
queries use ATTACH + UNION ALL. Each row carries a prefixed string id:
    "u<basic_info.id>"  spring undergrad
    "g<basic_info.id>"  spring graduate
    "s<basic_info.id>"  summer undergrad
    "a<basic_info.id>"  fall undergrad
    "r<basic_info.id>"  fall graduate

The prefix alone determines which DB the detail endpoint opens, so callers do
NOT need to pass ?term= when fetching a specific course.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "数据库"
UG_DB = DB_DIR / "2026春季学期本科生课程.db"
GR_DB = DB_DIR / "2026春季学期研究生课程.db"
SUMMER_DB = DB_DIR / "2026暑期本科生课程.db"
FALL_UG_DB = DB_DIR / "2026秋季学期本科生课程.db"
FALL_GR_DB = DB_DIR / "2026秋季学期研究生课程.db"

# (alias, path, id_prefix)
TERM_DBS = {
    "spring": [
        ("main", UG_DB, "u"),
        ("gr",   GR_DB, "g"),
    ],
    "summer": [
        ("main", SUMMER_DB, "s"),
    ],
    "fall": [
        ("main", FALL_UG_DB, "a"),
        ("gr",   FALL_GR_DB, "r"),
    ],
}

app = FastAPI(title="Pinhaoke")

HEALTH_CACHE_TTL_SECONDS = 300
_health_cache_lock = threading.Lock()
_health_cache_payload = None
_health_cache_checked_at = None


def _readonly_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


@contextmanager
def get_db(term: str = "fall"):
    config = TERM_DBS.get(term)
    if config is None:
        raise HTTPException(status_code=400, detail=f"Unknown term: {term}")
    conn = None
    try:
        conn = sqlite3.connect(_readonly_uri(config[0][1]), uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        for alias, path, _ in config[1:]:
            conn.execute(f"ATTACH DATABASE ? AS {alias}", (_readonly_uri(path),))
        yield conn
    finally:
        if conn is not None:
            conn.close()


def check_database_health() -> dict:
    databases = []
    for term, entries in TERM_DBS.items():
        for alias, path, prefix in entries:
            conn = sqlite3.connect(_readonly_uri(path), uri=True)
            try:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                basic = conn.execute("SELECT COUNT(*) FROM basic_info").fetchone()[0]
                detail = conn.execute("SELECT COUNT(*) FROM detail_info").fetchone()[0]
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                conn.close()
            if not {"basic_info", "detail_info", "translations"}.issubset(tables) or basic != detail or integrity != "ok":
                raise RuntimeError(f"Unhealthy database: {path.name}")
            databases.append(
                {
                    "term": term,
                    "level": alias,
                    "prefix": prefix,
                    "file": path.name,
                    "integrity": integrity,
                    "basic": basic,
                    "detail": detail,
                }
            )
    return {"status": "ok", "databases": databases}


def get_cached_database_health() -> dict:
    global _health_cache_payload, _health_cache_checked_at

    now = time.monotonic()
    with _health_cache_lock:
        if (
            _health_cache_payload is not None
            and _health_cache_checked_at is not None
            and now - _health_cache_checked_at < HEALTH_CACHE_TTL_SECONDS
        ):
            return _health_cache_payload

        payload = check_database_health()
        _health_cache_payload = payload
        _health_cache_checked_at = now
        return payload


# ---- shared SELECT fragments --------------------------------------------------

# Common basic columns plus course_type. Undergrad has it natively; graduate
# synthesises '研究生课'. pnp only exists on undergrad.
#
# Spring UG and summer UG share the exact same schema and live in `main` when
# their term is active — only the id prefix differs ('u' vs 's'), so both list
# SELECTs come from this one template.
_UG_LIST_SELECT = """
    SELECT
        '{prefix}' || b.id     AS id,
        b.course_type          AS course_type,
        b.course_code          AS course_code,
        b.class_no             AS class_no,
        b.course_name          AS course_name,
        b.category             AS category,
        b.credits              AS credits,
        b.teacher              AS teacher,
        b.department           AS department,
        b.major                AS major,
        b.grade                AS grade,
        b.schedule             AS schedule,
        b.classroom            AS classroom,
        b.weekdays             AS weekdays,
        b.first_period         AS first_period,
        b.enrollment           AS enrollment,
        b.pnp                  AS pnp,
        b.notes                AS notes,
        d.english_name         AS english_name,
        d.grading              AS grading,
        d.language             AS language,
        ''                     AS audience,
        '{prefix}'             AS _level
    FROM basic_info b
    LEFT JOIN detail_info d ON d.course_id = b.id
"""

LIST_SELECT_UG = _UG_LIST_SELECT.format(prefix="u")
LIST_SELECT_SUMMER = _UG_LIST_SELECT.format(prefix="s")
LIST_SELECT_FALL_UG = _UG_LIST_SELECT.format(prefix="a")

_GR_LIST_SELECT = """
    SELECT
        '{prefix}' || b.id     AS id,
        '研究生课'             AS course_type,
        b.course_code          AS course_code,
        b.class_no             AS class_no,
        b.course_name          AS course_name,
        b.category             AS category,
        b.credits              AS credits,
        b.teacher              AS teacher,
        b.department           AS department,
        b.major                AS major,
        b.grade                AS grade,
        b.schedule             AS schedule,
        b.classroom            AS classroom,
        b.weekdays             AS weekdays,
        b.first_period         AS first_period,
        b.enrollment           AS enrollment,
        ''                     AS pnp,
        b.notes                AS notes,
        d.english_name         AS english_name,
        ''                     AS grading,
        ''                     AS language,
        d.audience             AS audience,
        '{prefix}'             AS _level
    FROM {ns}.basic_info b
    LEFT JOIN {ns}.detail_info d ON d.course_id = b.id
"""

LIST_SELECT_GR = _GR_LIST_SELECT.format(prefix="g", ns="gr")
LIST_SELECT_FALL_GR = _GR_LIST_SELECT.format(prefix="r", ns="gr")

# Pre-built UNION expressions for each term.
TERM_UNION_SQL = {
    "spring": f"({LIST_SELECT_UG} UNION ALL {LIST_SELECT_GR})",
    "summer": f"({LIST_SELECT_SUMMER})",
    "fall": f"({LIST_SELECT_FALL_UG} UNION ALL {LIST_SELECT_FALL_GR})",
}


# ---- filters ------------------------------------------------------------------


@app.get("/api/filters")
def get_filters(term: str = Query("fall", description="spring | summer | fall")):
    with get_db(term) as conn:
        c = conn.cursor()

        def col(sql: str) -> list:
            return [r[0] for r in c.execute(sql)]

        has_graduate_db = any(alias == "gr" for alias, _, _ in TERM_DBS[term])
        if has_graduate_db:
            course_types = col(
                """SELECT DISTINCT course_type FROM basic_info
                   WHERE course_type != ''
                   UNION
                   SELECT '研究生课'
                   ORDER BY 1"""
            )
            categories = col(
                """SELECT DISTINCT category FROM basic_info WHERE category != ''
                   UNION
                   SELECT DISTINCT category FROM gr.basic_info WHERE category != ''
                   ORDER BY 1"""
            )
            departments = col(
                """SELECT DISTINCT department FROM basic_info WHERE department != ''
                   UNION
                   SELECT DISTINCT department FROM gr.basic_info WHERE department != ''
                   ORDER BY 1"""
            )
            credits = col(
                """SELECT DISTINCT credits FROM basic_info
                   UNION
                   SELECT DISTINCT credits FROM gr.basic_info
                   ORDER BY 1"""
            )
        else:  # summer — single DB
            course_types = col(
                """SELECT DISTINCT course_type FROM basic_info
                   WHERE course_type != '' ORDER BY 1"""
            )
            categories = col(
                """SELECT DISTINCT category FROM basic_info
                   WHERE category != '' ORDER BY 1"""
            )
            departments = col(
                """SELECT DISTINCT department FROM basic_info
                   WHERE department != '' ORDER BY 1"""
            )
            credits = col("SELECT DISTINCT credits FROM basic_info ORDER BY 1")

        # grading only exists on undergrad detail_info, which is `main` in both terms.
        gradings = col(
            """SELECT DISTINCT grading FROM detail_info
               WHERE grading != '' ORDER BY grading"""
        )

        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    payload = {
        "course_types": course_types,
        "categories": categories,
        "departments": departments,
        "credits": credits,
        "gradings": gradings,
        "weekdays": weekdays,
    }
    # Filter universes only change when DBs are rebuilt. 1 hour browser cache
    # keeps cold-load fast without making a redeploy require a hard refresh.
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=3600"})


# ---- list ---------------------------------------------------------------------


def _like(s: str) -> str:
    """Escape LIKE wildcards so `100%` searches literal '%', not "any chars"."""
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _build_where(
    q: str, type_: str, category: str, credits_: str,
    department: str, weekday: str, grading: str, classroom: str,
):
    """Build (where_clause, params) used inside the inner subquery alias `t`."""
    conds = []
    params = []
    if q:
        like = _like(q)
        conds.append(
            "(t.course_name LIKE ? ESCAPE '\\' "
            "OR t.teacher LIKE ? ESCAPE '\\' "
            "OR t.classroom LIKE ? ESCAPE '\\' "
            "OR t.course_code LIKE ? ESCAPE '\\' "
            "OR t.english_name LIKE ? ESCAPE '\\')"
        )
        params.extend([like] * 5)
    if classroom:
        # Dedicated classroom filter: matches ONLY the classroom column, so it
        # composes with q (the old frontend joined both into one q string,
        # which produced a single "%q classroom%" LIKE that matched nothing).
        conds.append("t.classroom LIKE ? ESCAPE '\\'")
        params.append(_like(classroom))
    if type_:
        conds.append("t.course_type = ?")
        params.append(type_)
    if category:
        conds.append("t.category = ?")
        params.append(category)
    if credits_:
        try:
            conds.append("t.credits = ?")
            params.append(float(credits_))
        except ValueError:
            pass
    if department:
        conds.append("t.department = ?")
        params.append(department)
    if weekday:
        conds.append("t.weekdays LIKE ?")
        params.append(f"%{weekday}%")
    if grading:
        # grading is only present on undergrad; this naturally excludes grad rows.
        conds.append("t.grading = ?")
        params.append(grading)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


@app.get("/api/courses")
def list_courses(
    q: str = Query("", description="Search query (course name / teacher / classroom / course code / english name)"),
    type: str = Query("", description="Course type filter"),
    category: str = Query("", description="Category filter"),
    credits: str = Query("", description="Credits filter"),
    department: str = Query("", description="Department filter"),
    weekday: str = Query("", description="Weekday filter"),
    grading: str = Query("", description="Grading filter"),
    classroom: str = Query("", description="Classroom filter (LIKE, classroom column only)"),
    sort: str = Query("", description="Sort: pinyin | pinyin_desc | credits_asc | credits_desc | time_asc | random"),
    random_seed: int = Query(0, description="Seed used by sort=random; same seed → same order"),
    lang: str = Query("zh", description="Display language"),
    term: str = Query("fall", description="spring | summer | fall"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if term not in TERM_UNION_SQL:
        raise HTTPException(status_code=400, detail=f"Unknown term: {term}")

    where, params = _build_where(q, type, category, credits, department, weekday, grading, classroom)

    if sort == "random":
        # Deterministic shuffle: same seed → stable order across pagination.
        # int param is already validated by FastAPI, safe to interpolate.
        # Two large primes mix the seed so even tiny seeds produce well-spread
        # mul/add values, giving genuinely different orderings per seed.
        # The CASE per id prefix breaks 'u123' / 'g123' / 's123' / etc. collisions.
        seed = max(1, int(random_seed))
        mul = ((seed * 1664525) % 999983) or 7
        add = (seed * 1013904223) % 999983
        # After GROUP BY the id column is the representative id (a single
        # 'u123' / 'g456' / 's789' / etc. string), so we reach into it via SUBSTR.
        order_by = (
            f"((CAST(SUBSTR(id, 2) AS INTEGER) * {mul} "
            f"+ CASE SUBSTR(id, 1, 1) "
            f"WHEN 'g' THEN 333331 WHEN 's' THEN 666661 "
            f"WHEN 'a' THEN 111113 WHEN 'r' THEN 444449 ELSE 0 END "
            f"+ {add}) % 999983)"
        )
    else:
        sort_map = {
            "pinyin":       "course_name COLLATE NOCASE",
            "pinyin_desc":  "course_name COLLATE NOCASE DESC",
            "credits_asc":  "credits ASC, course_name COLLATE NOCASE",
            "credits_desc": "credits DESC, course_name COLLATE NOCASE",
            # Earliest class period first; rows with no schedule sort last.
            "time_asc":     "(first_period IS NULL), first_period, course_name COLLATE NOCASE",
        }
        if sort in sort_map:
            order_by = sort_map[sort]
        else:
            # Default sort: course name asc, empty names last. For summer term
            # we additionally float "sociology-adjacent" courses to the very
            # top — the summer audience is primarily 社会学系 / 中国社会科学
            # 调查中心 partner students plus 爱心社, so this surfaces the most
            # relevant offerings at first scroll. Spring/fall keep a flat
            # alphabetical default.
            if term == "summer":
                sociology_priority = (
                    "(CASE WHEN department = '社会学系' "
                    "OR department = '中国社会科学调查中心' "
                    "OR course_name LIKE '%社会学%' THEN 0 ELSE 1 END), "
                )
            else:
                sociology_priority = ""
            order_by = (
                f"{sociology_priority}"
                "(course_name = '' OR course_name IS NULL), "
                "course_name COLLATE NOCASE, id"
            )
    offset = (page - 1) * page_size

    union_sql = TERM_UNION_SQL[term]

    # Cross-listed and duplicate-registered rows: PKU lists the same physical
    # class multiple times — once per (category / undergrad-vs-grad) entry.
    # We collapse them by (course_code, class_no, teacher), keeping every
    # distinct course_type and category as a GROUP_CONCAT'd list.
    #
    # Why include class_no instead of just (code, teacher)?
    #   - Some courses (e.g. 发展心理学 0163006) have ONE teacher running FIVE
    #     parallel sections — they share (code, teacher) but are 5 real classes.
    #     Dropping class_no from the key would erroneously merge them.
    #   - Cross-listed UG↔GR rows use different class_no namespaces (UG '1' /
    #     GR '00'), so they intentionally do NOT merge here; the UG and GR
    #     cards are kept distinct and the badges show which is which.
    #
    # Rows missing a teacher (a handful in UG/GR) get a unique synthetic key
    # so they never collapse with each other.
    inner = f"SELECT * FROM {union_sql} AS t {where}"
    merged_sql = f"""
        SELECT
            MIN(id) AS id,
            GROUP_CONCAT(DISTINCT course_type) AS course_type,
            MAX(course_code) AS course_code,
            MAX(class_no) AS class_no,
            MAX(course_name) AS course_name,
            GROUP_CONCAT(DISTINCT category) AS category,
            MAX(credits) AS credits,
            MAX(teacher) AS teacher,
            MAX(department) AS department,
            MAX(major) AS major,
            MAX(grade) AS grade,
            MAX(schedule) AS schedule,
            MAX(classroom) AS classroom,
            MAX(weekdays) AS weekdays,
            MIN(first_period) AS first_period,
            MAX(enrollment) AS enrollment,
            MAX(pnp) AS pnp,
            MAX(notes) AS notes,
            MAX(english_name) AS english_name,
            MAX(grading) AS grading,
            MAX(language) AS language,
            MAX(audience) AS audience
        FROM ({inner})
        GROUP BY course_code, class_no,
                 CASE WHEN teacher IS NULL OR teacher = '' THEN id ELSE teacher END
    """

    count_sql = f"SELECT COUNT(*) FROM ({merged_sql})"
    list_sql = f"{merged_sql} ORDER BY {order_by} LIMIT ? OFFSET ?"

    # Maps id prefix → attached DB alias for translation lookup.
    PREFIX_TO_NS = {prefix: alias for (alias, _, prefix) in TERM_DBS[term]}

    with get_db(term) as conn:
        cur = conn.cursor()
        total = cur.execute(count_sql, params).fetchone()[0]
        rows = cur.execute(list_sql, params + [page_size, offset]).fetchall()

        # Bulk-fetch translations for visible card fields when lang != zh.
        trans_by_prefix: dict = {p: {} for p in PREFIX_TO_NS}
        if lang != "zh" and rows:
            CARD_FIELDS = ("course_name", "classroom", "notes")
            for prefix, ns in PREFIX_TO_NS.items():
                ids = [int(r["id"][1:]) for r in rows if r["id"].startswith(prefix)]
                if not ids:
                    continue
                placeholders = ",".join("?" * len(ids))
                fld_placeholders = ",".join("?" * len(CARD_FIELDS))
                tr_rows = cur.execute(
                    f"SELECT course_id, field, text FROM {ns}.translations "
                    f"WHERE lang=? AND course_id IN ({placeholders}) "
                    f"AND field IN ({fld_placeholders})",
                    [lang, *ids, *CARD_FIELDS],
                ).fetchall()
                store = trans_by_prefix[prefix]
                for cid, field, text in tr_rows:
                    if text:
                        store.setdefault(cid, {})[field] = text

    courses = []
    for r in rows:
        rid = r["id"]
        prefix = rid[0]
        local_id = int(rid[1:])
        trans = trans_by_prefix.get(prefix, {}).get(local_id, {})
        # GROUP_CONCAT(DISTINCT) returns 'a,b' or '' or None. Categories and
        # course_types have no embedded commas (verified in build_common), so
        # splitting on ',' is safe. Empty / single-value cases collapse to a
        # 1-element list so the frontend can iterate uniformly.
        ct_raw = r["course_type"] or ""
        cat_raw = r["category"] or ""
        courses.append(
            {
                "id": rid,
                "course_type": [s for s in ct_raw.split(",") if s],
                "course_code": r["course_code"],
                "course_name": trans.get("course_name") or r["course_name"],
                "english_name": r["english_name"],
                "category": [s for s in cat_raw.split(",") if s],
                "credits": r["credits"],
                "teacher": r["teacher"],
                "class_no": r["class_no"],
                "department": r["department"],
                "schedule": r["schedule"],
                "classroom": trans.get("classroom") or r["classroom"],
                "enrollment": r["enrollment"],
                "pnp": r["pnp"],
                "notes": trans.get("notes") or r["notes"],
                "major": r["major"],
                "grade": r["grade"],
                "language": r["language"],
                "audience": r["audience"],
            }
        )

    return {"total": total, "page": page, "page_size": page_size, "courses": courses}


# ---- detail -------------------------------------------------------------------


# id prefix → term whose DB set contains that level's courses.
_PREFIX_TERM = {"u": "spring", "g": "spring", "s": "summer", "a": "fall", "r": "fall"}


def _parse_id(course_id: str):
    """Return (term, level, local_id) or (None, None, None) on parse failure.

    level is the single-char id prefix ('u' / 'g' / 's' / 'a' / 'r'); term tells get_db()
    which DBs to open.
    """
    if not course_id:
        return None, None, None
    prefix = course_id[0]
    term = _PREFIX_TERM.get(prefix)
    if term is None:
        return None, None, None
    try:
        local_id = int(course_id[1:])
    except ValueError:
        return None, None, None
    return term, prefix, local_id


# Fields the translations table can override per (course_id, field, lang).
# Only includes fields the frontend actually renders — keep in sync with the
# detail SELECTs and modal rendering in index.html.
TRANSLATABLE_FIELDS = (
    "course_name", "notes", "pnp", "classroom", "major",
    "prerequisites", "ge_series", "audience", "term",
    "syllabus", "evaluation",
    "intro_cn", "extra_notes",
)


def _apply_translations(cur, ns: str, local_id: int, lang: str, out: dict):
    """Replace translatable fields in `out` with values from translations table."""
    if lang == "zh":
        return
    rows = cur.execute(
        f"SELECT field, text FROM {ns}.translations "
        f"WHERE course_id=? AND lang=?",
        (local_id, lang),
    ).fetchall()
    for field, text in rows:
        if field in TRANSLATABLE_FIELDS and text:
            out[field] = text


# Shared SELECT for "UG-shape" detail rows (spring undergrad + summer undergrad).
# Only selects fields the frontend renders — see TRANSLATABLE_FIELDS and the
# modal renderer in index.html.
_UG_DETAIL_SELECT = """
    SELECT '{prefix}' || b.id AS id, b.course_type, b.course_code, b.class_no,
           b.course_name, b.category, b.credits, b.teacher,
           b.department, b.major, b.grade,
           b.schedule, b.classroom,
           b.enrollment, b.pnp, b.notes,
           d.english_name, d.prerequisites, d.intro_cn, d.intro_en,
           d.grading, d.ge_series, d.language,
           d.syllabus, d.evaluation
    FROM basic_info b
    LEFT JOIN detail_info d ON d.course_id = b.id
    WHERE b.id = ?
"""

_GR_DETAIL_SELECT = """
    SELECT '{prefix}' || b.id AS id, '研究生课' AS course_type,
           b.course_code, b.class_no, b.course_name, b.category,
           b.credits, b.teacher, b.department, b.major, b.grade,
           b.schedule, b.classroom,
           b.enrollment, '' AS pnp, b.notes,
           d.english_name,
           d.weekly_hours, d.total_hours, d.term, d.audience,
           d.intro AS intro_cn, d.extra_notes,
           {syllabus_expr} AS syllabus
    FROM gr.basic_info b
    LEFT JOIN gr.detail_info d ON d.course_id = b.id
    WHERE b.id = ?
"""


@app.get("/api/courses/{course_id}")
def get_course_detail(course_id: str, lang: str = Query("zh")):
    term, level, local_id = _parse_id(course_id)
    if level is None:
        raise HTTPException(status_code=404, detail="Course not found")

    with get_db(term) as conn:
        cur = conn.cursor()
        if level in ("g", "r"):
            syllabus_expr = "d.syllabus" if level == "r" else "''"
            sql = _GR_DETAIL_SELECT.format(prefix=level, syllabus_expr=syllabus_expr)
        else:  # 'u' / 's' / 'a' share the UG shape; the prefix is the level itself
            sql = _UG_DETAIL_SELECT.format(prefix=level)
        row = cur.execute(sql, (local_id,)).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Course not found")

        out = dict(row)

        # Replace every translatable field with its translation when present.
        ns = "gr" if level in ("g", "r") else "main"
        _apply_translations(cur, ns, local_id, lang, out)

    # Ensure all keys exist for both levels so frontend gating is consistent.
    for k in (
        "english_name", "prerequisites", "intro_cn", "intro_en",
        "grading", "ge_series", "language",
        "syllabus", "evaluation",
        "weekly_hours", "total_hours", "term", "audience", "extra_notes",
    ):
        out.setdefault(k, "")
    return out


# Static files ------------------------------------------------------------------

@app.get("/api/health")
def get_health():
    try:
        payload = get_cached_database_health()
    except (RuntimeError, sqlite3.Error):
        return JSONResponse({"status": "error"}, status_code=503, headers={"Cache-Control": "no-store"})
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


app.mount("/Images", StaticFiles(directory=str(BASE_DIR / "Images")), name="images")


@app.get("/")
def root():
    return FileResponse(BASE_DIR / "index.html")
