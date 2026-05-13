"""Pinhaoke Course Search API.

Reads two standalone SQLite databases:
  - 2026春季学期本科生课程.db  (undergraduate, AS main)
  - 2026春季学期研究生课程.db   (graduate,       AS gr)

The two share basic columns but have different detail schemas. Cross-DB queries
use ATTACH + UNION ALL. Each row gets a prefixed string id:
    "u<basic_info.id>"  for undergraduate
    "g<basic_info.id>"  for graduate
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
UG_DB = BASE_DIR / "2026春季学期本科生课程.db"
GR_DB = BASE_DIR / "2026春季学期研究生课程.db"

app = FastAPI(title="Pinhaoke")


@contextmanager
def get_db():
    conn = sqlite3.connect(str(UG_DB))
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{GR_DB}' AS gr")
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


# ---- filters ------------------------------------------------------------------


@app.get("/api/filters")
def get_filters():
    with get_db() as conn:
        c = conn.cursor()

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

        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    return {
        "course_types": course_types,
        "categories": categories,
        "departments": departments,
        "credits": credits,
        "gradings": gradings,
        "weekdays": weekdays,
    }


# ---- list ---------------------------------------------------------------------


def _build_where(
    q: str, type_: str, category: str, credits_: str,
    department: str, weekday: str, grading: str,
):
    """Build (where_clause, params) used inside the inner subquery alias `t`."""
    conds = []
    params = []
    if q:
        like = f"%{q}%"
        conds.append("(t.course_name LIKE ? OR t.teacher LIKE ? OR t.classroom LIKE ?)")
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
    sort: str = Query("", description="Sort: pinyin"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    where, params = _build_where(q, type, category, credits, department, weekday, grading)

    sort_map = {
        "pinyin": "t.course_name COLLATE NOCASE",
    }
    order_by = sort_map.get(sort, "t._level, t.id")
    offset = (page - 1) * page_size

    union_sql = f"({LIST_SELECT_UG} UNION ALL {LIST_SELECT_GR})"

    count_sql = f"SELECT COUNT(*) FROM {union_sql} AS t {where}"
    list_sql = (
        f"SELECT t.* FROM {union_sql} AS t {where} "
        f"ORDER BY {order_by} LIMIT ? OFFSET ?"
    )

    with get_db() as conn:
        cur = conn.cursor()
        total = cur.execute(count_sql, params).fetchone()[0]
        rows = cur.execute(list_sql, params + [page_size, offset]).fetchall()

    courses = [
        {
            "id": r["id"],
            "course_type": r["course_type"],
            "course_code": r["course_code"],
            "course_name": r["course_name"],
            "english_name": r["english_name"],
            "category": r["category"],
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
        for r in rows
    ]

    return {"total": total, "page": page, "page_size": page_size, "courses": courses}


# ---- detail -------------------------------------------------------------------


def _parse_id(course_id: str):
    if not course_id or course_id[0] not in ("u", "g"):
        return None, None
    try:
        return course_id[0], int(course_id[1:])
    except ValueError:
        return None, None


@app.get("/api/courses/{course_id}")
def get_course_detail(course_id: str, lang: str = Query("zh")):
    level, local_id = _parse_id(course_id)
    if level is None:
        raise HTTPException(status_code=404, detail="Course not found")

    with get_db() as conn:
        cur = conn.cursor()
        if level == "u":
            row = cur.execute(
                """SELECT 'u' || b.id AS id, b.course_type, b.course_code, b.class_no,
                          b.course_name, b.category, b.credits, b.teacher,
                          b.department, b.major, b.grade,
                          b.schedule, b.classroom, b.schedule_raw, b.weekdays,
                          b.enrollment, b.pnp, b.notes,
                          d.english_name, d.prerequisites, d.intro_cn, d.intro_en,
                          d.grading, d.ge_series, d.language, d.textbook,
                          d.reference_book, d.syllabus, d.evaluation
                   FROM basic_info b
                   LEFT JOIN detail_info d ON d.course_id = b.id
                   WHERE b.id = ?""",
                (local_id,),
            ).fetchone()
        else:
            row = cur.execute(
                """SELECT 'g' || b.id AS id, '研究生课' AS course_type,
                          b.course_code, b.class_no, b.course_name, b.category,
                          b.credits, b.teacher, b.department, b.major, b.grade,
                          b.schedule, b.classroom, b.schedule_raw, b.weekdays,
                          b.enrollment, '' AS pnp, b.notes,
                          d.english_name,
                          d.weekly_hours, d.total_hours, d.term, d.audience,
                          d.reference_book, d.intro AS intro_cn, d.extra_notes
                   FROM gr.basic_info b
                   LEFT JOIN gr.detail_info d ON d.course_id = b.id
                   WHERE b.id = ?""",
                (local_id,),
            ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Course not found")

        keys = row.keys()
        out = {k: row[k] for k in keys}

        # Replace intro_cn / extra_notes with translated text when available.
        if lang != "zh":
            ns = "main" if level == "u" else "gr"
            tr_intro = cur.execute(
                f"SELECT text FROM {ns}.translations "
                f"WHERE course_id=? AND field='intro_cn' AND lang=?",
                (local_id, lang),
            ).fetchone()
            if tr_intro:
                out["intro_cn"] = tr_intro[0]
            tr_extra = cur.execute(
                f"SELECT text FROM {ns}.translations "
                f"WHERE course_id=? AND field='extra_notes' AND lang=?",
                (local_id, lang),
            ).fetchone()
            if tr_extra:
                out["extra_notes"] = tr_extra[0]

    # Ensure all keys exist for both levels so frontend gating is consistent.
    for k in (
        "english_name", "prerequisites", "intro_cn", "intro_en",
        "grading", "ge_series", "language", "textbook",
        "reference_book", "syllabus", "evaluation",
        "weekly_hours", "total_hours", "term", "audience", "extra_notes",
    ):
        out.setdefault(k, "")
    return out


# Static files ------------------------------------------------------------------

app.mount("/Images", StaticFiles(directory="Images"), name="images")


@app.get("/")
def root():
    return FileResponse("index.html")
