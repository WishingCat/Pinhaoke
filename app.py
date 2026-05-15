"""Pinhaoke Course Search API.

Reads three standalone SQLite databases (term-switched at request time):
  spring →
    - 2026春季学期本科生课程.db  (undergraduate, AS main)
    - 2026春季学期研究生课程.db  (graduate,      AS gr)
  summer →
    - 2026暑期本科生课程.db      (undergraduate, AS main)

The DBs share the basic_info columns but have different detail schemas. Cross-DB
queries use ATTACH + UNION ALL. Each row carries a prefixed string id:
    "u<basic_info.id>"  spring undergrad
    "g<basic_info.id>"  spring graduate
    "s<basic_info.id>"  summer undergrad

The prefix alone determines which DB the detail endpoint opens, so callers do
NOT need to pass ?term= when fetching a specific course.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "数据库"
UG_DB = DB_DIR / "2026春季学期本科生课程.db"
GR_DB = DB_DIR / "2026春季学期研究生课程.db"
SUMMER_DB = DB_DIR / "2026暑期本科生课程.db"

# (alias, path, id_prefix, list_select_var_name)
TERM_DBS = {
    "spring": [
        ("main", UG_DB, "u"),
        ("gr",   GR_DB, "g"),
    ],
    "summer": [
        ("main", SUMMER_DB, "s"),
    ],
}

app = FastAPI(title="Pinhaoke")


@contextmanager
def get_db(term: str = "spring"):
    config = TERM_DBS.get(term)
    if config is None:
        raise HTTPException(status_code=400, detail=f"Unknown term: {term}")
    main_path = config[0][1]
    conn = sqlite3.connect(str(main_path))
    conn.row_factory = sqlite3.Row
    for alias, path, _ in config[1:]:
        conn.execute(f"ATTACH DATABASE '{path}' AS {alias}")
    try:
        yield conn
    finally:
        conn.close()


# ---- shared SELECT fragments --------------------------------------------------

# Common basic columns plus course_type. Undergrad has it natively; graduate
# synthesises '研究生课'. pnp only exists on undergrad.
LIST_SELECT_UG = """
    SELECT
        'u' || b.id            AS id,
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
        'u'                    AS _level
    FROM basic_info b
    LEFT JOIN detail_info d ON d.course_id = b.id
"""

LIST_SELECT_GR = """
    SELECT
        'g' || b.id            AS id,
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
        'g'                    AS _level
    FROM gr.basic_info b
    LEFT JOIN gr.detail_info d ON d.course_id = b.id
"""

# Summer undergrad: same schema as spring UG, but lives alone in `main` when
# term=summer is active. Prefix 's' to keep ids unique across terms.
LIST_SELECT_SUMMER = """
    SELECT
        's' || b.id            AS id,
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
        's'                    AS _level
    FROM basic_info b
    LEFT JOIN detail_info d ON d.course_id = b.id
"""

# Pre-built UNION expressions for each term.
TERM_UNION_SQL = {
    "spring": f"({LIST_SELECT_UG} UNION ALL {LIST_SELECT_GR})",
    "summer": f"({LIST_SELECT_SUMMER})",
}


# ---- filters ------------------------------------------------------------------


@app.get("/api/filters")
def get_filters(term: str = Query("spring", description="spring | summer")):
    with get_db(term) as conn:
        c = conn.cursor()

        if term == "spring":
            course_types = [r[0] for r in c.execute(
                """SELECT DISTINCT course_type FROM basic_info
                   WHERE course_type != ''
                   UNION
                   SELECT '研究生课'
                   ORDER BY 1"""
            )]
            categories = [r[0] for r in c.execute(
                """SELECT DISTINCT category FROM basic_info WHERE category != ''
                   UNION
                   SELECT DISTINCT category FROM gr.basic_info WHERE category != ''
                   ORDER BY 1"""
            )]
            departments = [r[0] for r in c.execute(
                """SELECT DISTINCT department FROM basic_info WHERE department != ''
                   UNION
                   SELECT DISTINCT department FROM gr.basic_info WHERE department != ''
                   ORDER BY 1"""
            )]
            credits = [r[0] for r in c.execute(
                """SELECT DISTINCT credits FROM basic_info
                   UNION
                   SELECT DISTINCT credits FROM gr.basic_info
                   ORDER BY 1"""
            )]
            gradings = [r[0] for r in c.execute(
                """SELECT DISTINCT grading FROM detail_info
                   WHERE grading != '' ORDER BY grading"""
            )]
        else:  # summer — single DB
            course_types = [r[0] for r in c.execute(
                """SELECT DISTINCT course_type FROM basic_info
                   WHERE course_type != '' ORDER BY 1"""
            )]
            categories = [r[0] for r in c.execute(
                """SELECT DISTINCT category FROM basic_info
                   WHERE category != '' ORDER BY 1"""
            )]
            departments = [r[0] for r in c.execute(
                """SELECT DISTINCT department FROM basic_info
                   WHERE department != '' ORDER BY 1"""
            )]
            credits = [r[0] for r in c.execute(
                "SELECT DISTINCT credits FROM basic_info ORDER BY 1"
            )]
            gradings = [r[0] for r in c.execute(
                """SELECT DISTINCT grading FROM detail_info
                   WHERE grading != '' ORDER BY grading"""
            )]

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


def _build_where(
    q: str, type_: str, category: str, credits_: str,
    department: str, weekday: str, grading: str,
):
    """Build (where_clause, params) used inside the inner subquery alias `t`."""
    conds = []
    params = []
    if q:
        # Escape LIKE wildcards so `100%` searches literal '%', not "any chars".
        q_esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{q_esc}%"
        conds.append(
            "(t.course_name LIKE ? ESCAPE '\\' "
            "OR t.teacher LIKE ? ESCAPE '\\' "
            "OR t.classroom LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like])
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
    q: str = Query("", description="Search query"),
    type: str = Query("", description="Course type filter"),
    category: str = Query("", description="Category filter"),
    credits: str = Query("", description="Credits filter"),
    department: str = Query("", description="Department filter"),
    weekday: str = Query("", description="Weekday filter"),
    grading: str = Query("", description="Grading filter"),
    sort: str = Query("", description="Sort: pinyin | pinyin_desc | credits_asc | credits_desc | time_asc | random"),
    random_seed: int = Query(0, description="Seed used by sort=random; same seed → same order"),
    lang: str = Query("zh", description="Display language"),
    term: str = Query("spring", description="spring | summer"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if term not in TERM_UNION_SQL:
        raise HTTPException(status_code=400, detail=f"Unknown term: {term}")

    where, params = _build_where(q, type, category, credits, department, weekday, grading)

    if sort == "random":
        # Deterministic shuffle: same seed → stable order across pagination.
        # int param is already validated by FastAPI, safe to interpolate.
        # Two large primes mix the seed so even tiny seeds produce well-spread
        # mul/add values, giving genuinely different orderings per seed.
        # The CASE per id prefix breaks 'u123' / 'g123' / 's123' hash collisions.
        seed = max(1, int(random_seed))
        mul = ((seed * 1664525) % 999983) or 7
        add = (seed * 1013904223) % 999983
        # After GROUP BY the id column is the representative id (a single
        # 'u123' / 'g456' / 's789' string), so we reach into it via SUBSTR.
        order_by = (
            f"((CAST(SUBSTR(id, 2) AS INTEGER) * {mul} "
            f"+ CASE SUBSTR(id, 1, 1) WHEN 'g' THEN 333331 WHEN 's' THEN 666661 ELSE 0 END "
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
        # Default: course name asc (stable, mixes UG/GR in spring instead of
        # piling all undergrad rows before any grad row). Empty names land last.
        order_by = sort_map.get(
            sort,
            "(course_name = '' OR course_name IS NULL), "
            "course_name COLLATE NOCASE, id",
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


def _parse_id(course_id: str):
    """Return (term, level, local_id) or (None, None, None) on parse failure.

    level is the single-char id prefix ('u' / 'g' / 's'); term tells get_db()
    which DBs to open.
    """
    if not course_id:
        return None, None, None
    prefix = course_id[0]
    try:
        local_id = int(course_id[1:])
    except ValueError:
        return None, None, None
    if prefix == "u":
        return "spring", "u", local_id
    if prefix == "g":
        return "spring", "g", local_id
    if prefix == "s":
        return "summer", "s", local_id
    return None, None, None


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
    SELECT 'g' || b.id AS id, '研究生课' AS course_type,
           b.course_code, b.class_no, b.course_name, b.category,
           b.credits, b.teacher, b.department, b.major, b.grade,
           b.schedule, b.classroom,
           b.enrollment, '' AS pnp, b.notes,
           d.english_name,
           d.weekly_hours, d.total_hours, d.term, d.audience,
           d.intro AS intro_cn, d.extra_notes
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
        if level == "u":
            sql = _UG_DETAIL_SELECT.format(prefix="u")
        elif level == "s":
            sql = _UG_DETAIL_SELECT.format(prefix="s")
        else:
            sql = _GR_DETAIL_SELECT
        row = cur.execute(sql, (local_id,)).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Course not found")

        keys = row.keys()
        out = {k: row[k] for k in keys}

        # Replace every translatable field with its translation when present.
        ns = "gr" if level == "g" else "main"
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

app.mount("/Images", StaticFiles(directory="Images"), name="images")


@app.get("/")
def root():
    return FileResponse("index.html")
