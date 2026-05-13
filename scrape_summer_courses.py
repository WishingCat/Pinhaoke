#!/usr/bin/env python3
"""Scrape PKU 2026 summer courses via Playwright with manual login handoff.

Usage:
    python3 scrape_summer_courses.py --types 公选课        # 单类型试跑（默认）
    python3 scrape_summer_courses.py --types all          # 全 6 类
    python3 scrape_summer_courses.py --types 公选课 通识课  # 指定多类
    python3 scrape_summer_courses.py --force              # 忽略 tmp 缓存重抓
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


def scrape_type(page: Page, type_name: str) -> list[dict]:
    """STUB — 在后续 Task 中逐步填充."""
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
