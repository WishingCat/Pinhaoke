import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DatabaseConnectionTests(unittest.TestCase):
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
        route = next((route for route in app.app.routes if route.path == "/api/health"), None)
        self.assertIsNotNone(route)
        if route is None:
            return

        response = route.endpoint()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_health_endpoint_returns_non_success_when_validation_fails(self):
        route = next((route for route in app.app.routes if route.path == "/api/health"), None)
        self.assertIsNotNone(route)
        if route is None:
            return

        with patch.object(app, "check_database_health", side_effect=RuntimeError("bad database")):
            response = route.endpoint()
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
