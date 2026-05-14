#!/usr/bin/env python3
"""Build 2026春季学期本科生课程.db from the three undergrad JSONs.

Schema:
    basic_info   one row per (course_code, class_no, course_type)
    detail_info  one row per basic_info.id, joined 1:1
    courses_view convenience view joining both
"""
import json
import sqlite3
from pathlib import Path

from build_common import parse_schedule, to_float

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "数据库" / "2026春季学期本科生课程.db"

SOURCES = [
    (PROJECT_ROOT / "课程数据" / "北大本科公选课_25-26第2学期.json", "公选课"),
    (PROJECT_ROOT / "课程数据" / "北大本科通识课_25-26第2学期.json", "通识课"),
    (PROJECT_ROOT / "课程数据" / "北大本科专业课_25-26第2学期.json", "专业课"),
]

SCHEMA = """
CREATE TABLE basic_info (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    course_type     TEXT NOT NULL,        -- 公选课 / 通识课 / 专业课
    course_code     TEXT NOT NULL,        -- 课程号
    class_no        TEXT NOT NULL,        -- 班号
    course_name     TEXT,                 -- 课程名
    category        TEXT,                 -- 课程类别
    credits         REAL,                 -- 学分
    teacher         TEXT,                 -- 教师
    department      TEXT,                 -- 开课单位
    major           TEXT,                 -- 专业
    grade           TEXT,                 -- 年级
    schedule        TEXT,                 -- 解析后的上课时间
    classroom       TEXT,                 -- 解析后的教室
    schedule_raw    TEXT,                 -- 上课时间及教室（原始）
    weekdays        TEXT,                 -- 周X,周Y
    enrollment      TEXT,                 -- 限数/已选
    pnp             TEXT,                 -- 自选PNP
    notes           TEXT,                 -- 备注
    UNIQUE(course_type, course_code, class_no)
);

CREATE TABLE detail_info (
    course_id       INTEGER PRIMARY KEY REFERENCES basic_info(id) ON DELETE CASCADE,
    english_name    TEXT,                 -- 英文名称
    prerequisites   TEXT,                 -- 先修课程
    intro_cn        TEXT,                 -- 中文简介
    intro_en        TEXT,                 -- 英文简介
    grading         TEXT,                 -- 成绩记载方式
    ge_series       TEXT,                 -- 通识课所属系列
    language        TEXT,                 -- 授课语言
    textbook        TEXT,                 -- 教材
    reference_book  TEXT,                 -- 参考书
    syllabus        TEXT,                 -- 教学大纲
    evaluation      TEXT                  -- 教学评估
);

CREATE INDEX idx_basic_course_type  ON basic_info(course_type);
CREATE INDEX idx_basic_course_code  ON basic_info(course_code);
CREATE INDEX idx_basic_course_name  ON basic_info(course_name);
CREATE INDEX idx_basic_department   ON basic_info(department);
CREATE INDEX idx_basic_category     ON basic_info(category);
CREATE INDEX idx_basic_credits      ON basic_info(credits);

CREATE VIEW courses_view AS
SELECT
    b.id, b.course_type, b.course_code, b.class_no, b.course_name, b.category, b.credits,
    b.teacher, b.department, b.major, b.grade,
    b.schedule, b.classroom, b.schedule_raw, b.weekdays,
    b.enrollment, b.pnp, b.notes,
    d.english_name, d.prerequisites, d.intro_cn, d.intro_en, d.grading,
    d.ge_series, d.language, d.textbook, d.reference_book, d.syllabus, d.evaluation
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

    counts = {}
    total = 0
    for path, course_type in SOURCES:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        n_added = 0
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

            c.execute(
                """INSERT INTO basic_info
                   (course_type, course_code, class_no, course_name, category, credits, teacher,
                    department, major, grade, schedule, classroom, schedule_raw,
                    weekdays, enrollment, pnp, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    course_type, course_code, class_no,
                    bi.get("课程名", ""),
                    bi.get("课程类别", ""),
                    to_float(bi.get("学分", "0")),
                    bi.get("教师", ""),
                    bi.get("开课单位", ""),
                    bi.get("专业", ""),
                    bi.get("年级", ""),
                    schedule, classroom, raw_sched, weekdays,
                    bi.get("限数已选", ""),
                    bi.get("自选PNP", ""),
                    bi.get("备注", ""),
                ),
            )
            course_id = c.lastrowid
            c.execute(
                """INSERT INTO detail_info
                   (course_id, english_name, prerequisites, intro_cn, intro_en, grading,
                    ge_series, language, textbook, reference_book, syllabus, evaluation)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    course_id,
                    di.get("英文名称", ""),
                    di.get("先修课程", ""),
                    di.get("中文简介", ""),
                    di.get("英文简介", ""),
                    di.get("成绩记载方式", ""),
                    di.get("通识课所属系列", ""),
                    di.get("授课语言", ""),
                    di.get("教材", ""),
                    di.get("参考书", ""),
                    di.get("教学大纲", ""),
                    di.get("教学评估", ""),
                ),
            )
            n_added += 1
        counts[course_type] = n_added
        total += n_added

    conn.commit()
    print(f"Database built: {DB_PATH}")
    for k, v in counts.items():
        print(f"  {k:6s}: {v}")
    print(f"  total : {total}")
    conn.close()


if __name__ == "__main__":
    build()
