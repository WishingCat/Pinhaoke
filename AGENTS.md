# AGENTS.md

本文件面向在仓库中工作的工程代理。用户入口与功能简介见 `README.md`；抓取、翻译、数据和部署的详细操作分别由各目录 README 负责。

## 项目定位

拼好课 V2 是北京大学课程搜索单页应用：

- `app.py`：FastAPI 后端，按学期只读查询五个 SQLite 数据库。
- `index.html`：无构建步骤的单文件 SPA，通过 `/api/*` 获取数据。
- 生产站点：`https://www.pinhaoke.love`，Nginx 终止 TLS，systemd 运行 Uvicorn。
- 页面学期顺序为春季、暑期、秋季；API 与页面默认学期均为 `fall`。

## 目录职责

```text
app.py                          唯一后端模块
index.html                      唯一前端入口
requirements.txt               固定版本的生产依赖
Images/                         `/Images/` 公共资源，文件名属于公开 URL
数据库/                         五个正式 SQLite 数据库
课程数据/                       七份源 JSON 与数据说明
数据库构建脚本/                 共享解析、原子构建、春季建库
北京大学选课网数据抓取/          页面内脚本、接收器、暑期/秋季建库
北京大学课程数据翻译/            七语翻译任务
deploy/                         更新脚本、systemd unit、Nginx 模板
tests/                          标准库 unittest 回归测试
访问统计/                       历史 PDF 制品
归档/                           V1 只读参考，生产禁用
```

## 本地运行与验证

仓库内 `venv/` 可能指向生产路径。macOS 开发使用独立临时环境：

```bash
python3 -m venv /tmp/pinhaoke-dev
/tmp/pinhaoke-dev/bin/python -m pip install -r requirements.txt
/tmp/pinhaoke-dev/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

访问 `http://127.0.0.1:8000/`，不要直接打开本地 `index.html`。

完整测试：

```bash
python3 -m unittest discover -s tests -v
```

按改动范围先运行对应模块，再运行完整测试。交付前至少执行：

```bash
python3 -m compileall -q app.py 数据库构建脚本 北京大学选课网数据抓取 北京大学课程数据翻译
for f in 北京大学选课网数据抓取/pku_inpage_*_scraper.js; do node --check "$f"; done
bash -n deploy/update.sh
git diff --check
```

## 数据库连接与学期

`TERM_DBS` 定义数据库集合：

| term | `main` | `gr` | 合并后 API 卡片数 |
|---|---|---|---:|
| `spring` | `2026春季学期本科生课程.db` | `2026春季学期研究生课程.db` | 3701 |
| `summer` | `2026暑期本科生课程.db` | 无 | 160 |
| `fall` | `2026秋季学期本科生课程.db` | `2026秋季学期研究生课程.db` | 4421 |

验收口径可写作 `fall=4421`、`spring=3701`、`summer=160`。这些是列表合并后的卡片数，不是数据库原始行数。

`get_db()` 用 SQLite URI `mode=ro` 打开主库，再按需 `ATTACH` 研究生库，并执行 `PRAGMA query_only = ON`。应用代码不得通过 API 请求写数据库。静态文件路径全部从 `BASE_DIR` 解析，使模块可从任意工作目录导入。

`GET /api/health` 检查五库可读性、`basic_info` / `detail_info` / `translations` 表、详情行数、两表 ID 集合与唯一性、`PRAGMA foreign_key_check` 和 `PRAGMA integrity_check`。结果使用短时进程内缓存并返回 `Cache-Control: no-store`。

## API 契约

| Endpoint | 契约 |
|---|---|
| `GET /api/filters` | 参数 `term=fall\|spring\|summer`，默认 `fall`。返回 `course_types`、`categories`、`departments`、`credits`、`gradings`、`weekdays`。 |
| `GET /api/courses` | 返回 `{total, page, page_size, courses}`。支持搜索、筛选、排序、语言和分页。 |
| `GET /api/courses/{id}` | 前缀已包含学期与学段，不接受 `term`；支持 `lang`。 |
| `GET /api/health` | 返回五库健康状态；异常时为 503。 |

`GET /api/courses` 参数：

- `term`：`fall`、`spring`、`summer`，默认 `fall`
- `q`：课程名、目标语言课程名、英文名、教师、教室、目标语言教室、课程号
- `classroom`：教室专用模糊搜索，可与 `q` 组合
- `type`、`category`、`credits`、`department`、`weekday`、`grading`
- `sort`：`name_asc`、`name_desc`、`credits_asc`、`credits_desc`、`time_asc`、`random`；兼容旧值 `pinyin`、`pinyin_desc`
- `random_seed`：使随机排序跨页稳定
- `lang`：`zh`、`en`、`ja`、`ko`、`fr`、`de`、`es`、`ru`
- `page`：1 到 10000；`page_size`：1 到 200

非法学期、语言、星期、排序、学分、页码或 canonical ID 必须返回 422/404，不得把未经允许的值拼进 SQL。

## 课程 ID

API ID 是带命名空间的字符串，不是整数：

- `a<id>`：秋季本科
- `r<id>`：秋季研究生
- `u<id>`：春季本科
- `g<id>`：春季研究生
- `s<id>`：暑期本科

规范形式匹配 `^[ugsar][1-9][0-9]*$`。详情路由通过前缀选择数据库，所以共享链接无需额外 `term`。前端必须把 ID 当字符串；课程卡使用事件监听器传递 ID，不在 inline JavaScript 中插入未加引号的 ID。

## 列表合并语义

选课网可能把同一教学班挂在多个课程类型或类别下。列表查询使用两阶段语义：

1. 在完整源数据中找出满足搜索与筛选条件的分组键。
2. 回到完整源数据聚合命中的整个分组。

分组键包含学段前缀、`course_code`、`class_no` 和教师；教师为空时使用原始 ID。学段前缀阻止本科与研究生误合并，`class_no` 防止同一教师的平行班误合并。

代表记录按详情完整度选择，分数相同取较小的本地 ID；另一条记录只在能补足代表记录空字段时作为 fallback。`course_type` 和 `category` 聚合为稳定排序的数组。列表响应中这两个字段是数组，详情响应中是字符串；前端统一通过 `asArray()` 和 badge helper 渲染。

筛选只决定哪些组命中，不得改变代表 ID、完整徽章集合或非空字段。所有排序以唯一 `id` 收尾；`random` 使用 `random_seed` 和代表 ID 的确定性表达式，禁止改成 `ORDER BY RANDOM()`。

## 多语言

多语言有两个来源：

1. `index.html` 的有限枚举字典负责院系、类别、成绩方式、星期等封闭集合。
2. 每个数据库的 `translations(course_id, field, lang, text)` 负责课程自由文本。

列表只联接卡片字段 `course_name`、`classroom`、`notes`；详情通过 `TRANSLATABLE_FIELDS` 替换完整字段。译文缺失或只含空白时保留原始中文。目标语言课程名和教室参与搜索，名称排序依据 API 返回的显示文本。

## 建库与数据安全

五个建库入口：

```bash
python3 数据库构建脚本/build_undergrad_db.py
python3 数据库构建脚本/build_graduate_db.py
python3 北京大学选课网数据抓取/build_summer_db.py
python3 北京大学选课网数据抓取/build_undergrad_2627_fall_db.py
python3 北京大学选课网数据抓取/build_graduate_2627_fall_db.py
```

`数据库构建脚本/build_atomic.py` 是唯一共享原子构建实现。各入口先完整解析源 JSON，严格检查必填字段、学分和冲突键，再通过 `atomic_database` 在目标同目录构建临时库。只有表、视图、外键、行数、1:1 详情和完整性检查全部通过后才 `os.replace` 正式库，并立即 fsync 目标父目录。替换前失败时正式文件不变；替换后的目录同步失败会明确报错，说明新目录项尚未确认持久化。替换沿用正式文件原权限模式。

重建会创建空 `translations` 表，等同于删除该库既有译文。运行建库前必须备份正式库或明确接受后续重新翻译。不要复制第二份 `build_common.py`。

正式数据库和七份源 JSON 属于受保护数据：普通代码修复不得修改、重建、格式化或提交这些文件。测试使用临时文件和临时数据库。

## 抓取边界

抓取必须在用户已登录的 Chrome 当前选课网页面内运行，复用页面登录态。接收器仅监听 `127.0.0.1`，使用一次性 token、PKU Origin 校验、请求体上限与严格 payload schema。

三个页面脚本只允许列表、翻页和课程详情端点，必须确保源码不含 `addToPlan.do` 或 `加入选课计划` 调用。任一类别、页面或详情校验失败时整次任务失败，不发布部分正式 JSON。具体流程见 `北京大学选课网数据抓取/README.md`。

## 翻译边界

翻译脚本只有在存在待处理记录且真正发起请求时读取 `DEEPSEEK_API_KEY`。`--help`、导入和测试不得需要密钥或产生网络费用。选择参数只能扫描所选数据库；API 返回空白译文必须拒绝；数据库锁重试只能重放写入，不能重复调用付费 API。

任何代理都不得自动运行翻译命令。只有用户明确批准费用、范围和目标数据库后才能执行。详细矩阵见 `北京大学课程数据翻译/README.md`。

## 部署边界

生产更新唯一入口：

```bash
sudo bash /opt/pinhaoke/deploy/update.sh
```

不要手工 `git pull` 后重启，不要绕过预检，不要让 `www-data` 持有代码、Git、虚拟环境或数据库。更新脚本部署精确 `origin/main`，在停服前完成目标工作树、LFS 和候选 venv 预检，激活失败或收到 INT/TERM 时自动恢复旧提交、旧 unit、旧 venv 和原服务状态。

`deploy/nginx.conf` 只是与 Certbot 共存的站点模板，必须手工安装并先运行 `nginx -t`；`deploy/update.sh` 不覆盖 Nginx。任何任务只有用户明确要求后才可 push 或部署。本地通过测试不代表生产已更新。

## 文档所有权

仓库只保留八份 tracked Markdown：

- `README.md`：产品入口、功能、本地运行和文档索引
- `AGENTS.md`：本文件，工程契约与边界
- `CLAUDE.md`：指向本文件的短说明
- `北京大学选课网数据抓取/README.md`：抓取与建库
- `北京大学课程数据翻译/README.md`：翻译任务
- `课程数据/数据说明.md`：数据口径
- `deploy/README.md`：生产运维
- `归档/README.md`：V1 禁用边界

不要新增已完成计划、临时设计或重复架构文档。实现变化必须更新对应权威文档和 `tests/test_documentation.py`。
