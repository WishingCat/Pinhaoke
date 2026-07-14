from contextlib import closing
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_reviews_page_resolves_from_project_directory(self):
        self.assertEqual(Path(app.reviews_page().path), app.BASE_DIR / "reviews.html")

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
        self.assertEqual(payload["reviews"]["integrity"], "ok")
        self.assertEqual(payload["reviews"]["threads"], 47843)
        self.assertEqual(payload["reviews"]["snapshot_replies"], 210570)
        self.assertEqual(payload["reviews"]["highlights"], 188759)

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
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER)")
                conn.execute("CREATE TABLE detail_info(course_id INTEGER)")
                conn.commit()
            with patch.dict(app.TERM_DBS, {"test": [("main", database, "x")]}, clear=True):
                response = self.health_endpoint()()

            self.assert_unhealthy_response(response, tmp)


    def test_health_endpoint_hides_basic_detail_count_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "count-mismatch.db"
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER)")
                conn.execute("CREATE TABLE detail_info(course_id INTEGER)")
                conn.execute("CREATE TABLE translations(course_id INTEGER)")
                conn.execute("INSERT INTO basic_info VALUES (1)")
                conn.commit()
            with patch.dict(app.TERM_DBS, {"test": [("main", database, "x")]}, clear=True):
                response = self.health_endpoint()()

            self.assert_unhealthy_response(response, tmp)

    def test_health_endpoint_hides_equal_count_but_mismatched_course_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "relation-mismatch.db"
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE detail_info(course_id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE translations(course_id INTEGER)")
                conn.execute("INSERT INTO basic_info VALUES (1)")
                conn.execute("INSERT INTO detail_info VALUES (2)")
                conn.commit()
            with patch.dict(app.TERM_DBS, {"test": [("main", database, "x")]}, clear=True):
                response = self.health_endpoint()()

            self.assert_unhealthy_response(response, tmp)

    def test_health_endpoint_hides_duplicate_course_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "duplicate-ids.db"
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER)")
                conn.execute("CREATE TABLE detail_info(course_id INTEGER)")
                conn.execute("CREATE TABLE translations(course_id INTEGER)")
                conn.executemany("INSERT INTO basic_info VALUES (?)", [(1,), (1,)])
                conn.executemany("INSERT INTO detail_info VALUES (?)", [(1,), (1,)])
                conn.commit()
            with patch.dict(app.TERM_DBS, {"test": [("main", database, "x")]}, clear=True):
                response = self.health_endpoint()()

            self.assert_unhealthy_response(response, tmp)

    def test_health_endpoint_hides_foreign_key_violations(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "foreign-key-violation.db"
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER PRIMARY KEY)")
                conn.execute(
                    "CREATE TABLE detail_info("
                    "course_id INTEGER PRIMARY KEY REFERENCES basic_info(id))"
                )
                conn.execute(
                    "CREATE TABLE translations("
                    "course_id INTEGER REFERENCES basic_info(id))"
                )
                conn.execute("INSERT INTO basic_info VALUES (1)")
                conn.execute("INSERT INTO detail_info VALUES (1)")
                conn.execute("INSERT INTO translations VALUES (2)")
                conn.commit()
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
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE basic_info(id INTEGER)")
                conn.execute("CREATE TABLE detail_info(course_id INTEGER)")
                conn.execute("CREATE TABLE translations(course_id INTEGER)")
                conn.commit()
            real_connect = sqlite3.connect
            with patch.dict(app.TERM_DBS, {"test": [("main", database, "x")]}, clear=True):
                with patch.object(
                    app.sqlite3,
                    "connect",
                    side_effect=lambda *args, **kwargs: IntegrityFailureConnection(real_connect(*args, **kwargs)),
                ):
                    response = self.health_endpoint()()

            self.assert_unhealthy_response(response, tmp)


class ReviewApiTests(unittest.TestCase):
    def call(self, **overrides):
        args = {"q": "", "page": 1, "page_size": 20}
        args.update(overrides)
        return app.list_reviews(**args)

    def test_reviews_database_is_query_only(self):
        with app.get_reviews_db() as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE forbidden_write(id INTEGER)")

    def test_review_metadata_matches_full_extraction(self):
        metadata = app.get_review_meta()
        self.assertEqual(metadata["snapshot_date"], "2026-07-13")
        self.assertEqual(metadata["start_date"], "2022-12-21")
        self.assertEqual(metadata["end_date"], "2026-07-13")
        self.assertEqual(metadata["highlight_version"], "3")
        self.assertEqual(metadata["matched_threads"], 47843)
        self.assertEqual(metadata["matched_entries"], 90880)
        self.assertEqual(metadata["matched_replies"], 43037)
        self.assertEqual(metadata["snapshot_replies"], 210570)
        self.assertEqual(
            metadata["matched_threads"] + metadata["matched_replies"],
            metadata["matched_entries"],
        )
        self.assertEqual(metadata["source_shards"], 44)
        self.assertEqual(metadata["cached_reply_coverage_percent"], 95.24)
        self.assertEqual(metadata["highlighted_entries"], 59773)
        self.assertEqual(metadata["course_highlights"], 135241)
        self.assertEqual(metadata["teacher_highlights"], 53518)
        self.assertEqual(metadata["course_aliases"], 802)
        self.assertEqual(metadata["teacher_aliases"], 1062)
        self.assertEqual(metadata["course_alias_highlights"], 56168)
        self.assertEqual(metadata["teacher_alias_highlights"], 27962)

    def test_course_name_search_returns_grouped_threads_and_entries(self):
        result = self.call(q="博弈论", page_size=10)
        self.assertGreater(result["total"], 0)
        self.assertEqual(result["page"], 1)
        self.assertLessEqual(len(result["threads"]), 10)
        for thread in result["threads"]:
            searchable = "\n".join(
                [thread["content"], *thread["courses"]]
                + [entry["content"] for entry in thread["entries"]]
            )
            self.assertIn("博弈论", searchable)
            self.assertEqual(thread["entries"][0]["kind"], "post")
            for entry in thread["entries"]:
                self.assertIsInstance(entry["highlights"], list)
                for highlight in entry["highlights"]:
                    self.assertIn(highlight["entity_type"], {"course", "teacher"})
                    self.assertIn(highlight["match_kind"], {"full", "alias"})
                    self.assertGreater(highlight["end_offset"], highlight["start_offset"])
            self.assertNotIn("authorTag", repr(thread))
            self.assertNotIn("replyTo", repr(thread))

    def test_review_api_returns_course_and_teacher_highlights(self):
        with app.get_reviews_db() as conn:
            row = conn.execute(
                """
                SELECT t.*
                FROM threads t
                WHERE EXISTS (
                    SELECT 1
                    FROM entries e
                    JOIN entry_highlights h ON h.entry_key=e.entry_key
                    WHERE e.pid=t.pid AND h.entity_type='teacher'
                )
                  AND EXISTS (
                    SELECT 1
                    FROM entries e
                    JOIN entry_highlights h ON h.entry_key=e.entry_key
                    WHERE e.pid=t.pid AND h.entity_type='course'
                )
                ORDER BY t.pid
                LIMIT 1
                """
            ).fetchone()
            thread = app._load_review_threads(conn, [row])[0]

        highlights = [
            highlight
            for entry in thread["entries"]
            for highlight in entry["highlights"]
        ]
        self.assertIn("course", {item["entity_type"] for item in highlights})
        self.assertIn("teacher", {item["entity_type"] for item in highlights})
        self.assertNotIn("entry_key", repr(thread))

    def test_review_thread_detail_returns_all_snapshot_replies_without_identity_fields(self):
        with app.get_reviews_db() as conn:
            pid, expected_count = conn.execute(
                "SELECT pid, COUNT(*) AS reply_count FROM thread_replies "
                "GROUP BY pid ORDER BY reply_count DESC, pid LIMIT 1"
            ).fetchone()

        thread = app.get_review_thread(pid)
        self.assertEqual(thread["pid"], pid)
        self.assertEqual(thread["reply_count"], expected_count)
        self.assertEqual(len(thread["replies"]), expected_count)
        self.assertEqual(
            set(thread),
            {
                "pid", "source_month", "posted_at", "content", "source_url",
                "post_kind", "reply_count", "replies",
            },
        )
        for reply in thread["replies"]:
            self.assertEqual(set(reply), {"cid", "floor", "posted_at", "content"})
        serialized = repr(thread)
        for private_field in ("authorTag", "authorLabel", "replyTo", "entry_key"):
            self.assertNotIn(private_field, serialized)

    def test_review_thread_detail_validates_id_and_returns_404(self):
        for pid in (0, -1, True, "1"):
            with self.subTest(pid=pid):
                with self.assertRaises(app.HTTPException) as ctx:
                    app.get_review_thread(pid)
                self.assertEqual(ctx.exception.status_code, 422)
        with self.assertRaises(app.HTTPException) as ctx:
            app.get_review_thread(9_999_999_999)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_review_pagination_is_stable_and_non_overlapping(self):
        first = self.call(page=1, page_size=17)
        second = self.call(page=2, page_size=17)
        first_ids = [thread["pid"] for thread in first["threads"]]
        second_ids = [thread["pid"] for thread in second["threads"]]
        self.assertEqual(len(first_ids), 17)
        self.assertEqual(len(second_ids), 17)
        self.assertFalse(set(first_ids) & set(second_ids))
        self.assertEqual(first_ids, [thread["pid"] for thread in self.call(page_size=17)["threads"]])

    def test_review_course_suggestions_are_ranked_and_searchable(self):
        suggestions = app.list_review_courses(q="博弈", limit=10)
        self.assertTrue(suggestions)
        self.assertTrue(any("博弈" in item["course_name"] for item in suggestions))
        self.assertTrue(all(item["thread_count"] > 0 for item in suggestions))

    def test_review_query_validation_rejects_unsafe_shapes(self):
        invalid = (
            {"q": 1},
            {"q": "x" * 121},
            {"page": 0},
            {"page": 10001},
            {"page_size": 0},
            {"page_size": 101},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(app.HTTPException) as ctx:
                    self.call(**values)
                self.assertEqual(ctx.exception.status_code, 422)

        invalid_suggestions = (
            {"q": 1, "limit": 10},
            {"q": "x" * 121, "limit": 10},
            {"q": "", "limit": 0},
        )
        for values in invalid_suggestions:
            with self.subTest(values=values):
                with self.assertRaises(app.HTTPException) as ctx:
                    app.list_review_courses(**values)
                self.assertEqual(ctx.exception.status_code, 422)

    def test_like_wildcards_are_escaped(self):
        self.assertEqual(app._escape_like(r"50%_done\\"), r"50\%\_done\\\\")


class CourseListTests(unittest.TestCase):
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
        with app.get_db("spring") as conn:
            rows = conn.execute(
                """
                SELECT b.id, d.evaluation
                FROM basic_info b
                JOIN detail_info d ON d.course_id = b.id
                WHERE b.course_code = '00137975'
                  AND b.class_no = '1'
                  AND b.teacher = '王杰(教授)'
                """
            ).fetchall()

        evaluation_by_id = {row["id"]: row["evaluation"] for row in rows}
        self.assertFalse((evaluation_by_id[288] or "").strip())
        self.assertTrue(evaluation_by_id[860].strip())

        courses = self.all_courses(term="spring", q="00137975")
        card = self.find_card(courses, "00137975", "1", "王杰(教授)")
        self.assertEqual(card["id"], "u860")

    def test_type_filter_preserves_representative_id_and_all_badges(self):
        courses = self.all_courses(term="spring", q="00137975")
        card = next(course for course in courses if course["course_code"] == "00137975")
        self.assertGreater(len(card["course_type"]), 1)

        for course_type in card["course_type"]:
            filtered = self.all_courses(term="spring", q="00137975", type=course_type)
            same = self.find_card(filtered, card["course_code"], card["class_no"], card["teacher"])
            self.assertEqual(same["id"], card["id"])
            self.assertEqual(same["course_type"], card["course_type"])

    def test_representative_counts_visible_basic_fields(self):
        courses = self.all_courses(term="spring", q="00430109")
        card = self.find_card(courses, "00430109", "1", "穆良柱(教授)")
        self.assertEqual(card["id"], "u2426")
        self.assertEqual(card["major"], "物理学类")

    def test_scalar_fallback_uses_one_coherent_source_row(self):
        columns = (
            "id", "course_type", "course_code", "class_no", "course_name",
            "category", "credits", "teacher", "department", "major", "grade",
            "schedule", "classroom", "weekdays", "first_period", "enrollment",
            "pnp", "notes", "english_name", "grading", "language", "audience",
            "_level", "detail_score", "completeness_score", "display_course_name",
            "display_classroom", "display_notes", "group_key",
        )
        base = {
            "course_type": "专业课",
            "course_code": "SYN001",
            "class_no": "1",
            "course_name": "Representative Name",
            "category": "任选",
            "credits": 2.0,
            "teacher": "Test Teacher",
            "department": "Representative Department",
            "major": "",
            "grade": "",
            "schedule": "Representative Schedule",
            "classroom": "R101",
            "weekdays": "周一",
            "first_period": 1,
            "enrollment": "10 / 0",
            "pnp": "可申请",
            "notes": "Representative Notes",
            "english_name": "Representative English Name",
            "grading": "百分制",
            "language": "中文",
            "audience": "",
            "_level": "x",
            "display_course_name": "Representative Name",
            "display_classroom": "R101",
            "display_notes": "Representative Notes",
            "group_key": "synthetic-group",
        }
        fixtures = [
            {**base, "id": "x1", "detail_score": 10, "completeness_score": 10},
            {
                **base,
                "id": "x2",
                "department": "Sibling Department A",
                "major": "Alpha major",
                "grade": "Z grade",
                "detail_score": 8,
                "completeness_score": 8,
            },
            {
                **base,
                "id": "x3",
                "department": "Sibling Department B",
                "major": "Zulu major",
                "grade": "A grade",
                "detail_score": 7,
                "completeness_score": 7,
            },
        ]
        row_placeholder = "(" + ",".join("?" for _ in columns) + ")"
        source_sql = (
            f"WITH fixture({','.join(columns)}) AS "
            f"(VALUES {','.join(row_placeholder for _ in fixtures)}) SELECT * FROM fixture"
        )
        params = [fixture[column] for fixture in fixtures for column in columns]
        sql = f"{app._grouped_course_ctes(source_sql, '')} SELECT * FROM grouped"

        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql, params).fetchone()

        self.assertEqual(row["id"], "x1")
        self.assertEqual(row["course_name"], "Representative Name")
        self.assertEqual(row["department"], "Representative Department")
        self.assertEqual(row["major"], "Alpha major")
        self.assertEqual(row["grade"], "Z grade")
        self.assertEqual(row["fallback_id"], "x2")

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

    def test_translated_graduate_course_name_is_searchable(self):
        with app.get_db("spring") as conn:
            row = conn.execute(
                """
                SELECT b.id, t.text
                FROM gr.basic_info b
                JOIN gr.detail_info d ON d.course_id = b.id
                JOIN gr.translations t
                  ON t.course_id = b.id AND t.lang = 'ja' AND t.field = 'course_name'
                WHERE TRIM(t.text) != ''
                  AND t.text NOT IN (b.course_name, COALESCE(d.english_name, ''))
                ORDER BY b.id
                LIMIT 1
                """
            ).fetchone()

        sample_id, translated_name = row
        result = self.all_courses(term="spring", lang="ja", q=translated_name)
        card = next((course for course in result if course["id"] == f"g{sample_id}"), None)
        self.assertIsNotNone(card)
        self.assertEqual(card["course_name"], translated_name)

    def test_cross_level_same_key_collision_returns_two_cards(self):
        with app.get_db("fall") as conn:
            row = conn.execute(
                """
                SELECT a.course_code, a.class_no, a.teacher, a.id, r.id
                FROM basic_info a
                JOIN gr.basic_info r
                  ON r.course_code = a.course_code
                 AND r.class_no = a.class_no
                 AND r.teacher = a.teacher
                WHERE TRIM(COALESCE(a.teacher, '')) != ''
                ORDER BY a.course_code, a.class_no, a.teacher
                LIMIT 1
                """
            ).fetchone()

        course_code, class_no, teacher, undergrad_id, graduate_id = row
        courses = self.all_courses(term="fall", q=course_code)
        matching = {
            course["id"]
            for course in courses
            if course["course_code"] == course_code
            and course["class_no"] == class_no
            and course["teacher"] == teacher
        }
        self.assertEqual(matching, {f"a{undergrad_id}", f"r{graduate_id}"})

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

    def test_random_signed_seeds_are_stable_and_pairwise_distinct(self):
        orders = {}
        for seed in (0, 1, -1):
            total, first = self.all_ids("summer", sort="random", random_seed=seed, page_size=37)
            _, second = self.all_ids("summer", sort="random", random_seed=seed, page_size=37)
            self.assertEqual(first, second)
            self.assertEqual(len(first), total)
            self.assertEqual(len(set(first)), total)
            orders[seed] = tuple(first)

        self.assertEqual(len(set(orders.values())), 3)

    def test_whitespace_only_teachers_get_id_specific_group_keys(self):
        group_key_sql = getattr(app, "_group_key_sql", None)
        self.assertIsNotNone(group_key_sql)
        if group_key_sql is None:
            return

        with closing(sqlite3.connect(":memory:")) as conn:
            rows = conn.execute(
                f"""
                WITH sample(_level, course_code, class_no, teacher, id) AS (
                    VALUES ('x', 'C1', '1', '   ', 'x1'),
                           ('x', 'C1', '1', '\t', 'x2'),
                           ('x', 'C1', '1', 'Teacher', 'x3')
                )
                SELECT id, {group_key_sql('sample')} AS group_key
                FROM sample
                ORDER BY id
                """
            ).fetchall()

        keys = {row[0]: row[1] for row in rows}
        self.assertNotEqual(keys["x1"], keys["x2"])
        self.assertTrue(keys["x1"].endswith("x1"))
        self.assertTrue(keys["x2"].endswith("x2"))
        self.assertTrue(keys["x3"].endswith("Teacher"))

    def test_count_query_stops_after_matching_group_keys(self):
        count_course_sql = getattr(app, "_count_course_sql", None)
        self.assertIsNotNone(count_course_sql)
        if count_course_sql is None:
            return

        filters = {
            "q": "",
            "type": "",
            "category": "",
            "credits": "",
            "department": "",
            "weekday": "",
            "grading": "",
            "classroom": "",
        }
        source_sql, params, matching_where = app._build_course_query("fall", "zh", filters)
        count_sql = count_course_sql(source_sql, matching_where)
        self.assertNotIn("ROW_NUMBER", count_sql)
        self.assertNotIn("badge_values", count_sql)
        self.assertNotIn("fallback_candidates", count_sql)

        with app.get_db("fall") as conn:
            total = conn.execute(count_sql, params).fetchone()[0]
            plan = conn.execute(f"EXPLAIN QUERY PLAN {count_sql}", params).fetchall()
        self.assertEqual(total, 4421)
        plan_details = [step["detail"] for step in plan]
        self.assertFalse(any("ranked" in detail or "badges" in detail for detail in plan_details))


class ValidationAndDetailTests(unittest.TestCase):
    def list_args(self, **overrides):
        args = {
            "q": "",
            "type": "",
            "category": "",
            "credits": "",
            "department": "",
            "weekday": "",
            "grading": "",
            "classroom": "",
            "sort": "",
            "random_seed": 0,
            "lang": "zh",
            "term": "fall",
            "page": 1,
            "page_size": 20,
        }
        args.update(overrides)
        return args

    def test_course_id_is_canonical(self):
        self.assertEqual(app._parse_id("a1"), ("fall", "a", 1))
        for value in ("", "a0", "a01", "a+1", "a-1", "a 1", "a1 ", " x1", "x1"):
            self.assertEqual(app._parse_id(value), (None, None, None))

    def test_invalid_filter_values_raise_422(self):
        invalid_values = (
            ("credits", "abc"),
            ("credits", "NaN"),
            ("credits", "Infinity"),
            ("credits", True),
            ("credits", False),
            ("credits", []),
            ("weekday", "%"),
            ("lang", "xx"),
            ("sort", "drop"),
            ("term", "winter"),
            ("page", 0),
            ("page", 10001),
            ("page_size", 0),
            ("page_size", 201),
        )
        for key, value in invalid_values:
            with self.subTest(key=key, value=value):
                with self.assertRaises(app.HTTPException) as ctx:
                    app.list_courses(**self.list_args(**{key: value}))
                self.assertEqual(ctx.exception.status_code, 422)

    def test_invalid_filter_term_raises_422(self):
        with self.assertRaises(app.HTTPException) as ctx:
            app.get_filters("winter")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_valid_but_out_of_range_page_is_empty(self):
        result = app.list_courses(**self.list_args(term="summer", page=10000))
        self.assertEqual(result["courses"], [])

    def test_detail_language_is_validated_before_opening_database(self):
        with patch.object(app, "get_db") as get_db:
            with self.assertRaises(app.HTTPException) as ctx:
                app.get_course_detail("a1", lang="xx")
        self.assertEqual(ctx.exception.status_code, 422)
        get_db.assert_not_called()

    def test_undergraduate_detail_exposes_books(self):
        detail = app.get_course_detail("a1", lang="zh")
        self.assertIn("textbook", detail)
        self.assertIn("reference_book", detail)

    def test_graduate_detail_has_empty_textbook_and_source_reference_book(self):
        with app.get_db("fall") as conn:
            row = conn.execute(
                """
                SELECT b.id, d.reference_book
                FROM gr.basic_info b
                JOIN gr.detail_info d ON d.course_id = b.id
                WHERE TRIM(COALESCE(d.reference_book, '')) != ''
                ORDER BY b.id
                LIMIT 1
                """
            ).fetchone()

        detail = app.get_course_detail(f"r{row['id']}", lang="zh")
        self.assertEqual(detail["textbook"], "")
        self.assertEqual(detail["reference_book"], row["reference_book"])

    def assert_translated_book_field_replaces_source_text(self, field):
        with app.get_db("fall") as conn:
            row = conn.execute(
                """
                SELECT b.id, t.text
                FROM translations t
                JOIN basic_info b ON b.id = t.course_id
                JOIN detail_info d ON d.course_id = b.id
                WHERE t.lang = 'en'
                  AND t.field = ?
                  AND TRIM(t.text) != ''
                  AND t.text != CASE t.field
                      WHEN 'textbook' THEN COALESCE(d.textbook, '')
                      ELSE COALESCE(d.reference_book, '')
                  END
                ORDER BY b.id, t.field
                LIMIT 1
                """,
                (field,),
            ).fetchone()

        self.assertIsNotNone(row)
        detail = app.get_course_detail(f"a{row['id']}", lang="en")
        self.assertEqual(detail[field], row["text"])

    def test_translated_textbook_replaces_source_text(self):
        self.assert_translated_book_field_replaces_source_text("textbook")

    def test_translated_reference_book_replaces_source_text(self):
        self.assert_translated_book_field_replaces_source_text("reference_book")

    def test_blank_translation_does_not_replace_original(self):
        out = {"course_name": "Original name"}
        cursor = Mock()
        cursor.execute.return_value.fetchall.return_value = [("course_name", "   ")]
        app._apply_translations(cursor, "main", 1, "en", out)
        self.assertEqual(out["course_name"], "Original name")
