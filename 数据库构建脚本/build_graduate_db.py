#!/usr/bin/env python3
"""Build 2026春季学期研究生课程.db from 北大研究生课程_25-26第2学期.json.

Schema:
    basic_info   one row per (course_code, class_no)
    detail_info  one row per basic_info.id, joined 1:1
    courses_view convenience view joining both
"""
import json
import sqlite3
from pathlib import Path

from build_common import parse_schedule, parse_first_period, to_float

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "数据库" / "2026春季学期研究生课程.db"
SOURCE = PROJECT_ROOT / "课程数据" / "北大研究生课程_25-26第2学期.json"

SCHEMA = """
CREATE TABLE basic_info (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    course_code     TEXT NOT NULL,        -- 课程号
    class_no        TEXT NOT NULL,        -- 班号
    course_name     TEXT,                 -- 课程名
    category        TEXT,                 -- 课程类别（必修/选修/限选）
    credits         REAL,                 -- 学分
    teacher         TEXT,                 -- 教师
    department      TEXT,                 -- 开课单位
    major           TEXT,                 -- 专业
    grade           TEXT,                 -- 年级
    schedule        TEXT,                 -- 解析后的上课时间
    classroom       TEXT,                 -- 解析后的教室
    schedule_raw    TEXT,                 -- 上课时间及教室（原始）
    weekdays        TEXT,                 -- 周X,周Y
    first_period    INTEGER,              -- 最早开始节次（用于按时间排序）
    enrollment      TEXT,                 -- 限数/已选
    notes           TEXT,                 -- 备注（如"博士"）
    UNIQUE(course_code, class_no)
);

CREATE TABLE detail_info (
    course_id       INTEGER PRIMARY KEY REFERENCES basic_info(id) ON DELETE CASCADE,
    english_name    TEXT,                 -- 英文名称
    weekly_hours    REAL,                 -- 周学时
    total_hours     REAL,                 -- 总学时
    term            TEXT,                 -- 开课学期
    audience        TEXT,                 -- 修读对象
    reference_book  TEXT,                 -- 参考书
    intro           TEXT,                 -- 课程简介
    extra_notes     TEXT                  -- 详情备注
);

CREATE INDEX idx_basic_course_code  ON basic_info(course_code);
CREATE INDEX idx_basic_course_name  ON basic_info(course_name);
CREATE INDEX idx_basic_department   ON basic_info(department);
CREATE INDEX idx_basic_category     ON basic_info(category);
CREATE INDEX idx_basic_credits      ON basic_info(credits);
CREATE INDEX idx_basic_first_period ON basic_info(first_period);

CREATE VIEW courses_view AS
SELECT
    b.id, b.course_code, b.class_no, b.course_name, b.category, b.credits,
    b.teacher, b.department, b.major, b.grade,
    b.schedule, b.classroom, b.schedule_raw, b.weekdays,
    b.enrollment, b.notes,
    d.english_name, d.weekly_hours, d.total_hours, d.term,
    d.audience, d.reference_book, d.intro, d.extra_notes
FROM basic_info b
LEFT JOIN detail_info d ON d.course_id = b.id;
"""


def build():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    c = conn.cursor()
    c.executescript(SCHEMA)

    with open(SOURCE, encoding="utf-8") as f:
        data = json.load(f)

    inserted = 0
    seen = set()
    for item in data:
        bi = item.get("基本信息", {}) or {}
        di = item.get("详细信息", {}) or {}
        course_code = bi.get("课程号", "").strip()
        class_no = bi.get("班号", "").strip()
        if not course_code:
            continue
        key = (course_code, class_no)
        if key in seen:
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
                bi.get("教师", ""),
                bi.get("开课单位", ""),
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
                audience, reference_book, intro, extra_notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
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
            ),
        )
        inserted += 1

    conn.commit()
    print(f"Database built: {DB_PATH}")
    print(f"  rows inserted: {inserted}")
    conn.close()


if __name__ == "__main__":
    build()
