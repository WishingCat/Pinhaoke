import json
import sqlite3
import threading
import unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import app
import test_app as account_tests

class TimetableTests(unittest.TestCase):
    setUp = account_tests.AccountApiTests.setUp
    register = account_tests.AccountApiTests.register
    request = staticmethod(account_tests.AccountApiTests.request)
    QUESTIONS = account_tests.AccountApiTests.QUESTIONS
    body = staticmethod(account_tests.AccountApiTests.body)

    def test_add_is_account_owned_idempotent_and_independent_of_favorites(self):
        _, token = self.register()
        request = self.request(cookie=token)
        first = self.body(app.add_timetable_course(request, {'id':'a1', 'course_name':'SPOOF'}))
        self.assertEqual(len(first['courses']),1)
        self.assertNotEqual(first['courses'][0]['course_name'],'SPOOF')
        self.assertEqual(len(self.body(app.add_timetable_course(request, {'id':'a1'}))['courses']),1)
        app.add_favorite(request, {'id':'a1'})
        app.remove_favorite(request, {'fav_key': first['courses'][0]['fav_key']})
        self.assertEqual(len(self.body(app.get_timetable(request))['courses']),1)
        _, other_token = self.register(username='Other_01',ip='203.0.113.19')
        other = self.request(cookie=other_token)
        self.assertEqual(self.body(app.get_timetable(other))['courses'],[])
        app.remove_timetable_course(other, {'course_key':first['courses'][0]['course_key']})
        self.assertEqual(len(self.body(app.get_timetable(request))['courses']),1)
        self.assertEqual(self.body(app.remove_timetable_course(request, {'course_key':first['courses'][0]['course_key']}))['courses'],[])

    def test_login_origin_and_limit_are_enforced(self):
        with self.assertRaises(app.HTTPException) as ctx: app.get_timetable(self.request())
        self.assertEqual(ctx.exception.status_code,401)
        _, token = self.register()
        request = self.request(cookie=token)
        with patch.object(app,'TIMETABLE_LIMIT',1):
            app.add_timetable_course(request, {'id':'a1'})
            app.add_timetable_course(request, {'id':'a1'})
            with self.assertRaises(app.HTTPException) as ctx: app.add_timetable_course(request, {'id':'s1'})
            self.assertEqual(ctx.exception.status_code,409)
        with self.assertRaises(app.HTTPException) as ctx:
            app.add_timetable_course(self.request(cookie=token,origin='https://evil.example'), {'id':'a1'})
        self.assertEqual(ctx.exception.status_code,403)
        self.assertEqual(len(self.body(app.get_timetable(request))['courses']),1)

    def test_old_favorites_gain_source_classroom_and_homepage_fields(self):
        _, token = self.register()
        request = self.request(cookie=token)
        for cid in ['a1','r1','u1','g1','s1']:
            detail=app.get_course_detail(cid,'zh')
            term = app._parse_id(cid)[0]
            expected = next(card for card in account_tests.CourseListTests().call(
                term=term, q=detail['course_code'],
            )['courses'] if card['id'] == cid)
            payload=self.body(app.add_favorite(request,{'id':cid}))
            item=next(i for i in payload['favorites'] if i['fav_key']==app._favorite_snapshot(cid)['fav_key'])
            for field in ['classroom','schedule','department','course_name']:
                self.assertEqual(item[field],expected[field])
            self.assertIn('course_type',item)

    def test_saved_course_enrichment_batches_each_term_and_keeps_search_representatives(self):
        ids = ['a2712', 'a2713', 'a2979', 'a2331', 'a2330', 'a534', 'u860', 'u2426']
        snapshots = [app._favorite_snapshot(cid) for cid in ids]
        for snapshot in snapshots:
            snapshot['id'] = snapshot.pop('course_id')
        statements = []
        get_db = app.get_db

        @contextmanager
        def traced_db(term='fall'):
            with get_db(term) as conn:
                conn.set_trace_callback(lambda sql: statements.append(sql) if sql.lstrip().upper().startswith(('SELECT', 'WITH')) else None)
                yield conn

        with patch.object(app, 'get_db', traced_db):
            app._enrich_saved_courses(snapshots)
        self.assertEqual(len(statements), 2, '补齐应按学期批量查询，不逐课程发起 SELECT')
        for item in snapshots:
            expected = next(card for card in account_tests.CourseListTests().call(
                term=item['term'], q=item['course_code'],
            )['courses'] if card['id'] == item['id'])
            self.assertTrue(item['available'])
            for field in ('id', 'course_name', 'schedule', 'classroom', 'course_type', 'category'):
                self.assertEqual(item[field], expected[field], field)

    def test_enrichment_keeps_unavailable_snapshot_fields_and_never_reuses_wrong_identity(self):
        source = app._favorite_snapshot('a1')
        source['id'] = source.pop('course_id')
        previous_term = {**source, 'term_label': '2025秋季学期', 'course_name': '旧学期课程', 'schedule': '旧时间'}
        disappeared = {**source, 'course_code': 'REMOVED', 'course_name': '已移除的课程', 'schedule': '原时间'}
        app._enrich_saved_courses([source, previous_term, disappeared])
        self.assertTrue(source['available'])
        for item, name, schedule in (
            (previous_term, '旧学期课程', '旧时间'),
            (disappeared, '已移除的课程', '原时间'),
        ):
            self.assertFalse(item['available'])
            self.assertEqual(item['id'], 'a1')
            self.assertEqual(item['course_name'], name)
            self.assertEqual(item['schedule'], schedule)

    def test_v4_upgrade_is_concurrent_and_cascades_user_deletion(self):
        _,token=self.register()
        with app.get_accounts_db() as conn:
            conn.execute('DROP TABLE timetable_courses')
            conn.execute('PRAGMA user_version=4');conn.commit()
        barrier=threading.Barrier(2)
        def migrate(_):
            barrier.wait()
            return self.body(app.get_timetable(self.request(cookie=token)))
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual([x['courses'] for x in pool.map(migrate,range(2))],[[],[]])
        app.add_timetable_course(self.request(cookie=token),{'id':'a1'})
        with app.get_accounts_db() as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],6)
            conn.execute('DELETE FROM users');conn.commit()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM timetable_courses').fetchone()[0],0)

    def test_personal_edits_preserve_source_identity_favorites_and_other_accounts(self):
        _, token = self.register()
        request = self.request(cookie=token)
        original = self.body(app.add_timetable_course(request, {'id':'a1'}))['courses'][0]
        app.add_favorite(request, {'id':'a1'})
        public = app.get_course_detail('a1', 'zh')
        _, other_token = self.register(username='Other_01', ip='203.0.113.19')
        other = self.request(cookie=other_token)
        app.add_timetable_course(other, {'id':'a1'})
        changes = {'course_name':'我的数学课', 'teacher':'个人教师', 'classroom':'个人教室', 'notes':'仅自己可见',
                   'sessions':[{'day':7,'start':13,'end':14,'weeks':[0,1,3],'parity':'每周'}]}
        edited = self.body(app.update_timetable_course(request, {'course_key':original['course_key'], 'changes':changes}))['courses'][0]
        self.assertTrue(edited['is_edited'])
        self.assertEqual(edited['teacher'], '个人教师')
        self.assertEqual(edited['sessions'][0]['weeks'], [0,1,3])
        for key in ('id','fav_key','course_key','course_code','added_at'):
            self.assertEqual(edited[key], original[key])
        self.assertEqual(app.get_course_detail('a1','zh'), public)
        self.assertEqual(self.body(app.get_timetable(other))['courses'][0]['course_name'], original['course_name'])
        account = self.body(app.get_account(request))
        self.assertNotEqual(account['favorites'][0]['teacher'], '个人教师')
        # Enrichment and duplicate adds must keep using the original teacher/key.
        again = self.body(app.add_timetable_course(request, {'id':'a1'}))['courses'][0]
        self.assertEqual(again['notes'], '仅自己可见')
        self.assertTrue(again['source_available'])
        restored = self.body(app.reset_timetable_course(request, {'course_key':original['course_key']}))['courses'][0]
        self.assertFalse(restored['is_edited'])
        self.assertEqual(restored['course_name'], original['course_name'])
        self.assertNotIn('notes', restored)

    def test_source_refresh_updates_unmodified_fields_and_preserves_overrides(self):
        _, token = self.register()
        request = self.request(cookie=token)
        item = self.body(app.add_timetable_course(request, {'id':'a1'}))['courses'][0]
        app.update_timetable_course(request, {'course_key':item['course_key'], 'changes':{'course_name':'我的名字'}})
        original_enrich = app._enrich_saved_courses
        def source_update(items):
            original_enrich(items)
            for item in items: item.update(course_name='新的公共名称', classroom='新的公共教室')
        with patch.object(app, '_enrich_saved_courses', source_update):
            updated = self.body(app.get_timetable(request))['courses'][0]
            self.assertEqual(updated['course_name'], '我的名字')
            self.assertEqual(updated['classroom'], '新的公共教室')
            reset = self.body(app.reset_timetable_course(request, {'course_key':item['course_key']}))['courses'][0]
            self.assertEqual(reset['course_name'], '新的公共名称')

    def custom_payload(self, nonce='a' * 32):
        return {'request_id':nonce, 'course':{'term':'fall','course_name':'自定义研讨课 <实验>', 'classroom':'理教101',
                'sessions':[{'day':1,'start':1,'end':2,'weeks':[0,1,2,3,4],'parity':'双周'},
                            {'day':7,'start':14,'end':14,'weeks':None,'parity':'单周'}]}}

    def test_custom_courses_persist_retry_safely_edit_move_and_remove(self):
        _, token = self.register()
        request = self.request(cookie=token)
        payload = self.custom_payload()
        response = app.create_custom_timetable_course(request, payload)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        item = self.body(response)['courses'][0]
        self.assertTrue(item['is_custom'])
        self.assertFalse(item['source_available'])
        self.assertTrue(item['available'])
        self.assertEqual(item['sessions'][0]['weeks'], [0,2,4])
        self.assertEqual(item['sessions'][1]['day'], 7)
        self.assertFalse(item['unparsed'])
        app.create_custom_timetable_course(request, payload)
        self.assertEqual(len(self.body(app.get_timetable(request))['courses']), 1)
        self.assertEqual(self.body(app.get_account(request))['favorites'], [])
        changed = self.body(app.update_timetable_course(request, {'course_key':item['course_key'], 'changes':{'term':'summer','teacher':'自定义老师','sessions':[],'notes':'待定安排'}}))['courses'][0]
        self.assertEqual(changed['term'], 'summer')
        self.assertEqual(changed['term_label'], app._term_label('summer'))
        self.assertEqual(changed['sessions'], [])
        self.assertTrue(changed['unparsed'])
        self.assertEqual(self.body(app.get_timetable(request))['courses'][0]['notes'], '待定安排')
        with self.assertRaises(app.HTTPException) as ctx:
            app.reset_timetable_course(request, {'course_key':item['course_key']})
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(self.body(app.remove_timetable_course(request, {'course_key':item['course_key']}))['courses'], [])

    def test_custom_and_source_courses_share_limit_and_updates_cannot_resurrect_removed_rows(self):
        _, token = self.register()
        request = self.request(cookie=token)
        with patch.object(app,'TIMETABLE_LIMIT',2):
            app.add_timetable_course(request, {'id':'a1'})
            item = self.body(app.create_custom_timetable_course(request, self.custom_payload()))['courses'][-1]
            app.create_custom_timetable_course(request, self.custom_payload())
            for call in (lambda:app.create_custom_timetable_course(request, self.custom_payload('b'*32)),
                         lambda:app.add_timetable_course(request, {'id':'s1'})):
                with self.assertRaises(app.HTTPException) as ctx: call()
                self.assertEqual(ctx.exception.status_code,409)
        app.remove_timetable_course(request, {'course_key':item['course_key']})
        with self.assertRaises(app.HTTPException) as ctx:
            app.update_timetable_course(request, {'course_key':item['course_key'],'changes':{'notes':'late'}})
        self.assertEqual(ctx.exception.status_code,404)

    def test_personal_writes_require_login_origin_ownership_and_rate_limit(self):
        _, token = self.register()
        request = self.request(cookie=token)
        item = self.body(app.add_timetable_course(request, {'id':'a1'}))['courses'][0]
        _, other_token = self.register(username='Other_01', ip='203.0.113.19')
        operations = [(app.create_custom_timetable_course, self.custom_payload()),
                      (app.update_timetable_course, {'course_key':item['course_key'],'changes':{'notes':'private'}}),
                      (app.reset_timetable_course, {'course_key':item['course_key']})]
        for operation, payload in operations:
            for req, status in [(self.request(),401), (self.request(cookie=token,origin='https://evil.example'),403)]:
                with self.assertRaises(app.HTTPException) as ctx: operation(req, payload)
                self.assertEqual(ctx.exception.status_code,status)
            with patch.object(app, '_enforce_rate_limit', side_effect=app.HTTPException(status_code=429)):
                with self.assertRaises(app.HTTPException) as ctx: operation(request, payload)
                self.assertEqual(ctx.exception.status_code,429)
        for operation, payload in operations[1:]:
            with self.assertRaises(app.HTTPException) as ctx: operation(self.request(cookie=other_token),payload)
            self.assertEqual(ctx.exception.status_code,404)
        self.assertFalse(self.body(app.get_timetable(request))['courses'][0]['is_edited'])

    def test_edits_can_reschedule_missing_source_without_reusing_public_identity(self):
        _, token = self.register()
        request = self.request(cookie=token)
        item = self.body(app.add_timetable_course(request, {'id':'a1'}))['courses'][0]
        def missing(items):
            for source in items: source['available'] = False
        with patch.object(app, '_enrich_saved_courses', missing):
            updated = self.body(app.update_timetable_course(request, {'course_key':item['course_key'], 'changes':{'sessions':[{'day':3,'start':2,'end':3,'weeks':[1]}]}}))['courses'][0]
            self.assertTrue(updated['available'])
            self.assertFalse(updated['source_available'])
            self.assertFalse(self.body(app.reset_timetable_course(request, {'course_key':item['course_key']}))['courses'][0]['available'])

    def test_v5_migration_preserves_courses_under_concurrent_first_access(self):
        _, token = self.register()
        request = self.request(cookie=token)
        before = self.body(app.add_timetable_course(request, {'id':'a1'}))['courses'][0]
        with app.get_accounts_db() as conn:
            conn.execute('ALTER TABLE timetable_courses DROP COLUMN customization')
            conn.execute('ALTER TABLE timetable_courses DROP COLUMN is_custom')
            conn.execute('PRAGMA user_version=5'); conn.commit()
        barrier = threading.Barrier(2)
        def migrate(_):
            barrier.wait()
            return self.body(app.get_timetable(request))['courses'][0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(migrate,range(2))), [before,before])
        app.create_custom_timetable_course(request,self.custom_payload())
        with app.get_accounts_db() as conn:
            conn.execute('DELETE FROM users'); conn.commit()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM timetable_courses').fetchone()[0],0)

class TimetableParserTests(unittest.TestCase):
    def test_custom_field_and_slot_validation_rejects_spoofed_or_invalid_data(self):
        for changes in [{'id':'a1'}, {'course_key':'x'}, {'is_custom':True}, {'available':True}, {'course_name':' '},
                        {'course_name':'x'*201}, {'notes':'\ud800'}, {'classroom':'\x00'}, {'teacher':[]}, {'term':'fall'},
                        {'sessions':[], 'schedule':'时间待定'}, {'sessions':[{}]},
                        {'sessions':[{'day':True,'start':1,'end':2}]}, {'sessions':[{'day':8,'start':1,'end':2}]},
                        {'sessions':[{'day':1,'start':4,'end':2}]}, {'sessions':[{'day':1,'start':1,'end':15}]},
                        {'sessions':[{'day':1,'start':1,'end':2,'weeks':[]}]},
                        {'sessions':[{'day':1,'start':1,'end':2,'weeks':[31]}]},
                        {'sessions':[{'day':1,'start':1,'end':2,'weeks':[False]}]},
                        {'sessions':[{'day':1,'start':1,'end':2,'weeks':[2,4],'parity':'单周'}]},
                        {'sessions':[{'day':1,'start':1,'end':2}]*13}]:
            with self.subTest(changes=repr(changes)):
                with self.assertRaises(app.HTTPException) as ctx: app._timetable_changes(changes)
                self.assertEqual(ctx.exception.status_code,422)
        with self.assertRaises(app.HTTPException): app._timetable_changes({'term':[]},custom=True)
        self.assertEqual(app._timetable_changes({'schedule':'时间另行通知'})['schedule'], '时间另行通知')

    def test_multiple_slots_week_ranges_and_parity(self):
        result=app._timetable_sessions('1~16周 单周周一3~4节\n2~16周 双周周四10~11节')
        self.assertFalse(result['unparsed'])
        self.assertEqual(result['sessions'][0]['weeks'],list(range(1,17,2)))
        self.assertEqual(result['sessions'][1]['weeks'],list(range(2,17,2)))
        self.assertEqual(result['sessions'][1]['day'],4)
        self.assertEqual(result['sessions'][1]['start'],10)
    def test_unknown_times_and_invalid_ranges_are_not_invented(self):
        for value in ['', '时间另行通知','周一15~16节','16~1周 每周周一1~2节']:
            self.assertTrue(app._timetable_sessions(value)['unparsed'])
        partial=app._timetable_sessions('1~16周 每周周一1~2节 周二时间待定')
        self.assertTrue(partial['unparsed'])
        single=app._timetable_sessions('1,3,5周 周日2节')['sessions'][0]
        self.assertEqual(single['weeks'],[1,3,5]);self.assertEqual(single['end'],2)
