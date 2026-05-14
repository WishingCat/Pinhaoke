#!/usr/bin/env python3
"""Build SQLite database from course JSON files."""
import json
import re
import sqlite3
import os

DB_PATH = "courses.db"

FILES = [
    ("Course data/北大公选课数据_25-26第2学期.json", "公选课"),
    ("Course data/北大通识课数据_25-26第2学期.json", "通识课"),
    ("Course data/北大专业课数据_25-26第2学期.json", "专业课"),
    ("Graduated_Course_data/pku_graduate_courses.json", "研究生课"),
]

WEEKDAY_ORDER = "一二三四五六日"


def parse_schedule(raw):
    """Parse '上课时间及教室' into (schedule, classroom, weekdays).

    The raw field concatenates slots without delimiters, e.g.:
    '1~15周 每周周一10~11节 二教5111~15周 每周周四10~11节 二教511'
    Room digits (511) merge with next week range (1~15), making regex splitting unreliable.

    Strategy:
    - schedule: store raw text, format for display
    - classroom: extract using PKU building name patterns
    - weekdays: extract all 周X occurrences
    """
    if not raw:
        return "", "", ""

    text = raw.strip()

    # Extract weekdays
    weekdays = set(re.findall(r"周([一二三四五六日])", text))
    weekday_str = ",".join(
        "周" + d for d in sorted(weekdays, key=lambda x: WEEKDAY_ORDER.index(x))
    )

    # Extract classrooms: find room names after 节
    # PKU rooms: Chinese prefix + optional letter + up to 3-digit number
    # Limiting to 3 digits avoids capturing merged digits from next week range
    room_matches = re.findall(
        r"节\s?([\u4e00-\u9fff]{1,6}[A-Za-z]?\d{1,3}[A-Za-z]?)", text
    )
    # Deduplicate: if room A is a prefix of room B, keep only B
    rooms = sorted(set(room_matches))
    filtered = []
    for r in rooms:
        if not any(other != r and other.startswith(r) for other in rooms):
            filtered.append(r)
    classroom = ", ".join(filtered)

    # Build schedule: remove room names from raw text, clean up
    # Replace room patterns with newline to separate slots
    schedule = re.sub(
        r"(节)\s*[\u4e00-\u9fff]{1,6}[A-Za-z]?\d{1,3}[A-Za-z]?",
        r"\1",
        text,
    )
    # Split on boundaries where a new week range starts after 节
    schedule = re.sub(r"(节)(\d{1,2}~\d{1,2}周)", r"\1\n\2", schedule)

    return schedule, classroom, weekday_str


def build_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY,
            course_type TEXT,
            course_code TEXT,
            course_name TEXT,
            category TEXT,
            credits REAL,
            teacher TEXT,
            class_no TEXT,
            department TEXT,
            major TEXT,
            grade TEXT,
            schedule TEXT,
            classroom TEXT,
            schedule_raw TEXT,
            weekdays TEXT,
            enrollment TEXT,
            pnp TEXT,
            notes TEXT
        );

        CREATE TABLE course_details (
            course_id INTEGER PRIMARY KEY REFERENCES courses(id),
            english_name TEXT,
            prerequisites TEXT,
            intro_cn TEXT,
            intro_en TEXT,
            grading TEXT,
            ge_series TEXT,
            language TEXT,
            textbook TEXT,
            reference TEXT,
            syllabus TEXT,
            evaluation TEXT
        );
    """)

    course_id = 0
    for filepath, course_type in FILES:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        is_grad = course_type == "研究生课"

        for item in data:
            course_id += 1
            bi = item["基本信息"]
            di = item["详细信息"]

            raw_schedule = bi.get("上课时间及教室", "")
            schedule, classroom, weekdays = parse_schedule(raw_schedule)

            credits_str = bi.get("学分", "0")
            try:
                credits = float(credits_str)
            except ValueError:
                credits = 0.0

            c.execute(
                """INSERT INTO courses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    course_id,
                    course_type,
                    bi.get("课程号", ""),
                    bi.get("课程名", ""),
                    bi.get("课程类别", ""),
                    credits,
                    bi.get("教师", ""),
                    bi.get("班号", ""),
                    bi.get("开课单位", ""),
                    bi.get("专业", ""),
                    bi.get("年级", ""),
                    schedule,
                    classroom,
                    raw_schedule,
                    weekdays,
                    bi.get("限数已选", ""),
                    bi.get("自选PNP", ""),
                    bi.get("备注", ""),
                ),
            )

            if is_grad:
                # Graduate courses have different detail fields
                c.execute(
                    """INSERT INTO course_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        course_id,
                        di.get("英文名称", ""),
                        "",  # prerequisites
                        di.get("课程简介", ""),  # intro_cn
                        "",  # intro_en
                        "",  # grading
                        "",  # ge_series
                        "",  # language
                        "",  # textbook
                        di.get("参考书", ""),
                        di.get("详情备注", ""),  # syllabus
                        "",  # evaluation
                    ),
                )
            else:
                c.execute(
                    """INSERT INTO course_details VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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

    # Indexes
    c.executescript("""
        CREATE INDEX idx_course_type ON courses(course_type);
        CREATE INDEX idx_category ON courses(category);
        CREATE INDEX idx_department ON courses(department);
        CREATE INDEX idx_credits ON courses(credits);
    """)

    conn.commit()
    conn.close()

    print(f"Database built: {DB_PATH}")
    print(f"Total courses: {course_id}")


if __name__ == "__main__":
    build_db()
