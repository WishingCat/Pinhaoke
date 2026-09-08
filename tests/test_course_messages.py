import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import app
import test_message_replies as message_tests
from tests.test_app import _account_request


class CourseMessageTests(unittest.TestCase):
    setUp = message_tests.MessageRepliesAndNicknameTests.setUp
    register = message_tests.MessageRepliesAndNicknameTests.register

    def listing(self, course_id='a1', before_id=0, page_size=5):
        response = app.list_course_messages(course_id, before_id, page_size)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        return json.loads(response.body)

    def post(self, course_id='a1', content='补充课程信息', request=None):
        response = app.create_course_message(course_id, request or _account_request(), {'content':content, 'nickname':'spoof', 'course_key':'spoof'})
        self.assertEqual(response.status_code,201)
        return json.loads(response.body)

    def test_course_board_isolation_and_server_nickname_with_shared_replies(self):
        request=self.register()
        app.change_nickname(request, {'nickname':'课程同学'})
        board=app.create_message(_account_request(), {'content':'站点留言'})
        course=self.post(request=request)
        other=self.post('a2')
        reply=app.create_message_reply(course['id'], _account_request(), {'content':'确实有变化'})
        listing=self.listing()
        self.assertEqual(listing['total'],1)
        self.assertEqual(listing['messages'][0]['nickname'],'课程同学')
        self.assertEqual(listing['messages'][0]['reply_count'],1)
        self.assertEqual(self.listing('a2')['messages'][0]['id'],other['id'])
        self.assertEqual(app.list_messages(1,20)['messages'][0]['id'],board['id'])
        self.assertEqual(app.list_messages(1,20)['total'],1)
        self.assertEqual(reply['nickname'],'路过的 PKUer')
        self.assertEqual(set(listing['messages'][0]), {'id','posted_at','content','nickname','reply_count'})
        with app.get_messages_db() as conn:
            keys=[row[0] for row in conn.execute('SELECT course_key FROM messages WHERE id IN (?,?) ORDER BY id',(course['id'],reply['id']))]
            self.assertEqual(keys[0], keys[1]);self.assertNotEqual(keys[0],'')

    def test_stable_identity_survives_rebuilt_ids_and_teacher_change_but_not_term_or_class(self):
        detail={'course_code':'TEST','class_no':'01','teacher':'甲','course_name':'测试课程'}
        with patch.object(app,'get_course_detail',return_value=detail):
            posted=self.post('a1')
            detail['teacher']='乙'
            self.assertEqual(self.listing('a999')['messages'][0]['id'],posted['id'])
            self.assertEqual(self.listing('u1')['messages'],[])
            self.assertEqual(self.listing('r1')['messages'],[])
            with patch.object(app,'_term_label',return_value='2027秋季学期'):
                self.assertEqual(self.listing('a1')['messages'],[])
            detail['class_no']='02'
            self.assertEqual(self.listing('a1')['messages'],[])

    def test_cursor_pagination_does_not_skip_when_new_message_arrives(self):
        with patch.object(app,'MESSAGE_RATE_LIMITS',()):
            rows=[self.post(content=str(i)) for i in range(7)]
            first=self.listing(page_size=3)
            self.post(content='最新')
            second=self.listing(before_id=first['messages'][-1]['id'],page_size=4)
        self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])
        self.assertEqual([r['id'] for r in first['messages']+second['messages']], [r['id'] for r in rows][::-1])

    def test_validation_origin_and_shared_rate_limit(self):
        for cid,code in [('bad',422),('a99999999',404)]:
            with self.assertRaises(app.HTTPException) as ctx: self.post(cid)
            self.assertEqual(ctx.exception.status_code,code)
        with self.assertRaises(app.HTTPException) as ctx:
            self.post(request=_account_request(origin='https://evil.example'))
        self.assertEqual(ctx.exception.status_code,403)
        for content in ['', ' ', '字'*501]:
            with self.assertRaises(app.HTTPException) as ctx: self.post(content=content)
            self.assertEqual(ctx.exception.status_code,422)
        for before in [-1,True,2**64]:
            with self.assertRaises(app.HTTPException): self.listing(before_id=before)
        board=app.create_message(_account_request(),{'content':'board'})
        self.post();self.post('a2')
        app.create_message_reply(board['id'],_account_request(),{'content':'reply'})
        self.post('u1')
        with self.assertRaises(app.HTTPException) as ctx: self.post('s1')
        self.assertEqual(ctx.exception.status_code,429)

    def test_version_one_migration_preserves_existing_threads_concurrently(self):
        with sqlite3.connect(app.MESSAGES_DB_PATH) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, posted_at INTEGER NOT NULL, content TEXT NOT NULL, ip_hash TEXT NOT NULL, nickname TEXT NOT NULL, parent_id INTEGER REFERENCES messages(id))')
            conn.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?)',[(7,100,'原留言','hash','旧昵称',None),(8,101,'原回复','hash','旧昵称',7)])
            conn.execute('PRAGMA user_version=1')
        barrier=threading.Barrier(2)
        def migrate(_):
            barrier.wait()
            with app.get_messages_db() as conn:
                return conn.execute('PRAGMA user_version').fetchone()[0]
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual(list(pool.map(migrate,range(2))),[2,2])
        self.assertEqual(app.list_messages(1,20)['messages'][0]['content'],'原留言')
        self.assertEqual(app.list_message_replies(7,0,20)['replies'][0]['content'],'原回复')
        with app.get_messages_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM messages WHERE course_key=\'\'').fetchone()[0],2)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
