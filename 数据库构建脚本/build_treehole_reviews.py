#!/usr/bin/env python3
"""Extract privacy-minimized PKU Treehole course reviews into SQLite."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import tempfile
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing, suppress
from pathlib import Path

try:
    from .build_atomic import _fsync_parent_directory
except ImportError:
    from build_atomic import _fsync_parent_directory

try:
    import orjson
except ImportError:  # pragma: no cover - the stdlib fallback is intentionally supported
    orjson = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = PROJECT_ROOT / "数据库" / "树洞课程评测.db"
COURSE_DB_DIR = PROJECT_ROOT / "数据库"
COURSE_DB_GLOB = "2026*课程.db"
CLASSIFIER_VERSION = "1"
HIGHLIGHT_VERSION = "2"

HIGHLIGHT_SCHEMA = """
CREATE TABLE IF NOT EXISTS entry_highlights (
    entry_key TEXT NOT NULL REFERENCES entries(entry_key) ON DELETE CASCADE,
    start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
    entity_type TEXT NOT NULL CHECK(entity_type IN ('course', 'teacher')),
    match_kind TEXT NOT NULL CHECK(match_kind IN ('full', 'alias')),
    PRIMARY KEY(entry_key, start_offset, end_offset, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_entry_highlights_entry
ON entry_highlights(entry_key, start_offset, end_offset);

CREATE TABLE IF NOT EXISTS entity_aliases (
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('course', 'teacher')),
    canonical_name TEXT NOT NULL,
    evidence_count INTEGER NOT NULL CHECK(evidence_count > 0),
    PRIMARY KEY(normalized_alias, entity_type, canonical_name)
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_lookup
ON entity_aliases(entity_type, normalized_alias);
"""

SCHEMA = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE threads (
    pid INTEGER PRIMARY KEY,
    source_month TEXT NOT NULL,
    posted_at INTEGER NOT NULL,
    content TEXT NOT NULL,
    source_url TEXT NOT NULL,
    post_kind TEXT NOT NULL CHECK(post_kind IN ('request', 'review', 'discussion')),
    relevant_reply_count INTEGER NOT NULL CHECK(relevant_reply_count >= 0)
);

CREATE TABLE entries (
    entry_key TEXT PRIMARY KEY,
    pid INTEGER NOT NULL REFERENCES threads(pid) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('post', 'reply')),
    cid INTEGER,
    floor INTEGER,
    posted_at INTEGER NOT NULL,
    content TEXT NOT NULL,
    CHECK((kind = 'post' AND cid IS NULL AND floor IS NULL)
       OR (kind = 'reply' AND cid IS NOT NULL AND floor IS NOT NULL))
);

CREATE TABLE thread_courses (
    pid INTEGER NOT NULL REFERENCES threads(pid) ON DELETE CASCADE,
    course_name TEXT NOT NULL,
    search_name TEXT NOT NULL,
    PRIMARY KEY(pid, course_name)
);

CREATE TABLE entry_courses (
    entry_key TEXT NOT NULL REFERENCES entries(entry_key) ON DELETE CASCADE,
    course_name TEXT NOT NULL,
    PRIMARY KEY(entry_key, course_name)
);

CREATE TABLE course_catalog (
    course_name TEXT PRIMARY KEY,
    search_name TEXT NOT NULL,
    course_codes TEXT NOT NULL,
    thread_count INTEGER NOT NULL,
    entry_count INTEGER NOT NULL
);

{HIGHLIGHT_SCHEMA}

CREATE INDEX idx_threads_time ON threads(posted_at DESC, pid DESC);
CREATE INDEX idx_entries_pid ON entries(pid, kind, floor);
CREATE INDEX idx_thread_courses_name ON thread_courses(search_name, pid);
CREATE INDEX idx_entry_courses_name ON entry_courses(course_name, entry_key);
CREATE INDEX idx_catalog_search ON course_catalog(search_name);
"""

_MATCH_STRIP_RE = re.compile(r"[\s\u200b\u200c\u200d·•・_—–\-:：,，.。/\\《》<>\[\]【】()（）'\"]+")
_PAREN_RE = re.compile(r"[（(][^（）()]{1,24}[）)]")
_COURSE_CODE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_MATCH_CONTEXT_TERMS = (
    "课", "课程", "老师", "教授", "给分", "作业", "考试", "考核", "点名", "签到",
    "学分", "选修", "必修", "教材", "难度", "推荐", "避雷", "修读", "旁听",
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_CONTACT_RE = re.compile(
    r"(?i)(?:(?:微信|微\s*信|v\s*x|v信|wechat|q\s*q|扣扣)(?:号)?\s*[:：]?\s*[A-Z0-9_-]{5,}|"
    r"(?<![A-Z0-9])q[1-9]\d{4,11}(?!\d))"
)
_TEACHER_SPLIT_RE = re.compile(r"[,，、;；]+")
_TEACHER_PAREN_RE = re.compile(r"[（(]([^（）()]{1,80})[）)]")
_TEACHER_TITLE_RE = re.compile(
    r"(?:教授|讲师|研究员|工程师|高工|医师|职称|外聘|助教|教师|教练|馆员|实验师|主任)"
)
_TEACHER_CONTEXT_RE = re.compile(
    r"(?:老师|教授|讲师|教员|助教|任课|授课|主讲|先生|女士)"
)
_TEACHER_ALIAS_EXCLUSIONS = frozenset({
    "印地语", "葡萄牙语", "英语", "日语", "德语", "法语", "西班牙语",
    "编辑", "教学", "课程", "教师", "老师", "助教", "教员", "未知", "待定",
})
_ASCII_ALIAS_EXCLUSIONS = frozenset({
    "ai", "aigc", "app", "btw", "ddl", "dz", "email", "final", "gpa", "hw",
    "id", "lab", "mid", "nice", "ok", "or", "pdf", "pf", "pku", "ppt", "pre",
    "qq", "quiz", "re", "sky", "tag", "ta", "uu",
    "vip", "vx", "wechat", "wx",
})
_COURSE_ALIAS_EXCLUSIONS = frozenset({
    "本科", "大一", "大一上", "大一下", "大二", "大二上", "大二下", "大三", "大四",
    "老师", "教授", "课程", "测评", "评测", "评价", "体验", "考核", "考试", "期中",
    "期末", "作业", "给分", "推荐", "避雷", "通选课", "通识课", "专业课", "英语课",
    "体育课", "政治课", "思政课", "必修课", "选修课", "春季", "秋季", "暑期",
    "这门课", "这个课", "本课程", "任课", "授课", "主讲",
    "教员", "讲师", "助教", "同学", "洞主", "成绩", "学分", "选课", "上课",
    "体育", "英语", "社系", "经院", "数院", "工院", "光华", "元培", "国关",
    "艺术学院", "社会学系", "中文系",
})
_ALIAS_REGION_SPLIT_RE = re.compile(r"[\s/／,，、;；:：|]+")
_COURSE_ALIAS_NOISE_RE = re.compile(
    r"(?:学分|得分|分数|总评|绩点|周[一二三四五六日天]|星期|春季?|秋季?|暑期?|"
    r"学期|学年|分钟|小时|期中|期末|老师|教授|助教|同学|洞主|课程|测评|"
    r"评测|作业|考试|拿到|扣分|实验班|专业选修|通识|通选|学院|学系|院系|"
    r"英文班|中文班|[abcd]级|级课|的uu|dz)"
)
_TEACHER_INITIAL_RE = re.compile(
    r"^[\s/／,，、;；:：()（）\[\]【】<>《》\-—]*([A-Za-z]{2,8})(?![A-Za-z0-9])"
)

_EXPLICIT_REVIEW_RE = re.compile(
    r"(?:(?:课程|选课|修课|上课)[^\n。！？]{0,6}(?:测评|评测|评价|体验)|"
    r"(?:求|蹲|想看|有没有)[^\n。！？]{0,8}(?:测评|评测)|"
    r"(?:春|秋|暑|学期)[^\n。！？]{0,10}(?:课程)?(?:测评|评测)|"
    r"(?:测评|评测)[^\n。！？]{0,6}(?:合集|汇总|总结)|评课|选课建议|课程推荐|"
    r"求推荐[^\n。！？]{0,18}(?:课程|通识课|公选课|体育课|英语课|专业课)|"
    r"(?:避雷|排雷)[^\n。！？]{0,12}(?:课|课程)|"
    r"(?:这门|这个|这节)[^\n。！？]{0,10}课[^\n。！？]{0,12}(?:怎么样|如何|好不好))",
    re.IGNORECASE,
)
_COURSE_CONTEXT_RE = re.compile(
    r"(?:课程|选课|退课|补退选|通识课|公选课|专业课|体育课|英语课|思政课|"
    r"上课|老师|教授|给分|作业|考试|考核|期末|期中|点名|签到)"
)
_GENERIC_REQUEST_RE = re.compile(
    r"(?:求|请问|想问|有没有|蹲|推荐|避雷|排雷)[^\n。！？]{0,24}"
    r"(?:课程|选课|通识课?|公选课?|体育课|英语课|专业课)"
    r"|(?:课程|选课|通识课?|公选课?|体育课|英语课|专业课)"
    r"[^\n。！？]{0,18}(?:推荐|怎么样|如何|好不好)"
)
_NON_COURSE_REVIEW_RE = re.compile(
    r"(?:知识测评|能力测评|专业测评|综合测评|心理测评|入学测评|"
    r"测评押题|测评试题|夏令营[^\n。！？]{0,12}测评|"
    r"建议[^\n。！？]{0,20}(?:中期|期中)[^\n。！？]{0,10}(?:课程)?测评|"
    r"(?:关于|对于)[^\n。！？]{0,8}课程测评[^\n。！？]{0,18}(?:态度|参考|慎重))"
)
_LOGISTICS_RE = re.compile(
    r"(?:课程群|课群|有群吗|群在哪里|拉群|进群|群二维码|第一节课[^\n。！？]{0,12}(?:内容|讲了什么)|"
    r"(?:今天|本次|这次)[^\n。！？]{0,10}作业[^\n。！？]{0,8}(?:题目|要求|是什么)|"
    r"(?:今天|今日|明天|刚才|刚刚|课前|来晚|刚补选)[^\n。！？]{0,40}(?:签到|考试|作业)|"
    r"签到[^\n。！？]{0,12}(?:来得及|迟到|晚到|起晚)|"
    r"没赶上[^\n。！？]{0,8}签到|"
    r"(?:什么时候|何时|几号|几点)[^\n。！？]{0,14}考试|"
    r"考试[^\n。！？]{0,16}(?:几点|时间|日期|哪天)|"
    r"(?:期中|期末)?考试[^\n。！？]{0,20}(?:范围|重点|哪几章|具体几道|答案)|"
    r"(?:迟迟|还没|快点|赶紧)[^\n。！？]{0,12}出分|登分[^\n。！？]{0,14}(?:拖延|还没|没有)|"
    r"(?:期中|期末)?作业[^\n。！？]{0,20}(?:邮件|提交)[^\n。！？]{0,10}(?:未读|没读|收到|成功)|"
    r"(?:布置)?作业[^\n。！？]{0,24}第几版|第几版[^\n。！？]{0,24}(?:作业|习题)|"
    r"(?:期中|期末)?出分(?:了|啦|咯|没|了吗)?(?:sky)?(?:$|[\n。！？])|"
    r"等级制[^\n。！？]{0,40}(?:换算|百分制|排名|加权)|"
    r"(?:哪里|怎么)[^\n。！？]{0,12}(?:查看|查)[^\n。！？]{0,12}(?:课程)?(?:均分|中位数)|"
    r"签到提醒|有偿[^\n。！？]{0,12}签到|"
    r"课程(?:评估|评教)[^\n。！？]{0,18}(?:成绩|查看|填写|脚本)|"
    r"(?:课程回放|教学网[^\n。！？]{0,8}回放)[^\n。！？]{0,16}(?:哪里|怎么|没有|找不到)|"
    r"(?:互相|轮流|帮忙|帮|代)[^\n。！？]{0,16}签到|"
    r"(?:组队|搭子)[^\n。！？]{0,16}(?:签到|考勤)|"
    r"老师[^\n。！？]{0,8}(?:通知了什么|群里说了什么)|"
    r"选课(?:系统|网|计划)|点击预选|本学期可选列表|培养方案|学分上限|超学分|抽签|补退选|"
    r"选课[^\n。！？]{0,18}退课[^\n。！？]{0,18}名额|"
    r"名额[^\n。！？]{0,18}(?:即退即空|统一时间|放出)|"
    r"(?:免修考试|课程替代|替代[^\n。！？]{0,14}(?:补修|学分))|"
    r"(?:建群了吗|建群了没|有一起上课的[^\n。！？]{0,8}(?:同学|友友))|"
    r"截止到现在[^\n。！？]{0,16}签到|"
    r"(?:快点|赶紧)[^\n。！？]{0,8}出分[^\n。！？]{0,20}课程评价|"
    r"(?:还能|可以|怎么|何时|什么时候)[^\n。！？]{0,10}退课|"
    r"(?:开课|考试时间|课程安排)[^\n。！？]{0,10}(?:吗|哪里|怎么|何时|什么时候)|"
    r"(?:捞捞|合写|求组队)[^\n。！？]{0,12}(?:作业|论文|pre)|"
    r"(?:期中|期末)作业[^\n。！？]{0,12}(?:合写|组队))"
)
_DOMAIN_NOISE_RE = re.compile(
    r"(?:招聘|招新|岗位|投递简历|简历投递|内推|社招|校招|实习offer|算法实习生|"
    r"科研合作|同学招募|一对一辅导|家教|辅导老师|教学网[^\n。！？]{0,24}(?:录制|录屏)|"
    r"考研[^\n。！？]{0,40}专业课[^\n。！？]{0,30}(?:考题|复习|真题|导师|分数线)|"
    r"招收[^\n。！？]{0,18}(?:实习生|研究助理)|暑期夏令营|"
    r"夏令营[^\n。！？]{0,24}(?:专业课|复习|考试题)|羡慕医学生|"
    r"毕业出书|出书|卖书|收书|包邮|有意向[^\n。！？]{0,8}(?:留言|私聊)|\d+r(?:一本|一套)?|"
    r"(?:收求|求收|求购)[^\n。！？]{0,10}(?:资料|课件|往年题)|"
    r"(?:学位论文|毕业论文)[^\n。！？]{0,20}(?:上传|提交|答辩|查重|盲审|格式|引用)|"
    r"清华大学求真书院|(?:据|近日)[^\n。！？]{0,12}(?:报道|爆出))",
    re.IGNORECASE,
)
_HARD_NOISE_RE = re.compile(
    r"(?:雅思|托福)[^\n。！？]{0,28}(?:资料|课程|老师)[^\n。！？]{0,12}推荐|"
    r"(?:课程\s*ppt|课件|考试资料|往年题)[^\n。！？]{0,18}(?:有偿|收求|求购|求)|"
    r"(?:有偿|求|求购)[^\n。！？]{0,18}(?:课程\s*ppt|课件|考试资料|往年题)|"
    r"课表[^\n。！？]{0,24}(?:美化|插件|github|发布|导出)|"
    r"培养方向[^\n。！？]{0,36}(?:前景|保研|就业)|"
    r"(?:总学分|学分要求)[^\n。！？]{0,40}(?:太高|课程|论文|考试)|"
    r"考研[^\n。！？]{0,70}(?:笔试|复试|跨考|招生|参考资料|分数线|导师)|"
    r"(?:初中|高中|中考|高考)[^\n。！？]{0,50}(?:教辅|课程资源|网课|b站)|"
    r"找一份[^\n。！？]{0,18}实习[^\n。！？]{0,28}(?:需要|准备|怎么|如何)|"
    r"(?:退课|记\s*w)[^\n。！？]{0,32}(?:留学|保研|申请|影响)|"
    r"绩点[^\n。！？]{0,30}(?:怎么改|改革|政策|排名)|"
    r"期末周[^\n。！？]{0,24}考试[^\n。！？]{0,18}(?:顺序|安排)|"
    r"(?:毕业论文导师|写推荐信|教师节)[^\n。！？]{0,36}(?:老师|联系|感谢|邮件)|"
    r"(?:双学位|双专业|辅修)[^\n。！？]{0,36}(?:退双|选课通道|有什么影响)|"
    r"(?:转院|转系|转专业)[^\n。！？]{0,40}(?:选课|课程难度|申请|影响)|"
    r"(?:脱单|找对象|恋爱)[^\n。！？]{0,24}(?:课|课程)|"
    r"(?:收|求收)[^\n。！？]{0,20}(?:教材|课本)",
    re.IGNORECASE,
)
_META_POINTER_RE = re.compile(
    r"(?:(?:课程)?(?:测评|评测)(?:洞)?[^\n。！？]{0,20}(?:\d{6,8}|更新完毕|填坑完毕)|"
    r"\d{6,8}[^\n。！？]{0,24}(?:课程)?(?:测评|评测))"
)
_UNTAGGED_REVIEW_RE = re.compile(
    r"(?:(?:这门|这个|本门|此门)[^\n。！？]{0,5}课|神人课|烂课|好课|"
    r"(?:最后悔|最推荐|最喜欢)[^\n。！？]{0,10}(?:课|选)|"
    r"课程[^\n。！？]{0,30}(?:评分|给分|作业量|考核|教学|讲课质量))"
)
_STRONG_REVIEW_RE = re.compile(
    r"(?:给分|均分|绩点|平时分|评分构成|作业量|作业多|作业少|任务量|"
    r"考试|考核|期末|期中|闭卷|开卷|点名|签到|考勤|难度|难不难|"
    r"水课|神课|好课|强推|避雷|不推荐|讲得|课堂体验|收获)"
)
_QUESTION_RE = re.compile(
    r"(?:求|请问|想问|同问|蹲|有没有|会不会|是否|吗[？?]?$|怎么样|如何|好不好|[？?])"
)

_POST_FACT_TERMS = (
    "给分", "均分", "绩点", "分数", "作业", "考试", "考核", "期末", "期中",
    "点名", "签到", "论文", "闭卷", "开卷", "课堂", "上课", "讲课", "讲得",
    "教学", "收获", "难度", "小组", "展示", "pre", "presentation", "水课",
    "神课", "好课", "推荐", "避雷", "退课", "旁听", "答疑", "教材", "老师",
)
_CORE_SIGNAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"给分|均分|绩点|平时分|分数|评分构成",
        r"作业(?:量|多|少|重|轻|难|简单|频繁|每周|不多)|(?:多|少|几次|每周)[^\n。！？]{0,3}作业",
        r"考试|考核|期末|期中|闭卷|开卷",
        r"点名|签到|考勤",
        r"论文|小组|展示|\bpre\b|presentation",
        r"讲得|教学|课堂(?:体验|氛围)|收获",
        r"难度|难不难|水课|神课|好课|推荐|避雷|不推荐|退课|旁听|答疑|教材",
        r"最后悔|神人课|坑人|离谱|不负责",
    )
)
_REPLY_FACT_TERMS = (
    "给分", "均分", "绩点", "分数", "作业", "考试", "考核", "期末", "期中",
    "点名", "签到", "论文", "闭卷", "开卷", "课堂", "上课", "讲课", "讲得",
    "教学", "收获", "难", "容易", "轻松", "小组", "展示", "pre", "presentation",
    "水", "卷", "推荐", "避雷", "退课", "旁听", "答疑", "教材", "老师", "教授",
    "不错", "很好", "一般", "不太行", "喜欢", "不推荐", "事情少", "任务少",
)
_ASSERTIVE_TERMS = tuple(
    term for term in _REPLY_FACT_TERMS if term not in {"老师", "教授", "上课", "难"}
)
_LOW_VALUE_RE = re.compile(
    r"^(?:蹲+|同问|dd+|d|mark|码住?|cy|收藏|顶|谢谢[!！。]*|感谢[!！。]*|"
    r"好人一生平安|求蹲|俺也蹲|插眼|蹲一个)[~～!！?？。,.，\s]*$",
    re.IGNORECASE,
)


def normalize_search_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return _MATCH_STRIP_RE.sub("", value)


def sanitize_text(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = _EMAIL_RE.sub("[联系方式已隐藏]", text)
    text = _PHONE_RE.sub("[联系方式已隐藏]", text)
    text = _CONTACT_RE.sub("[联系方式已隐藏]", text)
    return text


class CourseMatcher:
    def __init__(self, alias_names, code_names, canonical_codes):
        self._alias_names = {
            alias: frozenset(names) for alias, names in alias_names.items()
        }
        self._code_names = {
            code: frozenset(names) for code, names in code_names.items()
        }
        self.canonical_codes = {
            name: tuple(sorted(codes)) for name, codes in canonical_codes.items()
        }
        self._prefixes = defaultdict(list)
        for alias in sorted(self._alias_names, key=lambda item: (-len(item), item)):
            if len(alias) >= 2:
                self._prefixes[alias[:2]].append(alias)

    @classmethod
    def from_rows(cls, rows):
        alias_names = defaultdict(set)
        code_names = defaultdict(set)
        canonical_codes = defaultdict(set)
        for raw_code, raw_name in rows:
            name = str(raw_name or "").strip()
            code = str(raw_code or "").strip()
            if not name:
                continue
            aliases = {normalize_search_text(name)}
            without_parenthetical = normalize_search_text(_PAREN_RE.sub("", name))
            if len(without_parenthetical) >= 4:
                aliases.add(without_parenthetical)
            for alias in aliases:
                if len(alias) >= 2:
                    alias_names[alias].add(name)
            if re.fullmatch(r"\d{8}", code):
                code_names[code].add(name)
                canonical_codes[name].add(code)
            else:
                canonical_codes.setdefault(name, set())
        return cls(alias_names, code_names, canonical_codes)

    @property
    def course_count(self):
        return len(self.canonical_codes)

    def _alias_matches(self, normalized: str, allow_short: bool):
        matches = []
        for index in range(max(len(normalized) - 1, 0)):
            for alias in self._prefixes.get(normalized[index:index + 2], ()):
                if len(alias) <= 3 and not allow_short:
                    continue
                if len(alias) == 2 and normalized[index + len(alias):index + len(alias) + 1] != "课":
                    continue
                if normalized.startswith(alias, index):
                    matches.append((index, index + len(alias), alias))

        occupied = set()
        selected = []
        for start, end, alias in sorted(matches, key=lambda item: (-(item[1] - item[0]), item[0])):
            if any(position in occupied for position in range(start, end)):
                continue
            occupied.update(range(start, end))
            selected.append((start, end, alias))
        return selected

    def match(self, text: str, allow_short: bool = False) -> set[str]:
        normalized = normalize_search_text(text)
        selected = set()
        for _start, _end, alias in self._alias_matches(normalized, allow_short):
            selected.update(self._alias_names[alias])

        for code in _COURSE_CODE_RE.findall(normalized):
            selected.update(self._code_names.get(code, ()))
        return selected

    def match_contextual(self, text: str, allow_short: bool = False) -> set[str]:
        normalized = normalize_search_text(text)
        selected = set()
        for start, end, alias in self._alias_matches(normalized, True):
            window = normalized[max(0, start - 12):min(len(normalized), end + 12)]
            if any(term in window for term in _MATCH_CONTEXT_TERMS):
                selected.update(self._alias_names[alias])
        for code in _COURSE_CODE_RE.findall(normalized):
            selected.update(self._code_names.get(code, ()))
        return selected


def _normalized_with_offsets(text: str):
    normalized_chars = []
    offsets = []
    for index, char in enumerate(text or ""):
        for normalized_char in unicodedata.normalize("NFKC", char).lower():
            if _MATCH_STRIP_RE.fullmatch(normalized_char):
                continue
            normalized_chars.append(normalized_char)
            offsets.append((index, index + 1))
    return "".join(normalized_chars), offsets


def _course_name_aliases(name: str) -> set[str]:
    aliases = {normalize_search_text(name)}
    without_parenthetical = normalize_search_text(_PAREN_RE.sub("", name))
    if len(without_parenthetical) >= 4:
        aliases.add(without_parenthetical)
    return {alias for alias in aliases if len(alias) >= 2}


def _teacher_name_records(raw_teachers: str) -> set[tuple[str, str]]:
    records = set()
    for raw_part in _TEACHER_SPLIT_RE.split(str(raw_teachers or "")):
        part = raw_part.strip().removesuffix("等").strip()
        if not part:
            continue
        parenthetical = _TEACHER_PAREN_RE.findall(part)
        base = _TEACHER_PAREN_RE.sub("", part).strip()
        candidates = [base]
        candidates.extend(
            item.strip()
            for item in parenthetical
            if not _TEACHER_TITLE_RE.search(item)
        )
        for candidate in candidates:
            normalized = normalize_search_text(candidate)
            if (
                len(normalized) >= 2
                and candidate not in _TEACHER_ALIAS_EXCLUSIONS
                and not _TEACHER_TITLE_RE.search(candidate)
            ):
                records.add((normalized, candidate))
    return records


def _teacher_name_aliases(raw_teachers: str) -> set[str]:
    return {alias for alias, _name in _teacher_name_records(raw_teachers)}


class EntityHighlighter:
    """Locate full and mined course or teacher mentions in review entries."""

    def __init__(
        self,
        course_alias_names,
        teacher_alias_courses,
        *,
        teacher_alias_names=None,
        teacher_name_courses=None,
        course_alias_kinds=None,
        teacher_alias_kinds=None,
    ):
        self._course_alias_names = {
            alias: frozenset(names) for alias, names in course_alias_names.items()
        }
        self._teacher_alias_courses = {
            alias: frozenset(names) for alias, names in teacher_alias_courses.items()
        }
        self._teacher_alias_names = {
            alias: frozenset(names)
            for alias, names in (teacher_alias_names or {}).items()
        }
        self._teacher_name_courses = {
            name: frozenset(courses)
            for name, courses in (teacher_name_courses or {}).items()
        }
        self._course_alias_kinds = {
            alias: (course_alias_kinds or {}).get(alias, "full")
            for alias in self._course_alias_names
        }
        self._teacher_alias_kinds = {
            alias: (teacher_alias_kinds or {}).get(alias, "full")
            for alias in self._teacher_alias_courses
        }
        self._course_prefixes = defaultdict(list)
        self._teacher_prefixes = defaultdict(list)
        for alias in sorted(self._course_alias_names, key=lambda item: (-len(item), item)):
            self._course_prefixes[alias[:2]].append(alias)
        for alias in sorted(self._teacher_alias_courses, key=lambda item: (-len(item), item)):
            self._teacher_prefixes[alias[:2]].append(alias)

    @classmethod
    def from_rows(cls, rows):
        course_alias_names = defaultdict(set)
        teacher_alias_courses = defaultdict(set)
        teacher_alias_names = defaultdict(set)
        teacher_name_courses = defaultdict(set)
        for raw_name, raw_teachers in rows:
            course_name = str(raw_name or "").strip()
            if not course_name:
                continue
            for alias in _course_name_aliases(course_name):
                course_alias_names[alias].add(course_name)
            for alias, teacher_name in _teacher_name_records(raw_teachers):
                teacher_alias_courses[alias].add(course_name)
                teacher_alias_names[alias].add(teacher_name)
                teacher_name_courses[teacher_name].add(course_name)
        return cls(
            course_alias_names,
            teacher_alias_courses,
            teacher_alias_names=teacher_alias_names,
            teacher_name_courses=teacher_name_courses,
        )

    def with_alias_records(self, records):
        course_alias_names = defaultdict(set)
        teacher_alias_courses = defaultdict(set)
        teacher_alias_names = defaultdict(set)
        for alias, names in self._course_alias_names.items():
            course_alias_names[alias].update(names)
        for alias, courses in self._teacher_alias_courses.items():
            teacher_alias_courses[alias].update(courses)
        for alias, names in self._teacher_alias_names.items():
            teacher_alias_names[alias].update(names)
        course_kinds = dict(self._course_alias_kinds)
        teacher_kinds = dict(self._teacher_alias_kinds)

        for record in records:
            alias, normalized, entity_type, canonical_name, _evidence_count = record
            normalized = normalize_search_text(normalized or alias)
            if len(normalized) < 2:
                continue
            if entity_type == "course":
                course_alias_names[normalized].add(canonical_name)
                course_kinds.setdefault(normalized, "alias")
            elif entity_type == "teacher":
                courses = self._teacher_name_courses.get(canonical_name, ())
                if not courses:
                    continue
                teacher_alias_courses[normalized].update(courses)
                teacher_alias_names[normalized].add(canonical_name)
                teacher_kinds.setdefault(normalized, "alias")

        return EntityHighlighter(
            course_alias_names,
            teacher_alias_courses,
            teacher_alias_names=teacher_alias_names,
            teacher_name_courses=self._teacher_name_courses,
            course_alias_kinds=course_kinds,
            teacher_alias_kinds=teacher_kinds,
        )

    def canonical_names_for_span(self, text, start, end, entity_type):
        alias = normalize_search_text(text[start:end])
        if entity_type == "course":
            return self._course_alias_names.get(alias, frozenset())
        return self._teacher_alias_names.get(alias, frozenset())

    @staticmethod
    def _ascii_boundary_is_valid(text: str, start: int, end: int, alias: str) -> bool:
        if not re.search(r"[a-z]", alias):
            return True
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        return not re.match(r"[A-Za-z0-9]", before) and not re.match(r"[A-Za-z0-9]", after)

    def match(self, text: str, course_names) -> list[dict]:
        normalized, offsets = _normalized_with_offsets(text)
        if len(normalized) < 2:
            return []
        known_courses = set(course_names)
        candidates = set()
        for index in range(len(normalized) - 1):
            prefix = normalized[index:index + 2]
            for alias in self._course_prefixes.get(prefix, ()):
                end = index + len(alias)
                if end > len(normalized) or not normalized.startswith(alias, index):
                    continue
                if not self._course_alias_names[alias].intersection(known_courses):
                    continue
                start_offset = offsets[index][0]
                end_offset = offsets[end - 1][1]
                if self._course_alias_kinds[alias] == "full":
                    while end_offset < len(text) and text[end_offset] in ")）]】":
                        end_offset += 1
                if not self._ascii_boundary_is_valid(text, start_offset, end_offset, alias):
                    continue
                candidates.add(
                    (
                        start_offset,
                        end_offset,
                        "course",
                        self._course_alias_kinds[alias],
                    )
                )

            for alias in self._teacher_prefixes.get(prefix, ()):
                end = index + len(alias)
                if end > len(normalized) or not normalized.startswith(alias, index):
                    continue
                start_offset = offsets[index][0]
                end_offset = offsets[end - 1][1]
                if not self._ascii_boundary_is_valid(text, start_offset, end_offset, alias):
                    continue
                associated = self._teacher_alias_courses[alias].intersection(known_courses)
                context = text[max(0, start_offset - 8):min(len(text), end_offset + 8)]
                if not associated and not _TEACHER_CONTEXT_RE.search(context):
                    continue
                candidates.add(
                    (
                        start_offset,
                        end_offset,
                        "teacher",
                        self._teacher_alias_kinds[alias],
                    )
                )

        occupied = set()
        selected = []
        priority = {"course": 0, "teacher": 1}
        kind_priority = {"full": 0, "alias": 1}
        for start, end, entity_type, match_kind in sorted(
            candidates,
            key=lambda item: (
                -(item[1] - item[0]), item[0], kind_priority[item[3]], priority[item[2]]
            ),
        ):
            if any(position in occupied for position in range(start, end)):
                continue
            occupied.update(range(start, end))
            selected.append(
                {
                    "start_offset": start,
                    "end_offset": end,
                    "entity_type": entity_type,
                    "match_kind": match_kind,
                }
            )
        return sorted(selected, key=lambda item: (item["start_offset"], item["end_offset"]))


def _valid_teacher_initial(raw_alias: str) -> bool:
    normalized = normalize_search_text(raw_alias)
    return bool(
        re.fullmatch(r"[a-z]{2,4}", normalized)
        and normalized not in _ASCII_ALIAS_EXCLUSIONS
    )


def _leading_teacher_initials(text: str):
    aliases = []
    cursor = 0
    while cursor < len(text):
        match = _TEACHER_INITIAL_RE.match(text[cursor:])
        if match is None or not _valid_teacher_initial(match.group(1)):
            break
        aliases.append(match.group(1))
        cursor += match.end()
    return aliases, cursor


def _valid_course_alias(raw_alias: str, highlighter: EntityHighlighter) -> bool:
    alias = raw_alias.strip("()（）[]【】<>《》.。!?！？'\"-—")
    normalized = normalize_search_text(alias)
    if not 2 <= len(normalized) <= 10:
        return False
    if normalized[:1].isdigit():
        return False
    if normalized in _COURSE_ALIAS_EXCLUSIONS:
        return False
    if normalized in highlighter._course_alias_names:
        return False
    if normalized in highlighter._teacher_alias_courses:
        return False
    surface = unicodedata.normalize("NFKC", alias)
    if not re.fullmatch(r"[A-Za-z0-9\u3400-\u9fff]+", surface):
        return False
    if not re.fullmatch(r"[a-z0-9\u3400-\u9fff]+", normalized):
        return False
    if normalized.isascii():
        letters = "".join(char for char in alias if char.isalpha())
        return bool(
            3 <= len(normalized) <= 8
            and normalized not in _ASCII_ALIAS_EXCLUSIONS
            and letters
            and letters == letters.upper()
        )
    if not re.search(r"[\u3400-\u9fff]", normalized):
        return False
    if len(normalized) > 7:
        return False
    ascii_letters = "".join(char for char in surface if char.isascii() and char.isalpha())
    if (
        len(ascii_letters) > 1
        and ascii_letters != ascii_letters.upper()
        and ascii_letters.lower() not in {"ai", "mooc"}
    ):
        return False
    if re.fullmatch(r"[\u3400-\u9fff]+", normalized) and len(normalized) > 5:
        return False
    return True


def _course_alias_chunks(region: str, highlighter: EntityHighlighter):
    aliases = []
    for chunk in _ALIAS_REGION_SPLIT_RE.split(region):
        candidate = chunk.strip("()（）[]【】<>《》.。!?！？'\"-—")
        if _valid_course_alias(candidate, highlighter):
            aliases.append(candidate)
    return aliases


def _first_course_alias_chunk(region: str, highlighter: EntityHighlighter):
    for chunk in _ALIAS_REGION_SPLIT_RE.split(region):
        candidate = chunk.strip("()（）[]【】<>《》.。!?！？'\"-—")
        if not candidate:
            continue
        return candidate if _valid_course_alias(candidate, highlighter) else None
    return None


def _is_alias_header(line: str, course_mentions, teacher_mentions) -> bool:
    stripped = line.lstrip()
    has_structure = bool(
        "/" in line
        or "／" in line
        or stripped.startswith(("《", "【", "["))
        or re.match(r"^\d{1,2}\s*[.、)）]", stripped)
    )
    return bool(
        course_mentions
        and teacher_mentions
        and len(line) <= 120
        and not re.search(r"[。！？?!]", line)
        and has_structure
    )


def _line_alias_candidates(line: str, course_names, highlighter: EntityHighlighter):
    highlights = highlighter.match(line, course_names)
    course_mentions = []
    teacher_mentions = []
    known_courses = set(course_names)
    for item in highlights:
        names = set(
            highlighter.canonical_names_for_span(
                line,
                item["start_offset"],
                item["end_offset"],
                item["entity_type"],
            )
        )
        if item["entity_type"] == "course":
            names.intersection_update(known_courses)
        if not names:
            continue
        mention = {**item, "canonical_names": tuple(sorted(names))}
        if item["entity_type"] == "course":
            course_mentions.append(mention)
        else:
            teacher_mentions.append(mention)

    course_mentions.sort(key=lambda item: item["start_offset"])
    teacher_mentions.sort(key=lambda item: item["start_offset"])
    is_header = _is_alias_header(line, course_mentions, teacher_mentions)
    candidates = []

    for mention in course_mentions:
        following_teachers = [
            item for item in teacher_mentions
            if item["start_offset"] >= mention["end_offset"]
        ]
        following_courses = [
            item for item in course_mentions
            if item["start_offset"] >= mention["end_offset"]
        ]
        boundaries = [min(len(line), mention["end_offset"] + 40)]
        if following_teachers:
            boundaries.append(following_teachers[0]["start_offset"])
        if following_courses:
            boundaries.append(following_courses[0]["start_offset"])
        region_end = min(boundaries)
        region = line[mention["end_offset"]:region_end]
        explicit_separator = bool(
            re.match(r"^[\s)）\]】]*[/／》】]", region)
        )
        aliases = _course_alias_chunks(region, highlighter)
        for alias in aliases:
            normalized = normalize_search_text(alias)
            mixed_or_upper = bool(
                re.search(r"[a-z0-9]", normalized)
                or (normalized.isascii() and alias == alias.upper())
            )
            if not (explicit_separator or is_header or mixed_or_upper):
                continue
            for canonical_name in mention["canonical_names"]:
                candidates.append((alias, "course", canonical_name))

    grouped_teacher_indexes = set()
    trailing_initials = []
    trailing_consumed = 0
    if teacher_mentions:
        tail = line[teacher_mentions[-1]["end_offset"]:]
        trailing_initials, trailing_consumed = _leading_teacher_initials(tail)
        grouped_initials = bool(
            len(teacher_mentions) > 1
            and len(trailing_initials) == len(teacher_mentions)
            and len(line) <= 120
            and not re.search(r"[。！？?!]", line)
        )
        if grouped_initials:
            for index, (mention, alias) in enumerate(
                zip(teacher_mentions, trailing_initials)
            ):
                grouped_teacher_indexes.add(index)
                for canonical_name in mention["canonical_names"]:
                    candidates.append((alias, "teacher", canonical_name))

    for index, mention in enumerate(teacher_mentions):
        if index in grouped_teacher_indexes:
            continue
        previous_end = (
            teacher_mentions[index - 1]["end_offset"]
            if index else max(0, mention["start_offset"] - 18)
        )
        prefix = line[previous_end:mention["start_offset"]]
        prefix_match = re.search(
            r"(?<![A-Za-z0-9])([A-Za-z]{2,8})[\s/／,，、;；:：()（）]*$",
            prefix,
        )
        if (
            prefix_match is not None
            and prefix_match.group(1) != prefix_match.group(1).upper()
            and _valid_teacher_initial(prefix_match.group(1))
        ):
            for canonical_name in mention["canonical_names"]:
                candidates.append((prefix_match.group(1), "teacher", canonical_name))
        next_start = (
            teacher_mentions[index + 1]["start_offset"]
            if index + 1 < len(teacher_mentions)
            else min(len(line), mention["end_offset"] + 24)
        )
        region = line[mention["end_offset"]:next_start]
        initial_match = _TEACHER_INITIAL_RE.match(region)
        if initial_match is None or not _valid_teacher_initial(initial_match.group(1)):
            continue
        remainder = region[initial_match.end():].lstrip()
        if not is_header and remainder[:1].isascii() and remainder[:1].isalpha():
            continue
        for canonical_name in mention["canonical_names"]:
            candidates.append((initial_match.group(1), "teacher", canonical_name))

    if is_header and len(course_mentions) == 1 and teacher_mentions:
        tail = line[teacher_mentions[-1]["end_offset"] + trailing_consumed:]
        alias = _first_course_alias_chunk(tail, highlighter)
        if alias:
            for canonical_name in course_mentions[0]["canonical_names"]:
                candidates.append((alias, "course", canonical_name))

    return candidates


def mine_entity_aliases(conn, highlighter: EntityHighlighter):
    """Mine high-confidence abbreviations from full-name co-occurrence in entries."""
    courses_by_entry = defaultdict(set)
    for entry_key, course_name in conn.execute(
        "SELECT entry_key, course_name FROM entry_courses"
    ):
        courses_by_entry[entry_key].add(course_name)

    evidence = Counter()
    spellings = defaultdict(Counter)
    for entry_key, content in conn.execute("SELECT entry_key, content FROM entries"):
        entry_candidates = defaultdict(Counter)
        for line in str(content or "").splitlines() or [str(content or "")]:
            for alias, entity_type, canonical_name in _line_alias_candidates(
                line, courses_by_entry[entry_key], highlighter
            ):
                normalized = normalize_search_text(alias)
                key = (normalized, entity_type, canonical_name)
                entry_candidates[key][alias] += 1
        for key, variants in entry_candidates.items():
            evidence[key] += 1
            spellings[key].update(variants)

    grouped_evidence = defaultdict(list)
    for (normalized, entity_type, canonical_name), evidence_count in evidence.items():
        grouped_evidence[(normalized, entity_type)].append(
            (canonical_name, evidence_count)
        )

    teacher_initial_votes = defaultdict(Counter)
    for (normalized, entity_type, canonical_name), evidence_count in evidence.items():
        if (
            entity_type == "teacher"
            and re.fullmatch(r"[a-z]{2,4}", normalized)
            and re.fullmatch(r"[\u3400-\u9fff]{2,4}", canonical_name)
            and len(normalized) == len(canonical_name)
        ):
            for character, initial in zip(canonical_name, normalized):
                teacher_initial_votes[character][initial] += evidence_count

    records = []
    for (normalized, entity_type, canonical_name), evidence_count in sorted(evidence.items()):
        relations = grouped_evidence[(normalized, entity_type)]
        max_evidence = max(item[1] for item in relations)
        if len(relations) > 1 and max_evidence > 1:
            threshold = max(2, (max_evidence + 19) // 20)
            if evidence_count < threshold:
                continue
        if entity_type == "teacher":
            if (
                not re.fullmatch(r"[\u3400-\u9fff]{2,4}", canonical_name)
                or len(normalized) != len(canonical_name)
            ):
                continue
            plausible = True
            for character, initial in zip(canonical_name, normalized):
                votes = teacher_initial_votes[character]
                if votes[initial] * 5 < max(votes.values(), default=0):
                    plausible = False
                    break
            if not plausible:
                continue
        else:
            canonical_normalized = normalize_search_text(canonical_name)
            if evidence_count < 2 or len(normalized) >= len(canonical_normalized):
                continue
            if _COURSE_ALIAS_NOISE_RE.search(normalized):
                continue
            alias_characters = set(re.findall(r"[\u3400-\u9fff]", normalized))
            canonical_characters = set(
                re.findall(r"[\u3400-\u9fff]", canonical_normalized)
            )
            if alias_characters:
                overlap = len(alias_characters.intersection(canonical_characters))
                required_overlap = 1 if len(alias_characters) <= 2 else 2
                if overlap < required_overlap:
                    continue
        alias = sorted(
            spellings[(normalized, entity_type, canonical_name)].items(),
            key=lambda item: (-item[1], item[0].casefold(), item[0]),
        )[0][0]
        records.append((alias, normalized, entity_type, canonical_name, evidence_count))

    teacher_courses_by_alias = defaultdict(set)
    for _alias, normalized, entity_type, canonical_name, _count in records:
        if entity_type == "teacher":
            teacher_courses_by_alias[normalized].update(
                highlighter._teacher_name_courses.get(canonical_name, ())
            )
    teacher_like_ascii = {
        normalized
        for _alias, normalized, entity_type, canonical_name, _count in records
        if (
            entity_type == "course"
            and normalized.isascii()
            and canonical_name in teacher_courses_by_alias.get(normalized, ())
        )
    }
    return [
        record
        for record in records
        if not (
            record[2] == "course"
            and record[1].isascii()
            and record[1] in teacher_like_ascii
        )
    ]


def load_course_matcher(database_paths) -> CourseMatcher:
    rows = []
    for path in sorted(map(Path, database_paths)):
        with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)) as conn:
            rows.extend(
                conn.execute(
                    "SELECT course_code, course_name FROM basic_info "
                    "WHERE TRIM(COALESCE(course_name, '')) != ''"
                )
            )
    return CourseMatcher.from_rows(rows)


def load_entity_highlighter(database_paths) -> EntityHighlighter:
    rows = []
    for path in sorted(map(Path, database_paths)):
        with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)) as conn:
            rows.extend(
                conn.execute(
                    "SELECT course_name, teacher FROM basic_info "
                    "WHERE TRIM(COALESCE(course_name, '')) != ''"
                )
            )
    return EntityHighlighter.from_rows(rows)


def _term_count(text: str, terms) -> int:
    lowered = unicodedata.normalize("NFKC", text or "").lower()
    return sum(term.lower() in lowered for term in terms)


def _core_signal_count(text: str) -> int:
    return sum(bool(pattern.search(text or "")) for pattern in _CORE_SIGNAL_PATTERNS)


def _is_post_relevant(text: str, courses: set[str], early_courses: set[str]) -> bool:
    explicit = bool(_EXPLICIT_REVIEW_RE.search(text))
    generic_request = bool(_GENERIC_REQUEST_RE.search(text))
    core_signals = _core_signal_count(text)
    if _NON_COURSE_REVIEW_RE.search(text):
        return False
    if _HARD_NOISE_RE.search(text):
        return False
    normalized_length = len(normalize_search_text(text))
    if _META_POINTER_RE.search(text) and normalized_length < 100 and core_signals <= 1:
        return False
    if _DOMAIN_NOISE_RE.search(text) and not explicit:
        return False
    if _LOGISTICS_RE.search(text) and not explicit and core_signals < 2:
        return False
    if explicit:
        return bool(courses or core_signals or (_QUESTION_RE.search(text) and "课" in text))
    if generic_request:
        return core_signals >= 1
    if courses and early_courses:
        return bool(_STRONG_REVIEW_RE.search(text)) or core_signals >= 2
    if (
        normalized_length >= 40
        and core_signals >= 2
        and _UNTAGGED_REVIEW_RE.search(text)
    ):
        return True
    return False


def _post_kind(text: str) -> str:
    if _QUESTION_RE.search(text):
        return "request"
    if _EXPLICIT_REVIEW_RE.search(text) or _term_count(text, _POST_FACT_TERMS) >= 2:
        return "review"
    return "discussion"


def _is_reply_relevant(text: str, courses: set[str]) -> bool:
    stripped = text.strip()
    if not stripped or _LOW_VALUE_RE.fullmatch(stripped):
        return False
    if not courses:
        return False
    facts = _term_count(stripped, _REPLY_FACT_TERMS)
    if facts == 0:
        return False
    question_like = bool(_QUESTION_RE.search(stripped))
    assertive = _term_count(stripped, _ASSERTIVE_TERMS)
    if assertive == 0:
        return False
    if question_like and len(normalize_search_text(stripped)) < 36:
        return False
    return True


def analyze_thread(post: dict, matcher: CourseMatcher, source_month: str):
    pid = post.get("pid")
    raw_post_text = str(post.get("text") or "")
    post_text = sanitize_text(raw_post_text)
    if not isinstance(pid, int) or not post_text:
        return None

    allow_short = bool(_COURSE_CONTEXT_RE.search(raw_post_text) or _EXPLICIT_REVIEW_RE.search(raw_post_text))
    post_courses = matcher.match(raw_post_text, allow_short=allow_short)
    early_courses = matcher.match_contextual(raw_post_text[:320], allow_short=allow_short)
    if not _is_post_relevant(raw_post_text, post_courses, early_courses):
        return None

    comments = post.get("comments") or []
    direct_courses = {}
    for comment in comments:
        cid = comment.get("cid")
        raw_text = str(comment.get("text") or "")
        comment_allow_short = bool(_COURSE_CONTEXT_RE.search(raw_text))
        if isinstance(cid, int):
            direct_courses[cid] = matcher.match(raw_text, allow_short=comment_allow_short)

    context_by_cid = {}
    entries = [
        {
            "entry_key": f"p{pid}",
            "kind": "post",
            "cid": None,
            "floor": None,
            "posted_at": int(post.get("timestamp") or 0),
            "content": post_text,
            "courses": sorted(post_courses),
        }
    ]
    thread_courses = set(post_courses)

    for comment in comments:
        cid = comment.get("cid")
        floor = comment.get("floor")
        if not isinstance(cid, int) or not isinstance(floor, int):
            continue
        raw_text = str(comment.get("text") or "")
        cleaned = sanitize_text(raw_text)
        if not cleaned:
            continue
        courses = set(direct_courses.get(cid, ()))
        reply_to = comment.get("replyTo")
        if not courses and isinstance(reply_to, int):
            courses.update(context_by_cid.get(reply_to, ()))
        if not courses:
            courses.update(post_courses)
        context_by_cid[cid] = set(courses)
        if not _is_reply_relevant(raw_text, courses):
            continue
        entries.append(
            {
                "entry_key": f"c{cid}",
                "kind": "reply",
                "cid": cid,
                "floor": floor,
                "posted_at": int(comment.get("timestamp") or 0),
                "content": cleaned,
                "courses": sorted(courses),
            }
        )
        thread_courses.update(courses)

    source_url = str(post.get("url") or "").strip()
    if not source_url:
        source_url = f"https://treehole.pku.edu.cn/ch/web/pc/postDetail?pid={pid}"
    return {
        "pid": pid,
        "source_month": source_month,
        "posted_at": int(post.get("timestamp") or 0),
        "content": post_text,
        "source_url": source_url,
        "post_kind": _post_kind(raw_post_text),
        "entries": entries,
        "courses": sorted(thread_courses),
    }


def _load_shard(path: Path):
    if orjson is not None:
        return orjson.loads(path.read_bytes())
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _insert_thread(conn, thread):
    replies = sum(entry["kind"] == "reply" for entry in thread["entries"])
    conn.execute(
        "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            thread["pid"], thread["source_month"], thread["posted_at"],
            thread["content"], thread["source_url"], thread["post_kind"], replies,
        ),
    )
    for entry in thread["entries"]:
        conn.execute(
            "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry["entry_key"], thread["pid"], entry["kind"], entry["cid"],
                entry["floor"], entry["posted_at"], entry["content"],
            ),
        )
        conn.executemany(
            "INSERT INTO entry_courses VALUES (?, ?)",
            ((entry["entry_key"], name) for name in entry["courses"]),
        )
    conn.executemany(
        "INSERT INTO thread_courses VALUES (?, ?, ?)",
        (
            (thread["pid"], name, normalize_search_text(name))
            for name in thread["courses"]
        ),
    )


def _populate_entry_highlights(conn, highlighter: EntityHighlighter | None):
    conn.execute("DELETE FROM entry_highlights")
    stats = {
        "highlighted_entries": 0,
        "course_highlights": 0,
        "teacher_highlights": 0,
        "course_alias_highlights": 0,
        "teacher_alias_highlights": 0,
    }
    if highlighter is None:
        return stats

    courses_by_entry = defaultdict(set)
    for entry_key, course_name in conn.execute(
        "SELECT entry_key, course_name FROM entry_courses"
    ):
        courses_by_entry[entry_key].add(course_name)

    for entry_key, content in conn.execute("SELECT entry_key, content FROM entries"):
        highlights = highlighter.match(content, courses_by_entry[entry_key])
        if not highlights:
            continue
        stats["highlighted_entries"] += 1
        conn.executemany(
            "INSERT INTO entry_highlights VALUES (?, ?, ?, ?, ?)",
            (
                (
                    entry_key,
                    item["start_offset"],
                    item["end_offset"],
                    item["entity_type"],
                    item["match_kind"],
                )
                for item in highlights
            ),
        )
        for item in highlights:
            stats[f"{item['entity_type']}_highlights"] += 1
            if item["match_kind"] == "alias":
                stats[f"{item['entity_type']}_alias_highlights"] += 1
    return stats


def _refresh_entry_highlights(conn, highlighter: EntityHighlighter | None):
    conn.execute("DELETE FROM entity_aliases")
    alias_records = [] if highlighter is None else mine_entity_aliases(conn, highlighter)
    if alias_records:
        conn.executemany(
            "INSERT INTO entity_aliases VALUES (?, ?, ?, ?, ?)",
            alias_records,
        )
    enriched = (
        None if highlighter is None else highlighter.with_alias_records(alias_records)
    )
    stats = {
        "course_aliases": len({
            normalized for _alias, normalized, entity_type, _name, _count in alias_records
            if entity_type == "course"
        }),
        "teacher_aliases": len({
            normalized for _alias, normalized, entity_type, _name, _count in alias_records
            if entity_type == "teacher"
        }),
    }
    stats.update(_populate_entry_highlights(conn, enriched))
    return stats


def _validate_database(conn, expected_threads, expected_entries):
    required = {
        "metadata", "threads", "entries", "thread_courses", "entry_courses",
        "course_catalog", "entity_aliases", "entry_highlights",
    }
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not required.issubset(tables):
        raise RuntimeError(f"review database missing tables: {sorted(required - tables)}")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("review database has foreign key violations")
    if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise RuntimeError("review database integrity check failed")
    threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    posts = conn.execute("SELECT COUNT(*) FROM entries WHERE kind='post'").fetchone()[0]
    if (threads, entries, posts) != (expected_threads, expected_entries, expected_threads):
        raise RuntimeError(
            "review database row count mismatch: "
            f"threads={threads}/{expected_threads} entries={entries}/{expected_entries} posts={posts}"
        )
    invalid_alias = conn.execute(
        """
        SELECT 1 FROM entity_aliases
        WHERE TRIM(alias)='' OR TRIM(normalized_alias)='' OR evidence_count <= 0
        LIMIT 1
        """
    ).fetchone()
    if invalid_alias is not None:
        raise RuntimeError("review database has invalid entity aliases")
    previous_key = None
    previous_end = 0
    for entry_key, content, start, end in conn.execute(
        """
        SELECT h.entry_key, e.content, h.start_offset, h.end_offset
        FROM entry_highlights h
        JOIN entries e ON e.entry_key=h.entry_key
        ORDER BY h.entry_key, h.start_offset, h.end_offset
        """
    ):
        if entry_key != previous_key:
            previous_key = entry_key
            previous_end = 0
        if start < previous_end or start < 0 or end <= start or end > len(content):
            raise RuntimeError("review database has invalid or overlapping highlights")
        previous_end = end


def build_review_database(
    *,
    shard_paths,
    target: Path,
    matcher: CourseMatcher,
    highlighter: EntityHighlighter | None = None,
    snapshot_date: str,
    source_metadata: dict | None = None,
):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        target_mode = 0o644

    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temp_path = Path(raw_temp)
    conn = None
    stats = {
        "source_shards": 0,
        "source_posts": 0,
        "source_replies": 0,
        "matched_threads": 0,
        "matched_entries": 0,
        "matched_replies": 0,
    }

    try:
        conn = sqlite3.connect(temp_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        for shard_path in sorted(map(Path, shard_paths)):
            payload = _load_shard(shard_path)
            if payload.get("version") != 2 or not isinstance(payload.get("posts"), dict):
                raise ValueError(f"invalid treehole shard: {shard_path}")
            source_month = str(payload.get("key") or shard_path.stem)
            stats["source_shards"] += 1
            for post in payload["posts"].values():
                stats["source_posts"] += 1
                comments = post.get("comments") or []
                if isinstance(comments, list):
                    stats["source_replies"] += len(comments)
                thread = analyze_thread(post, matcher, source_month)
                if thread is None:
                    continue
                _insert_thread(conn, thread)
                stats["matched_threads"] += 1
                stats["matched_entries"] += len(thread["entries"])
                stats["matched_replies"] += len(thread["entries"]) - 1
            conn.commit()
            print(
                f"[{source_month}] posts={stats['source_posts']} "
                f"threads={stats['matched_threads']} replies={stats['matched_replies']}"
            )

        conn.execute(
            """
            INSERT INTO course_catalog(course_name, search_name, course_codes, thread_count, entry_count)
            SELECT tc.course_name,
                   MIN(tc.search_name),
                   '',
                   COUNT(DISTINCT tc.pid),
                   COUNT(DISTINCT ec.entry_key)
            FROM thread_courses tc
            LEFT JOIN entry_courses ec ON ec.course_name = tc.course_name
            GROUP BY tc.course_name
            """
        )
        for name, codes in matcher.canonical_codes.items():
            if not codes:
                continue
            conn.execute(
                "UPDATE course_catalog SET course_codes=? WHERE course_name=?",
                (",".join(codes), name),
            )

        stats.update(_refresh_entry_highlights(conn, highlighter))

        metadata = {
            "snapshot_date": snapshot_date,
            "classifier_version": CLASSIFIER_VERSION,
            "highlight_version": HIGHLIGHT_VERSION,
            "catalog_courses": matcher.course_count,
            **stats,
        }
        if source_metadata:
            for key in ("cachedReplyCoveragePercent", "uncachedReplyDifference"):
                if key in source_metadata:
                    metadata[key] = source_metadata[key]
        conn.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            ((key, str(value)) for key, value in metadata.items()),
        )
        conn.commit()
        _validate_database(conn, stats["matched_threads"], stats["matched_entries"])
        conn.close()
        conn = None

        temp_path.chmod(target_mode)
        with temp_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
        temp_path = None
        _fsync_parent_directory(target)
        print(f"Review database built: {target}")
        print(
            f"  threads={stats['matched_threads']} entries={stats['matched_entries']} "
            f"replies={stats['matched_replies']} highlights="
            f"{stats['course_highlights'] + stats['teacher_highlights']}"
        )
        return stats
    finally:
        if conn is not None:
            with suppress(Exception):
                conn.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def enrich_existing_database(target: Path, highlighter: EntityHighlighter):
    """Atomically add or refresh entity highlights without rescanning source shards."""
    target = Path(target)
    if not target.is_file():
        raise FileNotFoundError(target)
    target_mode = stat.S_IMODE(target.stat().st_mode)
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temp_path = Path(raw_temp)
    source_conn = None
    conn = None
    try:
        source_conn = sqlite3.connect(
            f"file:{target.resolve().as_posix()}?mode=ro", uri=True
        )
        conn = sqlite3.connect(temp_path)
        source_conn.backup(conn)
        source_conn.close()
        source_conn = None
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            "DROP TABLE IF EXISTS entry_highlights;"
            "DROP TABLE IF EXISTS entity_aliases;"
        )
        conn.executescript(HIGHLIGHT_SCHEMA)
        highlight_stats = _refresh_entry_highlights(conn, highlighter)
        highlight_metadata = {"highlight_version": HIGHLIGHT_VERSION, **highlight_stats}
        conn.executemany(
            """
            INSERT INTO metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            ((key, str(value)) for key, value in highlight_metadata.items()),
        )
        threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        conn.commit()
        _validate_database(conn, threads, entries)
        conn.close()
        conn = None

        temp_path.chmod(target_mode)
        with temp_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
        temp_path = None
        _fsync_parent_directory(target)
        print(f"Review highlights refreshed: {target}")
        print(
            f"  entries={highlight_stats['highlighted_entries']} "
            f"courses={highlight_stats['course_highlights']} "
            f"teachers={highlight_stats['teacher_highlights']} "
            f"course_aliases={highlight_stats['course_aliases']} "
            f"teacher_aliases={highlight_stats['teacher_aliases']}"
        )
        return highlight_stats
    finally:
        if source_conn is not None:
            with suppress(Exception):
                source_conn.close()
        if conn is not None:
            with suppress(Exception):
                conn.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _course_database_paths():
    course_paths = sorted(COURSE_DB_DIR.glob(COURSE_DB_GLOB))
    if len(course_paths) != 5:
        raise RuntimeError(f"expected five course databases, found {len(course_paths)}")
    return course_paths


def build(source_dir: Path, target: Path = DEFAULT_TARGET, months=None):
    source_dir = Path(source_dir)
    manifest_path = source_dir / "manifest.json"
    with manifest_path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    shard_dir = source_dir / "shards"
    shard_paths = sorted(shard_dir.glob("*.json"))
    if months:
        wanted = set(months)
        shard_paths = [path for path in shard_paths if path.stem in wanted]
    if not shard_paths:
        raise ValueError("no matching treehole shards")
    course_paths = _course_database_paths()
    matcher = load_course_matcher(course_paths)
    highlighter = load_entity_highlighter(course_paths)
    return build_review_database(
        shard_paths=shard_paths,
        target=target,
        matcher=matcher,
        highlighter=highlighter,
        snapshot_date=str(manifest["snapshotDate"]),
        source_metadata=manifest,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Treehole snapshot directory")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument(
        "--enrich-existing",
        action="store_true",
        help="Refresh course and teacher highlights in an existing review database",
    )
    parser.add_argument(
        "--month",
        action="append",
        default=[],
        help="Build selected YYYY-MM shard only; repeat for multiple months",
    )
    args = parser.parse_args(argv)
    if args.enrich_existing:
        if args.source or args.month:
            parser.error("--enrich-existing cannot be combined with --source or --month")
        highlighter = load_entity_highlighter(_course_database_paths())
        enrich_existing_database(args.target, highlighter)
        return 0
    if args.source is None:
        parser.error("--source is required unless --enrich-existing is used")
    build(args.source, args.target, months=args.month or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
