import contextlib
import importlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
TRANSLATION_DIR = ROOT / "北京大学课程数据翻译"
PACKAGE = "北京大学课程数据翻译"
SCRIPTS = ("translate_courses", "translate_misc", "translate_stubborn")


def import_script(name):
    return importlib.import_module(f"{PACKAGE}.{name}")


def make_translation_db(path, *, ug_shape=True):
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            conn.execute(
                "CREATE TABLE basic_info "
                "(id INTEGER PRIMARY KEY, course_name TEXT, notes TEXT, pnp TEXT, "
                "classroom TEXT, major TEXT)"
            )
            detail_columns = (
                "course_id INTEGER PRIMARY KEY, intro_cn TEXT, intro_en TEXT, "
                "english_name TEXT, prerequisites TEXT, ge_series TEXT, textbook TEXT, "
                "syllabus TEXT, evaluation TEXT, reference_book TEXT"
                if ug_shape
                else
                "course_id INTEGER PRIMARY KEY, intro TEXT, extra_notes TEXT, "
                "english_name TEXT, audience TEXT, term TEXT, reference_book TEXT, "
                "syllabus TEXT"
            )
            conn.execute(f"CREATE TABLE detail_info ({detail_columns})")


class ImportAndCliTests(unittest.TestCase):
    def test_help_works_without_api_key_in_fresh_processes(self):
        env = dict(os.environ)
        env.pop("DEEPSEEK_API_KEY", None)
        for script in SCRIPTS:
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, str(TRANSLATION_DIR / f"{script}.py"), "--help"],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())

    def test_modules_import_without_api_key_or_certifi(self):
        code = f"""
import builtins
import importlib
import os
import sys
os.environ.pop('DEEPSEEK_API_KEY', None)
real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'certifi':
        raise ImportError('certifi intentionally unavailable')
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked
sys.path.insert(0, {str(ROOT)!r})
for name in {SCRIPTS!r}:
    importlib.import_module({PACKAGE!r} + '.' + name)
print('ok')
"""
        env = dict(os.environ)
        env.pop("DEEPSEEK_API_KEY", None)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_numeric_arguments_are_validated_without_api_key(self):
        env = dict(os.environ)
        env.pop("DEEPSEEK_API_KEY", None)
        for script, args in (
            ("translate_courses", ["--workers", "0"]),
            ("translate_misc", ["--limit", "-1"]),
            ("translate_stubborn", ["--workers", "-2"]),
        ):
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, str(TRANSLATION_DIR / f"{script}.py"), *args],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("error", result.stderr.lower())


class CommonHelperTests(unittest.TestCase):
    def test_get_api_key_is_lazy_and_clear(self):
        common = importlib.import_module(f"{PACKAGE}.translation_common")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                common.get_api_key()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "  secret  "}, clear=True):
            self.assertEqual(common.get_api_key(), "secret")

    def test_clean_translation_rejects_blank_and_non_string_values(self):
        common = importlib.import_module(f"{PACKAGE}.translation_common")
        for value in (None, 7, [], {}, "", "  \n\t"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    common.clean_translation(value)
        self.assertEqual(common.clean_translation("  translated text \n"), "translated text")

    def test_write_retries_only_locked_attempts_and_closes_every_connection(self):
        common = importlib.import_module(f"{PACKAGE}.translation_common")

        class FakeConnection:
            def __init__(self, error=None):
                self.error = error
                self.closed = False
                self.executions = 0

            def execute(self, _sql, _params):
                self.executions += 1
                if self.error:
                    raise self.error

            def commit(self):
                return None

            def rollback(self):
                return None

            def close(self):
                self.closed = True

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        connections = [
            FakeConnection(sqlite3.OperationalError("database is locked")),
            FakeConnection(),
        ]
        with patch.object(common.sqlite3, "connect", side_effect=connections) as connect:
            with patch.object(common.time, "sleep") as sleep:
                common.write_translation_with_retry(
                    "fixture.db", 4, "intro_cn", "en", "  done  ",
                    attempts=2, base_delay=0.01,
                )
        self.assertEqual(connect.call_count, 2)
        sleep.assert_called_once_with(0.01)
        self.assertTrue(all(conn.closed for conn in connections))

    def test_write_raises_permanent_operational_error_without_retry(self):
        common = importlib.import_module(f"{PACKAGE}.translation_common")
        conn = Mock()
        conn.execute.side_effect = sqlite3.OperationalError("no such table: translations")
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        with patch.object(common.sqlite3, "connect", return_value=conn) as connect:
            with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
                common.write_translation_with_retry(
                    "fixture.db", 4, "intro_cn", "en", "done", attempts=5,
                )
        connect.assert_called_once()
        conn.close.assert_called_once()


class SelectionScopeTests(unittest.TestCase):
    def test_courses_only_fall_gr_intro_touches_only_fall_gr(self):
        module = import_script("translate_courses")
        target = module.DATABASES["fall_gr"]
        with patch.object(module, "setup_db") as setup:
            with patch.object(module, "fetch_pending_grad", return_value=[]) as fetch_grad:
                with patch.object(module, "fetch_pending_undergrad") as fetch_ug:
                    with contextlib.redirect_stdout(io.StringIO()):
                        status = module.main(["--only", "fall_gr_intro"])
        self.assertEqual(status, 0)
        setup.assert_called_once_with(target)
        fetch_ug.assert_not_called()
        fetch_grad.assert_called_once()
        self.assertIn(target, fetch_grad.call_args.args)

    def test_misc_fall_gr_selection_isolates_setup_reuse_and_fetch(self):
        module = import_script("translate_misc")
        target = module.DATABASES["fall_gr"]
        with patch.object(module, "setup_db") as setup:
            with patch.object(module, "reuse_english_for_course_names") as reuse:
                with patch.object(module, "fetch_jobs", return_value=[]) as fetch:
                    with contextlib.redirect_stdout(io.StringIO()):
                        status = module.main(["--db", "fall_gr", "--phase", "all"])
        self.assertEqual(status, 0)
        setup.assert_called_once_with(target)
        reuse.assert_called_once_with([target], limit=0)
        selected_jobs = fetch.call_args.args[0]
        self.assertTrue(selected_jobs)
        self.assertEqual({job[0] for job in selected_jobs}, {target})

    def test_misc_long_phase_does_not_reuse_short_course_name_field(self):
        module = import_script("translate_misc")
        target = module.DATABASES["ug"]
        with patch.object(module, "setup_db"):
            with patch.object(module, "reuse_english_for_course_names") as reuse:
                with patch.object(module, "fetch_jobs", return_value=[]) as fetch:
                    with contextlib.redirect_stdout(io.StringIO()):
                        status = module.main(["--db", "ug", "--phase", "long"])
        self.assertEqual(status, 0)
        reuse.assert_not_called()
        self.assertTrue(fetch.call_args.args[0])
        self.assertEqual({job[0] for job in fetch.call_args.args[0]}, {target})
        self.assertTrue(all(job[5] for job in fetch.call_args.args[0]))

    def test_misc_limit_is_shared_by_reuse_writes_and_api_tasks(self):
        module = import_script("translate_misc")
        target = module.DATABASES["ug"]
        pending = [(target, "notes", "note", 9, "备注", ["en"])]
        with patch.object(module, "setup_db"):
            with patch.object(
                module, "reuse_english_for_course_names", return_value=1
            ) as reuse:
                with patch.object(module, "fetch_jobs", return_value=pending):
                    with patch.object(module, "call_api") as call_api:
                        with contextlib.redirect_stdout(io.StringIO()):
                            status = module.main(
                                ["--db", "ug", "--phase", "short", "--limit", "1"]
                            )
        self.assertEqual(status, 0)
        reuse.assert_called_once_with([target], limit=1)
        call_api.assert_not_called()

    def test_stubborn_db_and_field_selection_happens_before_database_access(self):
        module = import_script("translate_stubborn")
        target = module.DATABASES["ug"]
        with patch.object(module, "setup_db") as setup:
            with patch.object(module, "process", return_value=(0, 0)) as process:
                with contextlib.redirect_stdout(io.StringIO()):
                    status = module.main(["--db", "ug", "--field", "syllabus"])
        self.assertEqual(status, 0)
        setup.assert_called_once_with(target)
        process.assert_called_once()
        self.assertEqual(process.call_args.args[:4], (target, "syllabus", "syllabus", "UG syllabus"))

    def test_fixture_reuse_only_opens_selected_database(self):
        common = importlib.import_module(f"{PACKAGE}.translation_common")
        module = import_script("translate_misc")
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "selected.db"
            untouched = Path(tmp) / "untouched.db"
            for path in (selected, untouched):
                make_translation_db(path)
                common.setup_translation_db(path)
                with closing(sqlite3.connect(path)) as conn:
                    with conn:
                        conn.execute("INSERT INTO basic_info(id, course_name) VALUES (1, '课程')")
                        conn.execute(
                            "INSERT INTO detail_info(course_id, english_name) VALUES (1, ' Course ')"
                        )
            module.reuse_english_for_course_names([selected])
            with closing(sqlite3.connect(selected)) as conn:
                selected_rows = conn.execute("SELECT text FROM translations").fetchall()
            with closing(sqlite3.connect(untouched)) as conn:
                untouched_rows = conn.execute("SELECT text FROM translations").fetchall()
        self.assertEqual(selected_rows, [("Course",)])
        self.assertEqual(untouched_rows, [])

    def test_fixture_reuse_honors_global_limit(self):
        common = importlib.import_module(f"{PACKAGE}.translation_common")
        module = import_script("translate_misc")
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "selected.db"
            make_translation_db(selected)
            common.setup_translation_db(selected)
            with closing(sqlite3.connect(selected)) as conn:
                with conn:
                    conn.executemany(
                        "INSERT INTO basic_info(id, course_name) VALUES (?, ?)",
                        [(1, "课程一"), (2, "课程二"), (3, "课程三")],
                    )
                    conn.executemany(
                        "INSERT INTO detail_info(course_id, english_name) VALUES (?, ?)",
                        [(1, "One"), (2, "Two"), (3, "Three")],
                    )
            written = module.reuse_english_for_course_names([selected], limit=2)
            with closing(sqlite3.connect(selected)) as conn:
                rows = conn.execute(
                    "SELECT course_id, text FROM translations ORDER BY course_id"
                ).fetchall()
        self.assertEqual(written, 2)
        self.assertEqual(rows, [(1, "One"), (2, "Two")])


class ApiAndFailureStatusTests(unittest.TestCase):
    def test_api_success_followed_by_transient_write_lock_calls_api_once(self):
        module = import_script("translate_courses")
        common = importlib.import_module(f"{PACKAGE}.translation_common")

        class FakeConnection:
            def __init__(self, locked):
                self.locked = locked
                self.closed = False

            def execute(self, _sql, _params):
                if self.locked:
                    raise sqlite3.OperationalError("database is busy")

            def commit(self):
                return None

            def rollback(self):
                return None

            def close(self):
                self.closed = True

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        api = Mock(return_value=({"en": "  translated  "}, {"prompt_tokens": 1}))
        connections = [FakeConnection(True), FakeConnection(False)]
        with patch.object(module, "call_api", api):
            with patch.object(common.sqlite3, "connect", side_effect=connections):
                with patch.object(common.time, "sleep"):
                    result = module.translate_one((1, "原文", ["en"]), "fixture.db", "intro_cn")
        self.assertTrue(result[1], result)
        api.assert_called_once()
        self.assertTrue(all(conn.closed for conn in connections))

    def test_courses_main_returns_one_for_unexpected_worker_failure(self):
        module = import_script("translate_courses")
        with patch.object(module, "setup_db"):
            with patch.object(
                module, "fetch_pending_undergrad", return_value=[(1, "原文", ["en"])]
            ):
                with patch.object(module, "translate_one", side_effect=RuntimeError("worker died")):
                    with contextlib.redirect_stdout(io.StringIO()):
                        status = module.main(["--only", "ug_intro", "--workers", "1"])
        self.assertEqual(status, 1)

    def test_courses_translate_one_reports_permanent_write_failure(self):
        module = import_script("translate_courses")
        api = Mock(return_value=({"en": "translation"}, {}))
        with patch.object(module, "call_api", api):
            with patch.object(
                module,
                "write_translation_with_retry",
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ):
                result = module.translate_one((1, "原文", ["en"]), "fixture.db", "intro_cn")
        self.assertFalse(result[1])
        api.assert_called_once()

    def test_misc_main_returns_one_for_api_failure(self):
        module = import_script("translate_misc")
        pending = [(module.DATABASES["ug"], "course_name", "title", 1, "课程", ["en"])]
        with patch.object(module, "setup_db"):
            with patch.object(module, "reuse_english_for_course_names"):
                with patch.object(module, "fetch_jobs", return_value=pending):
                    with patch.object(module, "call_api", side_effect=RuntimeError("api failed")):
                        with contextlib.redirect_stdout(io.StringIO()):
                            status = module.main(
                                ["--db", "ug", "--phase", "short", "--workers", "1"]
                            )
        self.assertEqual(status, 1)

    def test_misc_main_returns_one_for_write_failure(self):
        module = import_script("translate_misc")
        pending = [(module.DATABASES["ug"], "course_name", "title", 1, "课程", ["en"])]
        with patch.object(module, "setup_db"):
            with patch.object(module, "reuse_english_for_course_names"):
                with patch.object(module, "fetch_jobs", return_value=pending):
                    with patch.object(module, "call_api", return_value={"en": "translated"}):
                        with patch.object(
                            module, "write_translation_with_retry", side_effect=OSError("full")
                        ):
                            with contextlib.redirect_stdout(io.StringIO()):
                                status = module.main(
                                    ["--db", "ug", "--phase", "short", "--workers", "1"]
                                )
        self.assertEqual(status, 1)

    def test_stubborn_main_returns_one_when_any_task_fails(self):
        module = import_script("translate_stubborn")
        with patch.object(module, "setup_db"):
            with patch.object(module, "process", return_value=(3, 1)):
                with contextlib.redirect_stdout(io.StringIO()):
                    status = module.main(["--db", "ug", "--field", "intro"])
        self.assertEqual(status, 1)

    def test_stubborn_process_counts_unexpected_future_failure(self):
        module = import_script("translate_stubborn")
        with patch.object(module, "gather_missing", return_value=[(1, "原文", ["en"])]):
            with patch.object(module, "translate_one", side_effect=RuntimeError("worker died")):
                with contextlib.redirect_stdout(io.StringIO()):
                    ok, failed = module.process(
                        "fixture.db", "intro_cn", "intro_cn", "UG intro", workers=1
                    )
        self.assertEqual((ok, failed), (0, 1))


class StubbornMatrixTests(unittest.TestCase):
    def test_matrix_contains_required_spring_and_existing_fallback_tasks(self):
        module = import_script("translate_stubborn")
        actual = {
            (db_key, field_key, src_field, store_field, label)
            for db_key, field_key, _path, src_field, store_field, label in module.TASKS
        }
        required = {
            ("ug", "intro", "intro_cn", "intro_cn", "UG intro"),
            ("ug", "syllabus", "syllabus", "syllabus", "UG syllabus"),
            ("ug", "evaluation", "evaluation", "evaluation", "UG evaluation"),
            ("ug", "reference_book", "reference_book", "reference_book", "UG reference_book"),
            ("gr", "intro", "intro", "intro_cn", "GR intro"),
            ("gr", "extra_notes", "extra_notes", "extra_notes", "GR extra"),
            ("gr", "reference_book", "reference_book", "reference_book", "GR reference_book"),
            ("summer", "intro", "intro_cn", "intro_cn", "Summer intro"),
            ("summer", "syllabus", "syllabus", "syllabus", "Summer syllabus"),
            ("summer", "evaluation", "evaluation", "evaluation", "Summer evaluation"),
            ("fall", "intro", "intro_cn", "intro_cn", "Fall intro"),
            ("fall", "syllabus", "syllabus", "syllabus", "Fall syllabus"),
            ("fall", "evaluation", "evaluation", "evaluation", "Fall evaluation"),
            ("fall", "reference_book", "reference_book", "reference_book", "Fall reference_book"),
            ("fall_gr", "intro", "intro", "intro_cn", "Fall GR intro"),
            ("fall_gr", "extra_notes", "extra_notes", "extra_notes", "Fall GR extra"),
            ("fall_gr", "syllabus", "syllabus", "syllabus", "Fall GR syllabus"),
            ("fall_gr", "reference_book", "reference_book", "reference_book", "Fall GR reference_book"),
        }
        self.assertTrue(required.issubset(actual), required - actual)


if __name__ == "__main__":
    unittest.main()
