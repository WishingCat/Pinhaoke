# AGENTS.md

本文件面向在仓库中工作的工程代理。用户入口与功能简介见 `README.md`；抓取、翻译、数据和部署的详细操作分别由各目录 README 负责。

## 项目定位

拼好课 V2 是北京大学课程搜索与树洞课程评测应用：

- `app.py`：FastAPI 后端，只读查询五个课程 SQLite 数据库和一个树洞评测数据库。
- `index.html`：无构建步骤的课程搜索页；`reviews.html`：无构建步骤的树洞课程评测页。
- 生产站点：`https://www.pinhaoke.love`，Nginx 终止 TLS，systemd 运行 Uvicorn。
- 页面学期顺序为春季、暑期、秋季；API 与页面默认学期均为 `fall`。
- 树洞课程评测作为学期控件旁的独立入口，在手机窄屏下单独占行。评测搜索框输入即搜，不显示联想下拉；前 `24` 门课程热榜只由右侧“热门课程”按钮展开。列表卡片用六组轮换的整框颜色区分相邻树洞，点击卡片才请求快照中的完整主帖和全部回复。

## 目录职责

```text
app.py                          唯一后端模块
index.html                      课程搜索入口
reviews.html                    树洞课程评测入口
requirements.txt               固定版本的生产依赖
Images/                         `/Images/` 公共资源，文件名属于公开 URL
数据库/                         五个课程库与一个树洞评测正式库
课程数据/                       七份源 JSON 与数据说明
数据库构建脚本/                 共享解析、原子构建、春季及树洞评测建库
北京大学选课网数据抓取/          页面内脚本、接收器、暑期/秋季建库
北京大学课程数据翻译/            七语翻译任务
deploy/                         更新脚本、systemd unit、Nginx 模板
tests/                          标准库 unittest 回归测试
访问统计/                       历史 PDF 制品
归档/                           V1 只读参考，生产禁用
```

## 前端与网页设计契约

`index.html` 和 `reviews.html` 都是可直接由 FastAPI 返回的单文件页面，CSS 与 JavaScript 内联，无 npm、打包器或运行时框架。两页没有共享样式文件，因此修改共同控件时必须人工同步视觉尺寸、字体、主题变量和响应式行为，并由前端契约测试防止漂移。

### 视觉系统

- 内容容器最大宽度为 `1120px`。正文使用 `'PingFang SC'`、`'Hiragino Sans GB'` 和系统无衬线回退；课程号、树洞号、楼层与统计数字使用 JetBrains Mono 或系统等宽字体。
- 浅色背景为接近 `#F7F7F8` 的中性灰，深色背景为接近 `#0E1013` 的近黑色；品牌主色来自 Logo 的青绿色。页面不能退化成单一色相，课程类型、评测实体与树洞边框分别使用靛蓝、绿色、琥珀、玫红、蓝色等辅助色。
- 两页都有低对比度环境柔光。课程页使用青绿与靛蓝，评测页使用粉色与雾蓝；柔光固定在内容后方、不可拦截事件，并在 `prefers-reduced-motion` 下停止动画。
- 标题允许使用品牌渐变；普通命令按钮、筛选按钮、搜索框和卡片保持实体表面与清晰边界。圆角以 `7px`、`8px`、`10px`、`14px` 为主，不新增大面积胶囊卡片或嵌套卡片。
- 字号不能随 viewport 宽度连续缩放，`letter-spacing` 保持 `0`。长课程名、院系、标签和按钮文本必须换行或压缩布局，不得遮挡相邻内容或造成横向滚动。

### 页面结构与交互

- 两页共享吸顶顶栏、品牌标题、学期控件、独立评测入口、搜索框宽度、浅色/深色主题和主体容器。学期控件固定按春季、暑期、秋季排列，秋季默认；树洞入口与学期同级但不属于学期值。
- 课程页的筛选网格桌面为四列，`900px` 下三列，`720px` 下由居中、全宽、加粗的筛选按钮折叠，并至少保持两列筛选项。按钮使用与搜索框一致的白色表面和 `14px` 圆角，不使用绿色描边或按钮渐变。
- 课程卡片按公选、通识、专业、研究生类型使用不同颜色的完整边框，不使用左侧彩条。卡片点击或 Enter/Space 打开课程详情；详情弹窗包含可分享链接并恢复关闭前焦点。
- 评测搜索输入经过 `300 ms` 防抖，不显示联想下拉，也没有独立搜索按钮。“热门课程”按钮单独请求前 `24` 门课程并展开菜单。统计区只显示一行日期范围和靠右的评测数据量。
- 树洞卡片按结果索引在六组颜色间轮换，使用完整 `1.5px` 边框，不使用左侧彩条。卡片点击或 Enter/Space 按需请求 `/api/reviews/{pid}`；原树洞链接、课程标签、展开按钮和文本选择不得误触发弹窗。
- 评测列表只能渲染筛选后的评测主帖与相关回复；完整树洞弹窗才渲染 `thread_replies`。桌面弹窗居中，`640px` 下贴近底部，内部独立滚动，不能让长线程撑破 viewport。
- 项目开发人员悬浮卡在触发按钮或卡片上 hover/focus 时保持显示，文本允许选择和复制；联系方式 `tuzengji` 及欢迎联系文案同时保留在页脚。

### 状态、安全与无障碍

- `pinhaoke_theme` 保存共享主题；课程页另用 `pinhaoke_lang` 保存语言。课程页 URL 保存学期、搜索、筛选、排序、语言和课程详情；评测页 URL 保存 `q`。使用 `history.replaceState`，不要让每次输入污染浏览历史。
- 搜索、筛选、热门课程和详情请求使用 `AbortController` 或请求序号拒绝过时响应。修改时不得重新引入快速切换导致旧请求覆盖新状态的竞态。
- 卡片、筛选组合框、弹窗和图标按钮必须有语义角色、`aria-*` 标签及可见焦点。弹窗打开时使背景 `inert`，锁定焦点，支持 Escape/背景关闭，并在关闭后把焦点还给原触发元素。
- 课程页所有插入模板的数据先经过 `esc()`；评测正文与高亮必须通过 `textContent` 或文本节点分段，禁止把树洞正文拼入 `innerHTML`。原树洞链接只允许 PKU 树洞主机。
- 每次视觉或交互修改至少检查 `1440px`、`390px`、`320px`，覆盖浅色/深色、键盘路径、弹窗、无横向溢出和无控制台错误。页面行为变化同步更新 `tests/test_frontend_contract.py`。

## 本地运行与验证

仓库内 `venv/` 可能指向生产路径。macOS 开发使用独立临时环境：

```bash
python3 -m venv /tmp/pinhaoke-dev
/tmp/pinhaoke-dev/bin/python -m pip install -r requirements.txt
/tmp/pinhaoke-dev/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

访问 `http://127.0.0.1:8000/` 和 `http://127.0.0.1:8000/reviews`，不要直接打开本地 HTML 文件。

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

`get_reviews_db()` 以相同的 SQLite URI `mode=ro` 和 `PRAGMA query_only = ON` 打开 `树洞课程评测.db`。`GET /api/health` 检查五个课程库的表、详情行数、ID 集合、外键与完整性，同时检查评测库的必需表、元数据行数、外键和完整性。结果使用短时进程内缓存并返回 `Cache-Control: no-store`。

## API 契约

| Endpoint | 契约 |
|---|---|
| `GET /api/filters` | 参数 `term=fall\|spring\|summer`，默认 `fall`。返回 `course_types`、`categories`、`departments`、`credits`、`gradings`、`weekdays`。 |
| `GET /api/courses` | 返回 `{total, page, page_size, courses}`。支持搜索、筛选、排序、语言和分页。 |
| `GET /api/courses/{id}` | 前缀已包含学期与学段，不接受 `term`；支持 `lang`。 |
| `GET /api/reviews` | 返回 `{total, page, page_size, query, threads}`，按时间倒序检索评测主题及相关回复。 |
| `GET /api/reviews/{pid}` | 返回一个已命中评测主题的完整主帖与快照内全部回复；只在用户打开卡片时请求。 |
| `GET /api/review-courses` | 返回按热度排序且可按 `q` 过滤的课程名、课程号、主题数和条目数；评测页用它填充“热门课程”菜单。 |
| `GET /api/reviews/meta` | 返回保留条目的起止日期、树洞快照日期、源数量、命中数量和缓存回复覆盖率。 |
| `GET /api/health` | 返回五个课程库及一个评测库的健康状态；异常时为 503。 |

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

树洞评测 API 参数：

- `GET /api/reviews`：`q` 最长 120 字符，`page` 为 1 到 10000，`page_size` 为 1 到 100。
- `GET /api/reviews/{pid}`：`pid` 必须是正整数；只能读取 `threads` 中已筛选的主题，未命中返回 404。
- `GET /api/review-courses`：`q` 最长 120 字符，`limit` 为 1 到 50。
- 搜索同时匹配主帖、保留回复和规范化课程名；`LIKE` 通配符必须转义。
- 页面统计区只显示保留条目的日期范围和评测数据量；日期范围来自 `entries.posted_at` 的最小值与最大值，评测数据量等于 `matched_threads + matched_replies`，也就是 `matched_entries`。
- 列表 API 只返回筛选后的相关回复、课程标签和课程/教师高亮；详情 API 另外返回快照内全部回复。两者都只能包含树洞号、评论号、楼层、时间、来源月份、原帖链接和正文等公开字段，不得暴露作者标识或回复关系。高亮区间采用 Unicode 码点偏移，前端必须通过文本节点安全分段，不得把正文拼入 `innerHTML`。

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

五个课程建库入口：

```bash
python3 数据库构建脚本/build_undergrad_db.py
python3 数据库构建脚本/build_graduate_db.py
python3 北京大学选课网数据抓取/build_summer_db.py
python3 北京大学选课网数据抓取/build_undergrad_2627_fall_db.py
python3 北京大学选课网数据抓取/build_graduate_2627_fall_db.py
```

树洞评测建库入口：

```bash
python3 数据库构建脚本/build_treehole_reviews.py \
  --source /Users/wishingcat/LovingHeart/树洞全量数据截止20260713
```

评测构建脚本完整扫描 44 个月度分片，以五个课程数据库生成课程名与课程号词典，筛选课程评价主帖和评价相关回复，并清除电话号码、邮箱、微信与 QQ 联系方式。输出 `数据库/树洞课程评测.db` 包含 31642 个主题、62716 个评测条目，其中 31074 条为相关回复；`thread_replies` 另保存这些主题在快照内的全部 145738 条回复，仅供完整树洞弹窗按需读取。源缓存包含 15245822 条回复，覆盖率为 95.24%；未缓存的 761165 条回复不在输入快照中。

只为已验收的主题集合补齐完整回复而不重新运行分类器时，使用：

```bash
python3 数据库构建脚本/build_treehole_reviews.py \
  --source /Users/wishingcat/LovingHeart/树洞全量数据截止20260713 \
  --enrich-thread-replies
```

该模式会要求每个现有树洞号都在原快照中唯一出现，并在通过行数、外键和完整性检查后原子替换数据库。

课程与教师高亮来自五个课程库。课程名只在条目已有课程标签时匹配；教师名必须与条目课程的任课关系一致，或出现在“老师”“教授”等教师上下文中。构建器从全称与短写同现的目录行提取高置信别名，以复现次数、课程关键字重合和全库教师首字母关系过滤噪声。`entity_aliases` 保存 813 个课程缩写和 1047 个教师缩写，`entry_highlights` 保存不重叠的 Unicode 码点区间及 `full` / `alias` 类型；正式库含 96555 处课程实体和 60294 处教师实体高亮，其中缩写分别为 26892 和 36560 处。缩写在前端从可读配色中按文本稳定分配颜色。只刷新别名与高亮而不重扫树洞源数据时运行：

```bash
python3 数据库构建脚本/build_treehole_reviews.py --enrich-existing
```

`数据库构建脚本/build_atomic.py` 是唯一共享原子构建实现。各入口先完整解析源 JSON，严格检查必填字段、学分和冲突键，再通过 `atomic_database` 在目标同目录构建临时库。只有表、视图、外键、行数、1:1 详情和完整性检查全部通过后才 `os.replace` 正式库，并立即 fsync 目标父目录。替换前失败时正式文件不变；替换后的目录同步失败会明确报错，说明新目录项尚未确认持久化。替换沿用正式文件原权限模式。

重建会创建空 `translations` 表，等同于删除该库既有译文。运行建库前必须备份正式库或明确接受后续重新翻译。不要复制第二份 `build_common.py`。

六个正式数据库和七份源 JSON 属于受保护数据：普通代码修复不得修改、重建或格式化这些文件。课程数据修复不得提交它们；树洞评测功能的数据更新只有在用户明确要求重新提取时才可提交。测试使用临时文件和临时数据库。

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

- `README.md`：产品入口、功能、技术架构、网页设计、本地运行和文档索引
- `AGENTS.md`：本文件，工程契约与边界
- `CLAUDE.md`：指向本文件的短说明
- `北京大学选课网数据抓取/README.md`：抓取与建库
- `北京大学课程数据翻译/README.md`：翻译任务
- `课程数据/数据说明.md`：数据口径
- `deploy/README.md`：生产运维
- `归档/README.md`：V1 禁用边界

不要新增已完成计划、临时设计或重复架构文档。实现变化必须更新对应权威文档和 `tests/test_documentation.py`。
