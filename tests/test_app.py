import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DatabaseConnectionTests(unittest.TestCase):
    def setUp(self):
        app._health_cache_payload = None
        app._health_cache_checked_at = None

    def health_endpoint(self):
        route = next((route for route in app.app.routes if route.path == "/api/health"), None)
        self.assertIsNotNone(route)
        if route is None:
            self.fail("/api/health route is missing")
        return route.endpoint

    def assert_unhealthy_response(self, response, temporary_directory):
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.body, b'{"status":"error"}')
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn(str(temporary_directory).encode(), response.body)

    def test_get_db_defaults_to_fall_and_is_query_only(self):
        with app.get_db() as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE forbidden_write(id INTEGER)")

    def test_app_imports_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = "import app; print(app.root().path)"
            env = {"PYTHONPATH": str(app.BASE_DIR)}
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), app.BASE_DIR / "index.html")

    def test_attach_failure_closes_main_connection(self):
        fake = app.sqlite3.connect(":memory:")
        missing_db = Path("missing.db")
        self.addCleanup(missing_db.unlink, missing_ok=True)
        with patch.object(app.sqlite3, "connect", return_value=fake):
            with patch.dict(
                app.TERM_DBS,
                {"broken": [("main", Path("a.db"), "x"), ("gr", Path("missing.db"), "y")]},
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    with app.get_db("broken"):
                        pass
        with self.assertRaises(sqlite3.ProgrammingError):
            fake.execute("SELECT 1")

    def test_health_reports_all_five_databases(self):
        if not hasattr(app, "check_database_health"):
            self.fail("check_database_health is missing")
        payload = app.check_database_health()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["databases"]), 5)
        self.assertTrue(all(item["integrity"] == "ok" for item in payload["databases"]))

    def test_health_endpoint_disables_caching(self):
        response = self.health_endpoint()()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_health_endpoint_caches_successful_deep_scans_for_ttl(self):
        if not hasattr(app, "get_cached_database_health"):
            self.fail("get_cached_database_health is missing")

        payload = {"status": "ok", "databases": []}
        with patch.object(app, "check_database_health", return_value=payload) as check:
            with patch.object(app.time, "monotonic", side_effect=[100.0, 101.0, 400.0]):
                first = self.health_endpoint()()
                second = self.health_endpoint()()
                expired = self.health_endpoint()()

        self.assertEqual([first.status_code, second.status_code, expired.status_code], [200, 200, 200])
        self.assertEqual(check.call_count, 2)

    def test_health_endpoint_does_not_cache_failures(self):
        if not hasattr(app, "get_cached_database_health"):
            self.fail("get_cached_database_health is missing")

        with patch.object(
            app,
            "check_database_health",
            side_effect=[RuntimeError("bad database"), {"status": "ok", "databases": []}],
        ) as check:
            first = self.health_endpoint()()
            second = self.health_endpoint()()

        self.assertEqual(first.status_code, 503)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(check.call_count, 2)

    def test_health_endpoint_hides_missing_required_table_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "missing-translations.db"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER)")
                conn.execute("CREATE TABLE detail_info(course_id INTEGER)")
            with patch.dict(app.TERM_DBS, {"test": [("main", database, "x")]}, clear=True):
                response = self.health_endpoint()()

            self.assert_unhealthy_response(response, tmp)

    def test_health_endpoint_hides_basic_detail_count_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "count-mismatch.db"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER)")
                conn.execute("CREATE TABLE detail_info(course_id INTEGER)")
                conn.execute("CREATE TABLE translations(course_id INTEGER)")
                conn.execute("INSERT INTO basic_info VALUES (1)")
            with patch.dict(app.TERM_DBS, {"test": [("main", database, "x")]}, clear=True):
                response = self.health_endpoint()()

            self.assert_unhealthy_response(response, tmp)

    def test_health_endpoint_hides_integrity_check_failure(self):
        class IntegrityFailureConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql):
                if sql == "PRAGMA integrity_check":
                    return type("IntegrityResult", (), {"fetchone": lambda self: ("not ok",)})()
                return self.connection.execute(sql)

            def close(self):
                self.connection.close()

        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "integrity-failure.db"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER)")
                conn.execute("CREATE TABLE detail_info(course_id INTEGER)")
                conn.execute("CREATE TABLE translations(course_id INTEGER)")
            real_connect = sqlite3.connect
            with patch.dict(app.TERM_DBS, {"test": [("main", database, "x")]}, clear=True):
                with patch.object(
                    app.sqlite3,
                    "connect",
                    side_effect=lambda *args, **kwargs: IntegrityFailureConnection(real_connect(*args, **kwargs)),
                ):
                    response = self.health_endpoint()()

            self.assert_unhealthy_response(response, tmp)
