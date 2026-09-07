import json
import sqlite3
import threading
import unittest
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
            payload=self.body(app.add_favorite(request,{'id':cid}))
            item=next(i for i in payload['favorites'] if i['fav_key']==app._favorite_snapshot(cid)['fav_key'])
            for field in ['classroom','schedule','department','course_name']:
                self.assertEqual(item[field],detail[field])
            self.assertIn('course_type',item)

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
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],5)
            conn.execute('DELETE FROM users');conn.commit()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM timetable_courses').fetchone()[0],0)

class TimetableParserTests(unittest.TestCase):
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
