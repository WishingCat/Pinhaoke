# 北大 2026 暑期课程抓取与建库 — 设计

日期：2026-05-13
状态：草案 → 待用户确认

## 目标

从 PKU 选课系统 `https://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/controller/courseQuery/getCurriculmByForm.do` 抓取「2026 暑期学期」的全部课程，产出：

1. `Course data/北大暑期课程数据_25-26暑期.json` — 合成 JSON（一个文件，每条记录带「课程类型」字段）
2. `2026暑期学期课程.db` — 独立 SQLite 库，结构对齐 `2026春季学期本科生课程.db`

本轮**不**改动 `app.py` 与 `index.html`。前端、API、部署都不动；这一轮的产出是「可验证的数据 + 可重建的库」。

## 抓取范围

页面提供 10 个 tab，其中 6 个有数据要抓：

| Tab | 抓取 | 备注 |
|---|---|---|
| 培养方案 | ✗ | 空 |
| 专业课 | ✓ | 默认开课单位＝社会学院，需切换到「全部」 |
| 政治课 | ✗ | 空 |
| 英语课 | ✓ | |
| 体育课 | ✓ | |
| 通识课 | ✓ | 默认开课单位＝社会学院，需切换到「全部」 |
| 公选课 | ✓ | 首选试跑类型（量小、字段全） |
| 计算机基础课 | ✗ | 空 |
| 劳动教育课 | ✓ | |
| 思政选择性必修课 | ✗ | 空 |

## 架构

```
                          ┌── tmp_summer/<类型>.json   断点中间产物
Playwright (有头 Chromium) │
        │                  ├── Course data/北大暑期课程数据_25-26暑期.json  合成产物
        ▼                 ▼
scrape_summer_courses.py ──► build_summer_db.py ──► 2026暑期学期课程.db
        │
        └── 复用 .auth/pku_state.json (storage_state)
```

### 组件 1：`scrape_summer_courses.py`

Playwright 同步 API。流程：

1. **会话复用**：若 `.auth/pku_state.json` 存在，启动时传入 `storage_state`，免登录；否则启动空白上下文，跳转登录页等用户手工登录，登录成功后保存 storage state。
2. **导航**：跳到 `getCurriculmByForm.do`。
3. **类型循环**（命令行参数 `--types 公选课` 控制，默认只跑公选课；`--types all` 跑全 6 类）：
   - 切换到目标 tab
   - 若存在「开课单位」下拉，选「全部」
   - 提交查询表单
   - **分页遍历**：抓所有页的结果行 → 解析「基本信息」字段
   - 对每行：点击课程号链接 → 等待详情面板（或弹窗）出现 → 抓「详细信息」字段 → 关闭面板
   - 写一份 `tmp_summer/<类型>.json`
4. **合并**：所有指定类型抓完后，把 `tmp_summer/*.json` 合并写入 `Course data/北大暑期课程数据_25-26暑期.json`。
5. **断点**：tmp 文件存在则跳过该类型；可用 `--force` 重抓。

字段对齐既有本科 JSON：

```json
{
  "课程类型": "公选课",
  "基本信息": {
    "课程号": "...", "班号": "...", "课程名": "...", "课程类别": "...",
    "学分": "...", "教师": "...", "开课单位": "...", "专业": "...",
    "年级": "...", "上课时间及教室": "...", "限数已选": "...",
    "自选PNP": "...", "备注": "..."
  },
  "详细信息": {
    "英文名称": "...", "先修课程": "...", "中文简介": "...",
    "英文简介": "...", "成绩记载方式": "...", "通识课所属系列": "...",
    "授课语言": "...", "教材": "...", "参考书": "...",
    "教学大纲": "...", "教学评估": "..."
  }
}
```

字段缺失时填空字符串。详情面板上没有的列保持 `""`。

### 组件 2：`build_summer_db.py`

直接照抄 `build_undergrad_db.py` 的 schema（含 `course_type` 字段、`basic_info`/`detail_info`/`courses_view`），把 SOURCES 改成单文件读取，按记录里的「课程类型」入库。复用 `build_common.parse_schedule` / `to_float`。

DB 路径：`2026暑期学期课程.db`，加入 `.gitignore`（与 `2026春季学期本科生课程.db` 同处理）。

### 依赖

- 新增 `playwright`（仅开发期使用，不进 `requirements.txt`；本地 `pip install playwright && playwright install chromium`）
- 现有 `build_common.py` 直接复用

### `.gitignore` 增加

```
2026暑期学期课程.db
.auth/
tmp_summer/
```

## 试跑策略

1. 第一步：`python scrape_summer_courses.py --types 公选课`
   - 我陪你看：登录交接是否顺畅、tab 切换是否到位、「开课单位」是否能正确选「全部」、分页是否抓全、详情面板字段是否对齐。
   - 输出 `tmp_summer/公选课.json` 后人工抽样几条比对 `Course data/北大公选课数据_25-26第2学期.json` 结构。
2. 第二步：结构验收通过后，跑剩下 5 类。
3. 第三步：`python build_summer_db.py`，看课程总数是否合理（人工估算）。

## 风险与未知

- **登录方式**：PKU IAAA 登录可能含验证码/扫码。本方案让用户手动完成，不用脚本去碰登录字段。
- **页面 DOM 未确认**：tab 切换、分页、课程号点击的具体选择器要等启动 Playwright 看到页面后才知道。脚本第一版会用「打开页面后暂停 → 控制台手工确认 DOM → 填回选择器」的方式做，不预先猜。
- **详情面板形态未知**：可能是新窗口、可能是同页面 div、可能是 iframe。第一类试跑时确认。
- **频率限制**：暑期课程量比春季少，预计 6 类合计几百门。每请求加 200~500ms 抖动避免被限。
- **断网/超时**：tmp 文件分类型保存，重跑只补缺。

## 不做（YAGNI）

- 不做 app.py 适配、不动 index.html。
- 不做翻译（沿用现有的 translate_courses.py 流水线，本轮不触发）。
- 不做研究生暑期。用户没说要，且页面 URL 是本科系统。
- 不做 PR/部署。仅本地数据产物。

## 验收标准

- `Course data/北大暑期课程数据_25-26暑期.json` 存在，记录数 ≥ 试跑日页面所显示总数（人工抽样比对）。
- 每条记录至少有「基本信息.课程号」「基本信息.课程名」非空。
- `2026暑期学期课程.db` 可被 `sqlite3` 打开，`SELECT COUNT(*) FROM basic_info` 与 JSON 记录数一致。
- 用 `SELECT * FROM courses_view LIMIT 5` 抽样字段可读。
