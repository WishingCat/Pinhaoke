# 北大 2026 暑期课程抓取与建库 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 PKU elective2008 抓取 2026 暑期 6 个课程类型的全量数据，合成单个 JSON，再构建独立 SQLite 数据库 `2026暑期学期课程.db`。

**Architecture:** 用 Playwright 同步 API 启动有头 Chromium → 人工登录（首次）→ 保存 storage_state 复用 → 顺序遍历 6 个 tab → 每行抓基本信息 + 点课程号抓详细信息 → 每类型先写 tmp 文件做断点，最后合并 → 沿用 `build_undergrad_db.py` 的 schema 入库。

**Tech Stack:** Python 3.12, Playwright (sync API, Chromium), SQLite 3, 复用现有 `build_common.py`。

参考 spec：`docs/superpowers/specs/2026-05-13-summer-courses-scraper-design.md`

---

## 文件结构

```
Pinhaoke/
├── scrape_summer_courses.py       (新建：爬虫)
├── build_summer_db.py             (新建：建库)
├── build_common.py                (复用，不动)
├── .gitignore                     (修改：加 3 行)
├── Course data/
│   └── 北大暑期课程数据_25-26暑期.json   (产物)
├── tmp_summer/                    (产物目录：每类型一个 JSON)
├── .auth/
│   └── pku_state.json             (登录 session)
└── 2026暑期学期课程.db             (产物)
```

**职责划分：**
- `scrape_summer_courses.py` — Playwright 浏览器自动化、字段提取、断点续抓、合并 JSON。单文件，~300 行。
- `build_summer_db.py` — 读单个合成 JSON，按现有 schema 入库。结构对齐 `build_undergrad_db.py`。

---

## Task 1：环境与 .gitignore

**Files:**
- Modify: `.gitignore`
- 新增 venv 依赖：`playwright`

- [ ] **Step 1：把忽略项加到 .gitignore**

在 `.gitignore` 末尾追加：

```
2026暑期学期课程.db
.auth/
tmp_summer/
docs/
```

> 备注：`docs/` 里只有 spec 和本计划，按用户偏好不入 git。如果后续要把 spec 进 git，把这一行去掉。

- [ ] **Step 2：在 venv 装 playwright**

```bash
source venv/bin/activate
pip install playwright
playwright install chromium
```

预期：最后输出 `Chromium ... downloaded` 或 `is already installed`。

- [ ] **Step 3：冒烟测试 playwright 能起浏览器**

```bash
venv/bin/python -c "from playwright.sync_api import sync_playwright; print('ok')"
```

预期输出：`ok`

- [ ] **Step 4：提交**

```bash
git add .gitignore
git commit -m "scrape: gitignore summer artifacts"
```

---

## Task 2：脚本骨架 + 登录交接

**Files:**
- Create: `scrape_summer_courses.py`

- [ ] **Step 1：创建骨架文件**

```python
#!/usr/bin/env python3
"""Scrape PKU 2026 summer courses via Playwright with manual login handoff.

Usage:
    python scrape_summer_courses.py --types 公选课        # 单类型试跑（默认）
    python scrape_summer_courses.py --types all          # 全 6 类
    python scrape_summer_courses.py --types 公选课 通识课  # 指定多类
    python scrape_summer_courses.py --force              # 忽略 tmp 缓存重抓
"""
import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout

BASE = Path(__file__).resolve().parent
AUTH_DIR = BASE / ".auth"
AUTH_STATE = AUTH_DIR / "pku_state.json"
TMP_DIR = BASE / "tmp_summer"
OUT_JSON = BASE / "Course data" / "北大暑期课程数据_25-26暑期.json"

LOGIN_URL = "https://iaaa.pku.edu.cn/iaaa/oauthlogin.do?appid=syllabus&redirectUrl=https://elective.pku.edu.cn/elective2008/ssoLogin.do"
QUERY_URL = "https://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/controller/courseQuery/getCurriculmByForm.do"

ALL_TYPES = ["公选课", "通识课", "专业课", "英语课", "体育课", "劳动教育课"]


def jitter(a=0.2, b=0.5):
    time.sleep(random.uniform(a, b))


def open_context(p):
    """Return (browser, context, page). Reuse storage_state when available."""
    AUTH_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)
    browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
    kw = {"storage_state": str(AUTH_STATE)} if AUTH_STATE.exists() else {}
    context = browser.new_context(**kw)
    page = context.new_page()
    return browser, context, page


def ensure_logged_in(page: Page, context: BrowserContext):
    """Navigate to elective; if redirected to IAAA, wait for user to log in manually."""
    page.goto(QUERY_URL, wait_until="domcontentloaded")
    if "iaaa.pku.edu.cn" in page.url or "login" in page.url.lower():
        print("\n[!] 检测到未登录。请在浏览器里完成登录（IAAA 学号密码 + 验证码）。")
        print("[!] 登录完成、看到选课系统的课程查询页面后，回到这里按回车继续...")
        input()
    # 等真正进入查询页
    for _ in range(60):
        if "elective.pku.edu.cn" in page.url and "courseQuery" in page.url:
            break
        page.wait_for_timeout(1000)
    else:
        raise RuntimeError(f"未能跳转到课程查询页，当前 URL: {page.url}")
    context.storage_state(path=str(AUTH_STATE))
    print(f"[+] 已保存登录态到 {AUTH_STATE.relative_to(BASE)}")


def scrape_type(page: Page, type_name: str) -> list[dict]:
    """STUB - 在 Task 4-7 中逐步填充."""
    raise NotImplementedError(f"scrape_type({type_name}) not implemented yet")


def merge_to_final(types_done: list[str]) -> int:
    """合并 tmp_summer/<type>.json 到 OUT_JSON, 返回总数."""
    OUT_JSON.parent.mkdir(exist_ok=True)
    all_rows = []
    for t in types_done:
        p = TMP_DIR / f"{t}.json"
        if p.exists():
            all_rows.extend(json.loads(p.read_text(encoding="utf-8")))
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)
    return len(all_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", nargs="+", default=["公选课"],
                    help="课程类型；'all' 表示全部 6 类")
    ap.add_argument("--force", action="store_true", help="忽略 tmp 缓存重抓")
    args = ap.parse_args()

    types = ALL_TYPES if args.types == ["all"] else args.types
    invalid = [t for t in types if t not in ALL_TYPES]
    if invalid:
        print(f"[x] 未知类型: {invalid}, 可选: {ALL_TYPES}", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        browser, context, page = open_context(p)
        try:
            ensure_logged_in(page, context)
            for t in types:
                tmp = TMP_DIR / f"{t}.json"
                if tmp.exists() and not args.force:
                    print(f"[=] {t}: 命中缓存 {tmp.relative_to(BASE)}，跳过（用 --force 重抓）")
                    continue
                print(f"[>] 开始抓取：{t}")
                rows = scrape_type(page, t)
                tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[+] {t}: {len(rows)} 条 → {tmp.relative_to(BASE)}")

            total = merge_to_final(types)
            print(f"[+] 合并完成：{total} 条 → {OUT_JSON.relative_to(BASE)}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2：跑一次，确认登录交接打通**

```bash
venv/bin/python scrape_summer_courses.py --types 公选课
```

预期：浏览器弹出 → 我提示「请登录」→ 用户在浏览器里输学号密码 → 用户按回车 → 看到 `[+] 已保存登录态到 .auth/pku_state.json` → 然后报 `NotImplementedError: scrape_type(公选课) not implemented yet`。

如果 `LOGIN_URL` 直接没用上（脚本是先去 QUERY_URL，再被重定向到 IAAA），这是正常的，不用调。

- [ ] **Step 3：再跑一次，确认能复用 session**

```bash
venv/bin/python scrape_summer_courses.py --types 公选课
```

预期：不再提示登录，直接到 NotImplementedError。如果还是要求登录，说明 storage_state 没生效，要检查 `.auth/pku_state.json` 是否被生成。

- [ ] **Step 4：提交**

```bash
git add scrape_summer_courses.py
git commit -m "scrape: skeleton with playwright login handoff"
```

---

## Task 3：在登录态下检查页面 DOM（人在回路）

> 此 Task 不写代码，目的是确认下一步要用的选择器。这一步会把发现的选择器写到 `scrape_summer_courses.py` 顶部的常量里。

**Files:**
- Modify: `scrape_summer_courses.py`（加常量块）

- [ ] **Step 1：让脚本在抓取前暂停**

临时把 `scrape_type` 的内容改成：

```python
def scrape_type(page: Page, type_name: str) -> list[dict]:
    print(f"[i] 暂停在 {type_name} tab；用 DevTools 检查 DOM，然后按回车继续...")
    print(f"    当前 URL: {page.url}")
    input()
    raise NotImplementedError
```

- [ ] **Step 2：跑脚本，在 DevTools 里记录以下选择器**

```bash
venv/bin/python scrape_summer_courses.py --types 公选课
```

在浏览器 DevTools (Elements + Console) 里确认：

| 名称 | 选择器示例 | 怎么找 |
|---|---|---|
| `TAB_LINK` | `text="公选课"` 或 `a:has-text("公选课")` | 上面的 tab 栏，点不同 tab 看 URL 是否变 |
| `DEPT_SELECT` | `select[name="开课单位"]` | tab 里的「开课单位」下拉 |
| `SUBMIT_BTN` | `input[type=submit][value="检索"]` | 表单提交按钮 |
| `RESULT_ROWS` | `table.datagrid tr` 或类似 | 结果表格的行 |
| `RESULT_HEADERS` | `table.datagrid tr:first-child th` | 表头，确定列序 |
| `COURSE_LINK` | `a[href*="getCourseDetail"]` | 课程号上的链接 |
| `DETAIL_FRAME` | `iframe[name=...]` 或 modal div | 详情面板的载体 |
| `PAGE_NEXT` | `a:has-text("下一页")` 或类似 | 分页按钮 |

在 Console 跑 `document.querySelector('your-selector')` 验证每个选择器都返回单一元素。把表头列序记下来（课程号 / 课程名 / 学分 / 教师 / 开课单位 / ...）。

- [ ] **Step 3：把发现写进脚本顶部常量**

在 `QUERY_URL` 下加：

```python
# 以下选择器需要在 Task 3 的人工检查后填入实际值
SEL_TAB = 'a:has-text("{type_name}")'   # 用 .format 填入
SEL_DEPT_SELECT = 'select[name="..."]'  # TODO 填实际 name
SEL_DEPT_OPTION_ALL = 'option:has-text("全部")'
SEL_SUBMIT_BTN = 'input[type=submit]'   # TODO 填实际
SEL_RESULT_TABLE = 'table.datagrid'     # TODO 填实际 class
SEL_COURSE_LINK = 'a[href*="getCourseDetail"]'  # TODO 填实际
SEL_NEXT_PAGE = 'a:has-text("下一页")'   # TODO 填实际
# 表头列名 -> JSON 字段名
COL_MAP = {
    "课程号": "课程号",
    "课程名": "课程名",
    "学分": "学分",
    "教师": "教师",
    "开课单位": "开课单位",
    "上课时间及教室": "上课时间及教室",
    "限数已选": "限数已选",
    # 实际见到的列都列出来
}
```

- [ ] **Step 4：恢复 scrape_type，准备进入 Task 4**

把 `scrape_type` 的暂停代码删掉，留空 stub。

- [ ] **Step 5：提交常量**

```bash
git add scrape_summer_courses.py
git commit -m "scrape: record DOM selectors after manual inspection"
```

---

## Task 4：切 tab + 选「开课单位=全部」 + 提交

**Files:**
- Modify: `scrape_summer_courses.py`

- [ ] **Step 1：实现 switch_tab + select_all_dept + submit**

```python
def switch_tab(page: Page, type_name: str):
    """点击对应类型的 tab；如果当前已在则跳过."""
    page.locator(f'a:has-text("{type_name}")').first.click()
    page.wait_for_load_state("networkidle")
    jitter()


def select_all_dept(page: Page):
    """如果「开课单位」下拉存在且默认不是「全部」，切到「全部」."""
    select = page.locator(SEL_DEPT_SELECT)
    if select.count() == 0:
        return  # 该 tab 无此下拉（如英语/体育）
    current = select.evaluate("el => el.options[el.selectedIndex].text")
    if current.strip() != "全部":
        select.select_option(label="全部")
        jitter()


def submit_query(page: Page):
    page.locator(SEL_SUBMIT_BTN).first.click()
    page.wait_for_load_state("networkidle")
    jitter()
```

- [ ] **Step 2：把 scrape_type 改成只做"切 tab + 选全部 + 提交 + 打印结果数"**

```python
def scrape_type(page: Page, type_name: str) -> list[dict]:
    page.goto(QUERY_URL, wait_until="domcontentloaded")
    switch_tab(page, type_name)
    select_all_dept(page)
    submit_query(page)
    rows = page.locator(f"{SEL_RESULT_TABLE} tr").count()
    print(f"[i] {type_name}: 当前页表格行数（含表头）= {rows}")
    return []  # 暂时
```

- [ ] **Step 3：跑一次确认能跑到检索结果**

```bash
venv/bin/python scrape_summer_courses.py --types 公选课
```

预期：浏览器自动切到「公选课」tab → 自动把开课单位选到「全部」→ 点检索 → 控制台输出大概 `[i] 公选课: 当前页表格行数（含表头）= 21` 之类。

如果数量明显不对（如 0、1），停下来回 Task 3 调选择器。

- [ ] **Step 4：提交**

```bash
git add scrape_summer_courses.py
git commit -m "scrape: tab switch + dept-all + submit"
```

---

## Task 5：抽取单页结果行

**Files:**
- Modify: `scrape_summer_courses.py`

- [ ] **Step 1：实现 extract_rows_current_page**

```python
def extract_rows_current_page(page: Page) -> list[dict]:
    """读当前页的结果表格，返回每行的「基本信息」dict（不含详细信息）。"""
    rows = page.locator(f"{SEL_RESULT_TABLE} tr").all()
    if len(rows) < 2:
        return []

    # 第一行是表头
    headers = [c.inner_text().strip() for c in rows[0].locator("th, td").all()]
    out = []
    for r in rows[1:]:
        cells = r.locator("td").all()
        if len(cells) < len(headers):
            continue
        record = {}
        for h, c in zip(headers, cells):
            text = c.inner_text().strip()
            # 去掉表头里我们不关心的列
            key = COL_MAP.get(h)
            if key:
                record[key] = text
        if record.get("课程号"):
            # 补齐 spec 要求的全部字段
            for k in ["课程号", "班号", "课程名", "课程类别", "学分", "教师",
                      "开课单位", "专业", "年级", "上课时间及教室",
                      "限数已选", "自选PNP", "备注"]:
                record.setdefault(k, "")
            out.append(record)
    return out
```

- [ ] **Step 2：在 scrape_type 里调用并打印样本**

```python
def scrape_type(page: Page, type_name: str) -> list[dict]:
    page.goto(QUERY_URL, wait_until="domcontentloaded")
    switch_tab(page, type_name)
    select_all_dept(page)
    submit_query(page)
    rows = extract_rows_current_page(page)
    print(f"[i] {type_name}: 第 1 页抽到 {len(rows)} 行")
    if rows:
        print("[i] 样本：", json.dumps(rows[0], ensure_ascii=False))
    return rows  # 暂时只返回第一页
```

- [ ] **Step 3：跑一次比对**

```bash
venv/bin/python scrape_summer_courses.py --types 公选课
```

预期：`[i] 公选课: 第 1 页抽到 N 行`，N 应与浏览器里看到的一致；样本输出应该有「课程号」「课程名」「教师」「学分」「上课时间及教室」等关键字段。

如果字段缺位严重，检查 `COL_MAP` 是否齐。

- [ ] **Step 4：提交**

```bash
git add scrape_summer_courses.py
git commit -m "scrape: extract basic-info rows from current page"
```

---

## Task 6：分页全抓

**Files:**
- Modify: `scrape_summer_courses.py`

- [ ] **Step 1：实现 iterate_all_pages**

```python
def iterate_all_pages(page: Page) -> list[dict]:
    """从当前结果页开始，翻完所有页，把基本信息累加返回。"""
    all_rows = []
    seen_keys = set()
    page_no = 1
    while True:
        rows = extract_rows_current_page(page)
        new_count = 0
        for r in rows:
            k = (r["课程号"], r.get("班号", ""))
            if k in seen_keys:
                continue
            seen_keys.add(k)
            all_rows.append(r)
            new_count += 1
        print(f"[i]   第 {page_no} 页：+{new_count}（累计 {len(all_rows)}）")

        next_btn = page.locator(SEL_NEXT_PAGE)
        # 「下一页」可能是禁用的链接/按钮；视实际 DOM 调整
        if next_btn.count() == 0:
            break
        is_disabled = next_btn.evaluate(
            "el => el.classList.contains('disabled') || el.hasAttribute('disabled') || el.getAttribute('aria-disabled')==='true'"
        )
        if is_disabled:
            break
        next_btn.first.click()
        page.wait_for_load_state("networkidle")
        jitter()
        page_no += 1
        if page_no > 200:
            print("[!] 分页超过 200 页，强制中断防死循环")
            break
    return all_rows
```

> 备注：如果实际页面用的不是「下一页」链接而是表单 POST 翻页（PKU 的旧系统经常这样），把这块换成读取「共 N 页」字样 + 直接发 POST。Task 3 里要确认翻页机制。

- [ ] **Step 2：把 scrape_type 换成全分页**

```python
def scrape_type(page: Page, type_name: str) -> list[dict]:
    page.goto(QUERY_URL, wait_until="domcontentloaded")
    switch_tab(page, type_name)
    select_all_dept(page)
    submit_query(page)
    basic_rows = iterate_all_pages(page)
    print(f"[+] {type_name}: 全部页抽到 {len(basic_rows)} 行基本信息")
    return [{"课程类型": type_name, "基本信息": r, "详细信息": {}} for r in basic_rows]
```

- [ ] **Step 3：跑一次确认总数对得上**

```bash
venv/bin/python scrape_summer_courses.py --types 公选课
```

预期：总数大致与页面右上「共 X 条」一致。

- [ ] **Step 4：提交**

```bash
git add scrape_summer_courses.py
git commit -m "scrape: paginate through all result pages"
```

---

## Task 7：点课程号抓「详细信息」

**Files:**
- Modify: `scrape_summer_courses.py`

> 现实情况：详情可能是新窗口、modal、或 iframe。Task 3 里要把这事确认掉。下面默认是「新窗口」分支，如果实际是 modal，按注释里的提示调整。

- [ ] **Step 1：实现 fetch_detail**

```python
DETAIL_FIELDS = [
    "英文名称", "先修课程", "中文简介", "英文简介", "成绩记载方式",
    "通识课所属系列", "授课语言", "教材", "参考书", "教学大纲", "教学评估",
]


def fetch_detail(context: BrowserContext, page: Page, course_code: str, class_no: str) -> dict:
    """点课程号，等详情面板，抽 11 个字段，关闭返回。"""
    # 找到对应行的课程号链接
    link = page.locator(
        f'{SEL_RESULT_TABLE} tr:has(td:text-is("{course_code}")) {SEL_COURSE_LINK}'
    ).first
    if link.count() == 0:
        return {k: "" for k in DETAIL_FIELDS}

    # —— 分支 A：新窗口 ——
    with context.expect_page() as pinfo:
        link.click()
    detail = pinfo.value
    detail.wait_for_load_state("domcontentloaded")
    html_text = detail.locator("body").inner_text()
    detail.close()

    # —— 分支 B：modal/iframe（若上面拿不到 new page，改用以下） ——
    # link.click()
    # panel = page.locator("css for detail panel").first
    # panel.wait_for(state="visible")
    # html_text = panel.inner_text()
    # page.locator("css for close button").click()

    return parse_detail_text(html_text)


def parse_detail_text(text: str) -> dict:
    """从详情页/面板的纯文本里按字段名抠字段值."""
    out = {k: "" for k in DETAIL_FIELDS}
    # 字段名锚定：'英文名称：xxx 先修课程：yyy ...'
    # PKU 详情页通常用「：」分隔。下面用 lookahead 让一个字段一直读到下一个字段名。
    field_names_alt = "|".join(re.escape(k) for k in DETAIL_FIELDS)
    pattern = re.compile(rf"({field_names_alt})[：:]\s*(.*?)(?=(?:{field_names_alt})[：:]|$)", re.S)
    for m in pattern.finditer(text):
        key, val = m.group(1), m.group(2).strip()
        out[key] = val
    return out
```

- [ ] **Step 2：在 scrape_type 里对每行调 fetch_detail**

```python
def scrape_type(page: Page, type_name: str) -> list[dict]:
    page.goto(QUERY_URL, wait_until="domcontentloaded")
    switch_tab(page, type_name)
    select_all_dept(page)
    submit_query(page)
    basic_rows = iterate_all_pages(page)
    print(f"[i] {type_name}: 基本信息 {len(basic_rows)} 行，开始抓详细信息...")

    context = page.context
    results = []
    for i, r in enumerate(basic_rows, 1):
        try:
            d = fetch_detail(context, page, r["课程号"], r.get("班号", ""))
        except Exception as e:
            print(f"[!] {r['课程号']} 详情抓取失败: {e}")
            d = {k: "" for k in DETAIL_FIELDS}
        results.append({"课程类型": type_name, "基本信息": r, "详细信息": d})
        if i % 10 == 0:
            print(f"[.] {type_name}: 进度 {i}/{len(basic_rows)}")
        jitter()
    return results
```

- [ ] **Step 3：试跑公选课**

```bash
venv/bin/python scrape_summer_courses.py --types 公选课
```

预期：先打印基本信息总数 → 然后每 10 条打一次进度 → 最后写 `tmp_summer/公选课.json` → 合并出 `Course data/北大暑期课程数据_25-26暑期.json`。

- [ ] **Step 4：抽样比对**

打开 `tmp_summer/公选课.json`，随手取 3 条记录，跟 `Course data/北大公选课数据_25-26第2学期.json` 同字段比对结构是否一致（字段名、嵌套形状）。

```bash
venv/bin/python -c "
import json
data = json.load(open('tmp_summer/公选课.json'))
print('count:', len(data))
print('sample keys:', list(data[0].keys()))
print('basic keys:', list(data[0]['基本信息'].keys()))
print('detail keys:', list(data[0]['详细信息'].keys()))
"
```

预期：`basic keys` 含课程号/课程名/学分/教师/开课单位/上课时间及教室 等；`detail keys` 含 英文名称/中文简介/教学大纲 等。

- [ ] **Step 5：提交**

```bash
git add scrape_summer_courses.py
git commit -m "scrape: fetch detail panel per course"
```

---

## Task 8：抓剩下 5 类

**Files:**
- 仅产物：`tmp_summer/*.json`

- [ ] **Step 1：跑全量**

```bash
venv/bin/python scrape_summer_courses.py --types all
```

预期：公选课命中缓存跳过；依次抓 通识课、专业课、英语课、体育课、劳动教育课；最终合并写出 `Course data/北大暑期课程数据_25-26暑期.json`。

如果中途某类失败，可单独重跑：

```bash
venv/bin/python scrape_summer_courses.py --types 专业课 --force
```

- [ ] **Step 2：人工抽查每类至少 1 条记录**

```bash
venv/bin/python -c "
import json
data = json.load(open('Course data/北大暑期课程数据_25-26暑期.json'))
from collections import Counter
print('total:', len(data))
print('by type:', Counter(d['课程类型'] for d in data))
for t in sorted(set(d['课程类型'] for d in data)):
    s = next(d for d in data if d['课程类型']==t)
    print(f'-- {t} --')
    print('  课程名:', s['基本信息']['课程名'])
    print('  教师:', s['基本信息']['教师'])
    print('  上课时间及教室:', s['基本信息']['上课时间及教室'])
"
```

预期：每类样本字段都不空（除非该类真的不需要某字段，如英语/体育的「开课单位」可能不同）。

> 备注：JSON 文件本身在 `.gitignore` 里（`Course data/` 整目录），不提交。

---

## Task 9：build_summer_db.py

**Files:**
- Create: `build_summer_db.py`

- [ ] **Step 1：照搬 undergrad 写法**

```python
#!/usr/bin/env python3
"""Build 2026暑期学期课程.db from 北大暑期课程数据_25-26暑期.json.

Schema 与 2026春季学期本科生课程.db 一致；course_type 取自记录里的「课程类型」。
"""
import json
import os
import sqlite3

from build_common import parse_schedule, to_float

DB_PATH = "2026暑期学期课程.db"
SOURCE = "Course data/北大暑期课程数据_25-26暑期.json"

SCHEMA = """
CREATE TABLE basic_info (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    course_type     TEXT NOT NULL,
    course_code     TEXT NOT NULL,
    class_no        TEXT NOT NULL,
    course_name     TEXT,
    category        TEXT,
    credits         REAL,
    teacher         TEXT,
    department      TEXT,
    major           TEXT,
    grade           TEXT,
    schedule        TEXT,
    classroom       TEXT,
    schedule_raw    TEXT,
    weekdays        TEXT,
    enrollment      TEXT,
    pnp             TEXT,
    notes           TEXT,
    UNIQUE(course_type, course_code, class_no)
);

CREATE TABLE detail_info (
    course_id       INTEGER PRIMARY KEY REFERENCES basic_info(id) ON DELETE CASCADE,
    english_name    TEXT,
    prerequisites   TEXT,
    intro_cn        TEXT,
    intro_en        TEXT,
    grading         TEXT,
    ge_series       TEXT,
    language        TEXT,
    textbook        TEXT,
    reference_book  TEXT,
    syllabus        TEXT,
    evaluation      TEXT
);

CREATE INDEX idx_basic_course_type  ON basic_info(course_type);
CREATE INDEX idx_basic_course_code  ON basic_info(course_code);
CREATE INDEX idx_basic_course_name  ON basic_info(course_name);
CREATE INDEX idx_basic_department   ON basic_info(department);
CREATE INDEX idx_basic_category     ON basic_info(category);
CREATE INDEX idx_basic_credits      ON basic_info(credits);

CREATE VIEW courses_view AS
SELECT
    b.id, b.course_type, b.course_code, b.class_no, b.course_name, b.category, b.credits,
    b.teacher, b.department, b.major, b.grade,
    b.schedule, b.classroom, b.schedule_raw, b.weekdays,
    b.enrollment, b.pnp, b.notes,
    d.english_name, d.prerequisites, d.intro_cn, d.intro_en, d.grading,
    d.ge_series, d.language, d.textbook, d.reference_book, d.syllabus, d.evaluation
FROM basic_info b
LEFT JOIN detail_info d ON d.course_id = b.id;
"""


def build():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    c = conn.cursor()
    c.executescript(SCHEMA)

    with open(SOURCE, encoding="utf-8") as f:
        data = json.load(f)

    seen = set()
    counts = {}
    total = 0
    for item in data:
        course_type = item.get("课程类型", "").strip()
        bi = item.get("基本信息", {}) or {}
        di = item.get("详细信息", {}) or {}
        course_code = bi.get("课程号", "").strip()
        class_no = bi.get("班号", "").strip()
        if not course_code or not course_type:
            continue
        key = (course_type, course_code, class_no)
        if key in seen:
            continue
        seen.add(key)

        raw_sched = bi.get("上课时间及教室", "")
        schedule, classroom, weekdays = parse_schedule(raw_sched)

        c.execute(
            """INSERT INTO basic_info
               (course_type, course_code, class_no, course_name, category, credits, teacher,
                department, major, grade, schedule, classroom, schedule_raw,
                weekdays, enrollment, pnp, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                course_type, course_code, class_no,
                bi.get("课程名", ""),
                bi.get("课程类别", ""),
                to_float(bi.get("学分", "0")),
                bi.get("教师", ""),
                bi.get("开课单位", ""),
                bi.get("专业", ""),
                bi.get("年级", ""),
                schedule, classroom, raw_sched, weekdays,
                bi.get("限数已选", ""),
                bi.get("自选PNP", ""),
                bi.get("备注", ""),
            ),
        )
        course_id = c.lastrowid
        c.execute(
            """INSERT INTO detail_info
               (course_id, english_name, prerequisites, intro_cn, intro_en, grading,
                ge_series, language, textbook, reference_book, syllabus, evaluation)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                course_id,
                di.get("英文名称", ""),
                di.get("先修课程", ""),
                di.get("中文简介", ""),
                di.get("英文简介", ""),
                di.get("成绩记载方式", ""),
                di.get("通识课所属系列", ""),
                di.get("授课语言", ""),
                di.get("教材", ""),
                di.get("参考书", ""),
                di.get("教学大纲", ""),
                di.get("教学评估", ""),
            ),
        )
        counts[course_type] = counts.get(course_type, 0) + 1
        total += 1

    conn.commit()
    print(f"Database built: {DB_PATH}")
    for k, v in sorted(counts.items()):
        print(f"  {k:6s}: {v}")
    print(f"  total : {total}")
    conn.close()


if __name__ == "__main__":
    build()
```

- [ ] **Step 2：跑建库**

```bash
venv/bin/python build_summer_db.py
```

预期输出：

```
Database built: 2026暑期学期课程.db
  公选课: NN
  通识课: NN
  ...
  total : XXX
```

- [ ] **Step 3：校验**

```bash
sqlite3 2026暑期学期课程.db "SELECT COUNT(*) FROM basic_info;"
sqlite3 2026暑期学期课程.db "SELECT course_type, COUNT(*) FROM basic_info GROUP BY course_type;"
sqlite3 2026暑期学期课程.db "SELECT course_code, course_name, teacher, schedule FROM courses_view LIMIT 5;"
```

预期：`COUNT(*)` 与 Task 8 里 JSON 的 total 一致；分类计数与 Task 8 第 2 步打印的 `by type` 一致；样本字段可读、无明显乱码。

- [ ] **Step 4：提交**

```bash
git add build_summer_db.py
git commit -m "build: summer term sqlite db"
```

---

## Task 10：收尾验收

- [ ] **Step 1：核对验收标准**

| 标准 | 检查方法 |
|---|---|
| 合成 JSON 存在 | `ls -l "Course data/北大暑期课程数据_25-26暑期.json"` |
| 总数 ≥ 页面所显示 | 与 Task 8 第 2 步对比 |
| 每条至少 课程号+课程名 非空 | `venv/bin/python -c "import json; d=json.load(open('Course data/北大暑期课程数据_25-26暑期.json')); print(sum(1 for x in d if x['基本信息'].get('课程号') and x['基本信息'].get('课程名')), '/', len(d))"` |
| DB 可打开 | Task 9 Step 3 已做 |
| courses_view 字段可读 | Task 9 Step 3 已做 |

- [ ] **Step 2：git status 应该是干净的**

```bash
git status
```

预期：只剩未跟踪的 `.auth/`、`tmp_summer/`、`Course data/`、`docs/`、`2026暑期学期课程.db`，且都在 `.gitignore` 里。

---

## 风险预案

- **登录被反爬识别**：如果 Chromium 一启动就被拦，加 `args=["--disable-blink-features=AutomationControlled"]`（已经加了）；必要时改成连接已开浏览器的 `connect_over_cdp`。
- **翻页是 POST 而非 GET**：Task 3 里如果发现是表单 POST 翻页，把 Task 6 的 next_btn 逻辑换成读取「共 X 页」+ 多次 POST。
- **详情字段名变化**：parse_detail_text 用字段名做锚点；如果某类型的详情页字段名跟 spec 不一致（如英语课没有「通识课所属系列」），缺位会自然变空串，不算错。
- **频率限制**：每 jitter 200-500ms；如果出现 403/429，把 jitter 抬到 1-2s，或在每类型之间加 `time.sleep(5)`。

## 不在本计划内的事

- app.py / index.html 接入暑期库（用户明确说本轮不做）
- 翻译流水线（translate_courses.py 不触发）
- 研究生暑期课程（用户没要）
- 合成 JSON / DB 进 git（按现有 gitignore 策略，原始数据不入仓）
