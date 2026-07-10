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
UG_DETAIL_COLUMNS = (
    "english_name", "prerequisites", "intro_cn", "intro_en", "grading",
    "ge_series", "language", "textbook", "reference_book", "syllabus",
    "evaluation",
)
SPRING_GR_DETAIL_COLUMNS = (
    "english_name", "weekly_hours", "total_hours", "term", "audience",
    "reference_book", "intro", "extra_notes",
)
FALL_GR_DETAIL_COLUMNS = (*SPRING_GR_DETAIL_COLUMNS, "syllabus")


def _detail_score_sql(columns: tuple[str, ...]) -> str:
    return " + ".join(
        f"CASE WHEN d.{column} IS NOT NULL "
        f"AND TRIM(CAST(d.{column} AS TEXT)) != '' THEN 1 ELSE 0 END"
        for column in columns
    )


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
        '{prefix}'             AS _level,
        ({detail_score})       AS detail_score
    FROM basic_info b
    LEFT JOIN detail_info d ON d.course_id = b.id
"""

_UG_DETAIL_SCORE_SQL = _detail_score_sql(UG_DETAIL_COLUMNS)
LIST_SELECT_UG = _UG_LIST_SELECT.format(prefix="u", detail_score=_UG_DETAIL_SCORE_SQL)
LIST_SELECT_SUMMER = _UG_LIST_SELECT.format(prefix="s", detail_score=_UG_DETAIL_SCORE_SQL)
LIST_SELECT_FALL_UG = _UG_LIST_SELECT.format(prefix="a", detail_score=_UG_DETAIL_SCORE_SQL)

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
        '{prefix}'             AS _level,
        ({detail_score})       AS detail_score
    FROM {ns}.basic_info b
    LEFT JOIN {ns}.detail_info d ON d.course_id = b.id
"""

LIST_SELECT_GR = _GR_LIST_SELECT.format(
    prefix="g",
    ns="gr",
    detail_score=_detail_score_sql(SPRING_GR_DETAIL_COLUMNS),
)
LIST_SELECT_FALL_GR = _GR_LIST_SELECT.format(
    prefix="r",
    ns="gr",
    detail_score=_detail_score_sql(FALL_GR_DETAIL_COLUMNS),
)

# Pre-built UNION expressions for each term.
TERM_UNION_SQL = {
    "spring": f"({LIST_SELECT_UG} UNION ALL {LIST_SELECT_GR})",
    "summer": f"({LIST_SELECT_SUMMER})",
    "fall": f"({LIST_SELECT_FALL_UG} UNION ALL {LIST_SELECT_FALL_GR})",
}

TERM_LIST_SELECTS = {
    "spring": (("main", LIST_SELECT_UG), ("gr", LIST_SELECT_GR)),
    "summer": (("main", LIST_SELECT_SUMMER),),
    "fall": (("main", LIST_SELECT_FALL_UG), ("gr", LIST_SELECT_FALL_GR)),
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


def _build_source_where(filters: dict[str, object]) -> tuple[str, list[object]]:
    """Build the source-row predicate used only to identify matching groups."""
    conds = []
    params: list[object] = []
    q = str(filters.get("q") or "")
    if q:
        like = _like(q)
        conds.append(
            "(s.course_name LIKE ? ESCAPE '\\' "
            "OR s.display_course_name LIKE ? ESCAPE '\\' "
            "OR s.english_name LIKE ? ESCAPE '\\' "
            "OR s.teacher LIKE ? ESCAPE '\\' "
            "OR s.classroom LIKE ? ESCAPE '\\' "
            "OR s.display_classroom LIKE ? ESCAPE '\\' "
            "OR s.course_code LIKE ? ESCAPE '\\')"
        )
        params.extend([like] * 7)
    classroom = str(filters.get("classroom") or "")
    if classroom:
        like = _like(classroom)
        conds.append(
            "(s.classroom LIKE ? ESCAPE '\\' "
            "OR s.display_classroom LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like])
    type_ = str(filters.get("type") or "")
    if type_:
        conds.append("s.course_type = ?")
        params.append(type_)
    category = str(filters.get("category") or "")
    if category:
        conds.append("s.category = ?")
        params.append(category)
    credits_ = str(filters.get("credits") or "")
    if credits_:
        try:
            conds.append("s.credits = ?")
            params.append(float(credits_))
        except ValueError:
            pass
    department = str(filters.get("department") or "")
    if department:
        conds.append("s.department = ?")
        params.append(department)
    weekday = str(filters.get("weekday") or "")
    if weekday:
        conds.append("s.weekdays LIKE ?")
        params.append(f"%{weekday}%")
    grading = str(filters.get("grading") or "")
    if grading:
        conds.append("s.grading = ?")
        params.append(grading)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def _translated_source_select(base_select: str, ns: str, lang: str) -> tuple[str, list[object]]:
    if lang == "zh":
        display_columns = """
            t.course_name AS display_course_name,
            t.classroom AS display_classroom,
            t.notes AS display_notes
        """
        joins = ""
        params: list[object] = []
    else:
        display_columns = """
            COALESCE(NULLIF(TRIM(name_tr.text), ''), t.course_name) AS display_course_name,
            COALESCE(NULLIF(TRIM(room_tr.text), ''), t.classroom) AS display_classroom,
            COALESCE(NULLIF(TRIM(notes_tr.text), ''), t.notes) AS display_notes
        """
        joins = f"""
            LEFT JOIN {ns}.translations AS name_tr
              ON name_tr.course_id = CAST(SUBSTR(t.id, 2) AS INTEGER)
             AND name_tr.field = 'course_name' AND name_tr.lang = ?
            LEFT JOIN {ns}.translations AS room_tr
              ON room_tr.course_id = CAST(SUBSTR(t.id, 2) AS INTEGER)
             AND room_tr.field = 'classroom' AND room_tr.lang = ?
            LEFT JOIN {ns}.translations AS notes_tr
              ON notes_tr.course_id = CAST(SUBSTR(t.id, 2) AS INTEGER)
             AND notes_tr.field = 'notes' AND notes_tr.lang = ?
        """
        params = [lang, lang, lang]

    sql = f"""
        SELECT t.*,
               {display_columns},
               t._level || CHAR(31) || t.course_code || CHAR(31) || t.class_no || CHAR(31) ||
                 CASE WHEN COALESCE(t.teacher, '') = '' THEN t.id ELSE t.teacher END AS group_key
        FROM ({base_select}) AS t
        {joins}
    """
    return sql, params


def _build_course_query(
    term: str,
    lang: str,
    filters: dict[str, object],
) -> tuple[str, list[object], str]:
    """Return source-row SQL, ordered parameters, and matching-group predicate."""
    if lang == "zh":
        source_sql, source_params = _translated_source_select(TERM_UNION_SQL[term], "main", lang)
    else:
        selects = []
        source_params = []
        for ns, base_select in TERM_LIST_SELECTS[term]:
            select_sql, select_params = _translated_source_select(base_select, ns, lang)
            selects.append(select_sql)
            source_params.extend(select_params)
        source_sql = " UNION ALL ".join(selects)

    matching_where, filter_params = _build_source_where(filters)
    return source_sql, [*source_params, *filter_params], matching_where


def _preferred_text(column: str, alias: str | None = None) -> str:
    alias = alias or column
    return f"""
        COALESCE(
            MAX(CASE WHEN r.representative_rank = 1
                      AND TRIM(COALESCE(r.{column}, '')) != '' THEN r.{column} END),
            MAX(CASE WHEN TRIM(COALESCE(r.{column}, '')) != '' THEN r.{column} END),
            MAX(CASE WHEN r.representative_rank = 1 THEN r.{column} END),
            MAX(r.{column})
        ) AS {alias}
    """


def _preferred_value(column: str) -> str:
    return f"""
        COALESCE(
            MAX(CASE WHEN r.representative_rank = 1 THEN r.{column} END),
            MAX(r.{column})
        ) AS {column}
    """


def _grouped_course_ctes(source_sql: str, matching_where: str) -> str:
    text_columns = (
        _preferred_text("course_code"),
        _preferred_text("class_no"),
        _preferred_text("display_course_name", "course_name"),
        _preferred_text("course_name", "original_course_name"),
        _preferred_text("teacher"),
        _preferred_text("department"),
        _preferred_text("major"),
        _preferred_text("grade"),
        _preferred_text("schedule"),
        _preferred_text("display_classroom", "classroom"),
        _preferred_text("weekdays"),
        _preferred_text("enrollment"),
        _preferred_text("pnp"),
        _preferred_text("display_notes", "notes"),
        _preferred_text("english_name"),
        _preferred_text("grading"),
        _preferred_text("language"),
        _preferred_text("audience"),
    )
    value_columns = (_preferred_value("credits"), _preferred_value("first_period"))
    scalar_columns = ",\n".join((*text_columns, *value_columns))

    return f"""
        WITH source AS (
            {source_sql}
        ), matching_groups AS (
            SELECT DISTINCT s.group_key
            FROM source AS s
            {matching_where}
        ), ranked AS (
            SELECT s.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.group_key
                       ORDER BY s.detail_score DESC,
                                CAST(SUBSTR(s.id, 2) AS INTEGER),
                                s.id
                   ) AS representative_rank
            FROM source AS s
            JOIN matching_groups USING (group_key)
        ), badge_values AS (
            SELECT DISTINCT group_key, 'course_type' AS badge_kind, course_type AS badge_value
            FROM ranked
            WHERE TRIM(COALESCE(course_type, '')) != ''
            UNION ALL
            SELECT DISTINCT group_key, 'category' AS badge_kind, category AS badge_value
            FROM ranked
            WHERE TRIM(COALESCE(category, '')) != ''
        ), badge_sequences AS (
            SELECT group_key, badge_kind,
                   GROUP_CONCAT(badge_value) OVER (
                       PARTITION BY group_key, badge_kind
                       ORDER BY badge_value COLLATE BINARY
                       ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                   ) AS badge_list
            FROM badge_values
        ), badges AS (
            SELECT group_key,
                   MAX(CASE WHEN badge_kind = 'course_type' THEN badge_list END) AS course_type,
                   MAX(CASE WHEN badge_kind = 'category' THEN badge_list END) AS category
            FROM badge_sequences
            GROUP BY group_key
        ), grouped AS (
            SELECT MAX(CASE WHEN r.representative_rank = 1 THEN r.id END) AS id,
                   MAX(b.course_type) AS course_type,
                   MAX(b.category) AS category,
                   {scalar_columns}
            FROM ranked AS r
            LEFT JOIN badges AS b USING (group_key)
            GROUP BY r.group_key
        )
    """


def _course_order_by(sort: str, term: str, random_seed: int) -> str:
    if sort == "random":
        seed = max(1, int(random_seed))
        mul = ((seed * 1664525) % 999983) or 7
        add = (seed * 1013904223) % 999983
        return (
            f"((CAST(SUBSTR(id, 2) AS INTEGER) * {mul} "
            f"+ CASE SUBSTR(id, 1, 1) "
            f"WHEN 'g' THEN 333331 WHEN 's' THEN 666661 "
            f"WHEN 'a' THEN 111113 WHEN 'r' THEN 444449 ELSE 0 END "
            f"+ {add}) % 999983), id"
        )

    name_asc = "(course_name = '' OR course_name IS NULL), course_name COLLATE NOCASE, id"
    name_desc = "(course_name = '' OR course_name IS NULL), course_name COLLATE NOCASE DESC, id"
    sort_map = {
        "name_asc": name_asc,
        "pinyin": name_asc,
        "name_desc": name_desc,
        "pinyin_desc": name_desc,
        "credits_asc": f"(credits IS NULL), credits ASC, {name_asc}",
        "credits_desc": f"(credits IS NULL), credits DESC, {name_asc}",
        "time_asc": f"(first_period IS NULL), first_period, {name_asc}",
    }
    if sort in sort_map:
        return sort_map[sort]

    sociology_priority = ""
    if term == "summer":
        sociology_priority = (
            "(CASE WHEN department = '社会学系' "
            "OR department = '中国社会科学调查中心' "
            "OR original_course_name LIKE '%社会学%' THEN 0 ELSE 1 END), "
        )
    return f"{sociology_priority}{name_asc}"


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
    sort: str = Query("", description="Sort: name_asc | name_desc | pinyin | pinyin_desc | credits_asc | credits_desc | time_asc | random"),
    random_seed: int = Query(0, description="Seed used by sort=random; same seed → same order"),
    lang: str = Query("zh", description="Display language"),
    term: str = Query("fall", description="spring | summer | fall"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if term not in TERM_UNION_SQL:
        raise HTTPException(status_code=400, detail=f"Unknown term: {term}")

    filters = {
        "q": q,
        "type": type,
        "category": category,
        "credits": credits,
        "department": department,
        "weekday": weekday,
        "grading": grading,
        "classroom": classroom,
    }
    source_sql, params, matching_where = _build_course_query(term, lang, filters)
    ctes = _grouped_course_ctes(source_sql, matching_where)
    order_by = _course_order_by(sort, term, random_seed)
    offset = (page - 1) * page_size

    count_sql = f"{ctes} SELECT COUNT(*) FROM grouped"
    list_sql = f"{ctes} SELECT * FROM grouped ORDER BY {order_by} LIMIT ? OFFSET ?"

    with get_db(term) as conn:
        cur = conn.cursor()
        total = cur.execute(count_sql, params).fetchone()[0]
        rows = cur.execute(list_sql, params + [page_size, offset]).fetchall()

    courses = []
    for r in rows:
        rid = r["id"]
        ct_raw = r["course_type"] or ""
        cat_raw = r["category"] or ""
        courses.append(
            {
                "id": rid,
                "course_type": [s for s in ct_raw.split(",") if s],
                "course_code": r["course_code"],
                "course_name": r["course_name"],
                "english_name": r["english_name"],
                "category": [s for s in cat_raw.split(",") if s],
                "credits": r["credits"],
                "teacher": r["teacher"],
                "class_no": r["class_no"],
                "department": r["department"],
                "schedule": r["schedule"],
                "classroom": r["classroom"],
                "enrollment": r["enrollment"],
                "pnp": r["pnp"],
                "notes": r["notes"],
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
