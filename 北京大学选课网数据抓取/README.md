# 北京大学选课网数据抓取

本目录保存三套可复跑的页面内抓取流水线：2026 暑期本科、2026 秋季本科、2026 秋季研究生。方案必须复用**已登录的 Chrome 当前页面**，不会打开新浏览器，也不需要手工逐个点击课程号。

## 安全红线

**绝不点击或请求 `加入选课计划`，绝不访问 `addToPlan.do`。**

页面内脚本只允许访问选课网的三个课程读取端点：

```text
getCurriculmByForm.do              列表查询
queryCurriculum.jsp                翻页
goNested.do?course_seq_no=...      课程详情
```

不要用终端、curl、新浏览器或独立 Playwright 会话直接请求选课网页面。抓取必须由已经登录的同源页面发起并带上该页面的登录态。出现系统反爬提示或登录失效时，脚本会停止，不会继续重试并发布部分数据。

## 文件对应关系

| 范围 | 页面脚本 | 本机接收器 | 最终 JSON | 建库脚本 |
|---|---|---|---|---|
| 2026 暑期本科 | `pku_inpage_summer_scraper.js` | `receive_pku_summer_payload.py` | `课程数据/北大暑期课程_25-26第3学期.json` | `build_summer_db.py` |
| 2026 秋季本科 | `pku_inpage_undergrad_2627_fall_scraper.js` | `receive_pku_undergrad_2627_fall_payload.py` | `课程数据/北大本科课程_26-27第1学期.json` | `build_undergrad_2627_fall_db.py` |
| 2026 秋季研究生 | `pku_inpage_graduate_2627_fall_scraper.js` | `receive_pku_graduate_2627_fall_payload.py` | `课程数据/北大研究生课程_26-27第1学期.json` | `build_graduate_2627_fall_db.py` |

三套接收器共享 `receiver_common.py`。建库脚本共享 `数据库构建脚本/build_atomic.py` 和 `build_common.py`，本目录不复制公共构建代码。

## 抓取前检查

1. 在 Chrome 中登录 `https://elective.pku.edu.cn`。
2. 在同一 Chrome 标签页打开课程查询页：

```text
https://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/controller/courseQuery/getCurriculmByForm.do
```

3. 核对页面学期：暑期脚本要求 `25-26学年第3学期`；秋季脚本要求 `26-27学年第1学期`。
4. 核对本科或研究生界面与接收器匹配。
5. 关闭同端口的旧接收器，避免把 payload 发给错误任务。

## 抓取步骤

以下命令从项目根目录运行。

### 1. 启动匹配的接收器

```bash
python3 北京大学选课网数据抓取/receive_pku_summer_payload.py --port 8765

# 2026 秋季本科改用：
python3 北京大学选课网数据抓取/receive_pku_undergrad_2627_fall_payload.py --port 8765

# 2026 秋季研究生改用：
python3 北京大学选课网数据抓取/receive_pku_graduate_2627_fall_payload.py --port 8765
```

每次启动都会生成新的**一次性 token**，只监听 `127.0.0.1`，并打印一条完整的 JavaScript loader。不要复用旧命令，也不要手工删改 token。

### 2. 在 Chrome 当前页面执行 loader

打开该课程页的开发者工具 Console，把接收器打印的整条命令粘贴并执行。命令会带 `X-PKU-Receiver-Token` 请求头，从本机 `/inpage.js` 读取匹配脚本。

Chrome 可能显示“本地网络访问”或“访问此设备上的其他应用和服务”授权提示。选择允许；该权限只让 `elective.pku.edu.cn` 页面连接 `http://127.0.0.1:<port>`，用于获取脚本、上报进度和回传 JSON。拒绝后不会产生最终文件。

### 3. 等待自动抓取

脚本会自动完成：

- 校验页面 URL、目标学期和登录页面形态
- 本科遍历十个课程分类，研究生遍历课程列表
- 对所有可选择的分类提交 `开课单位=ALL`，不可选择时保持页面允许的空值
- 读取第一页并识别总页数，自动翻页，不能假设每类只有 100 条
- 解析每一页列表并校验表头、课程号和详情链接
- 通过课程号详情端点抓取课程详情，按 `课程序号` 共享缓存，避免重复请求
- 对网络超时和可重试 HTTP 状态进行有限退避重试
- 汇总统计、页码证据、错误和 validation 后发送 `/done`

任何课程分类、翻页、课程详情或结构校验失败都会使整次抓取失败。脚本不会把已抓到的一部分当作成功结果。

### 4. 确认接收器完成

接收器收到合法 `/done` 后打印 `[done]`，写入原始 payload 和最终课程 JSON，然后自动停止：

| 范围 | 原始 payload |
|---|---|
| 暑期本科 | `tmp_summer/inpage_payload.json` |
| 秋季本科 | `tmp_undergrad_2627_fall/inpage_payload.json` |
| 秋季研究生 | `tmp_graduate_2627_fall/inpage_payload.json` |

原始 payload 用于审计；最终 JSON 只包含 `rows`。收到拒绝响应、终端没有 `[done]` 或进程非零退出时，不得继续建库。

## 严格校验

页面脚本与接收器会交叉验证：

- Origin 必须是 `https://elective.pku.edu.cn`
- `/inpage.js`、`/progress`、`/done` 必须携带本次一次性 token
- 请求方法、CORS 预检、Content-Length、请求体大小和读取超时符合约束
- payload 学期、学段、课程分类和字段集合与接收器配置一致
- `rows` 非空，`errors` 明确存在且为空
- `totalRows`、分类统计、页数与实际记录一致
- 课程号非空，详情链接必须是 PKU `goNested.do` 且 `course_seq_no` 一致
- `missingCourseCodes`、`missingDetailLinks`、`suspiciousPages`、`duplicateKeys` 为空

同一 `课程序号` 因选课网多类别挂载而重复是合法现象，`duplicateSeqs` 必须精确反映实际重复顺序；它不能被简单判为错误。真正冲突的业务唯一键由 `duplicateKeys` 拒绝。

接收器先原子保存原始 payload，再校验并原子替换最终 JSON。拒绝 payload 可留在临时目录排查，但不会覆盖正式 JSON。文件替换失败或目录同步失败时会恢复旧文件。

## 本科分类与开课单位

本科脚本覆盖：

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

每个分类在查询前都保证开课单位参数为 `ALL`；页面没有可选单位时使用页面可接受的空值。这样不会继承社会学院等页面默认单位。英语课含额外的 `英语等级` 列，页面脚本使用专用表头映射，避免学分、教师和班号错位。

## 建库

只有 `[done]`、JSON 计数和 validation 都确认无误后，才运行对应命令：

```bash
python3 北京大学选课网数据抓取/build_summer_db.py
python3 北京大学选课网数据抓取/build_undergrad_2627_fall_db.py
python3 北京大学选课网数据抓取/build_graduate_2627_fall_db.py
```

建库入口先完整读取并校验 JSON，在目标目录构建临时 SQLite，检查 `basic_info`、`detail_info`、`translations`、`courses_view`、外键、1:1 行数和 `PRAGMA integrity_check`，全部通过后原子替换目标数据库。

**建库会生成空 `translations` 表，覆盖该库已有译文。** 对正式库运行前先备份，或明确接受按翻译 README 重新补齐。数据数量与翻译行数的权威口径见 [课程数据说明](../课程数据/数据说明.md)。

## 校验命令

以暑期库为例：

```bash
sqlite3 "数据库/2026暑期本科生课程.db" "
PRAGMA integrity_check;
select 'basic', count(*) from basic_info;
select 'detail', count(*) from detail_info;
select 'translations', count(*) from translations;
select course_type, count(*) from basic_info group by course_type order by course_type;
"
```

五库正式验收还应运行应用测试：

```bash
python3 -m unittest tests.test_receivers tests.test_scraper_contract tests.test_builders -v
for f in 北京大学选课网数据抓取/pku_inpage_*_scraper.js; do node --check "$f"; done
```

抓取流程本身不调用任何翻译 API，也不自动部署网站。
