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
    def run_node(self, script):
        if not NODE:
            self.skipTest("node is unavailable; JavaScript behavior contract skipped")
        result = subprocess.run(
            [NODE, "-e", textwrap.dedent(script)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_treehole_reviews_entry_is_adjacent_but_hidden_while_delisted(self):
        spring = HTML.index('id="termSpringBtn"')
        summer = HTML.index('id="termSummerBtn"')
        fall = HTML.index('id="termFallBtn"')
        reviews = HTML.index('id="reviewHubLink"')
        self.assertLess(spring, summer)
        self.assertLess(summer, fall)
        self.assertLess(fall, reviews)
        anchor = HTML[reviews:reviews + 180]
        self.assertIn('href="/reviews"', anchor)
        self.assertIn(" hidden>", anchor)
        self.assertIn(".review-hub-link[hidden] { display: none; }", HTML)

    def test_developer_contact_is_consistent_in_about_panel_and_footer(self):
        self.assertEqual(HTML.count("VX 联系方式："), 2)
        self.assertEqual(HTML.count("tuzengji"), 2)
        self.assertEqual(HTML.count("如果有问题或需求 欢迎联系！"), 2)
        self.assertIn('class="about-contact"', HTML)
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
        self.assertIn('.term-nav a, .review-nav-link { padding: 6px 14px; font-size: 0.82rem; }', REVIEWS_HTML)
        self.assertIn('.review-nav-shell { width: 100%;', REVIEWS_HTML)
        self.assertIn('.review-nav-link { width: 100%;', REVIEWS_HTML)
        self.assertIn('.term-nav a { flex-basis: 100%;', REVIEWS_HTML)

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
        # 顶栏右侧与课程页一致：主题、关于悬浮卡、GitHub、赞助悬浮卡
        self.assertIn('class="tip-wrap about-wrap"', REVIEWS_HTML)
        self.assertIn('class="tip-card about-tip"', REVIEWS_HTML)
        self.assertIn(".about-wrap:hover::after", REVIEWS_HTML)
        self.assertIn("user-select: text;", REVIEWS_HTML)
        self.assertIn('class="top-link sponsor-btn"', REVIEWS_HTML)
        self.assertIn('aria-label="GitHub"', REVIEWS_HTML)
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
              dept: '物理学院', day: '周一', grading: '百分制', sort: 'random',
              seed: '731', term: 'spring', lang: 'en', course: 'u42'
            }});
            currentTerm = 'fall';
            syncURL();
            params = new URL(replaced, 'http://local').searchParams;
            assert.equal(params.has('term'), false);
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

    def test_schedule_translation_preserves_source_and_adds_safe_boundaries(self):
        body = function_body("trSchedule")
        self.assertIn("let translated = String(s)", body)
        self.assertIn("return translated", body)
        self.assertRegex(body, r"replace\([^\n]+周")
        self.assertNotIn("split(", body)

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


if __name__ == "__main__":
    unittest.main()
