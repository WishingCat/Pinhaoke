# 拼好课 V2

拼好课是面向北京大学课程的搜索、筛选与课程评测检索工具。生产入口：[https://www.pinhaoke.love](https://www.pinhaoke.love)。

## 学期与评测入口

页面学期选项按以下顺序显示：

1. 2026 春季学期
2. 2026 暑期学期
3. 2026 秋季学期

秋季为默认学期。春季和秋季同时收录本科生、研究生课程；暑期收录本科生课程。选课系统中的重复挂载会在课程列表合并，因此页面卡片数少于源记录数。

“树洞课程评测”是三个学期选项旁边的独立入口，不参与学期切换。该页面可按课程名检索树洞中的课程评测主帖及其中与课程评价有关的回复，并显示树洞号、发布时间和原帖链接。搜索框输入后会自动检索，不显示输入联想；热门预选项只在点击右侧“查看热门课程”后展开。统计区显示数据范围 `2022-12-21` 至 `2026-07-13` 和评测数据量 `62716`，后者等于 `31642` 个主题加 `31074` 条相关回复。课程搜索页与评测页都适配桌面端、手机端和浅色/深色模式。

## 功能

- 按课程名、英文名、教师、教室或课程号搜索
- 按课程类型、类别、学分、开课单位、星期、成绩记载方式和教室筛选
- 按课程名、学分、最早节次排序，或使用可稳定翻页的随机排序
- 查看课程详情、简介、先修课程、教材、参考书、教学大纲和教学评估
- 按课程名或教师名自动搜索树洞课程评测，或通过“查看热门课程”选择常用课程；只展示评测主帖和相关评价回复，课程名、教师名及 `普生C`、`lsj` 等高置信缩写会加粗彩色高亮
- 将搜索、筛选、排序、学期、语言和课程详情保存在可分享 URL 中
- 支持中文、English、日本語、한국어、Français、Deutsch、Español、Русский
- 支持浅色/深色模式、键盘操作和手机窄屏

## 本地运行

仓库内 `venv/` 面向生产目录，不适合作为 macOS 开发环境。使用临时虚拟环境：

```bash
python3 -m venv /tmp/pinhaoke-dev
/tmp/pinhaoke-dev/bin/python -m pip install -r requirements.txt
/tmp/pinhaoke-dev/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

访问 [http://127.0.0.1:8000/](http://127.0.0.1:8000/) 使用课程搜索，访问 [http://127.0.0.1:8000/reviews](http://127.0.0.1:8000/reviews) 使用树洞课程评测。不要直接双击 HTML 文件，页面需要 `/api/*` 后端。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

## 项目结构

```text
app.py                         FastAPI API 与只读 SQLite 查询
index.html                     无构建步骤的课程搜索页
reviews.html                   无构建步骤的树洞课程评测页
数据库/                        五个课程库与一个树洞评测库
课程数据/                      七份源 JSON 与数据口径说明
数据库构建脚本/                春季、树洞评测建库及共享原子构建工具
北京大学选课网数据抓取/         页面内抓取、接收器与暑期/秋季建库
北京大学课程数据翻译/           七语翻译流水线
deploy/                        Nginx、systemd 与唯一更新脚本
归档/                          V1 只读参考
```

## 文档索引

- [工程与 API 约定](AGENTS.md)
- [选课网抓取与建库](北京大学选课网数据抓取/README.md)
- [课程翻译流水线](北京大学课程数据翻译/README.md)
- [数据来源、数量与 schema](课程数据/数据说明.md)
- [生产部署与回滚](deploy/README.md)
- [V1 归档说明](归档/README.md)

## 反馈与赞助

只要课程搜索仍被需要，项目会按学期维护。问题与功能建议可通过微信联系：

<p align="center"><img src="Images/MyWeChat.jpg" alt="微信联系方式" width="200"></p>

<table align="center"><tr>
<td align="center"><img src="Images/wechat_sponsor.jpg" alt="微信赞助码" width="150"><br>微信赞助码</td>
<td>&nbsp;&nbsp;&nbsp;&nbsp;</td>
<td align="center"><img src="Images/alipay_sponsor.jpg" alt="支付宝赞助码" width="150"><br>支付宝赞助码</td>
</tr></table>

### 鸣谢赞助

| 赞助者 | 金额 |
|---|---:|
| 噬铁侠 | ¥100 |
| 罗淦-PKU | ¥100 |
