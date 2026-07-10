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


class CourseListTests(unittest.TestCase):
    UG_DETAIL_COLUMNS = (
        "english_name",
        "prerequisites",
        "intro_cn",
        "intro_en",
        "grading",
        "ge_series",
        "language",
        "textbook",
        "reference_book",
        "syllabus",
        "evaluation",
    )

    def call(self, **overrides):
        args = dict(
            q="",
            type="",
            category="",
            credits="",
            department="",
            weekday="",
            grading="",
            classroom="",
            sort="",
            random_seed=0,
            lang="zh",
            term="fall",
            page=1,
            page_size=200,
        )
        args.update(overrides)
        return app.list_courses(**args)

    def all_courses(self, **overrides):
        first = self.call(**overrides)
        courses = list(first["courses"])
        pages = (first["total"] + first["page_size"] - 1) // first["page_size"]
        for page in range(2, pages + 1):
            courses.extend(self.call(page=page, **overrides)["courses"])
        self.assertEqual(len(courses), first["total"])
        return courses

    def all_ids(self, term, sort="", random_seed=0, lang="zh", q="", page_size=200):
        courses = self.all_courses(
            term=term,
            sort=sort,
            random_seed=random_seed,
            lang=lang,
            q=q,
            page_size=page_size,
        )
        return len(courses), [course["id"] for course in courses]

    def find_card(self, courses, course_code, class_no, teacher):
        return next(
            course
            for course in courses
            if course["course_code"] == course_code
            and course["class_no"] == class_no
            and course["teacher"] == teacher
        )

    def detail_score_sql(self):
        return " + ".join(
            f"CASE WHEN d.{column} IS NOT NULL "
            f"AND TRIM(CAST(d.{column} AS TEXT)) != '' THEN 1 ELSE 0 END"
            for column in self.UG_DETAIL_COLUMNS
        )

    def test_card_totals_keep_undergrad_and_graduate_separate(self):
        self.assertEqual(self.call(term="fall", page_size=1)["total"], 4421)
        self.assertEqual(self.call(term="spring", page_size=1)["total"], 3701)
        self.assertEqual(self.call(term="summer", page_size=1)["total"], 160)

    def test_filter_preserves_representative_id_and_all_badges(self):
        with app.get_db("fall") as conn:
            target = conn.execute(
                """
                SELECT course_code, class_no, teacher
                FROM basic_info
                WHERE TRIM(COALESCE(teacher, '')) != ''
                GROUP BY course_code, class_no, teacher
                HAVING COUNT(DISTINCT category) > 1
                ORDER BY course_code, class_no, teacher
                LIMIT 1
                """
            ).fetchone()

        course_code, class_no, teacher = target
        unfiltered = self.all_courses(term="fall", q=course_code)
        card = self.find_card(unfiltered, course_code, class_no, teacher)
        self.assertGreater(len(card["category"]), 1)

        for category in card["category"]:
            filtered = self.all_courses(term="fall", q=course_code, category=category)
            same = self.find_card(filtered, course_code, class_no, teacher)
            self.assertEqual(same["id"], card["id"])
            self.assertEqual(same["category"], card["category"])

    def test_representative_uses_long_detail_fields(self):
        score_sql = self.detail_score_sql()
        with app.get_db("spring") as conn:
            rows = conn.execute(
                f"""
                SELECT b.id, b.course_code, b.class_no, b.teacher,
                       d.evaluation, ({score_sql}) AS detail_score
                FROM basic_info b
                JOIN detail_info d ON d.course_id = b.id
                WHERE b.course_code = '00137975'
                  AND b.class_no = '1'
                  AND b.teacher = '王杰(教授)'
                """
            ).fetchall()

        expected = min(rows, key=lambda row: (-row["detail_score"], row["id"]))
        smallest_id = min(rows, key=lambda row: row["id"])
        self.assertNotEqual(expected["id"], smallest_id["id"])
        self.assertTrue(expected["evaluation"].strip())
        self.assertFalse((smallest_id["evaluation"] or "").strip())

        courses = self.all_courses(term="spring", q=expected["course_code"])
        card = self.find_card(
            courses,
            expected["course_code"],
            expected["class_no"],
            expected["teacher"],
        )
        self.assertEqual(card["id"], f"u{expected['id']}")

    def test_type_filter_preserves_representative_id_and_all_badges(self):
        courses = self.all_courses(term="spring", q="00137975")
        card = next(course for course in courses if course["course_code"] == "00137975")
        self.assertGreater(len(card["course_type"]), 1)

        for course_type in card["course_type"]:
            filtered = self.all_courses(term="spring", q="00137975", type=course_type)
            same = self.find_card(filtered, card["course_code"], card["class_no"], card["teacher"])
            self.assertEqual(same["id"], card["id"])
            self.assertEqual(same["course_type"], card["course_type"])

    def test_representative_scalar_fields_fall_back_to_a_nonblank_sibling(self):
        score_sql = self.detail_score_sql()
        with app.get_db("spring") as conn:
            rows = conn.execute(
                f"""
                SELECT b.id, b.course_code, b.class_no, b.teacher, b.major,
                       ({score_sql}) AS detail_score
                FROM basic_info b
                JOIN detail_info d ON d.course_id = b.id
                WHERE b.course_code = '00430109'
                  AND b.class_no = '1'
                  AND b.teacher = '穆良柱(教授)'
                """
            ).fetchall()

        representative = min(rows, key=lambda row: (-row["detail_score"], row["id"]))
        sibling_major = next(row["major"] for row in rows if (row["major"] or "").strip())
        self.assertFalse((representative["major"] or "").strip())

        courses = self.all_courses(term="spring", q=representative["course_code"])
        card = self.find_card(
            courses,
            representative["course_code"],
            representative["class_no"],
            representative["teacher"],
        )
        self.assertEqual(card["id"], f"u{representative['id']}")
        self.assertEqual(card["major"], sibling_major)

    def test_badge_arrays_have_deterministic_order(self):
        courses = self.all_courses(term="fall", q="00137975")
        card = next(course for course in courses if course["course_code"] == "00137975")
        self.assertEqual(card["course_type"], sorted(card["course_type"]))
        self.assertEqual(card["category"], sorted(card["category"]))

    def test_translated_course_name_is_searchable(self):
        with app.get_db("fall") as conn:
            row = conn.execute(
                """
                WITH singleton AS (
                    SELECT course_code, class_no, teacher
                    FROM basic_info
                    WHERE TRIM(COALESCE(teacher, '')) != ''
                    GROUP BY course_code, class_no, teacher
                    HAVING COUNT(*) = 1
                )
                SELECT b.id, t.text
                FROM basic_info b
                JOIN singleton USING (course_code, class_no, teacher)
                JOIN detail_info d ON d.course_id = b.id
                JOIN translations t
                  ON t.course_id = b.id AND t.lang = 'ja' AND t.field = 'course_name'
                WHERE TRIM(t.text) != ''
                  AND t.text NOT IN (b.course_name, COALESCE(d.english_name, ''))
                ORDER BY b.id
                LIMIT 1
                """
            ).fetchone()

        sample_id, translated_name = row
        result = self.all_courses(term="fall", lang="ja", q=translated_name, sort="name_asc")
        card = next((course for course in result if course["id"] == f"a{sample_id}"), None)
        self.assertIsNotNone(card)
        self.assertEqual(card["course_name"], translated_name)

    def test_translated_classroom_is_searchable(self):
        with app.get_db("fall") as conn:
            row = conn.execute(
                """
                SELECT b.id, t.text
                FROM basic_info b
                JOIN translations t
                  ON t.course_id = b.id AND t.lang = 'en' AND t.field = 'classroom'
                WHERE TRIM(t.text) != '' AND t.text != b.classroom
                ORDER BY b.id
                LIMIT 1
                """
            ).fetchone()

        sample_id, translated_classroom = row
        result = self.all_courses(term="fall", lang="en", q=translated_classroom)
        card = next((course for course in result if course["id"] == f"a{sample_id}"), None)
        self.assertIsNotNone(card)
        self.assertEqual(card["classroom"], translated_classroom)

    def test_name_sorts_use_display_name_and_legacy_aliases(self):
        ascending = self.all_courses(term="fall", lang="en", q="of", sort="name_asc")
        descending = self.all_courses(term="fall", lang="en", q="of", sort="name_desc")
        legacy_asc = self.all_courses(term="fall", lang="en", q="of", sort="pinyin")
        legacy_desc = self.all_courses(term="fall", lang="en", q="of", sort="pinyin_desc")

        ascending_names = [course["course_name"].casefold() for course in ascending]
        descending_names = [course["course_name"].casefold() for course in descending]
        self.assertEqual(ascending_names, sorted(ascending_names))
        self.assertEqual(descending_names, sorted(descending_names, reverse=True))
        self.assertEqual(
            [course["id"] for course in ascending],
            [course["id"] for course in legacy_asc],
        )
        self.assertEqual(
            [course["id"] for course in descending],
            [course["id"] for course in legacy_desc],
        )

    def test_random_sort_is_stable_complete_and_unique(self):
        total, first = self.all_ids("summer", sort="random", random_seed=731, page_size=37)
        _, second = self.all_ids("summer", sort="random", random_seed=731, page_size=37)
        _, different_seed = self.all_ids("summer", sort="random", random_seed=947, page_size=37)
        self.assertEqual(first, second)
        self.assertNotEqual(first, different_seed)
        self.assertEqual(len(first), total)
        self.assertEqual(len(set(first)), total)
