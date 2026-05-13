#!/usr/bin/env python3
"""Scrape PKU 2026 summer courses via Playwright with manual login handoff.

Usage:
    python3 scrape_summer_courses.py --types 公选课        # 单类型试跑（默认）
    python3 scrape_summer_courses.py --types all          # 全部课程分类
    python3 scrape_summer_courses.py --types 专业课 通识课  # 指定多类
    python3 scrape_summer_courses.py --force              # 忽略 tmp 缓存重抓
"""
import argparse
import json
import os
import random
import re
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
AUTH_DIR = BASE / ".auth"
AUTH_STATE = AUTH_DIR / "pku_state.json"
TMP_DIR = BASE / "tmp_summer"
OUT_JSON = BASE / "Course data" / "北大暑期课程数据_25-26第3学期.json"

LOGIN_URL = "https://iaaa.pku.edu.cn/iaaa/oauthlogin.do?appid=syllabus&redirectUrl=https://elective.pku.edu.cn/elective2008/ssoLogin.do"
QUERY_URL = "https://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/controller/courseQuery/getCurriculmByForm.do"
PAGER_URL = "https://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/controller/courseQuery/queryCurriculum.jsp"

COURSE_TYPES = {
    "培养方案": "education_plan_bk",
    "专业课": "speciality",
    "政治课": "politics",
    "英语课": "english",
    "体育课": "gym",
    "通识课": "tsk_choice",
    "公选课": "pub_choice",
    "计算机基础课": "liberal_computer",
    "劳动教育课": "ldjyk",
    "思政选择性必修课": "szxzxbx",
}
ALL_TYPES = list(COURSE_TYPES)
DEPT_SELECTABLE_ALL = {"speciality", "tsk_choice", "pub_choice"}

BASIC_HEADERS = [
    "课程号", "课程名", "课程类别", "学分", "教师", "班号", "开课单位",
    "专业", "年级", "上课时间及教室", "限数已选", "自选PNP", "备注",
]
DETAIL_FIELDS = [
    "英文名称", "先修课程", "中文简介", "英文简介", "成绩记载方式",
    "通识课所属系列", "授课语言", "教材", "参考书", "教学大纲", "教学评估",
]
DETAIL_ALIASES = {
    "英文名称": ("英文名称", "英文名", "课程英文名称"),
    "先修课程": ("先修课程", "先修要求"),
    "中文简介": ("中文简介", "课程中文简介", "课程简介(中文)", "课程简介（中文）"),
    "英文简介": ("英文简介", "课程英文简介", "课程简介(英文)", "课程简介（英文）"),
    "成绩记载方式": ("成绩记载方式", "成绩记录方式", "成绩方式"),
    "通识课所属系列": ("通识课所属系列", "通识课系列", "所属系列"),
    "授课语言": ("授课语言", "教学语言"),
    "教材": ("教材",),
    "参考书": ("参考书", "参考书目"),
    "教学大纲": ("教学大纲", "大纲"),
    "教学评估": ("教学评估", "课程评估", "评估"),
}


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
    page.set_default_timeout(15000)
    return browser, context, page


def ensure_logged_in(page: Page, context: BrowserContext):
    """Always start from IAAA login URL; poll URL until we land back on elective."""
    print(f"[i] 打开登录入口：{LOGIN_URL}", flush=True)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    print(f"[i] 跳转后 URL: {page.url}", flush=True)

    if "iaaa.pku.edu.cn" in page.url:
        print("\n[!] 检测到未登录。请在弹出的 Chromium 窗口里完成登录（学号 + 密码 + 验证码）。", flush=True)
        print("[!] 脚本会自动检测登录完成，无需操作终端。最多等 5 分钟。", flush=True)

    deadline = time.time() + 300
    last_print = 0
    while time.time() < deadline:
        url = page.url
        # 登录成功 = 离开 IAAA 域、回到 elective 域
        if "elective.pku.edu.cn" in url and "iaaa.pku.edu.cn" not in url:
            break
        now = time.time()
        if now - last_print > 10:
            print(f"[.] 等待登录中... 当前 URL: {url}", flush=True)
            last_print = now
        page.wait_for_timeout(1000)
    else:
        raise RuntimeError(f"超时未登录，当前 URL: {page.url}")

    print(f"[i] 登录后落点 URL: {page.url}", flush=True)
    # 跳到查询页
    page.goto(QUERY_URL, wait_until="domcontentloaded")
    print(f"[i] 查询页 URL: {page.url}", flush=True)
    if "iaaa.pku.edu.cn" in page.url:
        raise RuntimeError("访问查询页又被踢回 IAAA，登录态可能未真正生效")

    context.storage_state(path=str(AUTH_STATE))
    print(f"[+] 已保存登录态到 {AUTH_STATE.relative_to(BASE)}", flush=True)


def clean_text(text: str) -> str:
    if text is None:
        return ""
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def cell_text(cell) -> str:
    return clean_text(cell.get_text("", strip=True))


def query_form_data(type_value: str) -> dict[str, str]:
    """Build the NetUI form payload used by the course query page."""
    dept_value = "ALL" if type_value in DEPT_SELECTABLE_ALL else ""
    return {
        "wlw-radio_button_group_key:{actionForm.courseSettingType}": type_value,
        "{actionForm.courseID}": "",
        "{actionForm.courseName}": "",
        "wlw-select_key:{actionForm.deptID}OldValue": "true",
        "wlw-select_key:{actionForm.deptID}": dept_value,
        "wlw-select_key:{actionForm.courseDay}OldValue": "true",
        "wlw-select_key:{actionForm.courseDay}": "",
        "wlw-select_key:{actionForm.courseTime}OldValue": "true",
        "wlw-select_key:{actionForm.courseTime}": "",
        "wlw-checkbox_key:{actionForm.queryDateFlag}OldValue": "false",
        "deptIdHide": "",
    }


def is_system_prompt(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = soup.get_text("\n", strip=True)
    return title == "系统提示" or "提示:请不要用刷课机刷课" in text or "超时" in text


def post_query(page: Page, type_name: str, page_no: int | None = None) -> str:
    type_value = COURSE_TYPES[type_name]
    url = PAGER_URL if page_no is not None else QUERY_URL
    form = {"netui_row": str(page_no)} if page_no is not None else query_form_data(type_value)
    resp = page.request.post(
        url,
        form=form,
        headers={"Referer": QUERY_URL},
        timeout=30000,
    )
    html = resp.text()
    if resp.status >= 400:
        raise RuntimeError(f"{type_name}: HTTP {resp.status} from {url}")
    if is_system_prompt(html):
        raise RuntimeError(f"{type_name}: query returned system prompt; login state may be invalid")
    return html


def page_count(html: str) -> int:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = re.search(r"Page\s+\d+\s+of\s+(\d+)", text, re.I)
    return int(m.group(1)) if m else 1


def parse_course_rows(html: str, type_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.datagrid")
    if not table:
        return []
    rows = []
    for tr in table.find_all("tr"):
        if tr.find("th"):
            continue
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 13:
            continue
        values = [cell_text(c) for c in cells]
        basic = dict(zip(BASIC_HEADERS, values[:13]))
        link = cells[0].find("a", href=True)
        detail_url = urljoin(QUERY_URL, link["href"]) if link else ""
        item = {
            "课程类型": type_name,
            "数据学期": "25-26学年第3学期",
            "详情链接": detail_url,
            "基本信息": basic,
            "详细信息": {k: "" for k in DETAIL_FIELDS},
        }
        seq_match = re.search(r"course_seq_no=([^&]+)", detail_url)
        if seq_match:
            item["课程序号"] = seq_match.group(1)
        rows.append(item)
    return rows


def normalize_label(label: str) -> str:
    return clean_text(label).strip("：: ")


def parse_detail_pairs(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    details = {k: "" for k in DETAIL_FIELDS}

    def assign(label: str, value: str):
        label_norm = normalize_label(label)
        value = clean_text(value)
        if not value:
            return
        for field, aliases in DETAIL_ALIASES.items():
            if label_norm in aliases and not details[field]:
                details[field] = value
                return

    for tr in soup.find_all("tr"):
        cells = [clean_text(td.get_text("\n", strip=True)) for td in tr.find_all(["th", "td"], recursive=False)]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            for i, text in enumerate(cells[:-1]):
                label = normalize_label(text)
                # Some cells contain "标签：值"; handle that before using the next cell.
                matched = False
                for field, aliases in DETAIL_ALIASES.items():
                    for alias in aliases:
                        if text.startswith(alias + "：") or text.startswith(alias + ":"):
                            details[field] = clean_text(re.split(r"[:：]", text, 1)[1])
                            matched = True
                            break
                    if matched:
                        break
                if not matched:
                    assign(label, cells[i + 1])
        elif len(cells) == 1:
            text = cells[0]
            for field, aliases in DETAIL_ALIASES.items():
                if details[field]:
                    continue
                for alias in aliases:
                    if text.startswith(alias + "：") or text.startswith(alias + ":"):
                        details[field] = clean_text(re.split(r"[:：]", text, 1)[1])
                        break

    # Fallback for detail pages that render as label/value lines rather than cells.
    text = soup.get_text("\n", strip=True)
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    label_to_field = {
        alias: field for field, aliases in DETAIL_ALIASES.items() for alias in aliases
    }
    label_positions = []
    for i, line in enumerate(lines):
        label = normalize_label(line)
        if label in label_to_field:
            label_positions.append((i, label_to_field[label]))
        else:
            for alias, field in label_to_field.items():
                if line.startswith(alias + "：") or line.startswith(alias + ":"):
                    if not details[field]:
                        details[field] = clean_text(re.split(r"[:：]", line, 1)[1])
                    break
    for idx, (line_no, field) in enumerate(label_positions):
        if details[field]:
            continue
        end = label_positions[idx + 1][0] if idx + 1 < len(label_positions) else len(lines)
        value = "\n".join(lines[line_no + 1:end])
        details[field] = clean_text(value)

    return details


def fetch_detail(page: Page, item: dict, index: int, total: int) -> dict[str, str]:
    url = item.get("详情链接", "")
    if not url:
        return {k: "" for k in DETAIL_FIELDS}
    # The detail endpoint is sensitive to session/referer. Try a request first,
    # then fall back to a real navigation in the same browser context.
    html = ""
    try:
        resp = page.request.get(url, headers={"Referer": QUERY_URL}, timeout=30000)
        html = resp.text()
    except Exception:
        html = ""
    if not html or is_system_prompt(html):
        try:
            page.goto(url, wait_until="domcontentloaded", referer=QUERY_URL, timeout=30000)
            html = page.content()
        finally:
            page.goto(QUERY_URL, wait_until="domcontentloaded", timeout=30000)
    if is_system_prompt(html):
        print(f"[!] 详情失败 {index}/{total}: {item['基本信息'].get('课程号')} {item['基本信息'].get('课程名')}", flush=True)
        return {k: "" for k in DETAIL_FIELDS}
    return parse_detail_pairs(html)


def scrape_type(page: Page, type_name: str) -> list[dict]:
    html = post_query(page, type_name)
    pages = page_count(html)
    all_rows = []
    for n in range(1, pages + 1):
        page_html = html if n == 1 else post_query(page, type_name, page_no=n)
        rows = parse_course_rows(page_html, type_name)
        print(f"[i] {type_name}: 第 {n}/{pages} 页 {len(rows)} 条", flush=True)
        all_rows.extend(rows)
        jitter(0.1, 0.25)

    if not all_rows:
        return []

    detail_page = page.context.new_page()
    detail_page.set_default_timeout(15000)
    try:
        total = len(all_rows)
        for i, item in enumerate(all_rows, 1):
            item["详细信息"] = fetch_detail(detail_page, item, i, total)
            if i % 10 == 0 or i == total:
                print(f"[i] {type_name}: 详情 {i}/{total}", flush=True)
            jitter(0.15, 0.35)
    finally:
        detail_page.close()
    return all_rows


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
