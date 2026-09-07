import re
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
REVIEWS_HTML = (Path(__file__).resolve().parents[1] / "reviews.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_body(name):
    """Return a complete JS function body without stopping at nested braces."""
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", HTML)
    if not match:
        raise AssertionError(f"JavaScript function {name!r} is missing")

    start = match.end()
    depth = 1
    quote = None
    escaped = False
    line_comment = False
    block_comment = False
    index = start

    while index < len(HTML):
        char = HTML[index]
        nxt = HTML[index + 1] if index + 1 < len(HTML) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
        elif block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 1
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "/" and nxt == "/":
            line_comment = True
            index += 1
        elif char == "/" and nxt == "*":
            block_comment = True
            index += 1
        elif char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return HTML[start:index]
        index += 1

    raise AssertionError(f"JavaScript function {name!r} has an unclosed body")


def function_source(name):
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", HTML)
    if not match:
        raise AssertionError(f"JavaScript function {name!r} is missing")
    return match.group(0) + function_body(name) + "}"


class FrontendContractTests(unittest.TestCase):
    def test_translation_is_disabled_for_all_terms_and_course_fields(self):
        self.run_node(f"""
            const assert = require('node:assert/strict');
            const TR_IDX = {{ en: 0, ja: 1 }};
            let currentLang = 'en';
            {function_source('displayLanguageForTerm')}
            {function_source('trCourseName')}
            assert.equal(displayLanguageForTerm('en', 'fall'), 'zh');
            assert.equal(displayLanguageForTerm('ja', 'fall'), 'zh');
            assert.equal(displayLanguageForTerm('en', 'spring'), 'zh');
            assert.equal(displayLanguageForTerm('bad', 'summer'), 'zh');
            assert.equal(trCourseName({{ id: 'a1', course_name: '中文课名', english_name: 'English' }}), '中文课名');
            assert.equal(trCourseName({{ id: 'r1', course_name: '研究生课', english_name: 'English' }}), '研究生课');
            assert.equal(trCourseName({{ id: 'a1', course_name: '中文课名', english_name: '  ' }}), '中文课名');
            assert.equal(trCourseName({{ id: 'r1', course_name: 'Translated course' }}), 'Translated course');
            assert.equal(trCourseName({{ id: 'u1', course_name: '春季课', english_name: 'English' }}), '春季课');
        """)
        self.assertIn('displayLanguageForTerm(currentLang, currentTerm)', function_body('readURLState'))
        self.assertIn('displayLanguageForTerm(key, currentTerm)', function_body('setLang'))
        self.assertIn('refreshLangSelectorUI()', function_body('setTerm'))
        self.assertIn('id="langSelector" hidden', HTML)
        self.assertNotIn("p.get('lang')", function_body('readURLState'))
        self.assertNotIn("localStorage.getItem('pinhaoke_lang')", HTML)
        self.assertIn('.font-option[hidden] { display: none; }', HTML)

    def test_timetable_week_matching_and_safe_personal_rendering(self):
        self.run_node(f"""
            const assert = require('node:assert/strict');
            {function_source('sessionInWeek')}
            {function_source('sessionsOverlap')}
            const odd = {{weeks:[1,3,5], parity:'单周'}};
            const even = {{weeks:[2,4,6], parity:'双周'}};
            assert.equal(sessionsOverlap(odd, even), false);
            assert.equal(sessionInWeek(odd, 3), true);
            assert.equal(sessionInWeek(odd, 2), false);
            assert.equal(sessionInWeek({{weeks:[0,1], parity:'每周'}}, 0), true);
            assert.equal(sessionInWeek(odd, -1), true);
        """)
        self.assertNotIn('innerHTML', function_body('renderTimetable'))
        self.assertIn("createTimetableButton(c)", function_body('showDetail'))
        self.assertIn("favToggle.before(timetableAction)", function_body('showDetail'))
        self.assertIn("createCard(item", function_body('buildFavItem'))

    def run_node(self, script):
        if not NODE:
            self.skipTest("node is unavailable; JavaScript behavior contract skipped")
        result = subprocess.run(
            [NODE, "-e", textwrap.dedent(script)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_treehole_reviews_is_adjacent_to_ordered_term_controls(self):
        spring = HTML.index('id="termSpringBtn"')
        summer = HTML.index('id="termSummerBtn"')
        fall = HTML.index('id="termFallBtn"')
        reviews = HTML.index('id="reviewHubLink"')
        self.assertLess(spring, summer)
        self.assertLess(summer, fall)
        self.assertLess(fall, reviews)
        anchor = HTML[reviews:reviews + 180]
        self.assertIn('href="/reviews"', anchor)
        self.assertNotIn(" hidden>", anchor)

    def test_message_board_button_and_panel_contract(self):
        # 两页顶栏都有留言按钮：课程页位于语言切换旁，评测页位于主题切换前
        lang = HTML.index('id="langSelector"')
        button = HTML.index('id="msgBoardBtn"')
        theme = HTML.index('onclick="toggleTheme()"')
        self.assertLess(lang, button)
        self.assertLess(button, theme)
        self.assertLess(
            REVIEWS_HTML.index('id="msgBoardBtn"'),
            REVIEWS_HTML.index('id="themeButton"'),
        )
        for page in (HTML, REVIEWS_HTML):
            # 悬浮面板：提示语、输入框、可滚动列表、加载更多
            self.assertIn('id="msgOverlay" role="dialog" aria-modal="true"', page)
            self.assertIn("欢迎公开留言：问题反馈、功能建议、想对开发者说的话都可以写在这里，所有人可见。", page)
            self.assertIn('maxlength="500"', page)
            self.assertIn(".msg-list { flex: 1; overflow-y: auto;", page)
            self.assertIn('id="msgMore"', page)
            # 留言内容只能通过 textContent 渲染，禁止拼入 innerHTML
            self.assertIn("body.textContent = message.content", page)
            self.assertIn("time.textContent = formatMsgTime(message.posted_at)", page)
            # 焦点与键盘契约
            self.assertIn("function trapMsgFocus", page)
            self.assertIn("closeMsgBoard()", page)
            self.assertIn('aria-label="关闭留言板"', page)

    def test_message_replies_nickname_and_changelog_contract(self):
        blocks = []
        for page in (HTML, REVIEWS_HTML):
            for fragment in (
                'id="msgChangelogButton"', 'aria-controls="msgChangelog"',
                'id="msgBoardView"', 'id="msgChangelog" role="region"',
                "function addMessageReplies", "form.hidden = true", "input.maxLength = 500",
                "message.nickname || DEFAULT_NICKNAME", "renderMessage(reply, true)",
                "before_id=${beforeId}", "if (!loaded) await loadReplies()",
                "body: JSON.stringify({ content })", "replies.hidden = !replies.hidden",
                "button.setAttribute('aria-expanded', String(changelog))",
                "document.getElementById('msgBoardView').hidden = changelog",
                "document.getElementById('msgChangelog').hidden = !changelog",
                "fetch('/api/changelog'", "返回留言板", "路过的 PKUer",
            ):
                self.assertIn(fragment, page)
            start = page.index('let msgReturnFocus = null;')
            end = page.index('function trapMsgFocus', start)
            block = page[start:end]
            inert_helper = re.search(r'  (set\w+BackgroundInert)\(true\);', block).group(1)
            self.assertIn(f'function {inert_helper}(', page)
            self.assertIn(f'{inert_helper}(false);', block)
            blocks.append(block.replace('setThreadModalBackgroundInert', 'setModalBackgroundInert'))
            self.assertNotIn('innerHTML', page[start:end])
        self.assertEqual(blocks[0], blocks[1])
        self.assertIn("nickname: buildChangeNicknameForm", HTML)
        self.assertIn("favApi('POST', '/api/account/nickname', { nickname })", HTML)
        self.assertIn("autocomplete: 'nickname', maxlength: '30'", HTML)
        self.assertIn('id="accountStatus" role="status"', HTML)

    def test_visit_stats_button_and_panel_contract(self):
        # 两页顶栏都有统计按钮，且排在留言按钮之前
        lang = HTML.index('id="langSelector"')
        button = HTML.index('id="statsBtn"')
        self.assertLess(lang, button)
        self.assertLess(button, HTML.index('id="msgBoardBtn"'))
        self.assertLess(
            REVIEWS_HTML.index('id="statsBtn"'),
            REVIEWS_HTML.index('id="msgBoardBtn"'),
        )
        for page in (HTML, REVIEWS_HTML):
            # 悬浮面板：三块统计、近 7 天趋势、隐私说明
            self.assertIn('id="statsOverlay" role="dialog" aria-modal="true"', page)
            for stat_id in (
                'id="statTodayViews"',
                'id="statWeekViews"',
                'id="statTotalViews"',
                'id="trendChart"',
            ):
                self.assertIn(stat_id, page)
            self.assertIn("按北京时间分日统计", page)
            # 数字与趋势只经 textContent / 数值高度渲染，禁止拼入 innerHTML
            self.assertIn("textContent = Number(value).toLocaleString", page)
            self.assertIn("count.textContent = point.views", page)
            self.assertIn("bar.style.height = Math.round", page)
            # 打开时轮询、关闭时清理定时器；焦点与键盘契约
            self.assertIn("statsTimer = setInterval(loadStats, 20000)", page)
            self.assertIn("clearInterval(statsTimer)", page)
            self.assertIn("function trapStatsFocus", page)
            self.assertIn('aria-label="关闭访问统计"', page)

    def test_sponsor_button_opens_in_page_panel_with_qr_codes_and_thanks(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        sponsors = re.findall(r"^\| (?!赞助者)([^|]+?) \| (¥\d+) \|$", readme, flags=re.M)
        self.assertGreaterEqual(len(sponsors), 3)
        for page in (HTML, REVIEWS_HTML):
            # 赞助按钮不再跳转 GitHub，而是打开站内悬浮面板
            self.assertNotIn("github.com/WishingCat/Pinhaoke#", page)
            self.assertIn(
                '<button class="about-link-btn about-sponsor" type="button" onclick="openSponsor()" aria-label="赞助">',
                page,
            )
            self.assertIn('id="sponsorOverlay" role="dialog" aria-modal="true"', page)
            # 面板依次展示两个赞助码、微信联系二维码和鸣谢名单，图片来自 /Images/
            for asset in (
                'src="/Images/wechat_sponsor.jpg?v=2" alt="微信赞助码"',
                'src="/Images/alipay_sponsor.jpg?v=2" alt="支付宝赞助码"',
                'src="/Images/MyWeChat.jpg" alt="微信联系方式"',
            ):
                self.assertIn(asset, page)
            self.assertLess(page.index('alt="微信赞助码"'), page.index('alt="支付宝赞助码"'))
            self.assertLess(page.index('alt="支付宝赞助码"'), page.index('alt="微信联系方式"'))
            self.assertLess(page.index('alt="微信联系方式"'), page.index(">鸣谢赞助<"))
            # 鸣谢名单必须与 README 的“鸣谢赞助”表一致
            for name, amount in sponsors:
                self.assertIn(
                    f'<span class="sponsor-name">{name}</span><span class="sponsor-amount">{amount}</span>',
                    page,
                )
            self.assertEqual(page.count('class="sponsor-chip"'), len(sponsors))
            # 焦点与键盘契约；关闭后先钉住悬浮卡再把焦点还给赞助按钮
            self.assertIn("function trapSponsorFocus", page)
            self.assertIn("closeSponsor()", page)
            self.assertIn('aria-label="关闭赞助面板"', page)
            self.assertIn(".tip-wrap.tip-pinned .tip-card { display: block; }", page)
            self.assertIn("tipWrap.classList.add('tip-pinned')", page)
            self.assertIn("focusReturnTarget(returnTarget)", page)

    def test_developer_contact_is_consistent_in_about_panel_and_footer(self):
        self.assertEqual(HTML.count("VX 联系方式："), 1)
        self.assertIn("<dt>微信联系</dt>", HTML)
        self.assertEqual(HTML.count("tuzengji"), 2)
        self.assertEqual(HTML.count("如果有问题或需求 欢迎联系！"), 2)
        self.assertIn('class="about-details"', HTML)
        self.assertNotIn('class="about-avatar"', HTML)
        self.assertIn('class="footer-contact"', HTML)

    def test_about_panel_stays_open_and_allows_text_selection(self):
        self.assertIn('class="tip-wrap about-wrap"', HTML)
        self.assertIn(".about-wrap:hover::after", HTML)
        self.assertIn("pointer-events: auto;", HTML)
        self.assertIn("-webkit-user-select: text;", HTML)
        self.assertIn("user-select: text;", HTML)

    def test_review_page_uses_read_only_apis_and_safe_text_rendering(self):
        for endpoint in ("/api/reviews?", "/api/review-courses?", "/api/reviews/meta"):
            self.assertIn(endpoint, REVIEWS_HTML)
        self.assertIn("`/api/reviews/${encodeURIComponent(pid)}`", REVIEWS_HTML)
        self.assertIn("requestId !== state.requestId", REVIEWS_HTML)
        self.assertIn("textContent = text", REVIEWS_HTML)
        self.assertIn("function renderHighlightedText", REVIEWS_HTML)
        self.assertIn("document.createTextNode", REVIEWS_HTML)
        self.assertIn("Array.from(value)", REVIEWS_HTML)
        self.assertIn("entity-course", REVIEWS_HTML)
        self.assertIn("entity-teacher", REVIEWS_HTML)
        self.assertIn("function stableEntityColor", REVIEWS_HTML)
        self.assertIn("item.match_kind === 'alias'", REVIEWS_HTML)
        self.assertIn("entity-color-5", REVIEWS_HTML)
        self.assertIn("url.hostname === 'treehole.pku.edu.cn'", REVIEWS_HTML)
        self.assertNotIn("addToPlan.do", REVIEWS_HTML)

    def test_review_cards_use_full_tinted_borders_and_open_snapshot_modal(self):
        self.assertIn("border: 1.5px solid var(--border);", REVIEWS_HTML)
        self.assertIn(
            "border-color: color-mix(in srgb, var(--thread-c) 38%, var(--border));",
            REVIEWS_HTML,
        )
        for color in range(6):
            self.assertIn(f'.thread[data-color="{color}"]', REVIEWS_HTML)
        self.assertNotIn(".thread::before", REVIEWS_HTML)
        self.assertNotIn(".thread-review::before", REVIEWS_HTML)
        self.assertNotIn(".thread-discussion::before", REVIEWS_HTML)
        self.assertIn('role="dialog" aria-modal="true"', REVIEWS_HTML)
        self.assertIn('id="threadModalOverlay" hidden', REVIEWS_HTML)
        self.assertIn("openThreadModal(thread.pid, article)", REVIEWS_HTML)
        self.assertIn("event.target.closest('a, button')", REVIEWS_HTML)
        self.assertIn("setThreadModalBackgroundInert(true)", REVIEWS_HTML)
        self.assertIn("trapThreadModalFocus(event)", REVIEWS_HTML)
        self.assertIn("threadModalOverlay.hidden && returnTarget.isConnected", REVIEWS_HTML)
        self.assertIn("threadModalBody.replaceChildren(fragment)", REVIEWS_HTML)
        self.assertIn("快照内全部回复", REVIEWS_HTML)
        self.assertIn(".thread-modal-overlay { align-items: flex-end; padding: 12px; }", REVIEWS_HTML)

    def test_review_page_keeps_review_entry_separate_and_mobile_friendly(self):
        term_nav = REVIEWS_HTML.index('class="term-nav"')
        term_nav_end = REVIEWS_HTML.index('</div>', term_nav)
        review_shell = REVIEWS_HTML.index('class="review-nav-shell"')
        review_link = REVIEWS_HTML.index('class="review-nav-link"')
        self.assertLess(term_nav_end, review_shell)
        self.assertLess(term_nav_end, review_link)
        self.assertLess(review_shell, review_link)
        self.assertIn('.term-nav, .review-nav-shell {', REVIEWS_HTML)
        self.assertIn('.term-nav a { padding: 7px 18px; border-radius: 999px; }', REVIEWS_HTML)
        for page in (HTML, REVIEWS_HTML):
            self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr))', page)
            self.assertNotIn('flex-basis: 100%;', page)
        self.assertIn('.term-toggle { display: contents; }', HTML)
        self.assertIn('.term-nav, .review-nav-shell { display: contents; }', REVIEWS_HTML)

    def test_review_page_matches_index_visual_language(self):
        # 与课程页共享的视觉记号：标题字号、学期控件尺寸、暗色底色、
        # 激活态渐变、卡片/弹窗圆角、页脚与回到顶部
        for token in (
            "font-size: 3.3rem",
            "#0E1013",
            "linear-gradient(135deg, #08766B, #075F57)",
            "rgba(22, 123, 114, 0.35)",
            "linear-gradient(118deg, #2FBE9E 8%, #167B72 58%, #14606A 94%)",
        ):
            self.assertIn(token, REVIEWS_HTML)
            self.assertIn(token, HTML)
        self.assertIn("border-radius: 14px; background: var(--surface)", REVIEWS_HTML)
        self.assertIn('class="site-footer"', REVIEWS_HTML)
        self.assertIn('class="footer-contact"', REVIEWS_HTML)
        self.assertIn('id="backTop"', REVIEWS_HTML)
        self.assertIn("window.scrollY > 600", REVIEWS_HTML)
        # 顶栏右侧与课程页一致：主题、关于悬浮卡；GitHub 与赞助并列在关于卡头部下方
        self.assertIn('class="tip-wrap about-wrap"', REVIEWS_HTML)
        self.assertIn('class="tip-card about-tip"', REVIEWS_HTML)
        self.assertIn(".about-wrap:hover::after", REVIEWS_HTML)
        self.assertIn("user-select: text;", REVIEWS_HTML)
        for page in (HTML, REVIEWS_HTML):
            self.assertIn('class="about-links"', page)
            self.assertIn('class="about-link-btn about-sponsor"', page)
            self.assertIn('aria-label="GitHub"', page)
            self.assertNotIn('class="about-links-note">纯公益项目，承诺永久免费服务', page)
            self.assertIn("Zengji Tu", page)
            self.assertIn("Ningjing Wang", page)
            self.assertIn("Tingyi Huang", page)
            self.assertLess(
                page.index("纯公益项目 · 实时更新"),
                page.index('class="about-links"'),
            )
            # 赞助与 GitHub 已移出顶栏，赞助不再有悬浮文字
            self.assertNotIn("sponsor-btn", page)
            self.assertNotIn("点击跳转 GitHub 赞助页面", page)
        self.assertNotIn(">课程搜索</span>", REVIEWS_HTML)
        # 关于卡与页脚各保留一份联系方式
        self.assertEqual(REVIEWS_HTML.count("tuzengji"), 2)
        self.assertEqual(REVIEWS_HTML.count("如果有问题或需求 欢迎联系！"), 2)

    def test_review_page_shows_date_range_and_combined_review_count(self):
        # 统计信息以小字备注显示在结果标题右侧，不再是独立统计区
        self.assertNotIn('class="stat"', REVIEWS_HTML)
        self.assertNotIn('class="stats"', REVIEWS_HTML)
        self.assertIn('class="result-meta" id="stats"', REVIEWS_HTML)
        self.assertIn(".result-meta { margin: 0; color: var(--ink-3); font-size: 0.72rem; }", REVIEWS_HTML)
        self.assertIn("数据范围", REVIEWS_HTML)
        self.assertIn('id="statDateRange"', REVIEWS_HTML)
        self.assertIn("`${startDate} 至 ${endDate}`", REVIEWS_HTML)
        self.assertIn("评测数据量", REVIEWS_HTML)
        self.assertIn('id="statTotal"', REVIEWS_HTML)
        self.assertIn(
            "Number(meta.matched_threads) + Number(meta.matched_replies)",
            REVIEWS_HTML,
        )
        for removed_id in ("statThreads", "statReplies", "statCoverage"):
            self.assertNotIn(removed_id, REVIEWS_HTML)
        self.assertNotIn('id="resultCount"', REVIEWS_HTML)
        self.assertNotIn("个相关树洞", REVIEWS_HTML)
        self.assertNotIn(".result-count", REVIEWS_HTML)

    def test_review_page_uses_requested_source_copy_and_right_aligned_total(self):
        self.assertIn("数据全部来自北大树洞大家的真实回复", REVIEWS_HTML)
        self.assertIn('<p class="result-meta" id="stats" aria-label="数据范围">', REVIEWS_HTML)
        self.assertNotIn("搜索课程名，查看课程评测主帖及其中有实际评价信息的回复。", REVIEWS_HTML)

    def test_review_page_uses_distinct_two_color_ambient_glow(self):
        self.assertIn("radial-gradient(40% 62% at 14% 0%", REVIEWS_HTML)
        self.assertIn("radial-gradient(36% 58% at 88% 5%", REVIEWS_HTML)
        self.assertIn("radial-gradient(30% 50% at 60% 30%", REVIEWS_HTML)
        self.assertIn("#F2578F", REVIEWS_HTML)
        self.assertIn("#5C8FDB", REVIEWS_HTML)
        self.assertIn("#FF8FAB", REVIEWS_HTML)
        self.assertIn("#A7C5D8", REVIEWS_HTML)
        self.assertIn(".page-head::before", REVIEWS_HTML)
        self.assertIn("radial-gradient(45% 60% at 33% 42%", REVIEWS_HTML)
        self.assertIn("radial-gradient(40% 55% at 67% 46%", REVIEWS_HTML)
        self.assertIn("body::before { animation: none !important; }", REVIEWS_HTML)
        self.assertNotIn("linear-gradient(118deg, rgba(53, 198, 167, 0.10)", REVIEWS_HTML)
        # 页面环境光晕（body::before 浅色与深色两段）不得回退到课程页的薄荷绿/靛蓝
        ambient = REVIEWS_HTML[REVIEWS_HTML.index("body::before"):REVIEWS_HTML.index("::selection")]
        self.assertIn('html[data-theme="dark"] body::before', ambient)
        self.assertNotIn("rgba(53, 198, 167", ambient)
        self.assertNotIn("rgba(79, 70, 229", ambient)

    def test_review_search_placeholder_mentions_courses_and_teachers(self):
        self.assertIn(
            'placeholder="可以搜索课程、老师，例如 YQF，马原……"',
            REVIEWS_HTML,
        )

    def test_review_search_matches_course_search_and_gates_popular_courses(self):
        self.assertIn('class="search-controls"', REVIEWS_HTML)
        self.assertIn('class="search-form" id="searchForm"', REVIEWS_HTML)
        self.assertIn('padding: 14px 20px;', REVIEWS_HTML)
        self.assertIn('border-radius: 14px;', REVIEWS_HTML)
        self.assertIn('id="popularButton"', REVIEWS_HTML)
        self.assertIn('<span>热门课程</span>', REVIEWS_HTML)
        self.assertIn('aria-controls="popularCourses"', REVIEWS_HTML)
        self.assertIn("fetch('/api/review-courses?q=&limit=24'", REVIEWS_HTML)
        self.assertNotIn("查看热门课程", REVIEWS_HTML)
        self.assertIn("searchInput.addEventListener('input', scheduleSearch)", REVIEWS_HTML)
        self.assertIn("setTimeout(() => runSearch(searchInput.value), 300)", REVIEWS_HTML)
        self.assertNotIn('class="submit-search"', REVIEWS_HTML)
        self.assertNotIn('id="suggestions"', REVIEWS_HTML)
        self.assertNotIn('role="combobox"', REVIEWS_HTML)
        self.assertNotIn("searchInput.addEventListener('focus'", REVIEWS_HTML)

    def test_review_page_omits_archive_eyebrow(self):
        self.assertNotIn("PKU TREEHOLE ARCHIVE", REVIEWS_HTML)
        self.assertNotIn('class="eyebrow"', REVIEWS_HTML)
        self.assertNotIn(".eyebrow {", REVIEWS_HTML)

    def test_load_more_guards_before_next_page_and_commits_after_success(self):
        body = function_body("loadMore")
        guard = "if (isLoading || !hasMore) return"
        self.assertIn(guard, body)
        self.assertLess(body.index(guard), body.index("currentPage + 1"))
        self.assertIn("await fetchCourses({ page: nextPage, append: true })", body)
        self.assertIn("if (appended) currentPage = nextPage", body)
        self.assertIn("loadMoreBtn.disabled = true", body)
        self.assertIn("finally", body)

    def test_fetch_courses_has_explicit_page_contract_and_boolean_result(self):
        self.assertRegex(
            HTML,
            r"async function fetchCourses\s*\(\s*\{\s*page\s*=\s*1\s*,\s*append\s*=\s*false\s*\}\s*=\s*\{\}\s*\)",
        )
        body = function_body("fetchCourses")
        self.assertIn("return true", body)
        self.assertIn("return false", body)
        self.assertNotIn("currentPage++", body)

    def test_fetch_courses_owns_its_term_controller_and_request_params(self):
        body = function_body("fetchCourses")
        self.assertIn("const requestedTerm = currentTerm", body)
        self.assertIn("params.set('term', requestedTerm)", body)
        self.assertIn("fetchController === ctrl", body)
        self.assertIn("ctrl.signal.aborted", body)
        self.assertIn("currentTerm === requestedTerm", body)

    def test_stale_course_success_cannot_commit_or_append(self):
        source = function_source("fetchCourses")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            let currentTerm = 'spring';
            let currentLang = 'zh';
            let fetchController = null;
            let isFetching = false;
            let currentCourses = [{{ id: 'keep' }}];
            let totalCount = 1;
            let hasMore = true;
            let currentPage = 4;
            let randomSeed = 17;
            const PAGE_SIZE = 20;
            const csValues = {{}};
            const inputs = {{
              searchInput: {{ value: '' }},
              filterClassroom: {{ value: '' }},
            }};
            const document = {{ getElementById: id => inputs[id] }};
            let requestedUrl = '';
            let releaseFetch;
            let renderCardsCalls = 0;
            let appendCardsCalls = 0;
            let updateCalls = 0;
            let renderErrorCalls = 0;
            function fetch(url) {{
              requestedUrl = url;
              return new Promise(resolve => {{ releaseFetch = resolve; }});
            }}
            function clearLoadMoreError() {{}}
            function renderSkeletons() {{}}
            function syncURL() {{}}
            function renderCards() {{ renderCardsCalls += 1; }}
            function appendCards() {{ appendCardsCalls += 1; }}
            function updateLoadMoreState() {{ updateCalls += 1; }}
            function renderError() {{ renderErrorCalls += 1; }}
            {source}
            (async () => {{
              const stale = fetchCourses({{ page: 5, append: true }});
              const oldController = fetchController;
              currentTerm = 'fall';
              oldController.abort();
              assert.equal(fetchController, oldController);
              releaseFetch({{
                ok: true,
                json: async () => ({{ total: 99, courses: [{{ id: 'old' }}] }}),
              }});
              assert.equal(await stale, false);
              assert.equal(new URL(requestedUrl, 'http://local').searchParams.get('term'), 'spring');
              assert.deepEqual(currentCourses, [{{ id: 'keep' }}]);
              assert.equal(totalCount, 1);
              assert.equal(hasMore, true);
              assert.equal(currentPage, 4);
              assert.equal(renderCardsCalls, 0);
              assert.equal(appendCardsCalls, 0);
              assert.equal(updateCalls, 0);
              assert.equal(renderErrorCalls, 0);
            }})().catch(error => {{ globalThis.console.error(error); process.exitCode = 1; }});
            """
        )

    def test_stale_course_http_and_json_errors_are_suppressed(self):
        source = function_source("fetchCourses")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            const realConsole = globalThis.console;
            let loggedErrors = 0;
            const console = {{ error() {{ loggedErrors += 1; }} }};
            let currentTerm = 'spring';
            let currentLang = 'zh';
            let fetchController = null;
            let isFetching = false;
            let currentCourses = [{{ id: 'keep' }}];
            let totalCount = 1;
            let hasMore = true;
            let randomSeed = 17;
            const PAGE_SIZE = 20;
            const csValues = {{}};
            const inputs = {{
              searchInput: {{ value: '' }},
              filterClassroom: {{ value: '' }},
            }};
            const document = {{ getElementById: id => inputs[id] }};
            let scenario = 'http';
            let releaseHttp;
            let rejectJson;
            let markJsonStarted;
            const jsonStarted = new Promise(resolve => {{ markJsonStarted = resolve; }});
            let renderCardsCalls = 0;
            let appendCardsCalls = 0;
            let updateCalls = 0;
            let renderErrorCalls = 0;
            function fetch() {{
              if (scenario === 'http') {{
                return new Promise(resolve => {{ releaseHttp = resolve; }});
              }}
              return Promise.resolve({{
                ok: true,
                json: () => {{
                  markJsonStarted();
                  return new Promise((_resolve, reject) => {{ rejectJson = reject; }});
                }},
              }});
            }}
            function clearLoadMoreError() {{}}
            function renderSkeletons() {{}}
            function syncURL() {{}}
            function renderCards() {{ renderCardsCalls += 1; }}
            function appendCards() {{ appendCardsCalls += 1; }}
            function updateLoadMoreState() {{ updateCalls += 1; }}
            function renderError() {{ renderErrorCalls += 1; }}
            {source}
            (async () => {{
              const staleHttp = fetchCourses();
              const httpController = fetchController;
              currentTerm = 'fall';
              httpController.abort();
              assert.equal(fetchController, httpController);
              releaseHttp({{ ok: false, status: 503 }});
              assert.equal(await staleHttp, false);

              scenario = 'json';
              currentTerm = 'spring';
              const staleJson = fetchCourses();
              await jsonStarted;
              const jsonController = fetchController;
              currentTerm = 'fall';
              jsonController.abort();
              assert.equal(fetchController, jsonController);
              rejectJson(new Error('stale JSON failure'));
              assert.equal(await staleJson, false);

              assert.deepEqual(currentCourses, [{{ id: 'keep' }}]);
              assert.equal(totalCount, 1);
              assert.equal(hasMore, true);
              assert.equal(renderCardsCalls, 0);
              assert.equal(appendCardsCalls, 0);
              assert.equal(updateCalls, 0);
              assert.equal(renderErrorCalls, 0);
              assert.equal(loggedErrors, 0);
            }})().catch(error => {{ realConsole.error(error); process.exitCode = 1; }});
            """
        )

    def test_rapid_load_more_calls_execute_one_append_and_one_page_advance(self):
        source = function_source("loadMore")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            let currentPage = 1;
            let isLoading = false;
            let hasMore = true;
            let appendCalls = 0;
            let finishAppend;
            const loadMoreBtn = {{ disabled: false }};
            const document = {{ getElementById: id => loadMoreBtn }};
            function fetchCourses(args) {{
              appendCalls += 1;
              assert.deepEqual(args, {{ page: 2, append: true }});
              return new Promise(resolve => {{ finishAppend = resolve; }});
            }}
            {source}
            (async () => {{
              const calls = [loadMore(), loadMore(), loadMore()];
              assert.equal(appendCalls, 1);
              assert.equal(currentPage, 1);
              assert.equal(loadMoreBtn.disabled, true);
              finishAppend(true);
              await Promise.all(calls);
              assert.equal(currentPage, 2);
              assert.equal(appendCalls, 1);
              assert.equal(isLoading, false);
              assert.equal(loadMoreBtn.disabled, false);
            }})().catch(error => {{ console.error(error); process.exitCode = 1; }});
            """
        )

    def test_delayed_filter_response_cannot_overwrite_active_term_state(self):
        source = function_source("loadFiltersForCurrentTerm")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            let currentTerm = 'spring';
            let cachedFilters = null;
            let filtersController = null;
            const filtersByTerm = {{}};
            const pending = {{}};
            const populated = [];
            function populateFilters(data) {{ populated.push(data.term); }}
            function fetch(url, options) {{
              const term = new URL(url, 'http://local').searchParams.get('term');
              return new Promise(resolve => {{
                pending[term] = data => resolve({{ ok: true, json: async () => data }});
              }});
            }}
            {source}
            (async () => {{
              const spring = loadFiltersForCurrentTerm();
              currentTerm = 'fall';
              const fall = loadFiltersForCurrentTerm();
              pending.fall({{ term: 'fall' }});
              const fallResult = await fall;
              pending.spring({{ term: 'spring' }});
              const springResult = await spring;
              assert.equal(fallResult, true);
              assert.equal(springResult, false);
              assert.deepEqual(Object.keys(filtersByTerm), ['fall']);
              assert.equal(filtersByTerm.fall.term, 'fall');
              assert.equal(cachedFilters.term, 'fall');
              assert.deepEqual(populated, ['fall']);
            }})().catch(error => {{ console.error(error); process.exitCode = 1; }});
            """
        )

    def test_filter_loader_has_request_owned_term_and_controller_contract(self):
        body = function_body("loadFiltersForCurrentTerm")
        self.assertIn("const requestedTerm = currentTerm", body)
        self.assertIn("const controller = new AbortController()", body)
        self.assertIn("filtersController === controller", body)
        self.assertIn("currentTerm === requestedTerm", body)
        self.assertIn("return true", body)
        self.assertIn("return false", body)

    def test_stale_filter_http_and_json_failures_resolve_false(self):
        source = function_source("loadFiltersForCurrentTerm")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            let currentTerm = 'spring';
            let cachedFilters = null;
            let filtersController = null;
            const filtersByTerm = {{}};
            let scenario = 'http';
            let resolveSpringHttp;
            let rejectSpringJson;
            let markJsonStarted;
            const jsonStarted = new Promise(resolve => {{ markJsonStarted = resolve; }});
            function populateFilters(data) {{ cachedFilters = data; }}
            function fetch(url) {{
              const term = new URL(url, 'http://local').searchParams.get('term');
              if (term === 'fall') return Promise.resolve({{ ok: true, json: async () => ({{ term: 'fall' }}) }});
              if (scenario === 'http') {{
                return new Promise(resolve => {{ resolveSpringHttp = resolve; }});
              }}
              return Promise.resolve({{
                ok: true,
                json: () => {{
                  markJsonStarted();
                  return new Promise((_resolve, reject) => {{ rejectSpringJson = reject; }});
                }}
              }});
            }}
            {source}
            (async () => {{
              const staleHttp = loadFiltersForCurrentTerm();
              currentTerm = 'fall';
              const activeFall = loadFiltersForCurrentTerm();
              assert.equal(await activeFall, true);
              resolveSpringHttp({{ ok: false, status: 503, json: async () => {{ throw new Error('unused'); }} }});
              assert.equal(await staleHttp, false);

              for (const key of Object.keys(filtersByTerm)) delete filtersByTerm[key];
              filtersController = null;
              cachedFilters = null;
              scenario = 'json';
              currentTerm = 'spring';
              const staleJson = loadFiltersForCurrentTerm();
              await jsonStarted;
              currentTerm = 'fall';
              assert.equal(await loadFiltersForCurrentTerm(), true);
              rejectSpringJson(new Error('bad stale json'));
              assert.equal(await staleJson, false);
              assert.equal(cachedFilters.term, 'fall');
              assert.deepEqual(Object.keys(filtersByTerm), ['fall']);
            }})().catch(error => {{ console.error(error); process.exitCode = 1; }});
            """
        )

    def test_stale_init_and_set_term_callers_stop_all_continuations(self):
        init_source = function_source("init")
        set_term_source = function_source("setTerm")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            let currentTerm = 'fall';
            let currentModalCourseId = null;
            let fetchController = null;
            let isFetching = false;
            let isLoading = false;
            let hasMore = false;
            let currentPage = 1;
            const TERMS = new Set(['fall', 'spring', 'summer']);
            const csValues = {{}};
            const counts = {{ fetch: 0, error: 0, chips: 0, i18n: 0, detail: 0 }};
            let finishSetTermLoad;
            let loaderMode = 'init';
            const classList = {{ toggle() {{}}, add() {{}}, remove() {{}} }};
            const element = {{ value: '', classList, observe() {{}} }};
            const document = {{ getElementById: () => element, querySelectorAll: () => [] }};
            const window = {{ addEventListener() {{}} }};
            function readURLState() {{ return 'a1'; }}
            function initFavorites() {{}}
            function refreshTermToggleUI() {{}}
            function refreshLangSelectorUI() {{}}
            function applyI18n() {{ counts.i18n += 1; }}
            function updateResultsCount() {{}}
            function renderSkeletons() {{}}
            function applyThemeMeta() {{}}
            function renderChips() {{ counts.chips += 1; }}
            function renderError() {{ counts.error += 1; }}
            function showDetail() {{ counts.detail += 1; }}
            function closeModal() {{}}
            function fetchCourses() {{
              counts.fetch += 1;
              if (fetchController) fetchController.abort();
              return Promise.resolve(true);
            }}
            function loadMore() {{}}
            function loadFiltersForCurrentTerm() {{
              if (loaderMode === 'init') return Promise.resolve(false);
              return new Promise(resolve => {{ finishSetTermLoad = resolve; }});
            }}
            {init_source}
            {set_term_source}
            (async () => {{
              await init();
              assert.deepEqual(counts, {{ fetch: 0, error: 0, chips: 0, i18n: 1, detail: 0 }});

              loaderMode = 'setTerm';
              const staleSetTerm = setTerm('spring');
              currentTerm = 'fall';
              fetchController = {{ abort: () => {{ throw new Error('stale caller aborted active list'); }} }};
              finishSetTermLoad(false);
              assert.equal(await staleSetTerm, false);
              assert.equal(counts.fetch, 0);
              assert.equal(counts.error, 0);
              assert.equal(counts.i18n, 1);
            }})().catch(error => {{ console.error(error); process.exitCode = 1; }});
            """
        )

    def test_init_and_set_term_structurally_gate_filter_ownership(self):
        for name in ("init", "setTerm"):
            with self.subTest(function=name):
                body = function_body(name)
                self.assertIn("const filtersLoaded = await loadFiltersForCurrentTerm()", body)
                self.assertIn("if (!filtersLoaded", body)

    def test_copy_syncs_and_uses_complete_location(self):
        body = function_body("copyCourseLink")
        self.assertIn("syncURL()", body)
        self.assertIn("location.href", body)
        self.assertNotIn("location.origin + location.pathname", body)

    def test_clipboard_fallback_stays_in_modal_and_restores_modal_focus(self):
        body = function_body("copyCourseLink")
        self.assertIn("fallbackHost", body)
        self.assertIn("modalOverlay", body)
        self.assertIn("focusBeforeCopy", body)
        self.assertIn("fallbackHost.appendChild(ta)", body)
        self.assertIn("focusBeforeCopy.focus()", body)

    def test_sync_url_executes_complete_state_and_omits_default_fall(self):
        source = function_source("syncURL")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            const values = {{ searchInput: 'optics', filterClassroom: '二教511' }};
            const document = {{
              getElementById: id => ({{ value: values[id] || '' }})
            }};
            const csValues = {{
              filterCourseType: '专业课',
              filterCategory: '任选',
              filterCredits: '2',
              filterDepartment: '物理学院',
              filterWeekday: '周一',
              filterPeriod: '3-4',
              filterGrading: '百分制',
              filterSort: 'random',
            }};
            let randomSeed = 731;
            let currentTerm = 'spring';
            let currentLang = 'en';
            let currentModalCourseId = 'u42';
            const location = {{ pathname: '/courses' }};
            let replaced = '';
            const history = {{ replaceState: (_state, _title, url) => {{ replaced = url; }} }};
            {source}
            syncURL();
            let params = new URL(replaced, 'http://local').searchParams;
            assert.deepEqual(Object.fromEntries(params), {{
              q: 'optics', room: '二教511', type: '专业课', cat: '任选', credits: '2',
              dept: '物理学院', day: '周一', period: '3-4', grading: '百分制', sort: 'random',
              seed: '731', term: 'spring', lang: 'en', course: 'u42'
            }});
            currentTerm = 'fall';
            syncURL();
            params = new URL(replaced, 'http://local').searchParams;
            assert.equal(params.has('term'), false);
            """
        )

    def test_weekday_label_and_period_filter_contract(self):
        self.assertIn('<label id="labelWeekday">星期几</label>', HTML)
        self.assertIn('<label id="labelPeriod">上课节时</label>', HTML)
        self.assertIn('<div class="custom-select" id="csPeriod" data-filter="filterPeriod"></div>', HTML)
        self.assertLess(HTML.index('id="csWeekday"'), HTML.index('id="csPeriod"'))
        self.assertLess(HTML.index('id="csPeriod"'), HTML.index('id="filterClassroom"'))
        self.assertNotIn("labelWeekday: '上课时间'", HTML)
        self.assertIn("labelWeekday: '星期几', labelPeriod: '上课节时'", HTML)
        self.assertEqual(HTML.count("labelPeriod:"), 8)
        self.assertEqual(HTML.count("periodLabel: (a, b) =>"), 8)
        self.assertIn("#labelPeriod { --fl-icon:", HTML)
        self.assertIn("csValues.filterPeriod     = p.get('period') || '';", HTML)
        self.assertIn("p.set('period', csValues.filterPeriod)", HTML)
        self.assertIn("params.set('period', csValues.filterPeriod)", HTML)
        self.assertIn("(filters.periods || []).map(p => ({ value: p, label: fmtPeriod(p) }))", HTML)
        self.assertIn("'filterDepartment', 'filterWeekday', 'filterPeriod', 'filterGrading'", HTML)
        self.assertIn("document.getElementById('labelPeriod').textContent = lang.labelPeriod;", HTML)
        self.assertIn("items.push({ k: 'filterPeriod', label: fmtPeriod(csValues.filterPeriod) })", HTML)
        source = function_source("fmtPeriod")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            let currentLang = 'zh';
            const i18n = {{
              zh: {{ periodLabel: (a, b) => a === b ? `第${{a}}节` : `${{a}}-${{b}}节` }},
              en: {{ periodLabel: (a, b) => a === b ? `Period ${{a}}` : `Periods ${{a}}-${{b}}` }},
            }};
            {source}
            assert.equal(fmtPeriod('10-11'), '10-11节');
            assert.equal(fmtPeriod('7-7'), '第7节');
            assert.equal(fmtPeriod('bogus'), 'bogus');
            assert.equal(fmtPeriod(null), '');
            currentLang = 'en';
            assert.equal(fmtPeriod('3-4'), 'Periods 3-4');
            """
        )

    def test_sort_copy_no_longer_claims_pinyin(self):
        self.assertIn("sortNameAsc", HTML)
        self.assertIn("sortNameDesc", HTML)
        self.assertIn("value: 'name_asc'", HTML)
        self.assertIn("value: 'name_desc'", HTML)
        self.assertNotIn("sortPinyin", HTML)

    def test_detail_renders_book_fields_and_distinguishes_intro_labels(self):
        self.assertIn("c.textbook", HTML)
        self.assertIn("c.reference_book", HTML)
        self.assertIn("modalTextbook", HTML)
        self.assertIn("modalReferenceBook", HTML)
        self.assertIn("modalIntroTranslated", HTML)

    def test_course_field_helpers_preserve_exact_source_text(self):
        self.run_node(f"""
            const assert = require('node:assert/strict');
            let currentLang = 'en';
            {function_source('tr')}
            {function_source('trTeacher')}
            {function_source('trSchedule')}
            const schedule = '1~16周每周周二3~4节理教3189~16周单周周三5~6节';
            assert.equal(trSchedule(schedule), schedule);
            assert.equal(trTeacher('教师（教授）'), '教师（教授）');
            assert.equal(tr('departments', '数学科学学院'), '数学科学学院');
        """)

    def test_custom_select_exposes_keyboard_and_aria_contract(self):
        builder = function_body("buildCustomSelect")
        self.assertIn('role="combobox"', builder)
        self.assertIn('aria-haspopup="listbox"', builder)
        self.assertIn('aria-expanded="false"', builder)
        self.assertIn('role="listbox"', builder)
        self.assertIn('role="option"', builder)
        self.assertIn('aria-selected=', builder)
        self.assertIn('aria-controls=', builder)
        self.assertIn("ArrowDown", builder)
        self.assertIn("ArrowUp", builder)
        self.assertIn("Home", builder)
        self.assertIn("End", builder)
        self.assertIn("Escape", builder)
        self.assertIn("focusout", builder)

    def test_custom_select_focus_leave_closes_but_internal_focus_does_not(self):
        if not NODE:
            self.skipTest("node is unavailable; JavaScript behavior contract skipped")
        source = function_source("handleCustomSelectFocusOut")
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            const internal = {{ name: 'internal' }};
            const external = {{ name: 'external' }};
            const trigger = {{ expanded: 'true', setAttribute: (_key, value) => {{ trigger.expanded = value; }} }};
            const container = {{
              contains: element => element === internal,
              querySelector: () => trigger,
              classList: {{ remove: () => {{}} }},
            }};
            const document = {{ activeElement: internal }};
            const requestAnimationFrame = callback => callback();
            let closes = 0;
            function closeCustomSelect(target, restoreFocus) {{
              closes += 1;
              assert.equal(target, container);
              assert.equal(Boolean(restoreFocus), false);
              trigger.setAttribute('aria-expanded', 'false');
            }}
            {source}
            handleCustomSelectFocusOut(container, {{ relatedTarget: internal }});
            assert.equal(closes, 0);
            assert.equal(trigger.expanded, 'true');
            document.activeElement = external;
            handleCustomSelectFocusOut(container, {{ relatedTarget: external }});
            assert.equal(closes, 1);
            assert.equal(trigger.expanded, 'false');
            """
        )

    def test_modal_has_dialog_semantics_and_focus_management(self):
        self.assertIn('role="dialog"', HTML)
        self.assertIn('aria-modal="true"', HTML)
        self.assertIn("aria-labelledby=", HTML)
        self.assertIn("modalReturnFocus", HTML)
        self.assertIn("trapModalFocus", HTML)
        self.assertIn(".inert = true", HTML)
        self.assertIn(".inert = false", HTML)
        self.assertIn("aria-label=", function_body("showDetail"))

    def test_term_order_and_mobile_containment_contract(self):
        spring = HTML.index('id="termSpringBtn"')
        summer = HTML.index('id="termSummerBtn"')
        fall = HTML.index('id="termFallBtn"')
        self.assertLess(spring, summer)
        self.assertLess(summer, fall)
        self.assertIn("@media (max-width: 390px)", HTML)
        self.assertIn("@media (max-width: 320px)", HTML)
        self.assertRegex(HTML, r"\.term-toggle\s*\{[^}]*flex-wrap:\s*wrap")

    def test_mobile_filter_toggle_is_centered_wide_and_prominent(self):
        match = re.search(
            r"@media \(max-width: 720px\) \{\s*\.filters-toggle \{([^}]*)\}",
            HTML,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        rules = match.group(1)
        self.assertIn("display: flex", rules)
        self.assertIn("justify-content: center", rules)
        self.assertIn("width: 100%", rules)
        self.assertIn("min-height: 50px", rules)
        self.assertIn("margin: 0 auto 14px", rules)
        self.assertIn("padding: 12px 16px", rules)
        self.assertIn("border: 1px solid var(--border)", rules)
        self.assertIn("border-radius: var(--r-lg)", rules)
        self.assertIn("background: var(--surface)", rules)
        self.assertNotIn("var(--accent)", rules)
        self.assertNotIn("gradient", rules)
        self.assertIn("font-size: 0.94rem", rules)
        self.assertIn("font-weight: 700", rules)
        self.assertIn(".filters-toggle:focus-visible {", HTML)
        self.assertIn("border-color: var(--border-2);", HTML)
        self.assertIn("color-mix(in srgb, var(--ink) 8%, transparent)", HTML)
        self.assertRegex(HTML, r"\.container\s*\{[^}]*min-width:\s*0")

    def test_mobile_filter_grid_keeps_at_least_two_columns(self):
        self.assertIn(
            ".filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }",
            HTML,
        )
        self.assertIn(".filter-group { position: relative; min-width: 0; }", HTML)
        self.assertNotIn(
            "@media (max-width: 400px) { .filter-grid { grid-template-columns: 1fr; } }",
            HTML,
        )

    def test_privacy_and_dead_font_cleanup(self):
        self.assertNotIn("HarmonyOS Sans SC", HTML)
        self.assertNotIn("harmonyos-sans-font", HTML)
        self.assertIn("hm.src", HTML)
        self.assertIn("_hmt.push(['_setAutoPageview', false])", HTML)
        self.assertIn("_hmt.push(['_trackPageview', location.pathname])", HTML)

    def test_favorites_button_panel_and_account_contract(self):
        # 课程页顶栏：语言切换 < 我的收藏 < 访问统计；评测页不提供收藏
        lang = HTML.index('id="langSelector"')
        fav = HTML.index('id="favBtn"')
        self.assertLess(lang, fav)
        self.assertLess(fav, HTML.index('id="statsBtn"'))
        self.assertNotIn('id="favBtn"', REVIEWS_HTML)
        self.assertNotIn("favOverlay", REVIEWS_HTML)
        # 面板语义、状态区与滚动容器
        self.assertIn('id="favOverlay" role="dialog" aria-modal="true" aria-labelledby="favTitle"', HTML)
        self.assertIn('aria-label="关闭我的收藏"', HTML)
        self.assertIn('id="favStatus" role="status" aria-live="polite"', HTML)
        self.assertIn(".fav-body { flex: 1; min-height: 0; overflow-y: auto;", HTML)
        self.assertIn("function trapFavFocus", HTML)
        self.assertIn("closeFavorites()", HTML)
        keydown = HTML[HTML.index("document.addEventListener('keydown'"):]
        self.assertLess(keydown.index("favOverlay"), keydown.index("sponsorOverlay"))
        self.assertIn("if (favOpen) {", keydown)
        # 面板内容只用 DOM API 与 textContent 渲染
        self.assertIn("node.textContent = text", function_body("favEl"))
        for name in (
            "renderFavoritesPanel", "renderFavAuth", "renderFavAccount", "renderFavList",
            "buildLoginForm", "buildRegisterForm", "buildResetForm", "buildChangePasswordForm",
            "buildChangeQuestionsForm", "buildDeleteForm", "setFavStatus", "favToast",
        ):
            self.assertNotIn("innerHTML", function_body(name), name)
        item = function_body("buildFavItem")
        self.assertEqual(item.count("innerHTML"), 1)
        self.assertIn("remove.innerHTML = ICONS.close", item)
        self.assertIn("createCard(item", item)
        # 卡片星标与详情弹窗按钮
        card = function_body("createCard")
        self.assertIn('<button class="fav-btn" type="button" aria-pressed="false"></button>', card)
        self.assertIn("favBtn.dataset.key = favoriteKey(course)", card)
        self.assertEqual(card.count("e.stopPropagation()"), 2)
        detail = function_body("showDetail")
        self.assertIn('id="favToggleBtn"', detail)
        self.assertIn('data-key="${esc(favoriteKey(c))}"', detail)
        self.assertIn("toggleFavorite(c, favToggle)", detail)
        self.assertIn("refreshFavoriteButtons();", function_body("applyI18n"))
        self.assertIn("initFavorites();", function_body("init"))
        # 八种语言的按钮文案
        for key in ("favAdd:", "favRemove:", "myFavorites:"):
            self.assertEqual(HTML.count(key), 8, key)
        # 表单语义与请求方式
        for token in (
            "autocomplete: 'username'", "autocomplete: 'current-password'",
            "autocomplete: 'new-password'", "minlength: '8'", "maxlength: '128'",
            "pattern: FAV_USERNAME_PATTERN", "FAV_USERNAME_PATTERN = '[A-Za-z0-9_]{3,20}'",
        ):
            self.assertIn(token, HTML)
        api = function_body("favApi")
        self.assertIn("credentials: 'same-origin'", api)
        self.assertIn("cache: 'no-store'", api)
        self.assertIn("pinhaoke_fav_mode", HTML)
        self.assertIn("favModeGet() !== 'user'", function_body("initFavorites"))
        self.assertNotIn("fav", function_body("syncURL").lower())
        self.assertNotIn("fav", function_body("readURLState").lower())
        # 必须登录才能收藏：未登录只记录待收藏课程并打开面板，不落 localStorage
        toggle = function_body("toggleFavorite")
        self.assertIn("if (!favUser) {", toggle)
        self.assertIn("pendingFavoriteId = course.id", toggle)
        self.assertIn("openFavorites()", toggle)
        self.assertNotIn("localStorage", toggle)
        self.assertIn("pendingFavoriteId", function_body("afterFavAuth"))
        # 成功登录或注册后重绘面板前复位忙碌标记，退出后仍可再次登录
        self.assertIn("favBusy = false;", function_body("renderFavoritesPanel"))
        self.assertEqual(HTML.count("favSetBusy(form, false);\n    await afterFavAuth("), 2)
        # 叠在课程详情之上时冻结详情弹窗，关闭时恢复
        self.assertIn("favStackedOnModal = courseModal.classList.contains('open')", function_body("openFavorites"))
        self.assertIn("courseModal.inert = true", function_body("openFavorites"))
        self.assertIn("courseModal.inert = false", function_body("closeFavorites"))
        # 星标为实体表面，不使用渐变；主按钮实色
        self.assertNotIn("gradient", HTML[HTML.index(".fav-btn {"):HTML.index(".fav-toast {")])

        # ---- 登录 / 个人双态与个人中心全屏视图 ----
        for key in ("favLogin:", "favAccount:"):
            self.assertEqual(HTML.count(key), 8, key)
        self.assertIn('onclick="openPersonal()"', HTML)
        self.assertIn('id="favBtnLabel"', HTML)
        personal = function_body("openPersonal")
        self.assertIn("openAccountView()", personal)
        self.assertIn("openFavorites()", personal)
        rfb = function_body("refreshFavoriteButtons")
        self.assertIn("favUser ? lang.favAccount : lang.favLogin", rfb)
        # 个人中心视图语义
        self.assertIn('id="accountView" role="dialog" aria-modal="true" aria-labelledby="accountViewTitle"', HTML)
        self.assertIn('id="accountBody"', HTML)
        self.assertIn('id="accountBack"', HTML)
        self.assertIn(".account-view { display: none; position: fixed; inset: 0; z-index: 1100;", HTML)
        opener = function_body("openAccountView")
        self.assertIn("setModalBackgroundInert(true)", opener)
        self.assertIn("history.pushState({ phk: 'account' }", opener)
        self.assertIn("window.addEventListener('popstate'", HTML)
        self.assertIn("function trapAccountFocus", HTML)
        # keydown：个人视图分支在 favOverlay 之前
        keydown_new = HTML[HTML.index("document.addEventListener('keydown'"):]
        self.assertIn("if (accountViewOpen) {", keydown_new)
        self.assertLess(keydown_new.index("accountViewOpen"), keydown_new.index("favOverlay"))
        # 新渲染函数只用 DOM API，唯一 innerHTML 在 favIcon
        for name in (
            "applyFavData", "renderFavViews", "openPersonal", "openAccountView",
            "closeAccountView", "trapAccountFocus", "renderAccountView", "renderCollections",
            "buildCollectionCard", "startRenameCollection", "confirmRemoveCollection",
            "renderCollectionChooser", "buildCollectionChip", "refreshDetailChooser",
            "scheduleSetCollections", "createCollection", "renameCollection", "removeCollection",
        ):
            self.assertNotIn("innerHTML", function_body(name), name)
        icon = function_body("favIcon")
        self.assertEqual(icon.count("innerHTML"), 1)
        self.assertIn("span.innerHTML = ICONS[key]", icon)
        # 详情弹窗就地选夹
        detail2 = function_body("showDetail")
        self.assertIn('id="favCollections"', detail2)
        self.assertIn("refreshDetailChooser();", detail2)
        chip = function_body("buildCollectionChip")
        self.assertIn("scheduleSetCollections(item.fav_key)", chip)
        sched = function_body("scheduleSetCollections")
        self.assertIn("/api/favorites/set-collections", sched)
        self.assertIn("}, 350)", sched)
        # 收藏夹 CRUD 走对应端点
        self.assertIn("/api/collections", function_body("createCollection"))
        self.assertIn("/api/collections/rename", function_body("renameCollection"))
        self.assertIn("/api/collections/remove", function_body("removeCollection"))
        # 收藏与账号状态不进入 URL
        for token in ("account", "collection"):
            self.assertNotIn(token, function_body("syncURL").lower())
            self.assertNotIn(token, function_body("readURLState").lower())
        # 从个人视图打开某条收藏
        self.assertIn("if (accountViewOpen) {", function_body("openFavoriteItem"))

    def test_favorite_key_matches_server_normalization(self):
        term_map = re.search(r"const TERM_BY_PREFIX = \{[^}]*\};", HTML).group(0)
        level_map = re.search(r"const LEVEL_BY_PREFIX = \{[^}]*\};", HTML).group(0)
        self.run_node(
            f"""
            const assert = require('node:assert/strict');
            {term_map}
            {level_map}
            {function_source("favoriteKey")}
            assert.equal(
              favoriteKey({{ id: 'a1', course_code: '04831180', class_no: 1, teacher: ' 张三(教授) ' }}),
              'fall|ug|04831180|1|张三(教授)'
            );
            assert.equal(favoriteKey({{ id: 'r12', course_code: 'X', class_no: '2', teacher: '' }}), 'fall|gr|X|2|');
            assert.equal(favoriteKey({{ id: 'u5', course_code: 'C', class_no: null, teacher: null }}), 'spring|ug|C||');
            assert.equal(favoriteKey({{ id: 'g5', course_code: 'C', class_no: '1', teacher: 't' }}), 'spring|gr|C|1|t');
            assert.equal(favoriteKey({{ id: 's5', course_code: 'C', class_no: '1', teacher: 't' }}), 'summer|ug|C|1|t');
            assert.equal(favoriteKey({{ id: 'a7', course_code: 'C', class_no: 1, teacher: 't' }}),
                         favoriteKey({{ id: 'a9', course_code: 'C', class_no: '1', teacher: 't ' }}));
            """
        )


if __name__ == "__main__":
    unittest.main()
