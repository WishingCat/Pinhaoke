# 拼好课全面修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复已复现的课程合并、搜索排序、详情、前端交互、抓取建库、翻译、部署和文档问题，并安全释放本机 Git 中约 124.23 GiB 的不可达对象。

**Architecture:** 保持 FastAPI、单文件 SPA 和五个 SQLite 数据库的现有架构。后端用只读连接和两阶段分组查询保证课程语义，数据流水线通过共享的原子写入与校验帮助函数避免破坏正式文件，前端继续在 `index.html` 内实现但补齐状态、可访问性和隐私约束。

**Tech Stack:** Python 3.12、FastAPI 0.136.1、Starlette 1.3.1、Uvicorn 0.44.0、SQLite、原生 JavaScript、标准库 `unittest`、Nginx、systemd、Git LFS。

## Global Constraints

- 不重新抓取选课网，不触碰或请求 `加入选课计划` / `addToPlan.do`。
- 不调用 DeepSeek 或其他付费翻译 API。
- 不重建或改写当前五个正式数据库。
- 不改变现有 `a/r/u/g/s` 课程 ID 与公开 URL。
- 不拆分 `app.py` 或 `index.html` 到新框架。
- 不 push、不部署、不修改生产服务器；所有生产相关修改只落在仓库文件。
- 只使用标准库 `unittest` 作为项目测试框架；浏览器验收使用本机 Playwright 工具。
- 每项修复先写会失败的回归测试，再写最小实现，最后运行相关测试和全量测试。
- 任何现有数据库、JSON、译文和用户未提交修改都不得被覆盖。

---

### Task 1: Git LFS 预检与不可达对象清理

**Files:**
- Inspect: `.gitattributes`
- Inspect: `数据库/2026秋季学期本科生课程.db`
- Inspect: `数据库/2026秋季学期研究生课程.db`

**Interfaces:**
- Consumes: 当前 `main`、`origin/main`、Git LFS 指针与五个 SQLite 数据库。
- Produces: 不改变任何引用或工作文件、但移除 15,240 个不可达对象的健康仓库。

- [ ] **Step 1: 记录清理前证据**

```bash
git status --porcelain=v1
git rev-parse HEAD
git rev-parse origin/main
git merge-base --is-ancestor origin/main HEAD
git count-objects -vH
git fsck --unreachable --no-reflogs
git prune --dry-run --expire=now | wc -l
git show-ref > /tmp/pinhaoke-show-ref-before.txt
git reflog show --all --format='%H %gD' > /tmp/pinhaoke-reflog-before.txt
du -sh .git .git/lfs
df -h .
```

Expected: 工作树为空；`origin/main` 是当前本地提交的祖先；dry-run 精确列出 15,240 个对象；`.git` 约 125 GiB。

- [ ] **Step 2: 安装并初始化本机 Git LFS**

```bash
brew install git-lfs
git lfs install --local
git lfs version
git lfs ls-files
git lfs fsck
```

Expected: `git-lfs` 可执行；两个秋季数据库列在 `git lfs ls-files` 中；LFS 校验通过。若 Homebrew 已安装该包，第一条命令只确认现状。

- [ ] **Step 3: 记录五库只读完整性与 LFS 工作文件哈希**

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib
import sqlite3

for path in sorted(Path("数据库").glob("*.db")):
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        basic = conn.execute("SELECT COUNT(*) FROM basic_info").fetchone()[0]
        detail = conn.execute("SELECT COUNT(*) FROM detail_info").fetchone()[0]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(path.name, integrity, basic, detail, digest)
PY
```

Expected: 五库均为 `ok`，且每库 `basic == detail`；保存输出供 Step 5 对比。

- [ ] **Step 4: 先直接删除不可达松散对象，再压缩剩余仓库**

```bash
git prune --expire=now
git -c gc.reflogExpire=never -c gc.reflogExpireUnreachable=never gc --prune=now
```

Expected: 命令成功；因为先 prune，不需要为 124 GiB 不可达对象额外创建同等大小的 pack。

- [ ] **Step 5: 复验仓库、LFS、数据库与空间**

```bash
git fsck --full
git status --porcelain=v1
git count-objects -vH
git lfs fsck
git show-ref > /tmp/pinhaoke-show-ref-after.txt
git reflog show --all --format='%H %gD' > /tmp/pinhaoke-reflog-after.txt
cmp /tmp/pinhaoke-show-ref-before.txt /tmp/pinhaoke-show-ref-after.txt
cmp /tmp/pinhaoke-reflog-before.txt /tmp/pinhaoke-reflog-after.txt
du -sh .git .git/lfs
df -h .
```

Expected: `git fsck --full` 无错误；工作树仍为空；不可达对象为 0；`.git` 下降约 124.23 GiB。重新运行 Step 3 的脚本时五库计数和 SHA-256 与清理前完全一致。

---

### Task 2: 建立后端回归测试与只读连接基线

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_app.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `TERM_DBS: dict[str, list[tuple[str, Path, str]]]`。
- Produces: `get_db(term: str = "fall")`、`check_database_health() -> dict`、`GET /api/health`。

- [ ] **Step 1: 写只读连接、默认学期、绝对静态路径与健康检查失败测试**

```python
# tests/test_app.py
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DatabaseConnectionTests(unittest.TestCase):
    def test_get_db_defaults_to_fall_and_is_query_only(self):
        with app.get_db() as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE forbidden_write(id INTEGER)")

    def test_app_imports_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = "import app; print(app.root().path)"
            env = {"PYTHONPATH": str(app.BASE_DIR)}
            result = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), app.BASE_DIR / "index.html")

    def test_attach_failure_closes_main_connection(self):
        fake = app.sqlite3.connect(":memory:")
        with patch.object(app.sqlite3, "connect", return_value=fake):
            with patch.dict(app.TERM_DBS, {"broken": [("main", Path("a.db"), "x"), ("gr", Path("missing.db"), "y")]}):
                with self.assertRaises(sqlite3.OperationalError):
                    with app.get_db("broken"):
                        pass
        with self.assertRaises(sqlite3.ProgrammingError):
            fake.execute("SELECT 1")

    def test_health_reports_all_five_databases(self):
        payload = app.check_database_health()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["databases"]), 5)
        self.assertTrue(all(item["integrity"] == "ok" for item in payload["databases"]))
```

- [ ] **Step 2: 运行测试并确认旧实现失败**

Run: `python3 -m unittest tests.test_app.DatabaseConnectionTests -v`

Expected: 默认学期、只读模式、绝对静态路径、连接关闭或健康函数至少一项失败。

- [ ] **Step 3: 实现只读 URI、可靠关闭和健康检查**

```python
def _readonly_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


@contextmanager
def get_db(term: str = "fall"):
    config = TERM_DBS.get(term)
    if config is None:
        raise HTTPException(status_code=400, detail=f"Unknown term: {term}")
    conn = None
    try:
        conn = sqlite3.connect(_readonly_uri(config[0][1]), uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        for alias, path, _ in config[1:]:
            conn.execute(f"ATTACH DATABASE ? AS {alias}", (_readonly_uri(path),))
        yield conn
    finally:
        if conn is not None:
            conn.close()


def check_database_health() -> dict:
    databases = []
    for term, entries in TERM_DBS.items():
        for alias, path, prefix in entries:
            with sqlite3.connect(_readonly_uri(path), uri=True) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                basic = conn.execute("SELECT COUNT(*) FROM basic_info").fetchone()[0]
                detail = conn.execute("SELECT COUNT(*) FROM detail_info").fetchone()[0]
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if not {"basic_info", "detail_info", "translations"}.issubset(tables) or basic != detail or integrity != "ok":
                raise RuntimeError(f"Unhealthy database: {path.name}")
            databases.append({"term": term, "level": alias, "prefix": prefix, "file": path.name, "integrity": integrity, "basic": basic, "detail": detail})
    return {"status": "ok", "databases": databases}
```

Mount `StaticFiles(directory=str(BASE_DIR / "Images"))`, return `FileResponse(BASE_DIR / "index.html")`, and expose `@app.get("/api/health")` returning the health payload with `Cache-Control: no-store`.

- [ ] **Step 4: 运行连接测试与 Python 编译**

Run: `python3 -m unittest tests.test_app.DatabaseConnectionTests -v && python3 -m py_compile app.py`

Expected: 全部通过。

- [ ] **Step 5: 提交连接基线**

```bash
git add app.py tests/__init__.py tests/test_app.py
git commit -m "test: add read-only database health checks"
```

---

### Task 3: 修复课程合并、筛选、翻译搜索与稳定排序

**Files:**
- Modify: `tests/test_app.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `TERM_UNION_SQL` 与五库 `translations(course_id, field, lang, text)`。
- Produces: `_build_course_query(term: str, lang: str, filters: dict[str, object]) -> tuple[str, list[object], str]`，返回源 CTE SQL、参数和分组筛选 SQL；列表总数 fall=4421、spring=3701、summer=160。

- [ ] **Step 1: 写课程总数、本研分离、两阶段筛选、翻译搜索与排序测试**

```python
class CourseListTests(unittest.TestCase):
    def call(self, **overrides):
        args = dict(q="", type="", category="", credits="", department="", weekday="", grading="", classroom="", sort="", random_seed=0, lang="zh", term="fall", page=1, page_size=200)
        args.update(overrides)
        return app.list_courses(**args)

    def all_ids(self, term, sort="", random_seed=0, lang="zh", q=""):
        first = self.call(term=term, sort=sort, random_seed=random_seed, lang=lang, q=q)
        pages = (first["total"] + 199) // 200
        ids = []
        for page in range(1, pages + 1):
            ids.extend(c["id"] for c in self.call(term=term, sort=sort, random_seed=random_seed, lang=lang, q=q, page=page)["courses"])
        return first["total"], ids

    def test_card_totals_keep_undergrad_and_graduate_separate(self):
        self.assertEqual(self.call(term="fall", page_size=1)["total"], 4421)
        self.assertEqual(self.call(term="spring", page_size=1)["total"], 3701)
        self.assertEqual(self.call(term="summer", page_size=1)["total"], 160)

    def test_filter_preserves_representative_id_and_all_badges(self):
        unfiltered = self.call(term="fall")
        target = next(c for c in unfiltered["courses"] if len(c["category"]) > 1)
        filtered = self.call(term="fall", category=target["category"][0])
        same = next(c for c in filtered["courses"] if c["course_code"] == target["course_code"] and c["class_no"] == target["class_no"] and c["teacher"] == target["teacher"])
        self.assertEqual(same["id"], target["id"])
        self.assertEqual(same["category"], target["category"])

    def test_random_sort_is_stable_complete_and_unique(self):
        total, first = self.all_ids("summer", sort="random", random_seed=731)
        _, second = self.all_ids("summer", sort="random", random_seed=731)
        self.assertEqual(first, second)
        self.assertEqual(len(first), total)
        self.assertEqual(len(set(first)), total)

    def test_translated_course_name_is_searchable_and_sorted_by_display_name(self):
        with app.get_db("fall") as conn:
            row = conn.execute("SELECT 'a' || t.course_id, t.text FROM translations t JOIN basic_info b ON b.id=t.course_id WHERE t.lang='en' AND t.field='course_name' AND TRIM(t.text) != '' AND t.text != b.course_name LIMIT 1").fetchone()
        sample_id, translated_name = row
        token = translated_name.split()[0]
        result = self.call(term="fall", lang="en", q=token, sort="name_asc")
        self.assertTrue(any(c["id"] == sample_id for c in result["courses"]))
        names = [c["course_name"].casefold() for c in result["courses"]]
        self.assertEqual(names, sorted(names))
```

- [ ] **Step 2: 运行列表测试并确认旧查询失败**

Run: `python3 -m unittest tests.test_app.CourseListTests -v`

Expected: 旧实现返回 fall=4420、spring=3697，筛选后徽章变化，译名搜索失败。

- [ ] **Step 3: 改为带显示译文的源 CTE、命中组 CTE和完整组聚合**

查询结构固定为：

```sql
WITH source AS (
    SELECT t.*,
           COALESCE(NULLIF(TRIM(name_tr.text), ''), t.course_name) AS display_course_name,
           COALESCE(NULLIF(TRIM(room_tr.text), ''), t.classroom) AS display_classroom,
           COALESCE(NULLIF(TRIM(notes_tr.text), ''), t.notes) AS display_notes,
           t._level || char(31) || t.course_code || char(31) || t.class_no || char(31) ||
             CASE WHEN COALESCE(t.teacher, '') = '' THEN t.id ELSE t.teacher END AS group_key
    FROM TERM_UNION AS t
    LEFT JOIN translations_for_requested_language
), matching_groups AS (
    SELECT DISTINCT group_key FROM source WHERE validated_filters
), ranked AS (
    SELECT source.*,
           ROW_NUMBER() OVER (
             PARTITION BY group_key
             ORDER BY detail_score DESC, CAST(SUBSTR(id, 2) AS INTEGER), id
           ) AS representative_rank
    FROM source JOIN matching_groups USING (group_key)
), grouped AS (
    SELECT MAX(CASE WHEN representative_rank = 1 THEN id END) AS id,
           GROUP_CONCAT(DISTINCT course_type) AS course_type,
           GROUP_CONCAT(DISTINCT category) AS category,
           MAX(display_course_name) FILTER (WHERE representative_rank = 1) AS course_name,
           MAX(display_classroom) FILTER (WHERE representative_rank = 1) AS classroom,
           MAX(display_notes) FILTER (WHERE representative_rank = 1) AS notes
    FROM ranked
    GROUP BY group_key
)
SELECT * FROM grouped
```

在每个 UG/GR SELECT 内联接各自 namespace 的三条翻译别名，保持 UNION 列数一致。`detail_score` 是列表和详情可用字段的非空计数。所有 ORDER BY 末尾添加 `id`；随机哈希在 `grouped` 外层计算。新排序名为 `name_asc` / `name_desc`，旧 `pinyin` / `pinyin_desc` 映射到同一表达式。

- [ ] **Step 4: 运行列表测试并核对精确总数**

Run: `python3 -m unittest tests.test_app.CourseListTests -v`

Expected: 全部通过；三学期总数精确为 4421、3701、160。

- [ ] **Step 5: 提交列表查询修复**

```bash
git add app.py tests/test_app.py
git commit -m "fix: preserve course groups across filters"
```

---

### Task 4: 严格参数、完整详情与空白译文回退

**Files:**
- Modify: `tests/test_app.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `_parse_id(course_id: str)`、`TRANSLATABLE_FIELDS`。
- Produces: 严格 ID、参数 422、详情中的 `textbook` / `reference_book`。

- [ ] **Step 1: 写非法输入、详情字段和空白译文测试**

```python
class ValidationAndDetailTests(unittest.TestCase):
    def test_course_id_is_canonical(self):
        self.assertEqual(app._parse_id("a1"), ("fall", "a", 1))
        for value in ("", "a0", "a01", "a+1", "a-1", "x1"):
            self.assertEqual(app._parse_id(value), (None, None, None))

    def test_invalid_filter_values_raise_422(self):
        base = dict(q="", type="", category="", credits="", department="", weekday="", grading="", classroom="", sort="", random_seed=0, lang="zh", term="fall", page=1, page_size=20)
        for key, value in (("credits", "abc"), ("weekday", "%"), ("lang", "xx"), ("sort", "drop"), ("term", "winter"), ("page", 100001)):
            args = dict(base)
            args[key] = value
            with self.assertRaises(app.HTTPException) as ctx:
                app.list_courses(**args)
            self.assertEqual(ctx.exception.status_code, 422)

    def test_valid_but_out_of_range_page_is_empty(self):
        result = app.list_courses(q="", type="", category="", credits="", department="", weekday="", grading="", classroom="", sort="", random_seed=0, lang="zh", term="summer", page=10000, page_size=20)
        self.assertEqual(result["courses"], [])

    def test_detail_language_is_validated(self):
        with self.assertRaises(app.HTTPException) as ctx:
            app.get_course_detail("a1", lang="xx")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_detail_exposes_books(self):
        detail = app.get_course_detail("a1", lang="zh")
        self.assertIn("textbook", detail)
        self.assertIn("reference_book", detail)

    def test_blank_translation_does_not_replace_original(self):
        out = {"course_name": "原名"}
        cursor = unittest.mock.Mock()
        cursor.execute.return_value.fetchall.return_value = [("course_name", "   ")]
        app._apply_translations(cursor, "main", 1, "en", out)
        self.assertEqual(out["course_name"], "原名")
```

- [ ] **Step 2: 运行测试并确认旧实现失败**

Run: `python3 -m unittest tests.test_app.ValidationAndDetailTests -v`

Expected: 非法 ID、非法 credits、书目字段或空白回退失败。

- [ ] **Step 3: 增加统一允许列表与详情字段**

```python
VALID_TERMS = frozenset(TERM_DBS)
VALID_LANGS = frozenset({"zh", "en", "ja", "ko", "fr", "de", "es", "ru"})
VALID_WEEKDAYS = frozenset({"", "周一", "周二", "周三", "周四", "周五", "周六", "周日"})
VALID_SORTS = frozenset({"", "name_asc", "name_desc", "pinyin", "pinyin_desc", "credits_asc", "credits_desc", "time_asc", "random"})
COURSE_ID_RE = re.compile(r"^[ugsar][1-9][0-9]*$")


def _validate_list_params(term, lang, weekday, sort, credits, page):
    if term not in VALID_TERMS or lang not in VALID_LANGS or weekday not in VALID_WEEKDAYS or sort not in VALID_SORTS or page > 10000:
        raise HTTPException(status_code=422, detail="Invalid course query parameter")
    if credits:
        try:
            return float(credits)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid credits") from exc
    return None
```

在 UG 详情 SELECT 加入 `d.textbook, d.reference_book`；GR 详情把 `d.reference_book` 映射到同名字段并把 `textbook` 置空。将两字段加入 `TRANSLATABLE_FIELDS` 和统一 `setdefault`。所有译文仅在 `isinstance(text, str) and text.strip()` 时覆盖。

- [ ] **Step 4: 运行后端全量测试**

Run: `python3 -m unittest tests.test_app -v`

Expected: 全部通过。

- [ ] **Step 5: 提交参数与详情修复**

```bash
git add app.py tests/test_app.py
git commit -m "fix: validate course queries and expose book details"
```

---

### Task 5: 修复前端分页、链接、多语言、可访问性与窄屏

**Files:**
- Create: `tests/test_frontend_contract.py`
- Modify: `index.html`

**Interfaces:**
- Consumes: API 新排序值与详情 `textbook` / `reference_book`。
- Produces: 单请求 `loadMore()`、完整共享链接、键盘可用筛选、焦点受控弹窗和 320px 无横向溢出的页面。

- [ ] **Step 1: 写静态契约测试**

```python
# tests/test_frontend_contract.py
import re
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class FrontendContractTests(unittest.TestCase):
    def test_load_more_has_reentrancy_guard_before_page_change(self):
        body = re.search(r"function loadMore\(\)\s*\{([\s\S]*?)\n\}", HTML).group(1)
        self.assertLess(body.index("if (isLoading || !hasMore) return"), body.index("currentPage"))

    def test_copy_syncs_and_uses_complete_location(self):
        body = re.search(r"async function copyCourseLink\(\)\s*\{([\s\S]*?)\n\}", HTML).group(1)
        self.assertIn("syncURL()", body)
        self.assertIn("location.href", body)

    def test_sort_copy_no_longer_claims_pinyin(self):
        self.assertIn("name_asc", HTML)
        self.assertIn("name_desc", HTML)
        self.assertNotIn("sortPinyin", HTML)

    def test_detail_renders_book_fields(self):
        self.assertIn("detail.textbook", HTML)
        self.assertIn("detail.reference_book", HTML)

    def test_modal_and_custom_select_have_accessible_roles(self):
        self.assertIn('role="dialog"', HTML)
        self.assertIn('role="listbox"', HTML)
        self.assertIn('role="option"', HTML)
        self.assertIn("aria-expanded", HTML)
        self.assertIn("inert", HTML)

    def test_privacy_and_dead_font_cleanup(self):
        self.assertNotIn("HarmonyOS Sans SC", HTML)
        self.assertIn("hm.src", HTML)
        self.assertIn("_hmt.push(['_setAutoPageview', false])", HTML)
```

- [ ] **Step 2: 运行契约测试并确认旧页面失败**

Run: `python3 -m unittest tests.test_frontend_contract -v`

Expected: 分页保护、链接、排序文案、书目、可访问性、字体或统计至少一项失败。

- [ ] **Step 3: 修复分页和共享链接状态机**

```javascript
async function loadMore() {
  if (isLoading || !hasMore) return;
  const nextPage = currentPage + 1;
  isLoading = true;
  loadMoreBtn.disabled = true;
  try {
    const appended = await fetchCourses({ page: nextPage, append: true });
    if (appended) currentPage = nextPage;
  } finally {
    isLoading = false;
    loadMoreBtn.disabled = !hasMore;
  }
}

async function copyCourseLink() {
  syncURL();
  await navigator.clipboard.writeText(location.href);
}
```

让 `fetchCourses({page, append})` 只负责请求和渲染并返回布尔结果；AbortError 返回 `false`，HTTP 错误显示错误态且不修改页码。

- [ ] **Step 4: 修复排序文案、详情标签、课表与字典**

排序选项改成 `name_asc` / `name_desc`，七种译文统一表达“课程名正序/倒序”。详情增加教材与参考书。非中文语言下 `intro_cn` 标签使用“翻译简介”，`intro_en` 使用“英文简介”；中文界面保持“中文简介/英文简介”。`trSchedule()` 在连接的周次、星期、节次和教室之间插入安全空格，只替换已知词组并保留未知原文。

在浏览器测试中读取 `/api/filters` 的全部 `course_types`、`categories`、`departments`、`gradings`，逐项调用有限字典翻译函数；将所有仍回退中文的审计缺口补入 `dataI18n`，同时允许专有名称在确无约定译法时显式使用中文值，避免静默缺键。

- [ ] **Step 5: 实现 custom select 键盘语义与弹窗焦点管理**

```javascript
function openCustomSelect(container) {
  container.classList.add('open');
  const trigger = container.querySelector('.custom-select-trigger');
  trigger.setAttribute('aria-expanded', 'true');
  const active = container.querySelector('.custom-select-option.active') || container.querySelector('.custom-select-option');
  if (active) active.focus();
}

function trapModalFocus(event) {
  if (event.key !== 'Tab') return;
  const focusable = [...courseModal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter(el => !el.disabled);
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}
```

Trigger 使用 `role="combobox" aria-haspopup="listbox" aria-expanded="false"`；dropdown 使用唯一 id 与 `role="listbox"`；option 使用 `role="option" tabindex="-1" aria-selected`。支持 ArrowUp/Down、Home、End、Enter、Space、Escape。弹窗打开时保存 `modalReturnFocus`、设置页面主容器 `inert`、聚焦关闭按钮；关闭时移除 `inert` 并恢复焦点。

- [ ] **Step 6: 修复 320px 布局、对比度、字体与统计隐私**

增加 `min-width: 0`、可换行学期分段控件与 320px 媒体规则，确保所有固定宽度工具栏使用 `max-width: 100%`。浅色正文和辅助文本达到 WCAG AA。删除 HarmonyOS 字体名。百度统计初始化为：

```javascript
window._hmt = window._hmt || [];
window._hmt.push(['_setAutoPageview', false]);
window._hmt.push(['_trackPageview', location.pathname]);
```

- [ ] **Step 7: 运行静态测试和真实浏览器验收**

Run: `python3 -m unittest tests.test_frontend_contract -v`

Run local server: `python3 -m uvicorn app:app --host 127.0.0.1 --port 8000`

Playwright viewports: `1440x900`、`390x844`、`320x568`。验证秋季默认项位于第三个学期选项；连续触发加载只增加一页；复制链接保留筛选和弹窗；筛选器可全键盘操作；弹窗焦点不逃逸；`document.documentElement.scrollWidth === document.documentElement.clientWidth`；控制台无错误。

- [ ] **Step 8: 提交前端修复**

```bash
git add index.html tests/test_frontend_contract.py
git commit -m "fix: stabilize course browsing interactions"
```

---

### Task 6: 让五个建库入口原子化并统一 schema 校验

**Files:**
- Create: `数据库构建脚本/build_atomic.py`
- Create: `tests/test_builders.py`
- Modify: `数据库构建脚本/build_undergrad_db.py`
- Modify: `数据库构建脚本/build_graduate_db.py`
- Modify: `北京大学选课网数据抓取/build_summer_db.py`
- Modify: `北京大学选课网数据抓取/build_undergrad_2627_fall_db.py`
- Modify: `北京大学选课网数据抓取/build_graduate_2627_fall_db.py`

**Interfaces:**
- Consumes: 每个入口现有 `SCHEMA` 和经验证的 JSON 行。
- Produces: `atomic_database(target: Path, schema: str)` context manager、`validate_built_database(conn)`。

- [ ] **Step 1: 写原子替换、translations schema 与冲突拒绝测试**

```python
# tests/test_builders.py
import sqlite3
import tempfile
import unittest
from pathlib import Path

from 数据库构建脚本.build_atomic import atomic_database, validate_built_database


class AtomicBuilderTests(unittest.TestCase):
    def test_failure_keeps_existing_database_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "courses.db"
            target.write_bytes(b"official")
            with self.assertRaises(RuntimeError):
                with atomic_database(target, "CREATE TABLE basic_info(id INTEGER); CREATE TABLE detail_info(course_id INTEGER);") as conn:
                    conn.execute("INSERT INTO basic_info VALUES (1)")
                    raise RuntimeError("stop")
            self.assertEqual(target.read_bytes(), b"official")

    def test_success_replaces_only_after_integrity_validation(self):
        schema = """
        CREATE TABLE basic_info(id INTEGER PRIMARY KEY);
        CREATE TABLE detail_info(course_id INTEGER PRIMARY KEY REFERENCES basic_info(id));
        CREATE TABLE translations(course_id INTEGER, field TEXT, lang TEXT, text TEXT, PRIMARY KEY(course_id, field, lang));
        """
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "courses.db"
            with atomic_database(target, schema) as conn:
                conn.execute("INSERT INTO basic_info VALUES (1)")
                conn.execute("INSERT INTO detail_info VALUES (1)")
                validate_built_database(conn, expected_rows=1)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='translations'").fetchone())
```

另为五个 `SCHEMA` 写参数化测试，执行到内存库后断言存在 `basic_info`、`detail_info`、`translations`、`courses_view`，并检查 `translations` 主键为 `(course_id, field, lang)`。

- [ ] **Step 2: 运行测试并确认共享帮助函数不存在**

Run: `python3 -m unittest tests.test_builders -v`

Expected: import 或行为失败。

- [ ] **Step 3: 实现原子数据库 context manager 与统一验收**

```python
@contextmanager
def atomic_database(target: Path, schema: str):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temp_path = Path(raw)
    conn = sqlite3.connect(temp_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema)
        yield conn
        conn.commit()
        conn.close()
        os.replace(temp_path, target)
    except BaseException:
        conn.close()
        temp_path.unlink(missing_ok=True)
        raise


def validate_built_database(conn: sqlite3.Connection, expected_rows: int) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"basic_info", "detail_info", "translations"}.issubset(tables):
        raise ValueError("required tables missing")
    basic = conn.execute("SELECT COUNT(*) FROM basic_info").fetchone()[0]
    detail = conn.execute("SELECT COUNT(*) FROM detail_info").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if basic != expected_rows or detail != expected_rows or fk or integrity != "ok":
        raise ValueError(f"database validation failed: basic={basic} detail={detail} fk={fk} integrity={integrity}")
```

每个 builder 改为：先完整读取并校验 JSON；缺课程号、非法学分、必要对象类型错误或同一唯一键内容冲突时抛 `ValueError`；随后用 `atomic_database` 建临时库；调用 `validate_built_database` 后才替换。给春季两个 SCHEMA 补上空 `translations` 表及索引。

- [ ] **Step 4: 用临时输出运行五个 builder 的 fixture 测试**

Run: `python3 -m unittest tests.test_builders -v`

Expected: 全部通过；测试只写临时目录，不修改 `数据库/*.db`。

- [ ] **Step 5: 确认正式数据库完全未变并提交**

Run: Task 1 Step 3 的 SHA-256 脚本。

Expected: 五库哈希与 Task 1 清理前记录一致。

```bash
git add 数据库构建脚本 北京大学选课网数据抓取/build_*.py tests/test_builders.py
git commit -m "fix: build course databases atomically"
```

---

### Task 7: 保护抓取接收器与正式 JSON 发布

**Files:**
- Create: `北京大学选课网数据抓取/receiver_common.py`
- Create: `tests/test_receivers.py`
- Modify: `北京大学选课网数据抓取/receive_pku_summer_payload.py`
- Modify: `北京大学选课网数据抓取/receive_pku_undergrad_2627_fall_payload.py`
- Modify: `北京大学选课网数据抓取/receive_pku_graduate_2627_fall_payload.py`

**Interfaces:**
- Produces: `ReceiverConfig`、`validate_payload(payload, config)`、`publish_payload(body, config)`、`run_receiver(config, port)`。
- Security: Origin 固定为 `https://elective.pku.edu.cn`，一次性 token 通过 `X-PKU-Receiver-Token` 传递，正文上限 32 MiB。

- [ ] **Step 1: 写来源、token、学期、错误、validation 与原子发布测试**

```python
# tests/test_receivers.py
import json
import tempfile
import unittest
from pathlib import Path

from 北京大学选课网数据抓取.receiver_common import ReceiverConfig, PayloadRejected, publish_payload, validate_payload


class ReceiverValidationTests(unittest.TestCase):
    def config(self, root):
        return ReceiverConfig(script=Path(root) / "script.js", raw_output=Path(root) / "raw.json", final_output=Path(root) / "final.json", term="26-27学年第1学期", label="test")

    def valid_payload(self):
        return {"term": "26-27学年第1学期", "rows": [{"数据学期": "26-27学年第1学期", "基本信息": {"课程号": "001", "班号": "1"}, "详细信息": {}}], "errors": [], "validation": {"missingCourseCodes": [], "missingDetailLinks": [], "suspiciousPages": []}}

    def test_rejects_wrong_term_errors_empty_rows_and_failed_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            mutations = [
                ("term", "wrong"),
                ("rows", []),
                ("errors", [{"message": "failed"}]),
                ("validation", {"missingCourseCodes": ["001"]}),
            ]
            for key, value in mutations:
                payload = self.valid_payload()
                payload[key] = value
                with self.assertRaises(PayloadRejected):
                    validate_payload(payload, config)

    def test_rejected_payload_never_replaces_final_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.final_output.write_text("official", encoding="utf-8")
            body = json.dumps({"term": "wrong", "rows": []}).encode()
            with self.assertRaises(PayloadRejected):
                publish_payload(body, config)
            self.assertEqual(config.final_output.read_text(), "official")
            self.assertTrue(config.raw_output.exists())
```

HTTP handler 集成测试启动 `ThreadingHTTPServer(("127.0.0.1", 0), handler)`，断言错误 Origin、缺 token、超 32 MiB Content-Length 返回 403/413，正确请求返回 200。

- [ ] **Step 2: 运行测试并确认旧接收器失败**

Run: `python3 -m unittest tests.test_receivers -v`

Expected: 共享模块不存在或安全断言失败。

- [ ] **Step 3: 实现共享接收器与薄入口**

```python
@dataclass(frozen=True)
class ReceiverConfig:
    script: Path
    raw_output: Path
    final_output: Path
    term: str
    label: str


def validate_payload(payload: object, config: ReceiverConfig) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("term") != config.term:
        raise PayloadRejected("term mismatch")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or payload.get("errors"):
        raise PayloadRejected("payload is incomplete")
    validation = payload.get("validation")
    if not isinstance(validation, dict):
        raise PayloadRejected("validation missing")
    for key in ("missingCourseCodes", "missingDetailLinks", "suspiciousPages"):
        if validation.get(key):
            raise PayloadRejected(f"validation failed: {key}")
    if any(not isinstance(row, dict) or row.get("数据学期") != config.term for row in rows):
        raise PayloadRejected("row term mismatch")
    return rows
```

`publish_payload` 总是先用 `tempfile.NamedTemporaryFile` 在 raw 目录原子保存正文；校验通过后把规范化 rows 写入正式文件同目录的临时文件并 `os.replace`。HTTP handler 对 GET/POST/OPTIONS 都校验 Origin 与 token，CORS 只回显固定 Origin，并限制 Content-Length。三个原入口仅构造不同 `ReceiverConfig` 并调用 `run_receiver`。

- [ ] **Step 4: 运行接收器全量测试**

Run: `python3 -m unittest tests.test_receivers -v`

Expected: 全部通过。

- [ ] **Step 5: 提交接收器修复**

```bash
git add 北京大学选课网数据抓取/receiver_common.py 北京大学选课网数据抓取/receive_pku_*_payload.py tests/test_receivers.py
git commit -m "fix: validate scraped payloads before publishing"
```

---

### Task 8: 让三个页面内抓取脚本快速失败、可重试且不重复详情请求

**Files:**
- Create: `tests/test_scraper_contract.py`
- Modify: `北京大学选课网数据抓取/pku_inpage_summer_scraper.js`
- Modify: `北京大学选课网数据抓取/pku_inpage_undergrad_2627_fall_scraper.js`
- Modify: `北京大学选课网数据抓取/pku_inpage_graduate_2627_fall_scraper.js`

**Interfaces:**
- Consumes: 接收器注入的 `__RECEIVER_URL__` 与 `__RECEIVER_TOKEN__`。
- Produces: `fetchText(path, options, retries)`、任务级 `detailBySeq`、完整 validation payload。

- [ ] **Step 1: 写安全端点、ALL、token、学期、超时、重试与共享缓存契约测试**

```python
# tests/test_scraper_contract.py
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "北京大学选课网数据抓取"
SCRIPTS = sorted(ROOT.glob("pku_inpage_*_scraper.js"))


class ScraperContractTests(unittest.TestCase):
    def test_all_scrapers_keep_plan_endpoint_absent(self):
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("addToPlan.do", text, path.name)
            self.assertNotIn("加入选课计划", text, path.name)

    def test_all_scrapers_require_term_token_timeout_and_global_cache(self):
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("__RECEIVER_TOKEN__", text, path.name)
            self.assertIn("AbortController", text, path.name)
            self.assertIn("assertExpectedTerm", text, path.name)
            self.assertIn("const detailBySeq = new Map()", text, path.name)
            self.assertIn('"wlw-select_key:{actionForm.deptID}": "ALL"', text, path.name)

    def test_undergrad_scrapers_fail_whole_run_on_one_type_error(self):
        for name in ("pku_inpage_summer_scraper.js", "pku_inpage_undergrad_2627_fall_scraper.js"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("errors.push({ type:", text)
```

- [ ] **Step 2: 运行契约测试并确认旧脚本失败**

Run: `python3 -m unittest tests.test_scraper_contract -v`

Expected: token、超时、全局缓存、暑期 `ALL` 或 fail-fast 失败。

- [ ] **Step 3: 实现接收器 token、页面学期检查和有限重试**

```javascript
const RECEIVER_TOKEN = "__RECEIVER_TOKEN__";
const detailBySeq = new Map();

function assertExpectedTerm() {
  const pageText = document.body ? document.body.innerText : "";
  if (!pageText.includes(TERM)) throw new Error(`expected term not visible: ${TERM}`);
}

async function fetchText(path, options = {}, retries = 2) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, { credentials: "include", ...options, signal: controller.signal });
      const html = await response.text();
      if (html.includes("提示:请不要用刷课机刷课") || html.includes("<title>系统提示</title>")) throw new Error("PKU_SYSTEM_PROMPT");
      if (!response.ok) throw new Error(`${path} HTTP ${response.status}`);
      return html;
    } catch (error) {
      lastError = error;
      if (String(error.message).includes("PKU_SYSTEM_PROMPT") || attempt === retries) throw error;
      await sleep(400 * (2 ** attempt));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}
```

对 `/progress` 和 `/done` 增加 `X-PKU-Receiver-Token`。脚本开始第一行调用 `assertExpectedTerm()`。本科三类表单统一发送 `deptID=ALL`；详情缓存移到任务最外层。任何课程类别异常直接抛到顶层，不再继续并发布部分数据。暑期补齐 pageStats 与 `validatePayload`。

- [ ] **Step 4: 运行契约与 JavaScript 语法检查**

Run: `python3 -m unittest tests.test_scraper_contract -v`

Run: `for f in 北京大学选课网数据抓取/pku_inpage_*_scraper.js; do node --check "$f"; done`

Expected: 全部通过；三个脚本不含选课计划端点。

- [ ] **Step 5: 提交页面内脚本修复**

```bash
git add 北京大学选课网数据抓取/pku_inpage_*_scraper.js tests/test_scraper_contract.py
git commit -m "fix: make elective scrapers fail safely"
```

---

### Task 9: 让翻译 CLI 无密钥可检查、按选择范围运行并可靠失败

**Files:**
- Create: `北京大学课程数据翻译/translation_common.py`
- Create: `tests/test_translation_scripts.py`
- Modify: `北京大学课程数据翻译/translate_courses.py`
- Modify: `北京大学课程数据翻译/translate_misc.py`
- Modify: `北京大学课程数据翻译/translate_stubborn.py`

**Interfaces:**
- Produces: `get_api_key() -> str`、`clean_translation(text) -> str`、`write_translation_with_retry(...)`、统一 `DATABASES` / task matrix。

- [ ] **Step 1: 写无密钥 help、空白译文、选择范围和失败状态测试**

```python
# tests/test_translation_scripts.py
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "北京大学课程数据翻译"


class TranslationCliTests(unittest.TestCase):
    def test_help_works_without_api_key(self):
        env = dict(os.environ)
        env.pop("DEEPSEEK_API_KEY", None)
        for script in ("translate_courses.py", "translate_misc.py", "translate_stubborn.py"):
            result = subprocess.run([sys.executable, str(DIR / script), "--help"], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_blank_translation_is_rejected(self):
        sys.path.insert(0, str(DIR))
        from translation_common import clean_translation
        with self.assertRaises(ValueError):
            clean_translation("   \n")

    def test_stubborn_matrix_contains_spring_long_fields(self):
        text = (DIR / "translate_stubborn.py").read_text(encoding="utf-8")
        for label in ("UG syllabus", "UG evaluation", "UG reference_book", "GR reference_book"):
            self.assertIn(label, text)
```

使用临时 SQLite 和 mock API 再测试：`--db fall_gr` 不初始化其他路径；API 成功后数据库锁重试只重放写入；存在任一失败时 `main()` 返回 1。

- [ ] **Step 2: 运行测试并确认旧脚本在 import/help 时退出**

Run: `python3 -m unittest tests.test_translation_scripts -v`

Expected: 三个 `--help` 至少两个失败，公共模块不存在。

- [ ] **Step 3: 实现共享延迟配置、空白校验与写入重试**

```python
def get_api_key() -> str:
    value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not value:
        raise RuntimeError("Set DEEPSEEK_API_KEY before starting translation")
    return value


def clean_translation(text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("translation is blank")
    return text.strip()


def write_translation_with_retry(db_path, course_id, field, lang, text, attempts=5):
    clean = clean_translation(text)
    for attempt in range(attempts):
        try:
            with sqlite3.connect(db_path, timeout=30) as conn:
                conn.execute("INSERT OR REPLACE INTO translations VALUES (?,?,?,?)", (course_id, field, lang, clean))
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(0.25 * (2 ** attempt))
```

`ssl.create_default_context(cafile=certifi.where())` 改为可选 import 回退。调用 API 时才调用 `get_api_key()`。API 返回后先清洗并保存在内存，再独立重试数据库写入，禁止因为锁重新调用 API。

- [ ] **Step 4: 按 CLI 选择构造数据库和任务矩阵**

`translate_courses --only` 只 setup/fetch 对应 DB；`translate_misc --db` 先形成 `selected_paths` 再 setup/reuse/fetch，并补 `FALL_GR_DB`；`translate_stubborn` 把 task matrix 移到模块级，增加春季 UG `syllabus`、`evaluation`、`reference_book` 与春季 GR `reference_book`。所有 main 返回错误数是否为零，入口使用 `raise SystemExit(main())`。

- [ ] **Step 5: 运行翻译脚本测试和无密钥编译**

Run: `env -u DEEPSEEK_API_KEY python3 -m unittest tests.test_translation_scripts -v`

Run: `python3 -m py_compile 北京大学课程数据翻译/*.py`

Expected: 全部通过；不发起任何网络请求，也不修改正式数据库。

- [ ] **Step 6: 提交翻译 CLI 修复**

```bash
git add 北京大学课程数据翻译/*.py tests/test_translation_scripts.py
git commit -m "fix: make translation jobs selective and resumable"
```

---

### Task 10: 更新依赖并加固部署配置

**Files:**
- Create: `tests/test_deploy_contract.py`
- Modify: `requirements.txt`
- Modify: `deploy/update.sh`
- Modify: `deploy/pinhaoke.service`
- Modify: `deploy/nginx.conf`
- Create: `deploy/README.md`

**Interfaces:**
- Produces: 可审计的固定依赖、互斥且 fail-fast 的更新脚本、只读 systemd 服务和与 Certbot HTTPS 共存的 Nginx 模板。

- [ ] **Step 1: 写依赖与部署契约测试**

```python
# tests/test_deploy_contract.py
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeployContractTests(unittest.TestCase):
    def test_dependencies_are_pinned_to_audited_versions(self):
        requirements = (ROOT / "requirements.txt").read_text()
        self.assertIn("fastapi==0.136.1", requirements)
        self.assertIn("starlette==1.3.1", requirements)
        self.assertIn("uvicorn[standard]==0.44.0", requirements)

    def test_update_checks_lfs_before_stopping_and_fails_smoke_test(self):
        text = (ROOT / "deploy/update.sh").read_text()
        self.assertLess(text.index("command -v git-lfs"), text.index('systemctl stop "$SERVICE"'))
        self.assertIn("flock", text)
        self.assertIn("sha256sum requirements.txt", text)
        self.assertNotIn("chown -R", text)
        self.assertIn("exit 1", text)

    def test_service_is_sandboxed(self):
        text = (ROOT / "deploy/pinhaoke.service").read_text()
        for setting in ("NoNewPrivileges=true", "ProtectSystem=strict", "PrivateTmp=true", "CapabilityBoundingSet=", "UMask=0027"):
            self.assertIn(setting, text)

    def test_nginx_avoids_immutable_html_and_duplicate_mime(self):
        text = (ROOT / "deploy/nginx.conf").read_text()
        self.assertNotIn("gzip_types text/html", text)
        self.assertNotIn('Cache-Control "public, immutable"', text)
        self.assertIn("ssl_certificate /etc/letsencrypt/live/pinhaoke.love/fullchain.pem", text)
```

- [ ] **Step 2: 运行测试并确认旧配置失败**

Run: `python3 -m unittest tests.test_deploy_contract -v`

Expected: 四组断言失败。

- [ ] **Step 3: 固定经审计的依赖并在全新环境验证**

```text
fastapi==0.136.1
starlette==1.3.1
uvicorn[standard]==0.44.0
```

Run:

```bash
python3 -m venv /tmp/pinhaoke-audit
/tmp/pinhaoke-audit/bin/pip install --upgrade pip
/tmp/pinhaoke-audit/bin/pip install -r requirements.txt pip-audit
/tmp/pinhaoke-audit/bin/pip check
/tmp/pinhaoke-audit/bin/pip-audit -r requirements.txt
/tmp/pinhaoke-audit/bin/python -m unittest discover -s tests -v
```

Expected: 依赖解析成功、`pip check` 无冲突、`pip-audit` 无已知漏洞、测试通过。若解析证明三个精确版本不兼容，只允许选取 `pip-audit` 无漏洞且满足 FastAPI 声明约束的最新补丁版本，并同步测试断言与文档。

- [ ] **Step 4: 重写更新顺序与失败语义**

`deploy/update.sh` 顺序固定为：获取 `flock`；检查 root、Git、git-lfs、目标提交和 LFS 下载；计算 requirements SHA-256 并准备新 venv；停止服务；hard reset；必要时原子替换 venv；安装 systemd unit；设置 root:www-data 所有权与目录/文件只读权限；启动；执行 `/api/health`、秋季 `/api/filters` 和 `/api/courses?page_size=1` 三项烟测。任一烟测非 200 或 JSON 状态异常立即 `exit 1` 并打印日志。

- [ ] **Step 5: 加固 systemd 与 Nginx 模板**

systemd 增加：

```ini
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0027
ReadOnlyPaths=/opt/pinhaoke
```

Nginx 使用独立 80 跳转与 443 server，保留 Certbot 证书路径；移除 `gzip_types text/html`；图片使用 `Cache-Control: public, max-age=2592000` 而非 immutable；增加 `X-Content-Type-Options`、`Referrer-Policy`、`X-Frame-Options`。`deploy/README.md` 记录 `nginx -t` 后手动安装模板，更新脚本不自动覆盖 Certbot 管理区。

- [ ] **Step 6: 运行 Shell、配置和测试检查**

Run: `bash -n deploy/update.sh && python3 -m unittest tests.test_deploy_contract -v`

若本机有 ShellCheck，另运行 `shellcheck deploy/update.sh`。Nginx 语法在不修改生产的前提下用本地 `nginx -t -c "$PWD/deploy/nginx.conf"` 或容器验证；本机缺 Nginx 时在交付说明中明确记录该项由服务器部署前执行。

- [ ] **Step 7: 提交依赖与部署加固**

```bash
git add requirements.txt deploy tests/test_deploy_contract.py
git commit -m "fix: harden dependency and deployment workflow"
```

---

### Task 11: 合并权威文档、删除冗余资料并完成总体验收

**Files:**
- Create: `tests/test_documentation.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Replace: `CLAUDE.md`
- Modify: `北京大学选课网数据抓取/README.md`
- Modify: `北京大学课程数据翻译/README.md`
- Modify: `课程数据/数据说明.md`
- Modify: `归档/README.md`
- Modify: `deploy/README.md`
- Delete: `文档/superpowers/plans/2026-05-13-summer-courses-scraper.md`
- Delete: `文档/superpowers/specs/2026-05-13-summer-courses-scraper-design.md`
- Delete: `文档/品牌色与柔光渐变设计规范.md`
- Delete after facts are merged: `文档/superpowers/specs/2026-07-10-project-hardening-design.md`
- Delete after all checklist items complete: `文档/superpowers/plans/2026-07-10-project-hardening.md`

**Interfaces:**
- Produces: 八份必要说明文档，且每类事实只有一个权威来源。

- [ ] **Step 1: 写文档集合、链接与关键事实测试**

```python
# tests/test_documentation.py
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_only_required_markdown_files_remain(self):
        expected = {
            "README.md", "AGENTS.md", "CLAUDE.md", "deploy/README.md",
            "北京大学选课网数据抓取/README.md", "北京大学课程数据翻译/README.md",
            "课程数据/数据说明.md", "归档/README.md",
        }
        active_plan = ROOT / "文档/superpowers/plans/2026-07-10-project-hardening.md"
        actual = {
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*.md")
            if ".git" not in path.parts and path != active_plan
        }
        self.assertEqual(actual, expected)

    def test_claude_is_a_pointer_not_a_duplicate(self):
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 12)
        self.assertIn("AGENTS.md", text)

    def test_readme_uses_https_and_correct_sponsor_labels(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://www.pinhaoke.love", text)
        self.assertIn("wechat_sponsor.jpg", text)
        self.assertIn("alipay_sponsor.jpg", text)
```

- [ ] **Step 2: 运行文档测试并确认旧文档集合失败**

Run: `python3 -m unittest tests.test_documentation -v`

Expected: 多余文档、CLAUDE 重复或 README 事实至少一项失败。

- [ ] **Step 3: 将已实现事实并入八份权威文档**

每份文档职责固定为：

```text
README.md                         产品入口、功能、三学期、快速运行、文档索引
AGENTS.md                         架构、API、ID、查询合并、测试、数据安全、部署边界
CLAUDE.md                         指向 AGENTS.md 的短说明
抓取/README.md                    Chrome 当前页流程、令牌接收器、校验、禁止端点、建库
翻译/README.md                    五库任务矩阵、CLI、覆盖率口径、重试、无付费自动运行
课程数据/数据说明.md              数据来源日期、原始/合并记录数、schema、翻译缺口
deploy/README.md                  HTTPS、权限、更新、回滚、日志、烟测
归档/README.md                    旧 V1 文件用途与严禁用于生产
```

README 修正赞助二维码对应关系、使用 HTTPS；AGENTS 的版本、端点、总数、排序名和安全约束与代码一致；抓取 README 明确一次性 token 和“绝不调用加入选课计划”；翻译 README 明确春季长字段尚未完整翻译，不能宣称全覆盖。

- [ ] **Step 4: 删除已合并的旧设计、旧计划和品牌说明**

Run:

```bash
git rm 文档/superpowers/plans/2026-05-13-summer-courses-scraper.md
git rm 文档/superpowers/specs/2026-05-13-summer-courses-scraper-design.md
git rm 文档/品牌色与柔光渐变设计规范.md
git rm 文档/superpowers/specs/2026-07-10-project-hardening-design.md
```

在所有任务验收完成后再删除本计划文件，确保 `tests/test_documentation.py` 最终只见八份 Markdown。

- [ ] **Step 5: 运行全量自动验证**

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py 数据库构建脚本 北京大学选课网数据抓取 北京大学课程数据翻译
for f in 北京大学选课网数据抓取/pku_inpage_*_scraper.js; do node --check "$f"; done
bash -n deploy/update.sh
git diff --check
git fsck --full
git lfs fsck
```

Expected: 全部通过，无空白错误、Git/LFS 错误或数据库文件变化。

- [ ] **Step 6: 运行五库与本地 API 最终验收**

用 Task 1 Step 3 重跑五库哈希和 integrity；哈希必须与修复前相同。启动本地服务后检查：

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS 'http://127.0.0.1:8000/api/courses?term=fall&page_size=1'
curl -fsS 'http://127.0.0.1:8000/api/courses?term=spring&page_size=1'
curl -fsS 'http://127.0.0.1:8000/api/courses?term=summer&page_size=1'
```

Expected: health 为 ok；总数依次为 4421、3701、160。随后重复 Task 5 的三个视口浏览器验收并保存截图证据到系统临时目录，不加入仓库。

- [ ] **Step 7: 请求独立代码审查并修复确认的问题**

审查范围为设计规格以来全部提交，重点检查行为回归、数据破坏风险、SQL 注入/筛选语义、抓取禁止端点和部署权限。任何确认的问题先新增失败测试，再修复并重跑相关与全量验证。

- [ ] **Step 8: 删除临时计划并提交最终文档与修复**

```bash
git rm 文档/superpowers/plans/2026-07-10-project-hardening.md
git add README.md AGENTS.md CLAUDE.md deploy 北京大学选课网数据抓取/README.md 北京大学课程数据翻译/README.md 课程数据/数据说明.md 归档/README.md tests/test_documentation.py
git commit -m "docs: consolidate project operations guide"
```

- [ ] **Step 9: 最终状态确认**

```bash
git status --short --branch
git log --oneline --decorate -12
du -sh .git
df -h .
test ! -e 文档/superpowers/plans/2026-07-10-project-hardening.md
```

Expected: 工作树干净；本地提交完整；`.git` 已释放约 124.23 GiB；没有执行 push 或生产部署。
