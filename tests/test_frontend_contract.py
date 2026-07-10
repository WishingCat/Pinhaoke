import re
import unittest
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


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


class FrontendContractTests(unittest.TestCase):
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

    def test_copy_syncs_and_uses_complete_location(self):
        body = function_body("copyCourseLink")
        self.assertIn("syncURL()", body)
        self.assertIn("location.href", body)
        self.assertNotIn("location.origin + location.pathname", body)

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
