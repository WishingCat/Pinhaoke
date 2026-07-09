# 北大 2026 暑期课程抓取与建库 — 当前设计

日期：2026-05-13
最后同步：2026-07-09
状态：已完成，本文档按当前仓库状态更新

## 目标

从北京大学选课系统抓取 `25-26学年第3学期` 暑期本科课程，产出并接入拼好课 V2：

1. `课程数据/北大暑期课程_25-26第3学期.json`
2. `数据库/2026暑期本科生课程.db`
3. 数据库内 `translations` 表，经后续翻译流水线补齐 7 语译文

当前结果：

```text
总计 194 条
专业课 81
英语课 16
体育课 6
通识课 11
公选课 57
劳动教育课 23
```

培养方案、政治课、计算机基础课、思政选择性必修课为空。

## 最终方案

最终没有采用单独启动 Playwright 浏览器的方案，而是使用**当前已登录 Chrome 页面内脚本**。

原因：

- 选课网直接从终端或新浏览器请求详情页容易触发 `请不要用刷课机刷课`。
- 用户要求“直接在现有界面上抓取，不要开新的”。
- 页面内 `fetch(..., {credentials:'include'})` 能复用当前 Chrome 登录态，并稳定读取列表与课程号详情。

架构：

```text
当前已登录 Chrome 选课页
  └─ 注入 pku_inpage_summer_scraper.js
       ├─ POST getCurriculmByForm.do       # 列表查询
       ├─ POST queryCurriculum.jsp         # 翻页
       ├─ GET  goNested.do?course_seq_no=  # 课程号详情
       └─ POST http://127.0.0.1:8765/done # 回传 JSON 到本机接收器

receive_pku_summer_payload.py
  ├─ 写 tmp_summer/inpage_payload.json
  └─ 写 课程数据/北大暑期课程_25-26第3学期.json

build_summer_db.py
  └─ 写 数据库/2026暑期本科生课程.db
```

## 归档文件

当前实现位于 `北京大学选课网数据抓取/`：

| 文件 | 作用 |
|---|---|
| `pku_inpage_summer_scraper.js` | 页面内抓取脚本 |
| `receive_pku_summer_payload.py` | 本机接收器，监听 `127.0.0.1` |
| `build_summer_db.py` | JSON → SQLite |
| `README.md` | 完整操作说明 |

建库脚本复用 `数据库构建脚本/build_common.py`，不在抓取目录复制公共构建工具。

## 安全边界

抓取脚本只请求：

- `getCurriculmByForm.do`
- `queryCurriculum.jsp`
- `goNested.do?course_seq_no=...`

严禁点击或请求：

- `加入选课计划`
- `addToPlan.do`

专业课、通识课、公选课抓取前都提交 `开课单位=ALL`，避免页面默认停留在社会学院。英语课表格比其他类别多 `英语等级` 列，脚本有 `ENGLISH_HEADERS` 专用映射。

## 数据库接入

`app.py` 已支持 `term=spring|summer`：

- `term=spring`：打开本科春季库并 `ATTACH` 研究生库
- `term=summer`：打开暑期本科库

课程 ID 前缀：

- `u<id>`：春季本科
- `g<id>`：春季研究生
- `s<id>`：暑期本科

列表接口会按 `course_code + class_no + teacher` 合并重复挂载课程，`course_type` 与 `category` 返回数组。

## 翻译状态

抓取与建库不调用翻译 API。`build_summer_db.py` 重建后会创建空的 `translations` 表。

当前仓库里的暑期库已通过 `北京大学课程数据翻译/` 流水线补齐译文：

```text
数据库/2026暑期本科生课程.db translations = 10010
```

重建暑期库前应先备份，或重建后重新跑翻译。

## 验收命令

```bash
python3 北京大学选课网数据抓取/build_summer_db.py

sqlite3 "数据库/2026暑期本科生课程.db" "
select course_type,count(*) from basic_info group by course_type order by course_type;
select 'total', count(*) from basic_info;
select 'detail', count(*) from detail_info;
select 'translations', count(*) from translations;
"
```

期望基本数据：

```text
total = 194
detail = 194
```

如果刚重建，`translations = 0` 是正常现象；当前已翻译版本为 `10010`。
