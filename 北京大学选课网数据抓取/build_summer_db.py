#!/usr/bin/env python3
"""Build 2026暑期本科生课程.db from the scraped PKU summer JSON."""
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "数据库构建脚本"))
from build_atomic import (  # noqa: E402
    atomic_database,
    deduplicate_rows,
    load_course_rows,
    optional_text,
    required_text,
    strict_credit,
    validate_built_database,
)
from build_common import parse_first_period, parse_schedule  # noqa: E402

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
    first_period    INTEGER,
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
CREATE INDEX idx_basic_first_period ON basic_info(first_period);
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


def _prepare_rows(source):
    candidates = []
    for row in load_course_rows(source):
        bi = row.basic
        di = row.detail
        course_type = required_text(row.item, "课程类型", row.context)
        course_code = required_text(bi, "课程号", row.context)
        class_no = optional_text(bi, "班号", row.context, strip=True) or ""
        raw_sched = optional_text(bi, "上课时间及教室", row.context)
        schedule_input = raw_sched or ""
        schedule, classroom, weekdays = parse_schedule(schedule_input)
        first_period = parse_first_period(schedule_input)

        basic_values = (
            course_type,
            course_code,
            class_no,
            optional_text(bi, "课程名", row.context),
            optional_text(bi, "课程类别", row.context),
            strict_credit(bi, row.context),
            optional_text(bi, "教师", row.context),
            optional_text(bi, "开课单位", row.context),
            optional_text(bi, "专业", row.context),
            optional_text(bi, "年级", row.context),
            schedule,
            classroom,
            raw_sched,
            weekdays,
            first_period,
            optional_text(bi, "限数已选", row.context),
            optional_text(bi, "自选PNP", row.context),
            optional_text(bi, "备注", row.context),
        )
        detail_values = (
            optional_text(di, "英文名称", row.context),
            optional_text(di, "先修课程", row.context),
            optional_text(di, "中文简介", row.context),
            optional_text(di, "英文简介", row.context),
            optional_text(di, "成绩记载方式", row.context),
            optional_text(di, "通识课所属系列", row.context),
            optional_text(di, "授课语言", row.context),
            optional_text(di, "教材", row.context),
            optional_text(di, "参考书", row.context),
            optional_text(di, "教学大纲", row.context),
            optional_text(di, "教学评估", row.context),
        )
        key = (course_type, course_code, class_no)
        candidates.append(
            (key, (basic_values, detail_values), (basic_values, detail_values), row.context)
        )
    return deduplicate_rows(candidates)


def build(source=SOURCE, target=DB_PATH):
    prepared_rows, duplicate_rows = _prepare_rows(source)
    target = Path(target)
    counts = {}

    with atomic_database(target, SCHEMA) as conn:
        cursor = conn.cursor()
        for basic_values, detail_values in prepared_rows:
            cursor.execute(
                """INSERT INTO basic_info
                   (course_type, course_code, class_no, course_name, category, credits, teacher,
                    department, major, grade, schedule, classroom, schedule_raw,
                    weekdays, first_period, enrollment, pnp, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                basic_values,
            )
            course_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO detail_info
                   (course_id, english_name, prerequisites, intro_cn, intro_en, grading,
                    ge_series, language, textbook, reference_book, syllabus, evaluation)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (course_id, *detail_values),
            )
            course_type = basic_values[0]
            counts[course_type] = counts.get(course_type, 0) + 1
        validate_built_database(conn, expected_rows=len(prepared_rows))

    print(f"Database built: {target}")
    for k in sorted(counts):
        print(f"  {k:12s}: {counts[k]}")
    print(f"  total       : {len(prepared_rows)}")
    print(f"  skipped dup : {duplicate_rows}")


if __name__ == "__main__":
    build()
