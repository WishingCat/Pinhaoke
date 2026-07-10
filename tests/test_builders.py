import copy
import importlib
import json
import os
import runpy
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from 数据库构建脚本.build_atomic import atomic_database, validate_built_database


PROJECT_ROOT = Path(__file__).resolve().parent.parent

BUILDER_SPECS = (
    (
        "spring_undergrad",
        "数据库构建脚本.build_undergrad_db",
        PROJECT_ROOT / "数据库构建脚本" / "build_undergrad_db.py",
        "undergrad",
    ),
    (
        "spring_graduate",
        "数据库构建脚本.build_graduate_db",
        PROJECT_ROOT / "数据库构建脚本" / "build_graduate_db.py",
        "graduate",
    ),
    (
        "summer_undergrad",
        "北京大学选课网数据抓取.build_summer_db",
        PROJECT_ROOT / "北京大学选课网数据抓取" / "build_summer_db.py",
        "undergrad",
    ),
    (
        "fall_undergrad",
        "北京大学选课网数据抓取.build_undergrad_2627_fall_db",
        PROJECT_ROOT
        / "北京大学选课网数据抓取"
        / "build_undergrad_2627_fall_db.py",
        "undergrad",
    ),
    (
        "fall_graduate",
        "北京大学选课网数据抓取.build_graduate_2627_fall_db",
        PROJECT_ROOT
        / "北京大学选课网数据抓取"
        / "build_graduate_2627_fall_db.py",
        "graduate",
    ),
)


def undergrad_row():
    return {
        "课程类型": "专业课",
        "基本信息": {
            "课程号": "TEST-U-001",
            "班号": "01",
            "课程名": "测试本科课程",
            "课程类别": "任选",
            "学分": "2.5",
            "教师": "测试教师",
            "开课单位": "测试学院",
            "专业": "测试专业",
            "年级": "2026",
            "上课时间及教室": "1~2周 每周周一1~2节 一教101",
            "限数已选": "10/1",
            "自选PNP": "是",
            "备注": "测试备注",
        },
        "详细信息": {
            "英文名称": "Test Undergraduate Course",
            "先修课程": "无",
            "中文简介": "课程简介",
            "英文简介": "Introduction",
            "成绩记载方式": "百分制",
            "通识课所属系列": "",
            "授课语言": "汉语",
            "教材": "测试教材",
            "参考书": "测试参考书",
            "教学大纲": "测试大纲",
            "教学评估": "闭卷考试",
        },
    }


def graduate_row():
    return {
        "基本信息": {
            "课程号": "TEST-G-001",
            "班号": "00",
            "课程名": "测试研究生课程",
            "课程类别": "选修",
            "学分": "3",
            "教师": "研究生教师",
            "开课单位": "研究生院系",
            "专业": "测试专业",
            "年级": "2026",
            "上课时间及教室": "1~2周 每周周二3~4节 二教202",
            "限数已选": "20/2",
            "备注": "博士",
        },
        "详细信息": {
            "英文名称": "Test Graduate Course",
            "周学时": "2",
            "总学时": "32",
            "开课学期": "秋季",
            "修读对象": "硕士生",
            "参考书": "研究生参考书",
            "课程简介": "研究生课程简介",
            "详情备注": "详情",
            "大纲": "研究生大纲",
        },
    }


def atomic_schema(*, translations_pk=True, include_view=True):
    if translations_pk:
        primary_key = "PRIMARY KEY(course_id, field, lang)"
    else:
        primary_key = "PRIMARY KEY(field, course_id, lang)"
    view = (
        "CREATE VIEW courses_view AS SELECT b.id FROM basic_info b "
        "LEFT JOIN detail_info d ON d.course_id = b.id;"
        if include_view
        else ""
    )
    return f"""
        CREATE TABLE basic_info(id INTEGER PRIMARY KEY);
        CREATE TABLE detail_info(
            course_id INTEGER PRIMARY KEY REFERENCES basic_info(id)
        );
        CREATE TABLE translations(
            course_id INTEGER NOT NULL,
            field TEXT NOT NULL,
            lang TEXT NOT NULL,
            text TEXT NOT NULL,
            {primary_key}
        );
        CREATE INDEX idx_trans_cid_field ON translations(course_id, field);
        {view}
    """


class BuilderTestCase(unittest.TestCase):
    def setUp(self):
        self.modules = {
            name: importlib.import_module(module_name)
            for name, module_name, _script_path, _kind in BUILDER_SPECS
        }

    def write_json(self, directory, name, payload):
        path = Path(directory) / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def build(self, name, source, target):
        module = self.modules[name]
        if name == "spring_undergrad":
            return module.build(sources=[(source, "公选课")], target=target)
        return module.build(source=source, target=target)

    def fixture(self, kind):
        return undergrad_row() if kind == "undergrad" else graduate_row()


class AtomicDatabaseTests(unittest.TestCase):
    def assert_only_target_remains(self, directory, target):
        self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_caller_failure_keeps_existing_bytes_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "courses.db"
            target.write_bytes(b"official")

            with self.assertRaisesRegex(RuntimeError, "stop"):
                with atomic_database(target, atomic_schema()) as conn:
                    conn.execute("INSERT INTO basic_info VALUES (1)")
                    conn.execute("INSERT INTO detail_info VALUES (1)")
                    raise RuntimeError("stop")

            self.assertEqual(target.read_bytes(), b"official")
            self.assert_only_target_remains(tmp, target)

    def test_schema_failure_keeps_existing_bytes_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "courses.db"
            target.write_bytes(b"official")

            with self.assertRaises(sqlite3.Error):
                with atomic_database(target, "CREATE TABL broken"):
                    pass

            self.assertEqual(target.read_bytes(), b"official")
            self.assert_only_target_remains(tmp, target)

    def test_validation_is_required_and_rechecked_before_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "courses.db"
            target.write_bytes(b"official")

            with self.assertRaisesRegex(RuntimeError, "validate_built_database"):
                with atomic_database(target, atomic_schema()) as conn:
                    conn.execute("INSERT INTO basic_info VALUES (1)")
                    conn.execute("INSERT INTO detail_info VALUES (1)")

            self.assertEqual(target.read_bytes(), b"official")
            self.assert_only_target_remains(tmp, target)

            with self.assertRaisesRegex(ValueError, "row count"):
                with atomic_database(target, atomic_schema()) as conn:
                    conn.execute("INSERT INTO basic_info VALUES (1)")
                    conn.execute("INSERT INTO detail_info VALUES (1)")
                    validate_built_database(conn, expected_rows=1)
                    conn.execute("DELETE FROM detail_info")

            self.assertEqual(target.read_bytes(), b"official")
            self.assert_only_target_remains(tmp, target)

    def test_success_uses_unique_temp_in_target_directory_and_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "courses.db"
            target.write_bytes(b"official")
            temp_paths = []

            for course_id in (1, 2):
                with atomic_database(target, atomic_schema()) as conn:
                    temp_path = Path(
                        conn.execute("PRAGMA database_list").fetchone()[2]
                    )
                    temp_paths.append(temp_path)
                    self.assertEqual(temp_path.parent.resolve(), target.parent.resolve())
                    self.assertNotEqual(temp_path, target)
                    conn.execute("INSERT INTO basic_info VALUES (?)", (course_id,))
                    conn.execute("INSERT INTO detail_info VALUES (?)", (course_id,))
                    validate_built_database(conn, expected_rows=1)

            self.assertEqual(len(set(temp_paths)), 2)
            self.assertTrue(all(not path.exists() for path in temp_paths))
            with closing(sqlite3.connect(target)) as conn:
                self.assertEqual(
                    conn.execute("SELECT id FROM basic_info").fetchone()[0], 2
                )
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])

    def test_replacement_failure_keeps_existing_bytes_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "courses.db"
            target.write_bytes(b"official")

            with mock.patch(
                "数据库构建脚本.build_atomic.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    with atomic_database(target, atomic_schema()) as conn:
                        conn.execute("INSERT INTO basic_info VALUES (1)")
                        conn.execute("INSERT INTO detail_info VALUES (1)")
                        validate_built_database(conn, expected_rows=1)

            self.assertEqual(target.read_bytes(), b"official")
            self.assert_only_target_remains(tmp, target)


class DatabaseValidationTests(unittest.TestCase):
    def test_validation_checks_required_objects_translation_pk_and_counts(self):
        cases = (
            (atomic_schema(include_view=False), 0, "courses_view"),
            (atomic_schema(translations_pk=False), 0, "translations primary key"),
            (atomic_schema(), 1, "row count"),
        )
        for schema, expected_rows, message in cases:
            with self.subTest(message=message):
                conn = sqlite3.connect(":memory:")
                try:
                    conn.execute("PRAGMA foreign_keys = ON")
                    conn.executescript(schema)
                    with self.assertRaisesRegex(ValueError, message):
                        validate_built_database(conn, expected_rows=expected_rows)
                finally:
                    conn.close()

    def test_validation_checks_foreign_keys(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(atomic_schema())
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("INSERT INTO detail_info VALUES (99)")
            with self.assertRaisesRegex(ValueError, "foreign key"):
                validate_built_database(conn, expected_rows=0)
        finally:
            conn.close()


class SchemaContractTests(BuilderTestCase):
    def test_all_five_schemas_share_required_objects_and_translation_contract(self):
        for name, module in self.modules.items():
            with self.subTest(builder=name):
                conn = sqlite3.connect(":memory:")
                try:
                    conn.executescript(module.SCHEMA)
                    objects = {
                        (row[0], row[1])
                        for row in conn.execute(
                            "SELECT name, type FROM sqlite_master"
                        )
                    }
                    for table in ("basic_info", "detail_info", "translations"):
                        self.assertIn((table, "table"), objects)
                    self.assertIn(("courses_view", "view"), objects)
                    pk = [
                        row[1]
                        for row in sorted(
                            (
                                row
                                for row in conn.execute(
                                    "PRAGMA table_info(translations)"
                                )
                                if row[5]
                            ),
                            key=lambda row: row[5],
                        )
                    ]
                    self.assertEqual(pk, ["course_id", "field", "lang"])
                    indexes = {
                        row[1]
                        for row in conn.execute("PRAGMA index_list(translations)")
                    }
                    self.assertIn("idx_trans_cid_field", indexes)
                finally:
                    conn.close()

    def test_all_builders_import_as_modules_and_script_files(self):
        for name, _module_name, script_path, _kind in BUILDER_SPECS:
            with self.subTest(builder=name):
                namespace = runpy.run_path(
                    str(script_path), run_name=f"builder_import_{name}"
                )
                self.assertIn("build", namespace)


class BuilderEndToEndTests(BuilderTestCase):
    def test_all_five_builders_build_tiny_fixture_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, _module_name, _script_path, kind in BUILDER_SPECS:
                with self.subTest(builder=name):
                    row = self.fixture(kind)
                    source = self.write_json(tmp, f"{name}.json", [row])
                    target = Path(tmp) / f"{name}.db"

                    self.build(name, source, target)

                    with closing(sqlite3.connect(target)) as conn:
                        self.assertEqual(
                            conn.execute("SELECT COUNT(*) FROM basic_info").fetchone()[0],
                            1,
                        )
                        self.assertEqual(
                            conn.execute("SELECT COUNT(*) FROM detail_info").fetchone()[0],
                            1,
                        )
                        self.assertEqual(
                            conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0],
                            0,
                        )
                        self.assertEqual(
                            conn.execute("SELECT credits FROM basic_info").fetchone()[0],
                            2.5 if kind == "undergrad" else 3.0,
                        )
                        self.assertEqual(
                            conn.execute("PRAGMA foreign_key_check").fetchall(), []
                        )
                        self.assertEqual(
                            conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)]
                        )

    def test_missing_and_null_detail_are_deliberately_stored_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            for detail_value in (None, "missing"):
                with self.subTest(detail=detail_value):
                    row = undergrad_row()
                    if detail_value == "missing":
                        del row["详细信息"]
                    else:
                        row["详细信息"] = None
                    source = self.write_json(tmp, f"detail-{detail_value}.json", [row])
                    target = Path(tmp) / f"detail-{detail_value}.db"

                    self.build("summer_undergrad", source, target)

                    with closing(sqlite3.connect(target)) as conn:
                        self.assertEqual(
                            conn.execute(
                                "SELECT english_name, intro_cn FROM detail_info"
                            ).fetchone(),
                            ("", ""),
                        )

    def test_exact_duplicate_unique_keys_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, _module_name, _script_path, kind in BUILDER_SPECS:
                with self.subTest(builder=name):
                    row = self.fixture(kind)
                    source = self.write_json(
                        tmp, f"duplicate-{name}.json", [row, copy.deepcopy(row)]
                    )
                    target = Path(tmp) / f"duplicate-{name}.db"

                    self.build(name, source, target)

                    with closing(sqlite3.connect(target)) as conn:
                        self.assertEqual(
                            conn.execute("SELECT COUNT(*) FROM basic_info").fetchone()[0],
                            1,
                        )
                        self.assertEqual(
                            conn.execute("SELECT COUNT(*) FROM detail_info").fetchone()[0],
                            1,
                        )

    def test_conflicting_duplicate_unique_keys_fail_with_context_and_keep_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, _module_name, _script_path, kind in BUILDER_SPECS:
                with self.subTest(builder=name):
                    first = self.fixture(kind)
                    conflict = copy.deepcopy(first)
                    conflict["基本信息"]["课程名"] = "冲突课程名"
                    source = self.write_json(
                        tmp, f"conflict-{name}.json", [first, conflict]
                    )
                    target = Path(tmp) / f"conflict-{name}.db"
                    target.write_bytes(b"sentinel")

                    with self.assertRaisesRegex(
                        ValueError,
                        rf"{name}.*row 1.*conflicting duplicate.*key",
                    ):
                        self.build(name, source, target)

                    self.assertEqual(target.read_bytes(), b"sentinel")
                    self.assertEqual(
                        list(Path(tmp).glob(f".{target.name}.*.tmp")), []
                    )


class BuilderInputValidationTests(BuilderTestCase):
    def assert_rejected_before_database_open(self, payload, message):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.write_json(tmp, "invalid.json", payload)
            target = Path(tmp) / "sentinel.db"
            target.write_bytes(b"sentinel")
            module = self.modules["spring_graduate"]

            with mock.patch.object(module, "atomic_database") as atomic:
                with self.assertRaisesRegex(ValueError, message):
                    module.build(source=source, target=target)

            atomic.assert_not_called()
            self.assertEqual(target.read_bytes(), b"sentinel")

    def test_top_level_json_must_be_a_list(self):
        self.assert_rejected_before_database_open({}, r"invalid\.json.*top-level.*list")

    def test_rows_and_basic_detail_objects_must_be_mappings(self):
        cases = (
            (["bad"], r"invalid\.json.*row 0.*mapping"),
            ([{"基本信息": [], "详细信息": {}}], r"row 0.*基本信息.*mapping"),
            (
                [{"基本信息": graduate_row()["基本信息"], "详细信息": []}],
                r"row 0.*详细信息.*mapping",
            ),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                self.assert_rejected_before_database_open(payload, message)

    def test_missing_course_code_and_invalid_credit_have_source_context(self):
        cases = []
        missing_code = graduate_row()
        missing_code["基本信息"]["课程号"] = "  "
        cases.append(([missing_code], r"invalid\.json.*row 0.*课程号.*blank"))
        invalid_credit = graduate_row()
        invalid_credit["基本信息"]["学分"] = "two"
        cases.append(([invalid_credit], r"invalid\.json.*row 0.*学分.*two"))

        for payload, message in cases:
            with self.subTest(message=message):
                self.assert_rejected_before_database_open(payload, message)

    def test_all_builders_reject_invalid_credit_before_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, _module_name, _script_path, kind in BUILDER_SPECS:
                with self.subTest(builder=name):
                    row = self.fixture(kind)
                    row["基本信息"]["学分"] = "not-a-credit"
                    source = self.write_json(tmp, f"invalid-{name}.json", [row])
                    target = Path(tmp) / f"invalid-{name}.db"
                    target.write_bytes(b"sentinel")

                    with self.assertRaisesRegex(
                        ValueError, rf"invalid-{name}\.json.*row 0.*学分"
                    ):
                        self.build(name, source, target)

                    self.assertEqual(target.read_bytes(), b"sentinel")

    def test_required_course_type_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = undergrad_row()
            row["课程类型"] = " "
            source = self.write_json(tmp, "missing-type.json", [row])
            target = Path(tmp) / "missing-type.db"
            target.write_bytes(b"sentinel")

            with self.assertRaisesRegex(ValueError, r"row 0.*课程类型.*blank"):
                self.build("summer_undergrad", source, target)

            self.assertEqual(target.read_bytes(), b"sentinel")


if __name__ == "__main__":
    unittest.main()
