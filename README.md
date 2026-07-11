# 拼好课 V2

拼好课是面向北京大学课程的搜索与筛选工具。生产入口：[https://www.pinhaoke.love](https://www.pinhaoke.love)。

## 学期

页面学期选项按以下顺序显示：

1. 2026 春季学期
2. 2026 暑期学期
3. 2026 秋季学期

秋季为默认学期。春季和秋季同时收录本科生、研究生课程；暑期收录本科生课程。选课系统中的重复挂载会在课程列表合并，因此页面卡片数少于源记录数。

## 功能

- 按课程名、英文名、教师、教室或课程号搜索
- 按课程类型、类别、学分、开课单位、星期、成绩记载方式和教室筛选
- 按课程名、学分、最早节次排序，或使用可稳定翻页的随机排序
- 查看课程详情、简介、先修课程、教材、参考书、教学大纲和教学评估
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

访问 [http://127.0.0.1:8000/](http://127.0.0.1:8000/)。不要直接双击 `index.html`，页面需要 `/api/*` 后端。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

## 项目结构

```text
app.py                         FastAPI API 与只读 SQLite 查询
index.html                     无构建步骤的单页前端
数据库/                        五个课程 SQLite 数据库
课程数据/                      七份源 JSON 与数据口径说明
数据库构建脚本/                春季建库及共享原子构建工具
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
