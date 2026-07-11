import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "北京大学选课网数据抓取"
SCRIPTS = sorted(ROOT.glob("pku_inpage_*_scraper.js"))
UNDERGRAD_SCRIPTS = (
    ROOT / "pku_inpage_summer_scraper.js",
    ROOT / "pku_inpage_undergrad_2627_fall_scraper.js",
)


def script_prefix(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    start = source.index("\n") + 1
    end = source.rfind("\n  try {\n")
    if end < 0:
        raise AssertionError(f"top-level try block not found in {path.name}")
    return source[start:end]


def run_node(path: Path, harness: str) -> dict:
    program = (
        script_prefix(path)
        + "\n(async () => {\n"
        + harness
        + "\n})().catch((error) => {\n"
        + "  console.error(error && error.stack || error);\n"
        + "  process.exitCode = 1;\n"
        + "});\n"
    )
    result = subprocess.run(
        ["node", "-e", program],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise AssertionError(
            f"Node contract failed for {path.name}:\n{result.stderr}\n{result.stdout}"
        )
    return json.loads(result.stdout)


class ScraperContractTests(unittest.TestCase):
    def test_all_three_scrapers_keep_forbidden_actions_absent(self):
        self.assertEqual(len(SCRIPTS), 3)
        forbidden = ("addToPlan.do", "加入选课计划")
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, text, path.name)

    def test_all_scrapers_have_static_safety_contracts(self):
        validation_keys = (
            "totalRows",
            "duplicateSeqs",
            "duplicateKeys",
            "missingDetailLinks",
            "missingCourseCodes",
            "suspiciousPages",
        )
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            self.assertIn('const RECEIVER_TOKEN = "__RECEIVER_TOKEN__";', text, path.name)
            self.assertIn("async function fetchText(path, options = {}, retries = 2)", text, path.name)
            self.assertIn("AbortController", text, path.name)
            self.assertIn("clearTimeout(timer)", text, path.name)
            self.assertIn("function assertExpectedTerm()", text, path.name)
            self.assertIn("const detailBySeq = new Map();", text, path.name)
            self.assertEqual(text.count("const detailBySeq = new Map();"), 1, path.name)
            self.assertIn('"wlw-select_key:{actionForm.deptID}": "ALL"', text, path.name)
            self.assertGreaterEqual(text.count('"X-PKU-Receiver-Token": RECEIVER_TOKEN'), 2, path.name)
            for key in validation_keys:
                self.assertIn(key, text, path.name)
            for key in ("scrapedAt", "term", "stats", "rows", "pageStats", "validation", "errors: []"):
                self.assertIn(key, text, path.name)

    def test_terms_and_unique_keys_match_receiver_configs(self):
        expected = {
            "pku_inpage_summer_scraper.js": (
                "25-26学年第3学期",
                '["课程类型", "课程号", "班号"]',
            ),
            "pku_inpage_undergrad_2627_fall_scraper.js": (
                "26-27学年第1学期",
                '["课程类型", "课程号", "班号", "教师"]',
            ),
            "pku_inpage_graduate_2627_fall_scraper.js": (
                "26-27学年第1学期",
                '["课程号", "班号", "教师", "开课单位"]',
            ),
        }
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            term, unique_fields = expected[path.name]
            self.assertIn(f'const TERM = "{term}";', text, path.name)
            self.assertIn(f"const UNIQUE_KEY_FIELDS = {unique_fields};", text, path.name)

    def test_page_and_term_assertion_precedes_every_fetch(self):
        expected_path = (
            "/elective2008/edu/pku/stu/elective/controller/"
            "courseQuery/getCurriculmByForm.do"
        )
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            self.assertIn('const PKU_ORIGIN = "https://elective.pku.edu.cn";', text, path.name)
            self.assertIn(expected_path, text, path.name)
            top_level = text[text.rfind("\n  try {\n") :]
            self.assertLess(
                top_level.index("assertExpectedTerm();"),
                top_level.index("postProgress("),
                path.name,
            )

    def test_page_and_term_assertion_rejects_wrong_context(self):
        harness = r'''
globalThis.location = {
  origin: PKU_ORIGIN,
  pathname: QUERY_PATH,
  href: `${PKU_ORIGIN}${QUERY_PATH}`,
};
globalThis.document = { body: { innerText: `current ${TERM}` } };
let correctAccepted = true;
try { assertExpectedTerm(); } catch (_) { correctAccepted = false; }
location.pathname = "/wrong";
let wrongPageRejected = false;
try { assertExpectedTerm(); } catch (_) { wrongPageRejected = true; }
location.pathname = QUERY_PATH;
document.body.innerText = "different semester";
let wrongTermRejected = false;
try { assertExpectedTerm(); } catch (_) { wrongTermRejected = true; }
console.log(JSON.stringify({ correctAccepted, wrongPageRejected, wrongTermRejected }));
'''
        for path in SCRIPTS:
            result = run_node(path, harness)
            self.assertTrue(result["correctAccepted"], path.name)
            self.assertTrue(result["wrongPageRejected"], path.name)
            self.assertTrue(result["wrongTermRejected"], path.name)

    def test_undergraduate_scrapers_fail_the_whole_run_on_type_error(self):
        for path in UNDERGRAD_SCRIPTS:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("errors.push({ type:", text, path.name)
            loop = text[text.rfind("for (const [typeName, typeValue] of COURSE_TYPES)") :]
            self.assertNotIn("type_error", loop, path.name)

    def test_fetch_text_retries_only_transient_failures_and_cleans_timers(self):
        harness = r'''
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
let nextTimer = 0;
const activeTimers = new Set();
globalThis.setTimeout = (callback, delay) => {
  const id = ++nextTimer;
  activeTimers.add(id);
  queueMicrotask(() => {
    if (!activeTimers.delete(id)) return;
    callback();
  });
  return id;
};
globalThis.clearTimeout = (id) => activeTimers.delete(id);

const calls = [];
let attempt = 0;
globalThis.fetch = async (url, options) => {
  calls.push({ url, options });
  attempt += 1;
  if (attempt < 3) {
    return { ok: false, status: 503, text: async () => "busy" };
  }
  return { ok: true, status: 200, text: async () => "ok" };
};
const html = await fetchText(QUERY_PATH, {
  method: "POST",
  headers: { "X-PKU-Receiver-Token": "must-not-leak" },
});
const transient = {
  html,
  attempts: calls.length,
  credentials: calls.map((call) => call.options.credentials),
  signalsUnique: new Set(calls.map((call) => call.options.signal)).size,
  leakedToken: calls.some((call) => Object.keys(call.options.headers).some(
    (key) => key.toLowerCase() === "x-pku-receiver-token"
  )),
  activeTimers: activeTimers.size,
};

let deterministicAttempts = 0;
globalThis.fetch = async () => {
  deterministicAttempts += 1;
  return { ok: false, status: 404, text: async () => "missing" };
};
let deterministicError = "";
try { await fetchText(QUERY_PATH); } catch (error) { deterministicError = error.message; }

let promptAttempts = 0;
globalThis.fetch = async () => {
  promptAttempts += 1;
  return { ok: true, status: 200, text: async () => "<title>系统提示</title>" };
};
let promptError = "";
try { await fetchText(QUERY_PATH); } catch (error) { promptError = error.message; }

let timeoutAttempts = 0;
const timeoutSignals = [];
globalThis.fetch = (url, options) => {
  timeoutAttempts += 1;
  timeoutSignals.push(options.signal);
  return new Promise((resolve, reject) => {
    options.signal.addEventListener("abort", () => {
      reject(new DOMException("timed out", "AbortError"));
    }, { once: true });
  });
};
let timeoutError = "";
try { await fetchText(QUERY_PATH); } catch (error) { timeoutError = error.name; }

globalThis.setTimeout = realSetTimeout;
globalThis.clearTimeout = realClearTimeout;
console.log(JSON.stringify({
  transient,
  deterministicAttempts,
  deterministicError,
  promptAttempts,
  promptError,
  timeoutAttempts,
  timeoutSignalsUnique: new Set(timeoutSignals).size,
  finalActiveTimers: activeTimers.size,
}));
'''
        for path in SCRIPTS:
            result = run_node(path, harness)
            self.assertEqual(result["transient"]["html"], "ok", path.name)
            self.assertEqual(result["transient"]["attempts"], 3, path.name)
            self.assertEqual(result["transient"]["credentials"], ["include"] * 3, path.name)
            self.assertEqual(result["transient"]["signalsUnique"], 3, path.name)
            self.assertFalse(result["transient"]["leakedToken"], path.name)
            self.assertEqual(result["transient"]["activeTimers"], 0, path.name)
            self.assertEqual(result["deterministicAttempts"], 1, path.name)
            self.assertIn("404", result["deterministicError"], path.name)
            self.assertEqual(result["promptAttempts"], 1, path.name)
            self.assertIn("PKU_SYSTEM_PROMPT", result["promptError"], path.name)
            self.assertEqual(result["timeoutAttempts"], 3, path.name)
            self.assertEqual(result["timeoutSignalsUnique"], 3, path.name)
            self.assertEqual(result["finalActiveTimers"], 0, path.name)

    def test_detail_cache_shares_inflight_work_and_evicts_failures(self):
        harness = r'''
let fetchCalls = 0;
let failFirst = false;
globalThis.fetchText = async () => {
  fetchCalls += 1;
  if (failFirst) {
    failFirst = false;
    throw new TypeError("temporary network failure");
  }
  await Promise.resolve();
  return "detail html";
};
globalThis.parseDetail = () => Object.fromEntries(DETAIL_FIELDS.map((field) => [field, "value"]));
const link = `${PKU_ORIGIN}${DETAIL_PATH}?course_seq_no=SEQ-1`;
const first = { "课程序号": "SEQ-1", "详情链接": link };
const second = { "课程序号": "SEQ-1", "详情链接": link };
const details = await Promise.all([loadDetail(first), loadDetail(second)]);
const sharedFetchCalls = fetchCalls;
const sharedObject = details[0] === details[1];

detailBySeq.clear();
fetchCalls = 0;
failFirst = true;
let firstFailed = false;
try { await loadDetail(first); } catch (_) { firstFailed = true; }
const cacheEmptyAfterFailure = detailBySeq.size === 0;
await loadDetail(first);
console.log(JSON.stringify({
  sharedFetchCalls,
  sharedObject,
  firstFailed,
  cacheEmptyAfterFailure,
  retryFetchCalls: fetchCalls,
}));
'''
        for path in SCRIPTS:
            result = run_node(path, harness)
            self.assertEqual(result["sharedFetchCalls"], 1, path.name)
            self.assertTrue(result["sharedObject"], path.name)
            self.assertTrue(result["firstFailed"], path.name)
            self.assertTrue(result["cacheEmptyAfterFailure"], path.name)
            self.assertEqual(result["retryFetchCalls"], 2, path.name)

    def test_local_validation_allows_repeated_sequences_but_rejects_bad_payloads(self):
        harness = r'''
const isUndergraduate = typeof COURSE_TYPES !== "undefined";
const makeBasic = (suffix) => {
  const basic = Object.fromEntries(BASIC_HEADERS.map((field) => [field, `${field}-${suffix}`]));
  basic["课程号"] = `CODE-${suffix}`;
  basic["班号"] = `CLASS-${suffix}`;
  basic["教师"] = `TEACHER-${suffix}`;
  basic["开课单位"] = `DEPT-${suffix}`;
  return basic;
};
const makeRow = (suffix, typeName) => ({
  ...(isUndergraduate ? { "课程类型": typeName } : {}),
  "数据学期": TERM,
  "课程序号": "REPEATED-SEQ",
  "详情链接": `${PKU_ORIGIN}${DETAIL_PATH}?course_seq_no=REPEATED-SEQ`,
  "基本信息": makeBasic(suffix),
  "详细信息": Object.fromEntries(DETAIL_FIELDS.map((field) => [field, ""])),
});
const typeNames = isUndergraduate ? COURSE_TYPES.map(([name]) => name) : [];
const rows = [
  makeRow("1", isUndergraduate ? typeNames[0] : undefined),
  makeRow("2", isUndergraduate ? typeNames[1] : undefined),
];
const stats = isUndergraduate
  ? Object.fromEntries(typeNames.map((name) => [name, rows.filter((row) => row["课程类型"] === name).length]))
  : { "研究生课": rows.length };
const pageStats = isUndergraduate
  ? Object.fromEntries(typeNames.map((name) => [name, [{ page: 1, value: "1", rows: stats[name] }]]))
  : [{ page: 1, value: "1", rows: rows.length }];
const validation = validatePayload(rows, pageStats, stats);

const expectFailure = (mutate) => {
  const badRows = structuredClone(rows);
  const badPages = structuredClone(pageStats);
  const badStats = structuredClone(stats);
  mutate(badRows, badPages, badStats);
  try {
    validatePayload(badRows, badPages, badStats);
    return false;
  } catch (_) {
    return true;
  }
};
const duplicateKeyRejected = expectFailure((badRows) => {
  badRows[1]["基本信息"] = structuredClone(badRows[0]["基本信息"]);
  if (isUndergraduate) badRows[1]["课程类型"] = badRows[0]["课程类型"];
});
const missingCodeRejected = expectFailure((badRows) => { badRows[0]["基本信息"]["课程号"] = ""; });
const incompleteDetailRejected = expectFailure((badRows) => { delete badRows[0]["详细信息"][DETAIL_FIELDS[0]]; });
const credentialLinkRejected = expectFailure((badRows) => {
  badRows[0]["详情链接"] = `https://user@elective.pku.edu.cn${DETAIL_PATH}?course_seq_no=REPEATED-SEQ`;
});
const malformedQueryRejected = expectFailure((badRows) => {
  badRows[0]["详情链接"] = `${PKU_ORIGIN}${DETAIL_PATH}?course_seq_no=REPEATED-SEQ&`;
});
const suspiciousPageRejected = expectFailure((badRows, badPages) => {
  if (isUndergraduate) {
    badPages[typeNames[0]] = [
      { page: 1, value: "1", rows: 1 },
      { page: 2, value: "2", rows: 0 },
    ];
  } else {
    badPages.splice(0, 1,
      { page: 1, value: "1", rows: 1 },
      { page: 2, value: "2", rows: 1 },
    );
  }
});
console.log(JSON.stringify({
  duplicateSeqs: validation.duplicateSeqs,
  validationKeys: Object.keys(validation).sort(),
  duplicateKeyRejected,
  missingCodeRejected,
  incompleteDetailRejected,
  credentialLinkRejected,
  malformedQueryRejected,
  suspiciousPageRejected,
}));
'''
        expected_keys = sorted(
            [
                "totalRows",
                "duplicateSeqs",
                "duplicateKeys",
                "missingDetailLinks",
                "missingCourseCodes",
                "suspiciousPages",
            ]
        )
        for path in SCRIPTS:
            result = run_node(path, harness)
            self.assertEqual(result["duplicateSeqs"], ["REPEATED-SEQ"], path.name)
            self.assertEqual(result["validationKeys"], expected_keys, path.name)
            self.assertTrue(result["duplicateKeyRejected"], path.name)
            self.assertTrue(result["missingCodeRejected"], path.name)
            self.assertTrue(result["incompleteDetailRejected"], path.name)
            self.assertTrue(result["credentialLinkRejected"], path.name)
            self.assertTrue(result["malformedQueryRejected"], path.name)
            self.assertTrue(result["suspiciousPageRejected"], path.name)


if __name__ == "__main__":
    unittest.main()
