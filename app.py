"""Pinhaoke Course Search API."""
import sqlite3
from contextlib import contextmanager
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="Pinhaoke")

DB_PATH = "courses.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@app.get("/api/filters")
def get_filters():
    with get_db() as conn:
        c = conn.cursor()

        course_types = [r[0] for r in c.execute(
            "SELECT DISTINCT course_type FROM courses ORDER BY course_type"
        )]
        categories = [r[0] for r in c.execute(
            "SELECT DISTINCT category FROM courses ORDER BY category"
        )]
        departments = [r[0] for r in c.execute(
            "SELECT DISTINCT department FROM courses ORDER BY department"
        )]
        credits = [r[0] for r in c.execute(
            "SELECT DISTINCT credits FROM courses ORDER BY credits"
        )]
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    return {
        "course_types": course_types,
        "categories": categories,
        "departments": departments,
        "credits": credits,
        "weekdays": weekdays,
    }


@app.get("/api/courses")
def list_courses(
    q: str = Query("", description="Search query"),
    type: str = Query("", description="Course type filter"),
    category: str = Query("", description="Category filter"),
    credits: str = Query("", description="Credits filter"),
    department: str = Query("", description="Department filter"),
    weekday: str = Query("", description="Weekday filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conditions = []
    params = []

    if q:
        q_like = f"%{q}%"
        conditions.append(
            "(course_name LIKE ? OR teacher LIKE ? OR classroom LIKE ?)"
        )
        params.extend([q_like, q_like, q_like])

    if type:
        conditions.append("course_type = ?")
        params.append(type)

    if category:
        conditions.append("category = ?")
        params.append(category)

    if credits:
        conditions.append("credits = ?")
        params.append(float(credits))

    if department:
        conditions.append("department = ?")
        params.append(department)

    if weekday:
        conditions.append("weekdays LIKE ?")
        params.append(f"%{weekday}%")

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * page_size

    with get_db() as conn:
        c = conn.cursor()

        count_row = c.execute(
            f"SELECT COUNT(*) FROM courses{where}", params
        ).fetchone()
        total = count_row[0]

        rows = c.execute(
            f"""SELECT id, course_type, course_code, course_name, category,
                       credits, teacher, class_no, department, schedule,
                       classroom, enrollment, pnp, notes, major, grade
                FROM courses{where}
                ORDER BY id
                LIMIT ? OFFSET ?""",
            params + [page_size, offset],
        ).fetchall()

    courses = []
    for r in rows:
        courses.append({
            "id": r["id"],
            "course_type": r["course_type"],
            "course_code": r["course_code"],
            "course_name": r["course_name"],
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
        })

    return {"total": total, "page": page, "page_size": page_size, "courses": courses}


@app.get("/api/courses/{course_id}")
def get_course_detail(course_id: int):
    with get_db() as conn:
        c = conn.cursor()

        row = c.execute(
            """SELECT c.*, d.english_name, d.prerequisites, d.intro_cn,
                      d.intro_en, d.grading, d.ge_series, d.language,
                      d.textbook, d.reference, d.syllabus, d.evaluation
               FROM courses c
               LEFT JOIN course_details d ON c.id = d.course_id
               WHERE c.id = ?""",
            (course_id,),
        ).fetchone()

    if not row:
        return {"error": "Course not found"}

    return {
        "id": row["id"],
        "course_type": row["course_type"],
        "course_code": row["course_code"],
        "course_name": row["course_name"],
        "category": row["category"],
        "credits": row["credits"],
        "teacher": row["teacher"],
        "class_no": row["class_no"],
        "department": row["department"],
        "major": row["major"],
        "grade": row["grade"],
        "schedule": row["schedule"],
        "classroom": row["classroom"],
        "schedule_raw": row["schedule_raw"],
        "enrollment": row["enrollment"],
        "pnp": row["pnp"],
        "notes": row["notes"],
        "english_name": row["english_name"],
        "prerequisites": row["prerequisites"],
        "intro_cn": row["intro_cn"],
        "intro_en": row["intro_en"],
        "grading": row["grading"],
        "ge_series": row["ge_series"],
        "language": row["language"],
        "textbook": row["textbook"],
        "reference": row["reference"],
        "syllabus": row["syllabus"],
        "evaluation": row["evaluation"],
    }


# Static files
app.mount("/Images", StaticFiles(directory="Images"), name="images")


@app.get("/")
def root():
    return FileResponse("index.html")
