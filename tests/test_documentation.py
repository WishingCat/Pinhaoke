import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MARKDOWN = {
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "deploy/README.md",
    "北京大学选课网数据抓取/README.md",
    "北京大学课程数据翻译/README.md",
    "课程数据/数据说明.md",
    "归档/README.md",
}


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class DocumentationTests(unittest.TestCase):
    def test_only_required_tracked_markdown_files_remain(self):
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.md"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        tracked = {
            item.decode("utf-8")
            for item in result.stdout.split(b"\0")
            if item
        }
        self.assertEqual(tracked, EXPECTED_MARKDOWN)

    def test_local_markdown_links_resolve(self):
        link_pattern = re.compile(r"\[[^\]]+\]\((?!https?://)([^)#]+)(?:#[^)]+)?\)")
        for relative_path in EXPECTED_MARKDOWN:
            source = ROOT / relative_path
            for target in link_pattern.findall(read(relative_path)):
                destination = (source.parent / target).resolve()
                self.assertTrue(
                    destination.exists(),
                    f"broken link in {relative_path}: {target}",
                )

    def test_claude_is_a_short_pointer(self):
        text = read("CLAUDE.md")
        self.assertLess(len(text.splitlines()), 12)
        self.assertIn("AGENTS.md", text)
        self.assertNotIn("## API", text)

    def test_readme_has_https_terms_and_correct_sponsor_labels(self):
        text = read("README.md")
        self.assertIn("https://www.pinhaoke.love", text)
        self.assertLess(text.index("2026 春季"), text.index("2026 暑期"))
        self.assertLess(text.index("2026 暑期"), text.index("2026 秋季"))
        self.assertIn("秋季为默认学期", text)
        self.assertIn("树洞课程评测", text)
        self.assertIn("独立入口", text)
        self.assertIn("手机端", text)
        self.assertIn("课程名、教师名", text)
        self.assertIn("高置信缩写会加粗彩色高亮", text)
        self.assertIn("热度最高的 `24` 门课程", text)
        self.assertIn("“热门课程”", text)
        self.assertIn("不显示输入联想", text)
        self.assertIn("数据范围 `2022-12-21` 至 `2026-07-13`", text)
        self.assertIn("评测数据量 `62716`", text)
        self.assertIn("`31642` 个主题加 `31074` 条相关回复", text)
        self.assertRegex(
            text,
            r'wechat_sponsor\.jpg"[^>]*alt="微信赞助码"[^\n]*微信赞助码',
        )
        self.assertRegex(
            text,
            r'alipay_sponsor\.jpg"[^>]*alt="支付宝赞助码"[^\n]*支付宝赞助码',
        )

    def test_agents_matches_engineering_contracts(self):
        text = read("AGENTS.md")
        for fact in (
            "GET /api/health",
            "mode=ro",
            "PRAGMA query_only = ON",
            "a<id>",
            "r<id>",
            "u<id>",
            "g<id>",
            "s<id>",
            "fall=4421",
            "spring=3701",
            "summer=160",
            "GET /api/reviews",
            "GET /api/review-courses",
            "热门课程",
            "树洞课程评测.db",
            "31642",
            "62716",
            "entry_highlights",
            "entity_aliases",
            "96555",
            "60294",
            "26892",
            "36560",
            "python3 -m unittest discover -s tests -v",
            "atomic_database",
            "addToPlan.do",
            "deploy/update.sh",
        ):
            self.assertIn(fact, text)
        self.assertNotIn("There is no test suite", text)

    def test_scrape_guide_covers_safe_current_chrome_workflow(self):
        text = read("北京大学选课网数据抓取/README.md")
        for fact in (
            "已登录的 Chrome 当前页面",
            "一次性 token",
            "本地网络访问",
            "开课单位=ALL",
            "翻页",
            "课程详情",
            "严格校验",
            "addToPlan.do",
            "加入选课计划",
            "build_summer_db.py",
            "build_undergrad_2627_fall_db.py",
            "build_graduate_2627_fall_db.py",
        ):
            self.assertIn(fact, text)

    def test_translation_guide_has_matrix_counts_and_selectors(self):
        text = read("北京大学课程数据翻译/README.md")
        for fact in (
            "100156",
            "39445",
            "10010",
            "159138",
            "52430",
            "--only",
            "--db",
            "--phase",
            "数据库锁",
            "回退原始中文",
            "绝不自动运行付费 API",
        ):
            self.assertIn(fact, text)

    def test_data_guide_has_raw_and_merged_counts(self):
        text = read("课程数据/数据说明.md")
        for fact in (
            "2465",
            "1379",
            "194",
            "3032",
            "1611",
            "春季 | 3701",
            "暑期 | 160",
            "秋季 | 4421",
            "basic_info",
            "detail_info",
            "translations",
            "3561472",
            "15245822",
            "95.24%",
            "31642",
            "62716",
            "course_catalog",
            "entry_highlights",
            "entity_aliases",
            "96555",
            "60294",
            "26892",
            "36560",
        ):
            self.assertIn(fact, text)

    def test_deploy_and_archive_boundaries_are_explicit(self):
        deploy = read("deploy/README.md")
        for fact in (
            "https://www.pinhaoke.love",
            "sudo bash /opt/pinhaoke/deploy/update.sh",
            "停服前",
            "自动回滚",
            "journalctl -u pinhaoke",
            "/api/health",
            "/api/reviews?page_size=1",
        ):
            self.assertIn(fact, deploy)
        self.assertNotIn("本次任务未部署生产", deploy)
        self.assertNotIn("也没有 push", deploy)
        archive = read("归档/README.md")
        self.assertIn("V1", archive)
        self.assertIn("只读参考", archive)
        self.assertIn("严禁用于生产", archive)

    def test_documents_do_not_use_relative_time_words(self):
        forbidden = re.compile(
            r"今天|昨天|刚刚|最近|上周|下周|目前|近期|today|yesterday|recently",
            re.IGNORECASE,
        )
        for relative_path in EXPECTED_MARKDOWN:
            self.assertIsNone(
                forbidden.search(read(relative_path)),
                f"relative time found in {relative_path}",
            )


if __name__ == "__main__":
    unittest.main()
