# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

拼好课 V2 由 FastAPI、两个无构建步骤的 HTML 页面、五个课程 SQLite 数据库和一个树洞评测数据库组成。生产应用对课程与评测数据只读，可写数据只有留言板、访问统计与账户三个独立数据库；课程抓取必须复用已登录的 Chrome 页面，翻译任务不得自动产生 API 费用。以代码、数据库契约和测试为最终事实来源；用户入口与功能简介见 [README.md](README.md)，抓取、翻译、数据和部署的详细操作分别由各目录 README 负责。

## 项目定位

拼好课 V2 是北京大学课程搜索与树洞课程评测应用：

- `app.py`：FastAPI 后端，只读查询五个课程 SQLite 数据库和一个树洞评测数据库，另以三个独立可写 SQLite 库提供公开留言板、访问统计以及账号与课程收藏。
- 课程页与评测页顶栏都有访问统计与留言板两个按钮（课程页位于账号按钮旁，评测页位于主题切换前）。统计按钮打开悬浮面板，展示今日/近 7 天/累计的访问量与访客数及近 7 天柱状趋势，每 20 秒刷新。留言板按钮打开简洁的悬浮面板：顶部一句话提示欢迎写问题反馈、功能建议和想对开发者说的话，下方是发布输入框和可滚动的公开留言列表。
- 课程页顶栏在访问统计之前另有账号按钮：未登录显示“登录”，点击打开登录、注册与找回密码三个表单的悬浮面板；登录后按钮变为“个人”，点击进入页面内全屏“个人中心”视图。个人中心显示用户名、退出登录、账号管理折叠区，以及收藏夹：每账号有一个不可删除、可改名的默认收藏夹和若干自定义收藏夹，一门课可同时属于多个收藏夹。课程卡片右上角与课程详情弹窗各有一个星标切换按钮，点一下即把课程收进默认收藏夹；选择加入哪些收藏夹在课程详情弹窗与个人中心里就地勾选完成。必须登录才能收藏，收藏与收藏夹保存在服务器端的账户库并随账号在多端同步。评测页没有收藏入口。
- `index.html`：无构建步骤的课程搜索页；`reviews.html`：无构建步骤的树洞课程评测页。
- 生产站点：`https://www.pinhaoke.love`，Nginx 终止 TLS，systemd 运行 Uvicorn。
- 页面学期顺序为春季、暑期、秋季；API 与页面默认学期均为 `fall`。
- 树洞课程评测作为学期控件旁的独立入口，在手机端（不超过 `640px`）与三个学期组成每行两个的四入口网格，保持学期 tab 与评测链接的独立语义。评测搜索框输入即搜，不显示联想下拉；前 `24` 门课程热榜只由右侧“热门课程”按钮展开。列表卡片用六组轮换的整框颜色区分相邻树洞，点击卡片才请求快照中的完整主帖和全部回复。

## 目录职责

```text
app.py                          唯一后端模块
index.html                      课程搜索入口
reviews.html                    树洞课程评测入口
requirements.txt               固定版本的生产依赖
Images/                         `/Images/` 公共资源，文件名属于公开 URL
数据库/                         五个课程库与一个树洞评测正式库
课程数据/                       九份 JSON（含历史快照和合并结果）与数据说明
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

- 两页共享吸顶顶栏、品牌标题、顶栏右侧动作（访问统计、留言板、主题切换、关于悬浮卡）、学期控件、独立评测入口、搜索框宽度、浅色/深色主题、页脚和回到顶部按钮；我的收藏只在课程页；语言切换已隐藏。关于悬浮卡用无渐变的品牌标题和对齐的文字行展示开发者、鸣谢与微信联系，去掉彩色头像；微信联系紧接项目开发，微信号加粗突出、邀请联系的提示随微信号展示，鸣谢随后排列；GitHub 链接与红底白字的赞助按钮并列放在底部，赞助按钮不带悬浮文字，不再显示按钮下方的永久免费说明。关于面板与页脚的项目开发为 `Zengji Tu`，鸣谢为 `Ningjing Wang`、`Tingyi Huang`；面板 Logo 与首页共用 `/Images/favicon-192.png?v=2`，浏览器标签页使用 `/Images/favicon-round.svg?v=1` 的同图圆形裁切版。学期控件固定按春季、暑期、秋季排列，秋季默认；树洞入口与学期同级但不属于学期值。
- 课程页的筛选网格桌面为四列，`900px` 下三列，`720px` 下由居中、全宽、加粗的筛选按钮折叠，并至少保持两列筛选项。按钮使用与搜索框一致的白色表面和 `14px` 圆角，不使用绿色描边或按钮渐变。筛选项依次为课程类型、课程类别、学分、开课单位、成绩记载方式、星期几、上课节时和教室；上课节时的候选项来自 `/api/filters` 的 `periods`，与星期几同时选中时匹配同一节课。
- 课程卡片按公选、通识、专业、研究生类型使用不同颜色的完整边框，不使用左侧彩条。卡片点击或 Enter/Space 打开课程详情；详情弹窗包含可分享链接并恢复关闭前焦点。
- 评测搜索输入经过 `300 ms` 防抖，不显示联想下拉，也没有独立搜索按钮。“热门课程”按钮单独请求前 `24` 门课程并展开菜单。日期范围与评测数据量以小字备注显示在“最新课程评测”标题右侧，不设独立统计区。
- 树洞卡片按结果索引在六组颜色间轮换，使用完整 `1.5px` 边框，不使用左侧彩条。卡片点击或 Enter/Space 按需请求 `/api/reviews/{pid}`；原树洞链接、课程标签、展开按钮和文本选择不得误触发弹窗。
- 评测列表只能渲染筛选后的评测主帖与相关回复；完整树洞弹窗才渲染 `thread_replies`。桌面弹窗居中，`640px` 下贴近底部，内部独立滚动，不能让长线程撑破 viewport。
- 项目开发人员悬浮卡在触发按钮或卡片上 hover/focus 时保持显示，文本允许选择和复制；联系方式 `tuzengji` 及欢迎联系文案同时保留在页脚。
- 两页顶栏的留言板按钮打开悬浮留言面板：面板包含一句话提示、`500` 字上限的输入框、可滚动的公开留言列表和“加载更多”按钮。面板遵循与课程详情弹窗相同的焦点锁定、Escape、背景 `inert` 与关闭后焦点恢复要求；留言正文和时间只能通过 `textContent` 渲染。
- 两页顶栏的访问统计按钮打开悬浮统计面板：面板展示今日/近 7 天/累计的访问量与访客数、近 7 天柱状趋势和隐私说明，打开时拉取 `/api/stats` 并每 20 秒轮询，关闭时清除定时器。数字用 `textContent`、柱状高度用数值渲染，遵循与其它弹窗相同的焦点锁定、Escape、背景 `inert` 与关闭后焦点恢复要求。
- 课程页顶栏的账号按钮 `#favBtn` 双态：未登录显示“登录”、`onclick` 派发到打开 `#favOverlay`，登录后显示“个人”、派发到打开 `#accountView`。`#favOverlay` 只承载登录、注册、找回三个表单，由 `renderFavoritesPanel()` 用 DOM API 生成，`autocomplete` 的 `username`、`current-password`、`new-password` 语义、用户名 `pattern`、密码 `minlength` 与 `maxlength` 保持；面板遵循与其它弹窗相同的焦点锁定、Escape、背景 `inert` 与关闭后焦点恢复要求，从课程详情弹窗打开时叠在详情之上并把详情设为 `inert`。课程卡片右上角与详情弹窗的星标是独立按钮，click 与 Enter/Space 都 `stopPropagation()`，只把课程收进默认收藏夹，不打开详情；未登录时点星标记录待收藏课程并打开登录表单，登录或注册成功后进入个人中心并自动补做该次收藏。
- 登录后的个人中心是页面内全屏视图 `#accountView`，由 `renderAccountView()` 组装账号信息、账号管理折叠区（修改密码、修改密保、删除账号）与收藏夹导航；用户名、收藏快照、收藏夹名与状态只用 `textContent`，新代码唯一的 `innerHTML` 是 `favIcon()` 的图标常量，移除按钮仍用 `ICONS.close`。视图用 `history.pushState` 支持返回键、`setModalBackgroundInert` 冻结背景，keydown 分支排在 `#favOverlay` 之前处理 Escape 与焦点循环，关闭后把焦点还给 `#favBtn`。收藏夹导航含“全部”与各收藏夹卡片及计数、自定义夹的改名与两步确认删除、内联新建收藏夹，默认夹只能改名。课程详情弹窗与个人中心用可勾选的收藏夹 chip 就地选择归属，chip 勾选经 `350ms` 防抖调用 `set-collections`，取消勾选全部即取消收藏。从个人中心打开某条收藏时先关闭视图并调用 `showDetail()`。
- 关于悬浮卡中的赞助按钮不跳转 GitHub，而是打开站内赞助面板：面板顶部强调赞助后可微信联系或转账备注留名，以便加入赞助列表；随后依次展示微信赞助码、支付宝赞助码、开发者微信二维码和鸣谢赞助名单，图片直接引用 `/Images/` 下的 `wechat_sponsor.jpg`、`alipay_sponsor.jpg` 与 `MyWeChat.jpg`；Nginx 对 `/Images/` 设置 30 天 immutable 缓存，替换过内容的图片必须像两个赞助码一样带 `?v=N` 版本参数并在更换时递增，鸣谢名单必须与 README“鸣谢赞助”表一致并由前端契约测试校验。面板遵循与其它弹窗相同的焦点锁定、Escape、背景 `inert` 与关闭后焦点恢复要求；赞助按钮位于默认隐藏的悬浮卡内，因此恢复焦点前先给 `.tip-wrap` 加 `tip-pinned` 临时显示悬浮卡，聚焦后立即移除。

### 状态、安全与无障碍

- `pinhaoke_theme` 保存共享主题；界面固定中文，不读取历史 `pinhaoke_lang`。课程页 URL 保存学期、搜索、筛选、排序和课程详情，忽略旧语言参数；评测页 URL 保存 `q`。使用 `history.replaceState`，不要让每次输入污染浏览历史。
- 收藏、收藏夹与账号状态都不进入 URL，`syncURL()` 与 `readURLState()` 不得写入或读取任何收藏、收藏夹或账号参数；个人中心全屏视图 `#accountView` 用 `history.pushState` 支持系统返回键关闭并监听 `popstate`，不写查询参数。会话只存在于 HttpOnly cookie `pinhaoke_session` 中，脚本不可读；`localStorage.pinhaoke_fav_mode` 只是“上次已登录”的提示，用来决定页面加载时是否请求 `GET /api/account`，`authenticated: false` 时清除，它不能替代服务器判断。收藏请求使用 `credentials: 'same-origin'` 与 `cache: 'no-store'`，收到 401 时清空本地账号状态并回到未登录态。
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

运行单个模块或单个用例：

```bash
python3 -m unittest tests.test_app
python3 -m unittest tests.test_treehole_reviews.CuratedAliasAndPinyinInitialTests
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
| `fall` | `2026秋季学期本科生课程.db` | `2026秋季学期研究生课程.db` | 4529 |

验收口径可写作 `fall=4529`、`spring=3701`、`summer=160`。这些是列表合并后的卡片数，不是数据库原始行数。秋季本科使用 2026-09-07 合并库，raw=3152；原有 3032 个本地 ID 保留，新记录追加 ID。

`get_db()` 用 SQLite URI `mode=ro` 打开主库，再按需 `ATTACH` 研究生库，并执行 `PRAGMA query_only = ON`。应用代码不得通过 API 请求写课程或评测数据库；允许的写入只有留言板 API 对留言库的插入、`record_visit()` 对统计库的累加，以及账号与收藏 API 对账户库的读写。静态文件路径全部从 `BASE_DIR` 解析，使模块可从任意工作目录导入。

`get_reviews_db()` 以相同的 SQLite URI `mode=ro` 和 `PRAGMA query_only = ON` 打开 `树洞课程评测.db`。`GET /api/health` 检查五个课程库的表、详情行数、ID 集合、外键与完整性，同时检查评测库的必需表、元数据行数、外键和完整性。结果使用短时进程内缓存并返回 `Cache-Control: no-store`。

`get_messages_db()` 打开可写的留言板数据库：路径来自环境变量 `PINHAOKE_MESSAGES_DB`，本地开发默认仓库根目录 `留言板.db`（已被 `.gitignore` 排除，不进入仓库），生产由 systemd `StateDirectory` 提供 `/var/lib/pinhaoke/留言板.db`。连接启用 WAL 与 `busy_timeout`，首次使用时自建 `messages` 表并迁移到版本 1，增加昵称与回复关系（详见留言板 API 契约）；六个正式库保持只读，`GET /api/health` 不检查留言库。

`get_stats_db()` 以同样方式打开可写的访问统计数据库：路径来自 `PINHAOKE_STATS_DB`，本地默认仓库根目录 `访问统计.db`（已 `.gitignore`），生产为 `/var/lib/pinhaoke/访问统计.db`，首次使用时自建 `visit_days(day, ip_hash, views, last_at)` 表。`record_visit()` 在 `/` 和 `/reviews` 页面路由中记录访问，按北京时间分日、以 IP 哈希对当日访客去重、`views` 累加，并过滤明显的 bot User-Agent；任何异常都被吞掉，绝不影响页面返回。`GET /api/health` 不检查统计库。

`get_accounts_db()` 打开可写的账户数据库：路径来自 `PINHAOKE_ACCOUNTS_DB`，本地默认仓库根目录 `账户.db`（已 `.gitignore`），生产为 `/var/lib/pinhaoke/账户.db`。连接启用 WAL、`busy_timeout` 与 `PRAGMA foreign_keys = ON`，由 `_migrate_accounts_db()` 按 `PRAGMA user_version` 迁移：版本 1 建 `users`、`security_questions`、`sessions`、`favorites`、`auth_events` 五张表；版本 2 增建 `collections` 与 `favorite_collections`，并为已有收藏回填默认收藏夹与成员映射，回填在显式 `BEGIN IMMEDIATE` 事务内锁后重查版本以对齐 `--workers 2` 首连竞争；版本 3 给 `users` 增列 `last_collection_id` 以兼容已有开发版结构（当前发布不改变收藏落点），同样在显式 `BEGIN IMMEDIATE` 事务内锁后重查版本恰执行一次，只加列不回填；版本 4 增加 `nickname`，为新旧用户提供默认昵称“路过的 PKUer”，同样通过锁内版本复查保证只迁移一次。`collections` 以 `UNIQUE(user_id, name)` 和 `is_default = 1` 的唯一部分索引约束每账号恰一个默认夹；`favorite_collections` 复合外键指向 `favorites(user_id, fav_key)`，删收藏行级联清成员映射，删收藏夹级联清该夹映射且不动 `favorites`。删除用户时经两条 `ON DELETE CASCADE` 一并清除密保、会话、收藏、收藏夹与成员映射。默认收藏夹不可删除（409）但可改名，删除自定义收藏夹后清理不再属于任何夹的孤儿收藏行。收藏夹上限 `COLLECTIONS_MAX = 50`（不含默认夹），夹名 `strip()` 后 1 到 `COLLECTION_NAME_MAX = 30` 字，默认夹名为 `DEFAULT_COLLECTION_NAME`。密码与密保答案用标准库 `hashlib.scrypt` 加盐哈希，参数 `SCRYPT_PARAMS` 为 n=2^14、r=8、p=1，序列化为 `scrypt$n$r$p$salt$hash` 并随记录保存，登录成功时低于当前参数的哈希会重算；所有 scrypt 调用经过 `_SCRYPT_GATE`，每个进程最多两个并发。会话令牌由 `secrets.token_urlsafe(32)` 生成，库内只存 SHA-256；cookie `pinhaoke_session` 为 HttpOnly、SameSite=Lax、Path=/，只在 `X-Forwarded-Proto` 或请求 scheme 为 https 时带 Secure，有效期 180 天，`GET /api/account` 每天最多顺延一次。`auth_events(kind, subject, at)` 只保存 IP 哈希、`username_key` 或 `*` 作为限流主体，两天后清除。`GET /api/health` 不检查账户库。

## API 契约

| Endpoint | 契约 |
|---|---|
| `GET /api/filters` | 参数 `term=fall\|spring\|summer`，默认 `fall`。返回 `course_types`、`categories`、`departments`、`credits`、`gradings`、`weekdays`、`periods`。`periods` 是从当前学期 `schedule` 文本提取的全部 `N-M` 节次区间，两节课的区间（如 `1-2`、`3-4`、`10-11`）排在最前，其余区间随后，两组内部都按起止节次排序，每个候选项至少命中一门课。 |
| `GET /api/courses` | 返回 `{total, page, page_size, courses}`。支持搜索、筛选、排序、语言和分页。 |
| `GET /api/courses/{id}` | 前缀已包含学期与学段，不接受 `term`；支持 `lang`。 |
| `GET /api/reviews` | 返回 `{total, page, page_size, query, threads}`。无搜索词时先置顶 2026 年质量分最高的 `10` 个树洞，其余按时间倒序；有搜索词时全部按时间倒序。 |
| `GET /api/reviews/{pid}` | 返回一个已命中评测主题的完整主帖与快照内全部回复；只在用户打开卡片时请求。 |
| `GET /api/review-courses` | 返回按热度排序且可按 `q` 过滤的课程名、课程号、主题数和条目数；评测页用它填充“热门课程”菜单。 |
| `GET /api/reviews/meta` | 返回保留条目的起止日期、树洞快照日期、源数量、命中数量和缓存回复覆盖率。 |
| `GET /api/messages` | 返回 `{total, page, page_size, messages}`，公开留言按发布时间倒序分页。 |
| `POST /api/messages` | 发布一条公开留言，body 为 `{content}`；成功返回 201 和新留言。 |
| `GET /api/stats` | 返回今日/近 7 天/累计的访问量与访客数及近 7 天每日趋势；`no-store`。 |
| `GET /api/account` | 返回 `{authenticated}`；已登录时另含 `username`、`questions`（只含 `position` 与 `question`）、`favorites`、`limit`、`collections` 与 `collections_limit`，每条收藏含 `collection_ids`。 |
| `POST /api/auth/register` | body `{username, password, questions}`；成功 201 返回 `{username}` 并签发会话 cookie，用户名已占用 409。 |
| `POST /api/auth/login` | body `{username, password}`；成功 200 返回 `{username}` 并签发新会话，失败统一 401。 |
| `POST /api/auth/logout` | 删除当前会话并清除 cookie，204，幂等。 |
| `POST /api/auth/password` | body `{current_password, new_password}`；204，同时吊销当前会话以外的全部会话。 |
| `POST /api/auth/questions` | body `{current_password, questions}`；204，整体替换密保问题。 |
| `POST /api/auth/reset/questions` | body `{username}`；返回 `{questions}`，用户名不存在 404。 |
| `POST /api/auth/reset` | body `{username, position, answer, new_password}`；答对任意一个问题即 204 并吊销该用户全部会话，答错 401。 |
| `POST /api/auth/delete` | body `{password}`；204，级联删除密保、会话与收藏并清除 cookie。 |
| `GET /api/favorites` | 返回 `{favorites, limit, collections, collections_limit}`，每条收藏含 `collection_ids`，需登录。 |
| `POST /api/favorites` | body `{id}`；服务器从课程库生成快照并映射进默认收藏夹，新增 201、已存在 200，都返回全量 `{favorites, limit, collections, collections_limit}`，课程不存在 404，超过上限 409。 |
| `POST /api/favorites/remove` | body `{fav_key}`；200 返回全量收藏与收藏夹 payload，删收藏行并级联清成员映射，幂等。 |
| `POST /api/favorites/set-collections` | body `{fav_key, collection_ids}`；整集替换该收藏的收藏夹成员，收藏行不存在或含他人夹 id 返回 404，勾选清空即删收藏行取消收藏，200 返回全量 payload。 |
| `POST /api/collections` | body `{name}`；新建自定义收藏夹，201 返回全量 payload，撞名或超过 `COLLECTIONS_MAX` 409，名称非法 422。 |
| `POST /api/collections/rename` | body `{collection_id, name}`；默认夹与自定义夹都可改名，200 返回全量 payload，撞名 409，不存在 404，名称非法 422。 |
| `POST /api/collections/remove` | body `{collection_id}`；删自定义收藏夹并取消仅属于该夹的收藏，默认夹 409，不存在 404，200 返回全量 payload。 |
| `GET /api/health` | 返回五个课程库及一个评测库的健康状态；异常时为 503。 |

`GET /api/courses` 参数：

- `term`：`fall`、`spring`、`summer`，默认 `fall`
- `q`：课程名、原始英文名、教师、原始教室、课程号
- `classroom`：教室专用模糊搜索，可与 `q` 组合
- `type`、`category`、`credits`、`department`、`weekday`、`period`、`grading`
- `period`：形如 `3-4` 的节次区间，只匹配 `schedule` 中同一时段的 `周X3~4节`（`LIKE '%周_3~4节%'`，以 `周X` 作为左边界，避免 `1~12节` 误命中 `11~12节`）；与 `weekday` 同时给出时两者必须落在同一节课上，即 `周三3~4节`
- `sort`：`name_asc`、`name_desc`、`credits_asc`、`credits_desc`、`time_asc`、`random`；兼容旧值 `pinyin`、`pinyin_desc`
- `random_seed`：使随机排序跨页稳定
- `lang`：`zh`、`en`、`ja`、`ko`、`fr`、`de`、`es`、`ru`
- `page`：1 到 10000；`page_size`：1 到 200

非法学期、语言、星期、节次、排序、学分、页码或 canonical ID 必须返回 422/404，不得把未经允许的值拼进 SQL。

树洞评测 API 参数：

- `GET /api/reviews`：`q` 最长 120 字符，`page` 为 1 到 10000，`page_size` 为 1 到 100。
- `GET /api/reviews/{pid}`：`pid` 必须是正整数；只能读取 `threads` 中已筛选的主题，未命中返回 404。
- `GET /api/review-courses`：`q` 最长 120 字符，`limit` 为 1 到 50。
- 搜索同时匹配主帖、保留回复和规范化课程名；`LIKE` 通配符必须转义。
- 默认列表的置顶树洞由 `REVIEW_FEATURED_COUNT` 与 `REVIEW_FEATURED_RANGE`（2026 年北京时间区间）选出，质量分为 `REVIEW_QUALITY_SCORE_SQL`：主帖为评测 `+120`，每条相关回复 `+40`，正文长度按 `900` 封顶再除以 `6`。排序必须保持跨页确定性，置顶之后回到时间倒序。
- 页面只显示保留条目的日期范围和评测数据量，以结果标题旁的小字备注呈现；日期范围来自 `entries.posted_at` 的最小值与最大值，评测数据量等于 `matched_threads + matched_replies`，也就是 `matched_entries`。
- 列表 API 只返回筛选后的相关回复、课程标签和课程/教师高亮；详情 API 另外返回快照内全部回复。两者都只能包含树洞号、评论号、楼层、时间、来源月份、原帖链接和正文等公开字段，不得暴露作者标识或回复关系。高亮区间采用 Unicode 码点偏移，前端必须通过文本节点安全分段，不得把正文拼入 `innerHTML`。

留言板 API 参数：

- `GET /api/messages`：`page` 为 1 到 10000，`page_size` 为 1 到 50。
- `POST /api/messages`：`content` 去除首尾空白后为 1 到 `500` 字，非法 payload 返回 422。
- `GET /api/messages/{message_id}/replies`：只查询根留言的回复，按 ID 倒序；`before_id` 默认为 0，后续使用上一页最后一条 ID，`page_size` 为 1 到 50，返回 `{replies, has_more}`。不存在的留言或回复 ID 返回 404。
- `POST /api/messages/{message_id}/replies`：与发布留言使用相同的 `{content}` 校验；所有留言写入都校验可信 Origin。根留言列表只计算根留言数量，每项另带 `reply_count`。
- 留言与回复保存发布时间、正文、发言时的 `nickname` 和内部 `parent_id`；昵称仅由服务器读取当前有效会话确定，匿名、失效会话和旧留言使用 `DEFAULT_NICKNAME = "路过的 PKUer"`，不接受客户端伪造身份。历史昵称保留快照，不关联公开账号 ID。
- 来源 IP 以 SHA-256 哈希形式仅用于发布频率限制，同一 IP 哈希的留言和回复共用每小时最多 `5` 条、每天最多 `20` 条，超限返回 429；限流检查与插入在 `BEGIN IMMEDIATE` 内执行。公开响应仅包含 `id`、`posted_at`、`content`、`nickname`、`reply_count`，不包含 IP、登录用户名或账号主键。
- 留言库 schema 版本 1 增加 `nickname`、`parent_id` 和 `(parent_id, id)` 索引；锁内重查版本后迁移，保留旧 ID、正文、时间与 IP 哈希。账户库版本 4 同样在锁内新增 `nickname`，为旧账号补默认昵称，保留会话、密保与收藏。
- 前端渲染留言、回复、昵称和更新日志必须使用 `textContent` 或 DOM API，禁止拼入 `innerHTML`。两页的留言板 UI 与行为保持一致。
- `GET /api/changelog` 从 `BASE_DIR / "changelog.json"` 读取唯一的中文更新记录并返回 `no-store`；日志只收录主要功能与数据更新，不记录功能回退、修复和细微调整，重大记录设 `major: true`，两页均加粗日期、标题与内容；留言板顶部按钮在留言与更新日志子页面间切换，隐藏子页退出键盘焦点顺序，沿用弹窗焦点约束。

访问统计契约：

- `visit_days` 按北京时间（UTC+8）分日，主键 `(day, ip_hash)`；同一访客当天多次访问只累加 `views`，`COUNT(*)` 即当日访客数，跨日访客用 `COUNT(DISTINCT ip_hash)`。
- `record_visit()` 只在 `/` 和 `/reviews` 页面路由调用，过滤含 `bot/spider/crawl/curl/wget/python-` 等标记或空的 User-Agent，并把所有异常吞掉；页面返回不得因统计失败而受影响。
- `GET /api/stats` 返回当日、近 `7` 天、累计三组 `{views, visitors}`，以及 `trend`（近 `7` 天每日 `{day, views}`，按日期升序、末位为当日），响应 `no-store`。IP 哈希只用于去重，绝不进入响应。
- 前端统计数字用 `textContent`、柱状高度用数值渲染，禁止把统计数据拼入 `innerHTML`。

账号与收藏契约：

- 用户名 `strip()` 后必须匹配 `^[A-Za-z0-9_]{3,20}$`，按注册时写法显示，以小写 `username_key` 判定唯一，大小写变体注册返回 409。密码为 8 到 128 字符，`casefold()` 后不得等于用户名。
- `GET /api/account` 增加 `nickname`。`POST /api/account/nickname` 必须登录并校验 Origin，昵称 `strip()` 后为 1 到 `NICKNAME_MAX_LENGTH = 30` 字，拒绝控制字符和孤立代理字符；只修改当前用户昵称，不修改登录用户名，返回 `{nickname}` 与 `no-store`。新账号、旧账号默认昵称都是“路过的 PKUer”。
- 密保问题 1 到 3 个，问题 `strip()` 后 1 到 60 字且互不重复；答案经 NFKC 规范化、去除全部空白并 `casefold()` 后为 2 到 64 字，且不得等于 `username_key`。找回密码只需答对任意一个问题，成功后吊销该用户全部会话。
- 所有 `POST` 端点先经 `_require_trusted_origin()`：有 `Origin` 时其主机必须等于 `Host`（尊重 `X-Forwarded-Host`），`Origin: null` 返回 403；无 `Origin` 时按 `Referer` 判定；两者都缺失放行。请求体只接受 JSON。
- 登录失败与用户名不存在返回同一 401 文案，不存在的用户名也对假哈希校验一次；全部限流检查在 scrypt 之前执行，`AUTH_RATE_LIMITS` 的注册、登录失败、找回、全局校验与收藏写入窗口超限返回 429 并带 `Retry-After`。按用户名限流只按字符串计数，不泄露用户是否存在。
- 账号与收藏响应一律 `Cache-Control: no-store`，不得包含用户主键、IP、任何哈希、令牌明文或密保答案。
- 收藏稳定键为 `term|level|course_code|class_no|teacher`，各段 `strip()`，前端 `favoriteKey()` 与后端 `_favorite_key()` 必须保持同一规范化。客户端只提交课程 ID，快照字段由服务器通过 `get_course_detail()` 读取，`term_label` 取自学期主库文件名前缀；重复收藏只刷新快照并保留原 `added_at`；每账号上限 `FAVORITES_MAX = 300`，超限 409。
- 收藏条目字段为 `fav_key`、`id`、`available`、`term`、`term_label`、`level`、`course_code`、`class_no`、`teacher`、`course_name`、`credits`、`schedule`、`department`、`added_at`，按 `added_at` 倒序、`fav_key` 收尾。`_refresh_favorite_ids()` 在返回前确认课程 ID 仍存在，漂移的条目按 `(course_code, class_no, teacher)` 在同一学期库重新解析并回写新 ID，只有 `term_label` 与快照相同时才回写，否则标记 `available: false` 且不落库。
- 收藏夹数据模型的不变式：一条 `favorites` 行存在当且仅当该课程至少属于一个收藏夹。收藏一门课即建快照行并映射进默认收藏夹；取消收藏删 `favorites` 行并由复合外键级联清所有成员映射；`set-collections` 整集替换成员，勾选清空即删 `favorites` 行取消收藏；删自定义收藏夹级联清该夹映射后清理不再属于任何夹的孤儿收藏行。默认收藏夹每账号恰一个，`is_default = 1`，不可删除，可改名。
- 收藏夹名 `strip()` 后为 1 到 `COLLECTION_NAME_MAX = 30` 字，按 `UNIQUE(user_id, name)` 判重，撞名 409；每账号自定义收藏夹上限 `COLLECTIONS_MAX = 50`（不含默认夹），超限 409；`collection_ids` 只接受该用户拥有的收藏夹 id，含他人夹 id 返回 404。收藏夹写入与收藏写入共用 `favorite_write_ip` 限流窗口。
- `GET /api/account` 与四个 favorites、三个 collections 端点都经唯一装配出口返回全量 `{favorites, limit, collections, collections_limit}`，`collections` 每项为 `{id, name, is_default, position, count}` 且默认夹在前，`favorites` 每项带 `collection_ids`；成员映射以稳定 `fav_key` 为锚，课程库 ID 漂移不影响夹归属。

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

历史翻译代码及数据仍保留，但网站已关闭翻译。历史多语言有两个来源：

1. `index.html` 的有限枚举字典负责院系、类别、成绩方式、星期等封闭集合。
2. 每个数据库的 `translations(course_id, field, lang, text)` 负责课程自由文本。

列表和详情 API 校验 `lang` 后固定按 `zh` 读取来源字段，不联接或应用译文。搜索与排序使用来源字段，原始英文名称仍属于来源数据。

所有学期均关闭翻译：界面固定中文并隐藏语言入口，忽略旧 URL 及本地语言偏好。前端字段 helper 原样返回课名、教师、院系和上课时间，不再替换成英文课名或改写时间字符串。来源自带的英文名称、英文简介继续作为独立字段展示。收藏一直保存来源快照，不翻译或迁移收藏数据。历史译文和翻译脚本保留，不执行翻译任务。

### 个人课表与收藏卡片

- 账户库版本 5 新建 `timetable_courses(user_id, course_key, snapshot, added_at)`，以用户与稳定课程键去重，外键级联删除；显式写锁内复查版本保证多进程安全迁移，保留已有用户、会话和收藏。
- `/api/timetable` 的 GET/POST 和 `/api/timetable/remove` POST 均要求登录，写入要求可信 Origin 并复用收藏写入限流；客户端只能提供课程 ID，课程信息由服务端读取原始数据库。每账号最多 100 门，重复添加幂等，课表和收藏互不影响。
- 旧收藏与课表读取时按学期、课程号、班号、教师的稳定身份补齐当前地点、时间、类型等字段；找不到的课程保留快照并标记不可用，不误认复用的 ID。
- 收藏复用 `createCard` 的转义模板和主页样式，卡片打开仍走 `openFavoriteItem`，移除按钮独立阻止事件传播，不影响收藏夹行为。
- 个人中心提供收藏 / 课表切换，详情标题操作区的“加入课表”位于“加入收藏”左侧，复用相同按钮样式，手机端同样显示图标；再次点击“已加入课表”调用移除接口，列表与详情同步状态；个人中心内查看详情保留视图与滚动位置；登录后补做待添加操作，退出后清空课表内存，不写入 localStorage。账号变化和请求序号可丢弃旧响应。
- 课表按学期和原始第 0–30 周展示星期 / 第 1–14 节，解析周次范围、离散周次、单双周与多段上课时间；无法识别时显示原始记录，不臆造时间。重叠格子提示核对；移动端表格可横向滚动，下方列表支持详情和移除。

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

评测构建脚本完整扫描 44 个月度分片，以五个课程数据库生成课程名、课程号与教师词典，并叠加两类人工核定词典：`_CURATED_COURSE_FAMILIES` 提供思修、军理、高数、AI引论等课程别称（思政公共课映射到虚拟规范课名），`汉字拼音首字母.json` 为全部教师姓名生成拼音首字母缩写（ZZJ、lsj 等，二位缩写要求原文全大写，且须有课程关联或教师称谓语境）。脚本筛选课程评价主帖和评价相关回复，并清除电话号码、邮箱、微信与 QQ 联系方式。输出 `数据库/树洞课程评测.db` 包含 47843 个主题、90880 个评测条目，其中 43037 条为相关回复；`thread_replies` 另保存这些主题在快照内的全部 210570 条回复，仅供完整树洞弹窗按需读取。源缓存包含 15245822 条回复，覆盖率为 95.24%；未缓存的 761165 条回复不在输入快照中。

只为已验收的主题集合补齐完整回复而不重新运行分类器时，使用：

```bash
python3 数据库构建脚本/build_treehole_reviews.py \
  --source /Users/wishingcat/LovingHeart/树洞全量数据截止20260713 \
  --enrich-thread-replies
```

该模式会要求每个现有树洞号都在原快照中唯一出现，并在通过行数、外键和完整性检查后原子替换数据库。

课程与教师高亮来自五个课程库。课程名只在条目已有课程标签时匹配；教师名必须与条目课程的任课关系一致，或出现在“老师”“教授”等教师上下文中。除人工核定词典外，构建器还从全称与短写同现的目录行提取高置信别名，以复现次数、课程关键字重合和全库教师首字母关系过滤噪声；二字课程别称使用跨词边界否决表阻止“课程设置”“分数分布”这类误报。`entity_aliases` 保存 802 个课程缩写和 1062 个教师缩写，`entry_highlights` 保存不重叠的 Unicode 码点区间及 `full` / `alias` 类型；正式库含 135241 处课程实体和 53518 处教师实体高亮，其中缩写分别为 56168 和 27962 处。缩写在前端从可读配色中按文本稳定分配颜色。只刷新别名与高亮而不重扫树洞源数据时运行：

```bash
python3 数据库构建脚本/build_treehole_reviews.py --enrich-existing
```

`数据库构建脚本/build_atomic.py` 是唯一共享原子构建实现。各入口先完整解析源 JSON，严格检查必填字段、学分和冲突键，再通过 `atomic_database` 在目标同目录构建临时库。只有表、视图、外键、行数、1:1 详情和完整性检查全部通过后才 `os.replace` 正式库，并立即 fsync 目标父目录。替换前失败时正式文件不变；替换后的目录同步失败会明确报错，说明新目录项尚未确认持久化。替换沿用正式文件原权限模式。

重建会创建空 `translations` 表，等同于删除该库既有译文。运行建库前必须备份正式库或明确接受后续重新翻译。不要复制第二份 `build_common.py`。

六个正式数据库和九份课程 JSON 属于受保护数据：普通代码修复不得修改、重建或格式化这些文件。只有用户明确要求的数据更新才能提交对应数据；树洞评测功能的数据更新只有在用户明确要求重新提取时才可提交。测试使用临时文件和临时数据库。

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

不要手工 `git pull` 后重启，不要绕过预检，不要让 `www-data` 持有代码、Git、虚拟环境或六个正式数据库。更新脚本部署精确 `origin/main`，在停服前完成目标工作树、LFS 和候选 venv 预检，激活失败或收到 INT/TERM 时自动恢复旧提交、旧 unit、旧 venv 和原服务状态。

留言板、访问统计与账户三个可写数据库是仅有的例外：它们位于 `/var/lib/pinhaoke/留言板.db`、`/var/lib/pinhaoke/访问统计.db` 与 `/var/lib/pinhaoke/账户.db`，由 systemd `StateDirectory` 自动创建并归服务用户所有，`StateDirectoryMode=0750` 限制目录权限，不在仓库和 `/opt/pinhaoke` 内。`deploy/update.sh` 与回滚不触碰这些数据，备份需单独处理。

`deploy/nginx.conf` 只是与 Certbot 共存的站点模板，必须手工安装并先运行 `nginx -t`；`deploy/update.sh` 不覆盖 Nginx。任何任务只有用户明确要求后才可 push 或部署。本地通过测试不代表生产已更新。

## 文档所有权

仓库只保留七份 tracked Markdown：

- `README.md`：产品入口、功能、技术架构、网页设计、本地运行和文档索引
- `CLAUDE.md`：本文件，工程契约与边界
- `北京大学选课网数据抓取/README.md`：抓取与建库
- `北京大学课程数据翻译/README.md`：翻译任务
- `课程数据/数据说明.md`：数据口径
- `deploy/README.md`：生产运维
- `归档/README.md`：V1 禁用边界

不要新增已完成计划、临时设计或重复架构文档。实现变化必须更新对应权威文档和 `tests/test_documentation.py`。
