#!/usr/bin/env python3
"""Build 2026暑期本科生课程.db from the scraped PKU summer JSON."""
import json
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "数据库构建脚本"))
from build_common import parse_schedule, to_float  # noqa: E402

DB_PATH = PROJECT_ROOT / "数据库" / "2026暑期本科生课程.db"
SOURCE = PROJECT_ROOT / "课程数据" / "北大暑期课程_25-26第3学期.json"

SCHEMA = """
CREATE TABLE basic_info (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    course_type     TEXT NOT NULL,
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
    enrollment      TEXT,
    pnp             TEXT,
    notes           TEXT,
    UNIQUE(course_type, course_code, class_no)
);

CREATE TABLE detail_info (
    course_id       INTEGER PRIMARY KEY REFERENCES basic_info(id) ON DELETE CASCADE,
    english_name    TEXT,
    prerequisites   TEXT,
    intro_cn        TEXT,
    intro_en        TEXT,
    grading         TEXT,
    ge_series       TEXT,
    language        TEXT,
    textbook        TEXT,
    reference_book  TEXT,
    syllabus        TEXT,
    evaluation      TEXT
);

CREATE TABLE translations (
    course_id INTEGER NOT NULL,
    field     TEXT NOT NULL,
    lang      TEXT NOT NULL,
    text      TEXT NOT NULL,
    PRIMARY KEY (course_id, field, lang)
);

CREATE INDEX idx_basic_course_type  ON basic_info(course_type);
CREATE INDEX idx_basic_course_code  ON basic_info(course_code);
CREATE INDEX idx_basic_course_name  ON basic_info(course_name);
CREATE INDEX idx_basic_department   ON basic_info(department);
CREATE INDEX idx_basic_category     ON basic_info(category);
CREATE INDEX idx_basic_credits      ON basic_info(credits);
CREATE INDEX idx_trans_cid_field    ON translations(course_id, field);

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
    with SOURCE.open(encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    c = conn.cursor()
    c.executescript(SCHEMA)

    counts = {}
    seen = set()
    for item in data:
        bi = item.get("基本信息", {}) or {}
        di = item.get("详细信息", {}) or {}
        course_type = item.get("课程类型", "")
        course_code = bi.get("课程号", "").strip()
        class_no = bi.get("班号", "").strip()
        if not course_type or not course_code:
            continue
        key = (course_type, course_code, class_no)
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
        counts[course_type] = counts.get(course_type, 0) + 1

    conn.commit()
    print(f"Database built: {DB_PATH}")
    for k in sorted(counts):
        print(f"  {k:12s}: {counts[k]}")
    print(f"  total       : {sum(counts.values())}")
    conn.close()


if __name__ == "__main__":
    build()
