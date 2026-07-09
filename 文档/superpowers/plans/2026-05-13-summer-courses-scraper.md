# 北大 2026 暑期课程抓取与建库 — 当前复跑计划

最后同步：2026-07-09

本文档记录暑期课程抓取任务的最终可复跑流程。早期曾计划用 Playwright 单独启动浏览器，但最终实现改为**在用户已经打开并登录的 Chrome 当前页面内注入脚本**。权威操作说明见 `北京大学选课网数据抓取/README.md`。

## 当前产物

| 产物 | 当前状态 |
|---|---|
| `课程数据/北大暑期课程_25-26第3学期.json` | 194 条 |
| `数据库/2026暑期本科生课程.db` | 194 条课程，194 条详情 |
| `数据库/2026暑期本科生课程.db.translations` | 当前已写入 10010 行译文 |
| `北京大学选课网数据抓取/` | 页面内抓取脚本、接收器、建库脚本 |

分类计数：

```text
专业课 81
英语课 16
体育课 6
通识课 11
公选课 57
劳动教育课 23
```

培养方案、政治课、计算机基础课、思政选择性必修课为空。

## 文件结构

```text
Pinhaoke/
├── 北京大学选课网数据抓取/
│   ├── README.md
│   ├── pku_inpage_summer_scraper.js
│   ├── receive_pku_summer_payload.py
│   └── build_summer_db.py
├── 数据库构建脚本/
│   └── build_common.py
├── 课程数据/
│   └── 北大暑期课程_25-26第3学期.json
├── 数据库/
│   └── 2026暑期本科生课程.db
└── tmp_summer/
    └── inpage_payload.json
```

## 复跑步骤

### 1. 打开选课网页面

在用户已经登录的 Chrome 中打开：

```text
https://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/controller/courseQuery/getCurriculmByForm.do
```

确认页面是 `25-26学年第3学期`。

不要点击 `加入选课计划`。

### 2. 启动本机接收器

```bash
python3 "北京大学选课网数据抓取/receive_pku_summer_payload.py" --port 8765
```

### 3. 注入页面内脚本

在当前选课网标签页地址栏执行：

```javascript
javascript:(async()=>{eval(await (await fetch('http://127.0.0.1:8765/inpage.js?'+Date.now())).text())})()
```

如果 Chrome 弹出本机服务权限提示，允许它访问 `127.0.0.1`，用于回传 JSON。

### 4. 等待完成

接收器输出 `[done]` 后会写入：

```text
tmp_summer/inpage_payload.json
课程数据/北大暑期课程_25-26第3学期.json
```

### 5. 重建暑期数据库

```bash
python3 "北京大学选课网数据抓取/build_summer_db.py"
```

该命令会覆盖 `数据库/2026暑期本科生课程.db`。如果需要保留当前翻译，先备份数据库。

### 6. 校验

```bash
sqlite3 "数据库/2026暑期本科生课程.db" "
select course_type,count(*) from basic_info group by course_type order by course_type;
select 'total', count(*) from basic_info;
select 'detail', count(*) from detail_info;
select 'translations', count(*) from translations;
"
```

刚重建后 `translations = 0` 是正常的；当前已翻译版本应为 `10010`。

检查 JSON：

```bash
python3 - <<'PY'
import json, collections

rows = json.load(open("课程数据/北大暑期课程_25-26第3学期.json", encoding="utf-8"))
payload = json.load(open("tmp_summer/inpage_payload.json", encoding="utf-8"))

print("rows", len(rows))
print("types", dict(collections.Counter(r.get("课程类型") for r in rows)))
print("errors", payload.get("errors"))

keys = []
for r in rows:
    bi = r.get("基本信息") or {}
    keys.append((r.get("课程类型"), bi.get("课程号", "").strip(), bi.get("班号", "").strip()))
print("duplicate_keys", sum(1 for v in collections.Counter(keys).values() if v > 1))
PY
```

期望：

```text
rows 194
errors []
duplicate_keys 0
```

## 后续翻译

重建数据库后，如需恢复多语言：

```bash
export DEEPSEEK_API_KEY=sk-xxxxx
python3 北京大学课程数据翻译/translate_courses.py --only summer_intro
python3 北京大学课程数据翻译/translate_misc.py --phase short
python3 北京大学课程数据翻译/translate_misc.py --phase long
python3 北京大学课程数据翻译/translate_stubborn.py
```

`translate_misc.py` 覆盖三套数据库；如果只想补暑期，需要在脚本里临时限制任务列表，或接受它顺带检查春季库的缺口。

## 风险预案

- 如果出现 `请不要用刷课机刷课`，停止终端直连请求，回到当前已登录 Chrome 页面内执行脚本。
- 如果数据库行数小于 JSON 行数，先检查重复主键。英语课有额外 `英语等级` 列，当前脚本已专门处理。
- 如果专业课、通识课、公选课数量异常，检查 `DEPT_ALL_TYPES` 是否仍为这些类别提交 `开课单位=ALL`。
- 如果页面新增课程分类或表格列，更新 `pku_inpage_summer_scraper.js` 中的 `COURSE_TYPES`、`BASIC_HEADERS` 或类别专用 header。
