import re
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
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
              await fall;
              pending.spring({{ term: 'spring' }});
              await spring;
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
        self.assertRegex(HTML, r"\.container\s*\{[^}]*min-width:\s*0")

    def test_privacy_and_dead_font_cleanup(self):
        self.assertNotIn("HarmonyOS Sans SC", HTML)
        self.assertNotIn("harmonyos-sans-font", HTML)
        self.assertIn("hm.src", HTML)
        self.assertIn("_hmt.push(['_setAutoPageview', false])", HTML)
        self.assertIn("_hmt.push(['_trackPageview', location.pathname])", HTML)


if __name__ == "__main__":
    unittest.main()
