# 北京大学选课网数据抓取归档说明

本文档记录本项目抓取北京大学选课网课程数据的页面内脚本方案。当前归档包含 2026 暑期本科、2026 秋季本科和 2026 秋季研究生三套页面内抓取脚本。

## 文件说明

归档目录应放在项目根目录下：

```text
北京大学选课网数据抓取/
├── README.md
├── pku_inpage_summer_scraper.js
├── receive_pku_summer_payload.py
├── build_summer_db.py
├── pku_inpage_undergrad_2627_fall_scraper.js
├── receive_pku_undergrad_2627_fall_payload.py
├── build_undergrad_2627_fall_db.py
├── pku_inpage_graduate_2627_fall_scraper.js
├── receive_pku_graduate_2627_fall_payload.py
└── build_graduate_2627_fall_db.py
```

各文件用途：

- `pku_inpage_*_scraper.js`：注入到已经打开并登录的北京大学选课网页面中，在页面上下文里批量请求课程列表和课程号详情。
- `receive_pku_*_payload.py`：本机接收器，只监听 `127.0.0.1`，负责向页面提供脚本、接收进度和最终 JSON。
- `build_*_db.py`：把抓取到的课程 JSON 转成对应 SQLite 数据库。建库脚本依赖项目根目录下 `数据库构建脚本/build_common.py`，通过 `sys.path` 注入引用，不在本目录单独保留一份。

归档版脚本默认把结果写回项目根目录：

- 原始完整 payload：`tmp_summer/inpage_payload.json`
- 课程 JSON：`课程数据/北大暑期课程_25-26第3学期.json`
- SQLite 数据库：`数据库/2026暑期本科生课程.db`
- 原始完整 payload：`tmp_undergrad_2627_fall/inpage_payload.json`
- 课程 JSON：`课程数据/北大本科课程_26-27第1学期.json`
- SQLite 数据库：`数据库/2026秋季学期本科生课程.db`
- 原始完整 payload：`tmp_graduate_2627_fall/inpage_payload.json`
- 课程 JSON：`课程数据/北大研究生课程_26-27第1学期.json`
- SQLite 数据库：`数据库/2026秋季学期研究生课程.db`

## 重要安全提醒

抓取过程中不要点击页面上的 `加入选课计划`。本方案也不会请求 `addToPlan.do`，只请求课程查询列表和课程号详情页：

- 列表接口：`getCurriculmByForm.do`
- 翻页接口：`queryCurriculum.jsp`
- 详情接口：`goNested.do?course_seq_no=...`

不要用终端直接请求选课网详情页。直接 HTTP 请求更容易触发系统的 `请不要用刷课机刷课` 提示。本方案必须在当前已登录 Chrome 页面上下文中运行，让浏览器页面自己发起同源请求。

## 抓取范围与结果

本科脚本中的课程分类包括：

```text
培养方案
专业课
政治课
英语课
体育课
通识课
公选课
计算机基础课
劳动教育课
思政选择性必修课
```

2026 暑期本科课程抓取结果：

```text
总计 194 条
专业课 81
英语课 16
体育课 6
通识课 11
公选课 57
劳动教育课 23

培养方案 0
政治课 0
计算机基础课 0
思政选择性必修课 0
```

专业课、通识课、公选课抓取时脚本会把开课单位参数提交为 `ALL`，避免页面默认停留在社会学院。其他没有可选开课单位的类别提交为空。

英语课列表比普通类别多一个等级列，例如 `B`、`C`、`C+`。归档脚本已对英语课做专用字段映射，避免学分、教师、班号等字段错位。

2026 秋季本科课程抓取结果：

```text
总计 3032 条
专业课 2149
体育课 251
公选课 188
通识课 153
英语课 152
计算机基础课 55
劳动教育课 47
思政选择性必修课 25
政治课 12
培养方案 0
```

2026 秋季研究生课程抓取结果：

```text
总计 1611 条
必修 762
选修 756
限选 93
```

秋季本科同样会对专业课、通识课、公选课等带开课单位筛选的类别提交 `ALL`；秋季研究生课程页没有本科这组课程分类 tab，脚本按研究生课程列表翻页并逐条抓课程号详情。

## 操作流程

以下命令默认在项目根目录执行：

```bash
cd /Users/wishingcat/LovingHeart/Pinhaoke
```

### 1. 准备 Chrome 页面

在已经打开并登录的 Chrome 中进入北京大学选课网课程查询页：

```text
https://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/controller/courseQuery/getCurriculmByForm.do
```

确认页面属于目标学期，并选择匹配的脚本。本仓库当前归档的目标包括：

```text
26-27学年第1学期（2026 秋季本科 / 研究生）
25-26学年第3学期
```

不需要手动逐个点击课程号，也不要点击 `加入选课计划`。

### 2. 启动本机接收器

```bash
python3 "北京大学选课网数据抓取/receive_pku_summer_payload.py" --port 8765
# 或：python3 "北京大学选课网数据抓取/receive_pku_undergrad_2627_fall_payload.py" --port 8765
# 或：python3 "北京大学选课网数据抓取/receive_pku_graduate_2627_fall_payload.py" --port 8765
```

正常输出类似：

```text
[receiver] serving http://127.0.0.1:8765/inpage.js
[receiver] waiting for /done ...
```

如果端口被占用，可以换一个端口，例如：

```bash
python3 "北京大学选课网数据抓取/receive_pku_summer_payload.py" --port 8766
```

同时把下面注入脚本里的 `8765` 改成新端口。

### 3. 在当前 Chrome 标签页注入脚本

在当前选课网标签页的地址栏粘贴并执行：

```javascript
javascript:(async()=>{eval(await (await fetch('http://127.0.0.1:8765/inpage.js?'+Date.now())).text())})()
```

如果 Chrome 弹出 `elective.pku.edu.cn 想要访问此设备上的其他应用和服务`，选择允许。这个权限只用于页面把抓取结果发回本机 `127.0.0.1` 接收器。

开始运行后，页面右下角会出现状态提示，接收器终端会持续输出进度，例如：

```text
[progress] {"stage":"list","type":"专业课","page":1,"pages":1,"rows":81}
[progress] {"stage":"detail","type":"专业课","done":41,"total":81}
```

### 4. 等待抓取完成

完成后接收器会自动停止，并输出：

```text
[done] raw payload: /Users/wishingcat/LovingHeart/Pinhaoke/tmp_summer/inpage_payload.json
[done] final json : /Users/wishingcat/LovingHeart/Pinhaoke/课程数据/北大暑期课程_25-26第3学期.json
[done] rows       : 194
[receiver] stopped
```

如果 `errors` 非空，需要先检查失败类别。不要在失败后改用终端直接抓选课网页面。

### 5. 生成 SQLite 数据库

```bash
python3 "北京大学选课网数据抓取/build_summer_db.py"
# 或：python3 "北京大学选课网数据抓取/build_undergrad_2627_fall_db.py"
# 或：python3 "北京大学选课网数据抓取/build_graduate_2627_fall_db.py"
```

正常输出类似：

```text
Database built: /Users/wishingcat/LovingHeart/Pinhaoke/数据库/2026暑期本科生课程.db
  专业课         : 81
  体育课         : 6
  公选课         : 57
  劳动教育课       : 23
  英语课         : 16
  通识课         : 11
  total       : 194
```

建库脚本会重建对应的 SQLite 数据库。秋季本科、秋季研究生和暑期本科如果旧库里已有翻译或额外手工数据，先备份。

### 6. 校验数据

基础计数校验：

```bash
sqlite3 "数据库/2026暑期本科生课程.db" "
select course_type,count(*) from basic_info group by course_type order by course_type;
select 'total', count(*) from basic_info;
select 'detail', count(*) from detail_info;
select 'with_detail_text', count(*) from detail_info
where coalesce(english_name,'')<>'' or coalesce(intro_cn,'')<>'' or coalesce(syllabus,'')<>'';
select 'translations', count(*) from translations;
"
```

检查原始 JSON 是否有错误和重复主键：

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

本次抓取的期望结果是：

```text
rows 194
errors []
duplicate_keys 0
```

## 字段与数据库结构

`basic_info` 保存列表字段：

```text
course_type, course_code, class_no, course_name, category, credits,
teacher, department, major, grade, schedule, classroom, schedule_raw,
weekdays, enrollment, pnp, notes
```

`detail_info` 保存点开课程号后的详情字段：

```text
english_name, prerequisites, intro_cn, intro_en, grading, ge_series,
language, textbook, reference_book, syllabus, evaluation
```

`translations` 表保持和春季库一致的结构。抓取和建库本身不调用翻译 API，所以刚重建出来的新库会是空表：

```text
course_id, field, lang, text
```

当前仓库里的 `数据库/2026暑期本科生课程.db` 已经通过翻译流水线写入 10010 行暑期译文；`数据库/2026秋季学期本科生课程.db` 已写入 159138 行秋季本科译文；`数据库/2026秋季学期研究生课程.db` 已写入 52430 行秋季研究生译文。以后如果重新运行本目录的建库脚本，会覆盖数据库并清空这些译文；需要先备份旧库，或重建后按 `北京大学课程数据翻译/README.md` 重新跑翻译流水线。

## 修改到其他学期

如果要抓其他学期，至少检查这些位置：

1. 对应 `pku_inpage_*_scraper.js` 中的 `TERM`。
2. 对应 `receive_pku_*_payload.py` 中的 `FINAL_OUT` 文件名。
3. 对应 `build_*_db.py` 中的 `DB_PATH` 和 `SOURCE`。
4. 课程分类 `COURSE_TYPES` 是否和页面上的分类值一致。
5. 哪些类别需要把开课单位设为 `ALL`，对应 `DEPT_ALL_TYPES`。

页面表格列发生变化时，优先更新 `BASIC_HEADERS` 或类别专用映射。英语课已经有 `ENGLISH_HEADERS`。

## 常见问题

### 地址栏粘贴后没有运行

Chrome 有时会拦截粘贴的 `javascript:`。可以手动补上开头的 `javascript:`，然后按回车。确认接收器终端出现 `GET /inpage.js`。

### 出现本机服务权限弹窗

选择允许。页面需要把最终 JSON 通过 `POST /done` 发回 `127.0.0.1`，否则无法自动保存结果。

### 看到 `请不要用刷课机刷课`

不要继续用终端或新浏览器直接请求页面。回到已登录的当前 Chrome 选课网页面，用页面内注入脚本运行。

### 数据库条数比 JSON 少

先检查是否有重复主键：

```bash
python3 - <<'PY'
import json, collections
rows = json.load(open("课程数据/北大暑期课程_25-26第3学期.json", encoding="utf-8"))
keys = []
for r in rows:
    bi = r.get("基本信息") or {}
    keys.append((r.get("课程类型"), bi.get("课程号", "").strip(), bi.get("班号", "").strip()))
for key, count in collections.Counter(keys).items():
    if count > 1:
        print(key, count)
PY
```

如果集中出现在某个课程类别，通常是该类别的表格列和默认映射不一致，需要增加类别专用字段映射。本次英语课的问题已经在归档脚本中修复。
