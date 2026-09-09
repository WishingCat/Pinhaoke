from copy import deepcopy
from contextlib import closing, redirect_stdout
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

merge = importlib.import_module("北京大学选课网数据抓取.merge_undergrad_2627_fall")


def course(seq, kind="专业课", **fields):
    return {"课程类型": kind, "课程序号": seq, "数据学期": "26-27第1学期",
            "基本信息": {"课程号": seq, "班号": "1", "教师": "旧教师", "课程类别": "任选", **fields},
            "详细信息": {"中文简介": "旧简介"}}


class FallMergeTests(unittest.TestCase):
    def test_latest_overwrites_blanks_and_teacher_without_moving_old_slots(self):
        baseline = [course("A"), course("B")]
        fresh = course("A", 教师="新教师")
        fresh["详细信息"]["中文简介"] = ""
        merged, audit = merge.merge_rows(baseline, [fresh, course("C")])
        self.assertEqual(merged, [fresh, baseline[1], course("C")])
        self.assertEqual(audit["counts"], {"updated": 1, "retained_old_only": 1, "added": 1})
        self.assertEqual(baseline[0]["基本信息"]["教师"], "旧教师")

    def test_reclassification_reuses_id_and_does_not_keep_obsolete_type(self):
        baseline = [course("A"), course("B")]
        fresh = course("A", "公选课", 课程类别="全校任选")
        merged, audit = merge.merge_rows(baseline, [fresh])
        self.assertEqual(merged, [fresh, baseline[1]])
        self.assertEqual(audit["counts"]["reclassified"], 1)

    def test_other_old_category_keeps_classification_with_new_shared_fields(self):
        baseline = [course("A"), course("A", "劳动教育课", 课程类别="劳动教育")]
        fresh = course("A", 教师="新教师")
        merged, audit = merge.merge_rows(baseline, [fresh])
        self.assertEqual(merged[0], fresh)
        self.assertEqual(merged[1]["课程类型"], "劳动教育课")
        self.assertEqual(merged[1]["基本信息"]["课程类别"], "劳动教育")
        self.assertEqual(merged[1]["基本信息"]["教师"], "新教师")
        self.assertEqual(audit["counts"]["refreshed_other_category"], 1)

    def test_new_cross_category_membership_is_added_without_losing_old_class(self):
        baseline = [course("A", "体育课")]
        fresh = [course("A", "专业课"), course("A", "通识课")]
        merged, _ = merge.merge_rows(baseline, fresh)
        self.assertEqual([r["课程类型"] for r in merged], ["体育课", "专业课", "通识课"])
        self.assertEqual(merged[1:], fresh)

    def test_conflicting_identity_duplicate_or_semester_fail_closed(self):
        baseline = [course("A")]
        bad_identity = course("A", 班号="2")
        bad_term = deepcopy(baseline[0])
        bad_term["数据学期"] = "27-28第1学期"
        for latest in ([bad_identity], [bad_term], [course("A"), course("A")]):
            with self.subTest(latest=latest), self.assertRaises(ValueError):
                merge.merge_rows(baseline, latest)

    def test_ambiguous_reclassification_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            merge.merge_rows([course("A")], [course("A", "通识课"), course("A", "公选课")])


class FallMergePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = {key: self.root / name for key, name in (
            ("baseline", "baseline.json"), ("latest", "latest.json"),
            ("output", "merged.json"), ("database", "courses.db"), ("report", "report.json"),
        )}
        baseline = [course("A", 学分="2", 课程名="课程 A")]
        latest = [course("A", 学分="2", 课程名="课程 A", 教师="新教师"),
                  course("B", 学分="3", 课程名="课程 B")]
        for key, rows in (("baseline", baseline), ("latest", latest), ("output", baseline)):
            self.paths[key].write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self.paths["report"].write_text('{"previous": true}', encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            merge.builder.build(source=self.paths["baseline"], target=self.paths["database"])
        self.before = {key: path.read_bytes() for key, path in self.paths.items()}

    def run_build(self, **overrides):
        with redirect_stdout(io.StringIO()):
            return merge.build(**(self.paths | overrides))

    def assert_unchanged(self, keys=None):
        for key in keys or self.paths:
            self.assertEqual(self.paths[key].read_bytes(), self.before[key], key)
        self.assertEqual(set(self.root.iterdir()), set(self.paths.values()))

    def test_success_publishes_matching_json_database_and_report(self):
        self.paths["database"].chmod(0o640)
        self.paths["output"].chmod(0o644)
        audit = self.run_build()
        rows = json.loads(self.paths["output"].read_text())
        with closing(sqlite3.connect(self.paths["database"])) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(conn.execute("SELECT id, course_code, teacher FROM basic_info ORDER BY id").fetchall(),
                             [(1, "A", "新教师"), (2, "B", "旧教师")])
        self.assertEqual(len(rows), 2)
        self.assertEqual(json.loads(self.paths["report"].read_text()), audit)
        self.assertEqual(audit["sources"]["merged"]["sha256"], hashlib.sha256(self.paths["output"].read_bytes()).hexdigest())
        self.assertEqual(audit["database"]["sha256"], hashlib.sha256(self.paths["database"].read_bytes()).hexdigest())
        self.assertEqual(self.paths["database"].stat().st_mode & 0o777, 0o640)
        self.assert_unchanged(["baseline", "latest"])

    def test_invalid_course_does_not_publish_merged_json(self):
        latest = json.loads(self.paths["latest"].read_text())
        latest[0]["基本信息"]["学分"] = "invalid"
        self.paths["latest"].write_text(json.dumps(latest), encoding="utf-8")
        self.before["latest"] = self.paths["latest"].read_bytes()
        with self.assertRaisesRegex(ValueError, "学分"):
            self.run_build()
        self.assert_unchanged()

    def test_builder_failure_does_not_publish_any_output(self):
        with patch.object(merge.builder, "build", side_effect=OSError("build failed")):
            with self.assertRaisesRegex(OSError, "build failed"):
                self.run_build()
        self.assert_unchanged()

    def test_report_preparation_failure_does_not_publish_any_output(self):
        real_write = merge.write_json_atomic

        def fail_report(path, payload):
            if isinstance(payload, dict) and "database" in payload:
                raise OSError("report failed")
            return real_write(path, payload)

        with patch.object(merge, "write_json_atomic", side_effect=fail_report):
            with self.assertRaisesRegex(OSError, "report failed"):
                self.run_build()
        self.assert_unchanged()

    def test_later_file_failure_restores_all_previous_outputs(self):
        real_replace = os.replace
        for failure_key in ("database", "report"):
            with self.subTest(failure_key=failure_key):
                failed = False

                def fail_once(source, destination):
                    nonlocal failed
                    if Path(destination) == self.paths[failure_key] and not failed:
                        failed = True
                        raise OSError("publication failed")
                    return real_replace(source, destination)

                with patch.object(merge.os, "replace", side_effect=fail_once):
                    with self.assertRaisesRegex(OSError, "publication failed"):
                        self.run_build()
                self.assertTrue(failed)
                self.assert_unchanged()

    def test_directory_sync_failure_restores_previous_outputs(self):
        real_sync = merge.sync_directory
        failed = False

        def fail_once(directory):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("directory sync failed")
            real_sync(directory)

        with patch.object(merge, "sync_directory", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "directory sync failed"):
                self.run_build()
        self.assert_unchanged()

    def test_failed_rollback_retains_old_file_for_manual_recovery(self):
        real_replace = os.replace

        def fail_publication_and_restore(source, destination):
            if Path(destination) == self.paths["database"] or (
                Path(destination) == self.paths["output"] and Path(source).name == "previous"
            ):
                raise OSError("disk unavailable")
            return real_replace(source, destination)

        with patch.object(merge.os, "replace", side_effect=fail_publication_and_restore):
            with self.assertRaisesRegex(merge.PublicationRollbackError, "Staged files retained"):
                self.run_build()
        backups = list(self.root.glob(".merged.json.merge-*/previous"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.before["output"])
        self.assertEqual(self.paths["database"].read_bytes(), self.before["database"])

    def test_failed_initial_publication_removes_new_outputs(self):
        for key in ("output", "database", "report"):
            self.paths[key].unlink()
        real_replace = os.replace

        def fail_database(source, destination):
            if Path(destination) == self.paths["database"]:
                raise OSError("publication failed")
            return real_replace(source, destination)

        with patch.object(merge.os, "replace", side_effect=fail_database):
            with self.assertRaisesRegex(OSError, "publication failed"):
                self.run_build()
        self.assertEqual(set(self.root.iterdir()), {self.paths["baseline"], self.paths["latest"]})

    def test_destination_aliases_fail_before_any_file_changes(self):
        for output_key, target_key in (
            ("report", "baseline"), ("report", "latest"), ("report", "output"), ("report", "database"),
            ("database", "baseline"), ("database", "latest"), ("database", "output"),
            ("output", "baseline"), ("output", "latest"),
        ):
            with self.subTest(output_key=output_key, target_key=target_key):
                with self.assertRaisesRegex(ValueError, "distinct"):
                    self.run_build(**{output_key: self.paths[target_key]})
                self.assert_unchanged()

    def test_symlink_and_hardlink_aliases_cannot_overwrite_source(self):
        alias = self.root / "alias.json"
        for create_alias in (lambda: alias.symlink_to(self.paths["baseline"]),
                             lambda: os.link(self.paths["baseline"], alias)):
            create_alias()
            try:
                with self.assertRaisesRegex(ValueError, "distinct"):
                    self.run_build(report=alias)
            finally:
                alias.unlink()
            self.assert_unchanged()


if __name__ == "__main__":
    unittest.main()
