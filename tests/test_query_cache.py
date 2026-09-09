"""Read-only query caching must not cache mutable state or survive data updates."""
from pathlib import Path
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app
import tests.test_app as app_tests


class QueryCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.database = self.root / '课程 #?%.db'
        shutil.copyfile(app.SUMMER_DB, self.database)
        patcher = patch.dict(app.TERM_DBS, {'summer': [('main', self.database, 's')]})
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(app, 'MESSAGES_DB_PATH', self.root / 'messages.db')
        patcher.start(); self.addCleanup(patcher.stop)
        for cache in (app._course_page_rows, app._review_page):
            cache.cache_clear()
            self.addCleanup(cache.cache_clear)

    def courses(self, **kwargs):
        return app_tests.CourseListTests.call(self, term='summer', page_size=5, **kwargs)

    def test_cache_reuses_source_rows_but_messages_and_response_lists_stay_fresh(self):
        with patch.object(app, 'get_db', wraps=app.get_db) as source:
            first = self.courses()
            self.assertEqual(source.call_count, 1)
            again = self.courses()
            self.assertEqual(source.call_count, 1)
        self.assertEqual(first, again)
        target = first['courses'][0]
        key = app._course_message_key(target['id'])
        app._insert_message('补充信息', 'test', course_key=key)
        target['course_type'].clear()
        target['course_name'] = '不能污染其他请求'
        with patch.object(app, 'get_db', side_effect=AssertionError('unexpected source read')):
            updated = self.courses()
        self.assertTrue(updated['courses'][0]['has_course_corrections'])
        self.assertEqual(updated['courses'][0]['course_type'], again['courses'][0]['course_type'])
        self.assertEqual(updated['courses'][0]['course_name'], again['courses'][0]['course_name'])

    def test_cache_invalidates_for_in_place_wal_and_atomic_replacement(self):
        self.courses()
        with sqlite3.connect(self.database) as writer:
            writer.execute("UPDATE basic_info SET notes='原位更新'")
        self.assertTrue(all(c['notes'] == '原位更新' for c in self.courses()['courses']))
        writer = sqlite3.connect(self.database)
        try:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("UPDATE basic_info SET notes='WAL 更新'")
            writer.commit()
            self.assertTrue(all(c['notes'] == 'WAL 更新' for c in self.courses()['courses']))
        finally:
            writer.close()
        replacement = self.root / 'replacement.db'
        shutil.copyfile(app.SUMMER_DB, replacement)
        with sqlite3.connect(replacement) as writer:
            writer.execute("UPDATE basic_info SET notes='原子替换'")
        os.replace(replacement, self.database)
        self.assertTrue(all(c['notes'] == '原子替换' for c in self.courses()['courses']))
        self.assertEqual(app._course_page_rows.cache_info().misses, 4)

    def test_queries_and_pages_have_independent_entries(self):
        first = self.courses()
        second = self.courses(page=2)
        self.assertFalse({c['id'] for c in first['courses']} & {c['id'] for c in second['courses']})
        filtered = self.courses(q=first['courses'][0]['course_code'])
        self.assertTrue(all(c['course_code'] == first['courses'][0]['course_code'] for c in filtered['courses']))
        self.assertEqual(self.courses(), first)
        self.assertEqual(app._course_page_rows.cache_info().hits, 1)

    def test_review_cache_preserves_nested_data_and_tracks_revision(self):
        with patch.object(app, '_database_revision', return_value=('revision1',)), \
             patch.object(app, 'get_reviews_db', wraps=app.get_reviews_db) as source:
            first = app.list_reviews('高数', 1, 2)
            expected = json.loads(json.dumps(first))
            first['threads'][0]['entries'].clear()
            first['threads'][0]['highlights'].clear()
            self.assertEqual(app.list_reviews('高数', 1, 2), expected)
            self.assertEqual(source.call_count, 1)
        with patch.object(app, '_database_revision', return_value=('revision2',)), \
             patch.object(app, 'get_reviews_db', wraps=app.get_reviews_db) as source:
            self.assertEqual(app.list_reviews('高数', 1, 2), expected)
            self.assertEqual(source.call_count, 1)

    def test_sqlite_uri_escapes_paths_and_stays_read_only(self):
        with app.get_db('summer') as conn:
            self.assertGreater(conn.execute('SELECT count(*) FROM basic_info').fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('CREATE TABLE forbidden(id)')

    def test_oversized_course_and_review_ids_never_reach_sqlite(self):
        for course_id in ('a' + str(2**63), 'u' + '9' * 5000):
            with patch.object(app, 'get_db') as source:
                with self.assertRaises(app.HTTPException) as caught:
                    app.get_course_detail(course_id, 'zh')
                self.assertEqual(caught.exception.status_code, 404)
                source.assert_not_called()
        with patch.object(app, 'get_reviews_db') as source:
            with self.assertRaises(app.HTTPException) as caught:
                app.get_review_thread(2**63)
            self.assertEqual(caught.exception.status_code, 422)
            source.assert_not_called()

    def test_invalid_unicode_is_rejected_before_search_or_message_storage(self):
        for call in (lambda: self.courses(q='\ud800'),
                     lambda: app.list_reviews('\ud800', 1, 20),
                     lambda: app._validate_message_content({'content': '\ud800'})):
            with self.assertRaises(app.HTTPException) as caught:
                call()
            self.assertEqual(caught.exception.status_code, 422)
