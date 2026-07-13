import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from 数据库构建脚本.build_treehole_reviews import (
    CourseMatcher,
    EntityHighlighter,
    analyze_thread,
    build_review_database,
    enrich_existing_database,
    enrich_thread_replies_database,
    mine_entity_aliases,
    sanitize_text,
)


class CourseMatcherTests(unittest.TestCase):
    def setUp(self):
        self.matcher = CourseMatcher.from_rows(
            [
                ("00100001", "博弈论"),
                ("00100002", "足球"),
                ("00100003", "非线性光学"),
                ("00100004", "光学"),
                ("00100005", "统计学"),
            ]
        )

    def test_longest_course_name_wins_over_overlapping_short_name(self):
        self.assertEqual(
            self.matcher.match("非线性光学的作业和考试都不轻松"),
            {"非线性光学"},
        )

    def test_short_course_name_requires_explicit_course_context(self):
        self.assertEqual(self.matcher.match("今天和朋友踢足球"), set())
        self.assertEqual(
            self.matcher.match("足球课老师会点名吗", allow_short=True),
            {"足球"},
        )

    def test_eight_digit_course_code_maps_back_to_course_name(self):
        self.assertEqual(
            self.matcher.match("求问00100001的给分情况"),
            {"博弈论"},
        )

    def test_contextual_matching_distinguishes_subject_from_course(self):
        self.assertEqual(
            self.matcher.match_contextual("从统计学上来说，这个结论并不奇怪"),
            set(),
        )
        self.assertEqual(
            self.matcher.match_contextual("统计学这门课作业量和给分怎么样"),
            {"统计学"},
        )


class EntityHighlighterTests(unittest.TestCase):
    def setUp(self):
        self.highlighter = EntityHighlighter.from_rows(
            [
                ("博弈论", "张三(教授),Alice Smith(外聘)"),
                ("非线性光学", "李四(副教授)"),
                ("光学", "王五(讲师)"),
                ("政治经济学（下）", "赵六(教授)"),
            ]
        )

    def highlighted_text(self, text, courses):
        return [
            (text[item["start_offset"]:item["end_offset"]], item["entity_type"])
            for item in self.highlighter.match(text, courses)
        ]

    def test_courses_and_associated_teachers_get_distinct_highlights(self):
        text = "博弈论由张三老师和 Alice Smith 授课，作业不多"
        self.assertEqual(
            self.highlighted_text(text, {"博弈论"}),
            [("博弈论", "course"), ("张三", "teacher"), ("Alice Smith", "teacher")],
        )

    def test_teacher_requires_course_relationship_or_teacher_context(self):
        self.assertEqual(self.highlighted_text("张三同学回答得很好", set()), [])
        self.assertEqual(
            self.highlighted_text("张三老师讲得很好", set()),
            [("张三", "teacher")],
        )

    def test_longest_course_name_wins_and_offsets_follow_stored_text(self):
        self.assertEqual(
            self.highlighted_text("非线性光学很有收获", {"非线性光学", "光学"}),
            [("非线性光学", "course")],
        )
        self.assertEqual(
            self.highlighted_text("政治经济学下给分不错", {"政治经济学（下）"}),
            [("政治经济学下", "course")],
        )
        self.assertEqual(
            self.highlighted_text("推荐👍博弈论，张三老师很好", {"博弈论"}),
            [("博弈论", "course"), ("张三", "teacher")],
        )

    def test_alias_mining_pairs_grouped_initials_and_rejects_common_noise(self):
        highlighter = EntityHighlighter.from_rows(
            [
                ("软件设计实践", "谢涛,马郓"),
                ("普通生物学(C)", "罗述金(教授)"),
                ("经济学", "刘俏,王辉,陈玉宇"),
            ]
        )
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript(
                """
                CREATE TABLE entries(entry_key TEXT PRIMARY KEY, content TEXT NOT NULL);
                CREATE TABLE entry_courses(entry_key TEXT NOT NULL, course_name TEXT NOT NULL);
                """
            )
            conn.executemany(
                "INSERT INTO entries VALUES (?, ?)",
                [
                    ("p1", "软件设计实践/软设 谢涛，马郓 xt my"),
                    ("p2", "普通生物学(C)/普生C 罗述金 ppt sky"),
                    ("p3", "软件设计实践/软设 谢涛，马郓 xt my"),
                    ("p4", "普通生物学(C)/普生C 罗述金 ppt sky"),
                    ("p5", "经济学 刘俏 王辉 陈玉宇 lq wh cyy"),
                    ("p6", "经济学 刘俏 王辉 陈玉宇 lq wh cyy"),
                ],
            )
            conn.executemany(
                "INSERT INTO entry_courses VALUES (?, ?)",
                [
                    ("p1", "软件设计实践"),
                    ("p2", "普通生物学(C)"),
                    ("p3", "软件设计实践"),
                    ("p4", "普通生物学(C)"),
                    ("p5", "经济学"),
                    ("p6", "经济学"),
                ],
            )
            records = mine_entity_aliases(conn, highlighter)

        relations = {
            (alias, entity_type, canonical_name)
            for alias, _normalized, entity_type, canonical_name, _count in records
        }
        self.assertIn(("软设", "course", "软件设计实践"), relations)
        self.assertIn(("普生C", "course", "普通生物学(C)"), relations)
        self.assertIn(("xt", "teacher", "谢涛"), relations)
        self.assertIn(("my", "teacher", "马郓"), relations)
        self.assertIn(("lq", "teacher", "刘俏"), relations)
        self.assertIn(("wh", "teacher", "王辉"), relations)
        self.assertIn(("cyy", "teacher", "陈玉宇"), relations)
        self.assertNotIn(("lq", "teacher", "陈玉宇"), relations)
        self.assertNotIn(("ppt", "teacher", "罗述金"), relations)
        self.assertNotIn(("sky", "teacher", "罗述金"), relations)


class ReviewClassificationTests(unittest.TestCase):
    def setUp(self):
        self.matcher = CourseMatcher.from_rows(
            [
                ("00100001", "博弈论"),
                ("00100002", "地震概论"),
                ("00100003", "足球"),
                ("00100004", "操作系统"),
                ("00100005", "听觉文化与世界文明"),
                ("00100006", "实习"),
                ("00100007", "学位论文"),
                ("00100008", "人工智能"),
                ("00100009", "高等数学"),
                ("00100010", "政治经济学（下）"),
                ("00100011", "信号与系统"),
                ("00100012", "生物化学"),
                ("00100013", "生理学"),
                ("00100014", "户外探索"),
            ]
        )

    def test_relevant_reply_inherits_single_course_from_post(self):
        result = analyze_thread(
            {
                "pid": 100,
                "text": "求测评博弈论，想知道作业量和给分",
                "timestamp": 1_700_000_000,
                "url": "https://treehole.pku.edu.cn/p/100",
                "comments": [
                    {
                        "cid": 101,
                        "floor": 1,
                        "text": "老师讲得不错，作业不多，期末闭卷，给分很好",
                        "timestamp": 1_700_000_100,
                        "replyTo": None,
                    },
                    {
                        "cid": 102,
                        "floor": 2,
                        "text": "蹲蹲，同问",
                        "timestamp": 1_700_000_200,
                        "replyTo": None,
                    },
                ],
            },
            self.matcher,
            "2023-11",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["courses"], ["博弈论"])
        self.assertEqual([entry["entry_key"] for entry in result["entries"]], ["p100", "c101"])
        self.assertEqual(result["entries"][1]["courses"], ["博弈论"])
        self.assertEqual(
            [reply["content"] for reply in result["all_replies"]],
            ["老师讲得不错，作业不多，期末闭卷，给分很好", "蹲蹲，同问"],
        )

    def test_reply_requires_evaluation_information_not_only_a_follow_up(self):
        result = analyze_thread(
            {
                "pid": 103,
                "text": "求测评博弈论，想知道作业量和给分",
                "timestamp": 1_700_000_000,
                "comments": [
                    {
                        "cid": 104,
                        "floor": 1,
                        "text": "老师上课会不会比较严？",
                        "timestamp": 1_700_000_100,
                    },
                    {
                        "cid": 105,
                        "floor": 2,
                        "text": "给分有多好？",
                        "timestamp": 1_700_000_200,
                    },
                    {
                        "cid": 106,
                        "floor": 3,
                        "text": "等老师回复吧",
                        "timestamp": 1_700_000_300,
                    },
                    {
                        "cid": 107,
                        "floor": 4,
                        "text": "去年给分很好，每周一次作业，期末开卷",
                        "timestamp": 1_700_000_400,
                    },
                ],
            },
            self.matcher,
            "2023-11",
        )

        self.assertEqual(
            [entry["entry_key"] for entry in result["entries"]],
            ["p103", "c107"],
        )

    def test_unrelated_daily_chat_is_not_a_course_review(self):
        result = analyze_thread(
            {
                "pid": 200,
                "text": "今天和同学踢足球，晚上吃什么",
                "timestamp": 1_700_000_000,
                "comments": [],
            },
            self.matcher,
            "2023-11",
        )
        self.assertIsNone(result)

    def test_course_group_and_teaching_logistics_are_not_reviews(self):
        for pid, text in (
            (210, "请问听觉文化与世界文明课程群在哪里加"),
            (211, "听觉文化与世界文明老师今天布置的作业题目是什么"),
            (212, "听觉文化与世界文明第一节课讲了什么，迟到了没听见"),
            (213, "听觉文化与世界文明签到了，起晚了不知道现在去还来得及不"),
            (214, "速成法语今天又签到嘛"),
            (215, "政治经济学下为什么我的期中作业邮件还显示未读"),
            (216, "信号与系统老师布置作业用的教材是第几版呢"),
            (217, "生物化学期末出分了sky"),
            (219, "今天院里有事，听觉文化与世界文明的作业和签到谁能帮看一下"),
            (255, "考古学通论明天会不会签到"),
            (256, "健美操来晚了没赶上签到怎么办"),
            (257, "太极拳什么时候考试，具体是几点"),
            (258, "机器学习考试是下午三点还是三点十分"),
            (259, "期末考试范围是整本书吗，要重点复习哪几章"),
            (260, "课程群没进，老师通知的期末考试时间是什么"),
            (261, "这门课迟迟还没出分，助教登分为什么拖延"),
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    analyze_thread(
                        {"pid": pid, "text": text, "timestamp": 1_700_000_000},
                        self.matcher,
                        "2023-11",
                    )
                )

    def test_general_attendance_method_is_course_review_information(self):
        result = analyze_thread(
            {
                "pid": 218,
                "text": "听觉文化与世界文明这门课往年怎么签到，考勤占比高吗？",
                "timestamp": 1_700_000_000,
                "comments": [],
            },
            self.matcher,
            "2023-11",
        )
        self.assertIsNotNone(result)

    def test_non_course_assessment_and_internship_ad_are_not_reviews(self):
        for pid, text in (
            (220, "北京大学计算机学院夏令营专业知识测评押题卷，包含操作系统考试题"),
            (221, "需要实习的同学看过来，欢迎评价含金量，推荐参加暑期夏令营"),
            (222, "求推荐给高一学生上课的竞赛辅导老师"),
            (223, "学位论文已经收录，发现引用页码有误还能重新上传吗"),
            (224, "社招岗位要求熟悉人工智能安全，欢迎投递简历"),
            (225, "毕业出书，高等数学教材九成新，20r一本，有意向留言"),
            (226, "选课系统只会给本学期的课推荐吗，其他学期专业课不行吗"),
            (227, "又到一年选课时，户外探索测评洞 8302252"),
            (228, "7039291 本学期课程测评更新完毕，收获了意想不到的高分"),
            (229, "求问夏令营专业课应该复习到什么程度，期末考试题都会做吗"),
            (
                230,
                "好羡慕医学生，能学生理学、生物化学，也能参加资格考试，"
                "毕业后还要轮转实习和熬夜写病历。",
            ),
            (231, "教学网的课程想自己录制下来，有什么快速办法，推荐好的方式"),
            (232, "考研想问专业课考题形式、复习范围、历年真题和导师分数线"),
            (233, "暑校课程开始后还能退课吗，发给教务老师的邮件还没有回复"),
            (234, "课程已经添加到选课计划，点击预选后列表里却没有这门课"),
            (235, "等级制通识课参与排名时怎么换算成百分制，A-按多少分加权"),
            (236, "请问在哪里可以查看课程均分和中位数"),
            (237, "下学期想选东方文学，求一个签到提醒，可有偿"),
            (238, "雅思写作资料和课程推荐，只有一个月准备，目标小分7"),
            (239, "求信号与系统课程PPT和考试资料，有偿"),
            (240, "课程表美化浏览器插件发布，支持导出课表和显示期末安排"),
            (241, "应用物理培养方向前景如何，保研情况和就业情况怎么样"),
            (242, "学校总学分要求太高，每学期课程和论文考试都太多"),
            (243, "想找互助搭子轮流帮签到，期末一起复习"),
            (244, "求问英语词汇与英美文化的课程回放在哪里看"),
            (245, "建议学院加入中期课程测评，及时听取学生意见"),
            (246, "医学部选课有人退课后，名额会统一放出还是即退即空"),
            (247, "普物一能替代力学吗，参加电磁学免修考试后还要补修吗"),
            (248, "考研想问笔试参考资料、复试问题、跨考难度和招生方向"),
            (249, "影视文化与批评建群了吗，有一起上课的同学吗，这课签到吗"),
            (250, "戏曲与中国传统文化截止到现在有签到嘛"),
            (251, "这门课快点出分，我想看大家之后写的课程评价"),
            (252, "关于课程测评的态度：课程测评可以参考，但需要慎重看待"),
            (253, "想给初中生找教辅或者B站课程资源，请大家推荐"),
            (254, "如果我想在大四暑假找一份量化实习，需要做些什么准备"),
            (262, "每学期退课记W会不会影响留学申请和保研"),
            (263, "绩点改革后排名怎么计算，有没有政策来源"),
            (264, "期末周考试安排顺序是公共课、专业课还是研究生课"),
            (265, "创新创业学院办公室招新，欢迎同学加入"),
            (266, "想找毕业论文导师，还要请老师写推荐信，教师节忘记联系了"),
            (267, "申请双学位走选课通道拿到热门课后再退双有什么影响"),
            (268, "想转院到工学院，求介绍转院难度和课程难度"),
            (269, "求推荐方便脱单的课程，明年不想再一个人过情人节"),
            (270, "收一本英语听说教材，也想问第一节课布置了什么作业"),
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    analyze_thread(
                        {"pid": pid, "text": text, "timestamp": 1_700_000_000},
                        self.matcher,
                        "2023-11",
                    )
                )

    def test_generic_course_request_keeps_only_substantive_named_reply(self):
        result = analyze_thread(
            {
                "pid": 300,
                "text": "求推荐作业少、给分好的通识课",
                "timestamp": 1_700_000_000,
                "comments": [
                    {
                        "cid": 301,
                        "floor": 1,
                        "text": "推荐地震概论，老师讲得清楚，作业少，给分也不错",
                        "timestamp": 1_700_000_100,
                        "replyTo": None,
                    },
                    {
                        "cid": 302,
                        "floor": 2,
                        "text": "谢谢！",
                        "timestamp": 1_700_000_200,
                        "replyTo": None,
                    },
                ],
            },
            self.matcher,
            "2023-11",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["courses"], ["地震概论"])
        self.assertEqual([entry["entry_key"] for entry in result["entries"]], ["p300", "c301"])

    def test_generic_review_request_without_catalog_course_is_kept(self):
        result = analyze_thread(
            {
                "pid": 310,
                "text": "求法学院专业课课程测评，想听听大家的体验",
                "timestamp": 1_700_000_000,
                "comments": [],
            },
            self.matcher,
            "2023-11",
        )
        self.assertIsNotNone(result)

    def test_full_review_with_previous_thread_pointer_is_kept(self):
        result = analyze_thread(
            {
                "pid": 311,
                "text": (
                    "继续更新本学期课程测评，往期测评见 7654321。"
                    "这学期修了博弈论和地震概论，后续会分别介绍老师的教学风格、"
                    "作业量、考核方式和给分，课程很多所以正文会比较长，欢迎点课。"
                ),
                "timestamp": 1_700_000_000,
                "comments": [],
            },
            self.matcher,
            "2023-11",
        )
        self.assertIsNotNone(result)

    def test_substantive_untagged_course_criticism_is_kept(self):
        result = analyze_thread(
            {
                "pid": 312,
                "text": (
                    "这门课是我本学期最后悔选的课，评分很离谱，助教和教授也不负责。"
                    "花了很多时间完成任务，最后分数仍然很低，不推荐大家选。"
                ),
                "timestamp": 1_700_000_000,
                "comments": [],
            },
            self.matcher,
            "2023-11",
        )
        self.assertIsNotNone(result)

    def test_substantive_review_can_mention_course_group(self):
        result = analyze_thread(
            {
                "pid": 313,
                "text": (
                    "这门课临时更改评分方式，把签到和作业改成课堂小测，"
                    "课程群里一直没有说明评分标准，任务量和考核压力都明显增加。"
                ),
                "timestamp": 1_700_000_000,
                "comments": [],
            },
            self.matcher,
            "2023-11",
        )
        self.assertIsNotNone(result)

    def test_contact_information_is_redacted(self):
        text = "课程资料加微信 course_helper88，手机 13812345678，邮箱 a@b.com，q463909082"
        cleaned = sanitize_text(text)
        self.assertNotIn("course_helper88", cleaned)
        self.assertNotIn("13812345678", cleaned)
        self.assertNotIn("a@b.com", cleaned)
        self.assertNotIn("q463909082", cleaned)
        self.assertNotIn("\x00", sanitize_text("博弈论\x00给分很好"))
        self.assertIn("[联系方式已隐藏]", cleaned)

    def test_invalid_comment_container_fails_instead_of_losing_snapshot_replies(self):
        with self.assertRaisesRegex(ValueError, "invalid comments for treehole 400"):
            analyze_thread(
                {
                    "pid": 400,
                    "text": "求测评博弈论，想知道作业量和给分",
                    "timestamp": 1_700_000_000,
                    "comments": {},
                },
                self.matcher,
                "2023-11",
            )


class ReviewDatabaseTests(unittest.TestCase):
    def test_tiny_shard_builds_privacy_minimized_search_database(self):
        matcher = CourseMatcher.from_rows(
            [
                ("00100001", "博弈论"),
                ("00100002", "地震概论"),
                ("00100003", "足球"),
            ]
        )
        highlighter = EntityHighlighter.from_rows(
            [
                ("博弈论", "张三(教授)"),
                ("地震概论", "李四(副教授)"),
                ("足球", "王五(讲师)"),
            ]
        )
        shard = {
            "version": 2,
            "key": "2023-11",
            "posts": {
                "100": {
                    "pid": 100,
                    "text": "求测评博弈论，想知道作业量和给分",
                    "timestamp": 1_700_000_000,
                    "url": "https://treehole.pku.edu.cn/p/100",
                    "authorTag": "must-not-leak",
                    "comments": [
                        {
                            "cid": 101,
                            "floor": 1,
                            "text": "老师讲得不错，作业少，给分很好，微信 course_helper88",
                            "timestamp": 1_700_000_100,
                            "replyTo": None,
                            "authorTag": "also-private",
                            "authorLabel": "洞主",
                        },
                        {
                            "cid": 102,
                            "floor": 2,
                            "text": "蹲，同问",
                            "timestamp": 1_700_000_200,
                            "replyTo": None,
                        },
                    ],
                },
                "200": {
                    "pid": 200,
                    "text": "今天和朋友踢足球",
                    "timestamp": 1_700_000_000,
                    "comments": [],
                },
                "300": {
                    "pid": 300,
                    "text": "求推荐作业少、给分好的通识课",
                    "timestamp": 1_700_000_000,
                    "comments": [
                        {
                            "cid": 301,
                            "floor": 1,
                            "text": "地震概论老师讲得好，作业少，推荐",
                            "timestamp": 1_700_000_100,
                            "replyTo": None,
                        }
                    ],
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            shard_path = Path(tmp) / "2023-11.json"
            target = Path(tmp) / "reviews.db"
            shard_path.write_text(json.dumps(shard, ensure_ascii=False), encoding="utf-8")
            build_review_database(
                shard_paths=[shard_path],
                target=target,
                matcher=matcher,
                highlighter=highlighter,
                snapshot_date="2026-07-13",
            )

            with closing(sqlite3.connect(target)) as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                self.assertIsNone(conn.execute("PRAGMA foreign_key_check").fetchone())
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 4)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM thread_replies").fetchone()[0],
                    3,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT GROUP_CONCAT(course_name, ',') FROM thread_courses WHERE pid=100"
                    ).fetchone()[0],
                    "博弈论",
                )
                stored = "\n".join(
                    row[0] for row in conn.execute("SELECT content FROM entries ORDER BY entry_key")
                )
                snapshot_replies = "\n".join(
                    row[0]
                    for row in conn.execute(
                        "SELECT content FROM thread_replies ORDER BY pid, ordinal"
                    )
                )
                metadata = dict(conn.execute("SELECT key, value FROM metadata"))
                highlights = conn.execute(
                    "SELECT entity_type, COUNT(*) FROM entry_highlights GROUP BY entity_type"
                ).fetchall()

        self.assertNotIn("must-not-leak", stored)
        self.assertNotIn("also-private", stored)
        self.assertNotIn("蹲，同问", stored)
        self.assertIn("蹲，同问", snapshot_replies)
        self.assertNotIn("course_helper88", snapshot_replies)
        self.assertIn("[联系方式已隐藏]", snapshot_replies)
        self.assertEqual(metadata["snapshot_date"], "2026-07-13")
        self.assertEqual(metadata["matched_threads"], "2")
        self.assertEqual(metadata["matched_entries"], "4")
        self.assertEqual(metadata["snapshot_replies"], "3")
        self.assertEqual(dict(highlights)["course"], 2)
        self.assertEqual(metadata["course_highlights"], "2")

    def test_existing_thread_set_can_gain_all_snapshot_replies_without_reclassification(self):
        matcher = CourseMatcher.from_rows([("00100001", "博弈论")])
        shard = {
            "version": 2,
            "key": "2023-11",
            "posts": {
                "100": {
                    "pid": 100,
                    "text": "求测评博弈论，想知道作业量和给分",
                    "timestamp": 1_700_000_000,
                    "comments": [
                        {
                            "cid": 101,
                            "floor": 1,
                            "text": "老师讲得不错，作业少，给分很好",
                            "timestamp": 1_700_000_100,
                        },
                        {
                            "cid": 102,
                            "floor": 2,
                            "text": "蹲，同问",
                            "timestamp": 1_700_000_200,
                        },
                    ],
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            shards = source / "shards"
            shards.mkdir(parents=True)
            shard_path = shards / "2023-11.json"
            shard_path.write_text(json.dumps(shard, ensure_ascii=False), encoding="utf-8")
            (source / "manifest.json").write_text(
                json.dumps({"snapshotDate": "2026-07-13"}),
                encoding="utf-8",
            )
            target = Path(tmp) / "reviews.db"
            build_review_database(
                shard_paths=[shard_path],
                target=target,
                matcher=matcher,
                snapshot_date="2026-07-13",
            )
            with closing(sqlite3.connect(target)) as conn:
                before_entries = conn.execute(
                    "SELECT entry_key, content FROM entries ORDER BY entry_key"
                ).fetchall()
                conn.execute("DROP TABLE thread_replies")
                conn.execute("DELETE FROM metadata WHERE key='snapshot_replies'")
                conn.commit()

            stats = enrich_thread_replies_database(source, target)
            with closing(sqlite3.connect(target)) as conn:
                after_entries = conn.execute(
                    "SELECT entry_key, content FROM entries ORDER BY entry_key"
                ).fetchall()
                snapshot_replies = conn.execute(
                    "SELECT content FROM thread_replies ORDER BY ordinal"
                ).fetchall()
                metadata = dict(conn.execute("SELECT key, value FROM metadata"))

        self.assertEqual(stats, {"threads": 1, "snapshot_replies": 2})
        self.assertEqual(before_entries, after_entries)
        self.assertEqual(
            snapshot_replies,
            [
                ("老师讲得不错，作业少，给分很好",),
                ("蹲，同问",),
            ],
        )
        self.assertEqual(metadata["snapshot_replies"], "2")

    def test_snapshot_reply_enrichment_failure_keeps_existing_database_bytes(self):
        matcher = CourseMatcher.from_rows([("00100001", "博弈论")])
        shard = {
            "version": 2,
            "key": "2023-11",
            "posts": {
                "100": {
                    "pid": 100,
                    "text": "求测评博弈论，想知道作业量和给分",
                    "timestamp": 1_700_000_000,
                    "comments": [],
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            shards = source / "shards"
            shards.mkdir(parents=True)
            shard_path = shards / "2023-11.json"
            shard_path.write_text(json.dumps(shard, ensure_ascii=False), encoding="utf-8")
            (source / "manifest.json").write_text(
                json.dumps({"snapshotDate": "2026-07-13"}),
                encoding="utf-8",
            )
            target = Path(tmp) / "reviews.db"
            build_review_database(
                shard_paths=[shard_path],
                target=target,
                matcher=matcher,
                snapshot_date="2026-07-13",
            )
            before = target.read_bytes()
            shard["posts"] = {}
            shard_path.write_text(json.dumps(shard, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "missing 1 review threads"):
                enrich_thread_replies_database(source, target)

            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_existing_database_can_be_atomically_enriched(self):
        matcher = CourseMatcher.from_rows([("00100001", "普通生物学(C)")])
        highlighter = EntityHighlighter.from_rows(
            [("普通生物学(C)", "罗述金(教授)")]
        )
        shard = {
            "version": 2,
            "key": "2023-11",
            "posts": {
                "100": {
                    "pid": 100,
                    "text": "普通生物学(C)/普生C 罗述金 lsj 老师讲得很好，作业少，给分不错",
                    "timestamp": 1_700_000_000,
                    "comments": [],
                },
                "101": {
                    "pid": 101,
                    "text": "普通生物学(C)/普生C 罗述金 lsj 老师讲得很好，作业少，给分不错",
                    "timestamp": 1_700_000_100,
                    "comments": [],
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            shard_path = Path(tmp) / "2023-11.json"
            target = Path(tmp) / "reviews.db"
            shard_path.write_text(json.dumps(shard, ensure_ascii=False), encoding="utf-8")
            build_review_database(
                shard_paths=[shard_path],
                target=target,
                matcher=matcher,
                snapshot_date="2026-07-13",
            )
            before_mode = target.stat().st_mode
            stats = enrich_existing_database(target, highlighter)
            self.assertEqual(target.stat().st_mode, before_mode)
            with closing(sqlite3.connect(target)) as conn:
                stored = conn.execute(
                    "SELECT substr(e.content, h.start_offset + 1, "
                    "h.end_offset - h.start_offset), h.entity_type, h.match_kind "
                    "FROM entry_highlights h JOIN entries e USING(entry_key) "
                    "WHERE h.entry_key='p100' "
                    "ORDER BY h.start_offset"
                ).fetchall()
                aliases = conn.execute(
                    "SELECT alias, normalized_alias, entity_type, canonical_name, evidence_count "
                    "FROM entity_aliases ORDER BY entity_type, normalized_alias"
                ).fetchall()
                metadata = dict(conn.execute("SELECT key, value FROM metadata"))

        self.assertEqual(stats["course_highlights"], 4)
        self.assertEqual(stats["teacher_highlights"], 4)
        self.assertEqual(stats["course_aliases"], 1)
        self.assertEqual(stats["teacher_aliases"], 1)
        self.assertEqual(
            stored,
            [
                ("普通生物学(C)", "course", "full"),
                ("普生C", "course", "alias"),
                ("罗述金", "teacher", "full"),
                ("lsj", "teacher", "alias"),
            ],
        )
        self.assertEqual(
            aliases,
            [
                ("普生C", "普生c", "course", "普通生物学(C)", 2),
                ("lsj", "lsj", "teacher", "罗述金", 2),
            ],
        )
        self.assertEqual(metadata["highlight_version"], "2")
        self.assertEqual(metadata["teacher_alias_highlights"], "2")


if __name__ == "__main__":
    unittest.main()
