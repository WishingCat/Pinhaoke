


# 拼好课 V2.0 - 北京大学课程搜索

北京大学 2026 年春季 + 暑期学期课程搜索工具，涵盖公选课、通识课、专业课、研究生课、暑期课共约 3958 门课程的快速检索与筛选。

## 使用方法
直接打开网页：[www.pinhaoke.love](http://www.pinhaoke.love) 即可使用

## 功能

- 按课程名、教师姓名或教室搜索
- 七维度筛选：课程类型（公选课/通识课/专业课/研究生课）、课程类别、学分、开课单位、成绩记载方式、上课时间（星期）、教室
- 排序功能：支持按课程名拼音排序
- 课程详情弹窗：查看完整课程信息，包括中英文简介、教学大纲、教学评估、先修课程等
- 上课时间与教室分开展示
- 多语言切换：中文、English、日本語、한국어、Français、Deutsch、Español、Русский
- 玻璃拟态 (Glassmorphism) 风格 UI

## 技术架构

```
Nginx (反向代理) → FastAPI (Python 后端) → SQLite × 2（本科 + 研究生）
```

- **后端**：Python FastAPI + Uvicorn；`app.py` 用 SQLite `ATTACH` 同时连接两库
- **数据库**（`数据库/` 目录下）：
  - `2026春季学期本科生课程.db`（2465 门 = 公选 256 + 通识 143 + 专业 2066）
  - `2026春季学期研究生课程.db`（1379 门）
  - `2026暑期本科生课程.db`（114 门）
  - 各自三表：`basic_info` + `detail_info`(1:1) + 视图 `courses_view`
- **前端**：单页 HTML，通过 API 动态加载数据
- **部署**：阿里云服务器（Ubuntu 24.04），Nginx + systemd

## 数据来源

源 JSON 都在 `课程数据/`：

- `北大本科公选课_25-26第2学期.json` — 256 门
- `北大本科通识课_25-26第2学期.json` — 143 门
- `北大本科专业课_25-26第2学期.json` — 2066 门
- `北大研究生课程_25-26第2学期.json` — 1379 门
- `北大暑期课程_25-26第3学期.json` — 114 门

数据来源于 2026 年 3 月 20 日（春季）和 5 月 13 日（暑期）从北京大学选课系统 `elective.pku.edu.cn` 获取。暑期数据抓取流程见 `北京大学选课网数据抓取/README.md`。

## 重建数据库

```bash
python3 数据库构建脚本/build_undergrad_db.py        # → 数据库/2026春季学期本科生课程.db
python3 数据库构建脚本/build_graduate_db.py         # → 数据库/2026春季学期研究生课程.db
python3 北京大学选课网数据抓取/build_summer_db.py   # → 数据库/2026暑期本科生课程.db
```

`数据库构建脚本/build_common.py` 提供共用的课表解析函数。旧的派生数据与构建脚本归档在 `归档/`。

## 多语言翻译

利用 DeepSeek API 把课程内容批量翻译成 7 种语言，落入每个 DB 的 `translations` 表，由 `app.py` 在 `?lang=` 参数下即时切换。脚本与设计见 `北京大学课程数据翻译/README.md`。

## 如果有能力的话欢迎赞助 支持网站的运行
## 只要有人需要 每学期都会一直更新下去的！

<table align="center"><tr>
<td align="center"><img src="Images/wechat_sponsor.jpg" alt="微信赞助" width="150"><br>支付宝赞助码</td>
<td>&nbsp;&nbsp;&nbsp;&nbsp;</td>
<td align="center"><img src="Images/alipay_sponsor.jpg" alt="支付宝赞助" width="150"><br>微信赞助码</td>
</tr></table>
赞助后请微信联系我，我将您加入到赞助列表中感谢！Love

## 如果想要反馈BUG,或者希望有新的功能意见,欢迎微信联系,一定会尽快安排!
<p align="center"><img src="Images/MyWeChat.jpg" alt="微信联系方式" width="200"></p>


### 鸣谢赞助

感谢以下朋友的慷慨赞助，你们的支持是项目持续运营的动力！

| 赞助者 | 金额 |
|--------|------|
| 噬铁侠 | 100¥ |
