#!/usr/bin/env python3
"""Build 2026秋季学期研究生课程.db from the scraped PKU 26-27 fall JSON."""
import json
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "数据库构建脚本"))
from build_common import parse_schedule, parse_first_period, to_float  # noqa: E402

DB_PATH = PROJECT_ROOT / "数据库" / "2026秋季学期研究生课程.db"
SOURCE = PROJECT_ROOT / "课程数据" / "北大研究生课程_26-27第1学期.json"

SCHEMA = """
CREATE TABLE basic_info (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    course_code     TEXT NOT NULL,
    class_no        TEXT NOT NULL,
    course_name     TEXT,
    category        TEXT,
    credits         REAL,
    teacher         TEXT,
    department      TEXT,
    major           TEXT,
    grade           TEXT,
    schedule        TEXT,
    classroom       TEXT,
    schedule_raw    TEXT,
    weekdays        TEXT,
    first_period    INTEGER,
    enrollment      TEXT,
    notes           TEXT,
    UNIQUE(course_code, class_no, teacher, department)
);

CREATE TABLE detail_info (
    course_id       INTEGER PRIMARY KEY REFERENCES basic_info(id) ON DELETE CASCADE,
    english_name    TEXT,
    weekly_hours    REAL,
    total_hours     REAL,
    term            TEXT,
    audience        TEXT,
    reference_book  TEXT,
    intro           TEXT,
    extra_notes     TEXT,
    syllabus        TEXT
);

CREATE TABLE translations (
    course_id INTEGER NOT NULL,
    field     TEXT NOT NULL,
    lang      TEXT NOT NULL,
    text      TEXT NOT NULL,
    PRIMARY KEY (course_id, field, lang)
);

CREATE INDEX idx_basic_course_code  ON basic_info(course_code);
CREATE INDEX idx_basic_course_name  ON basic_info(course_name);
CREATE INDEX idx_basic_department   ON basic_info(department);
CREATE INDEX idx_basic_category     ON basic_info(category);
CREATE INDEX idx_basic_credits      ON basic_info(credits);
CREATE INDEX idx_basic_first_period ON basic_info(first_period);
CREATE INDEX idx_trans_cid_field    ON translations(course_id, field);

CREATE VIEW courses_view AS
SELECT
    b.id, b.course_code, b.class_no, b.course_name, b.category, b.credits,
    b.teacher, b.department, b.major, b.grade,
    b.schedule, b.classroom, b.schedule_raw, b.weekdays,
    b.enrollment, b.notes,
    d.english_name, d.weekly_hours, d.total_hours, d.term,
    d.audience, d.reference_book, d.intro, d.extra_notes, d.syllabus
FROM basic_info b
LEFT JOIN detail_info d ON d.course_id = b.id;
"""


def build():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    with SOURCE.open(encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    c = conn.cursor()
    c.executescript(SCHEMA)

    inserted = 0
    duplicate_rows = 0
    seen = set()
    for item in data:
        bi = item.get("基本信息", {}) or {}
        di = item.get("详细信息", {}) or {}
        course_code = bi.get("课程号", "").strip()
        class_no = bi.get("班号", "").strip()
        teacher = bi.get("教师", "").strip()
        department = bi.get("开课单位", "").strip()
        if not course_code:
            continue
        key = (course_code, class_no, teacher, department)
        if key in seen:
            duplicate_rows += 1
            continue
        seen.add(key)

        raw_sched = bi.get("上课时间及教室", "")
        schedule, classroom, weekdays = parse_schedule(raw_sched)
        first_period = parse_first_period(raw_sched)

        c.execute(
            """INSERT INTO basic_info
               (course_code, class_no, course_name, category, credits, teacher,
                department, major, grade, schedule, classroom, schedule_raw,
                weekdays, first_period, enrollment, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                course_code, class_no,
                bi.get("课程名", ""),
                bi.get("课程类别", ""),
                to_float(bi.get("学分", "0")),
                teacher,
                department,
                bi.get("专业", ""),
                bi.get("年级", ""),
                schedule, classroom, raw_sched, weekdays,
                first_period,
                bi.get("限数已选", ""),
                bi.get("备注", ""),
            ),
        )
        course_id = c.lastrowid
        c.execute(
            """INSERT INTO detail_info
               (course_id, english_name, weekly_hours, total_hours, term,
                audience, reference_book, intro, extra_notes, syllabus)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                course_id,
                di.get("英文名称", ""),
                to_float(di.get("周学时", "0")),
                to_float(di.get("总学时", "0")),
                di.get("开课学期", ""),
                di.get("修读对象", ""),
                di.get("参考书", ""),
                di.get("课程简介", ""),
                di.get("详情备注", ""),
                di.get("大纲", ""),
            ),
        )
        inserted += 1

    conn.commit()
    print(f"Database built: {DB_PATH}")
    print(f"  rows inserted: {inserted}")
    print(f"  skipped dup  : {duplicate_rows}")
    conn.close()


if __name__ == "__main__":
    build()
