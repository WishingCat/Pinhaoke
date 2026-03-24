

# 拼好课 V2.0 - 北京大学课程搜索

北京大学 2026 年春季学期课程搜索工具，涵盖公选课、通识课、专业课共 2465 门课程的快速检索与筛选。

## 使用方法
直接打开网页：[www.pinhaoke.love](http://www.pinhaoke.love) 即可使用

## 功能

- 按课程名、教师姓名或教室搜索
- 六维度筛选：课程类型（公选课/通识课/专业课）、课程类别、学分、开课单位、上课时间（星期）、教室
- 课程详情弹窗：查看完整课程信息，包括中英文简介、教学大纲、教学评估、先修课程等
- 上课时间与教室分开展示
- 多语言切换：中文、English、日本語、한국어、Français、Deutsch、Español、Русский
- 玻璃拟态 (Glassmorphism) 风格 UI

## 技术架构

```
Nginx (反向代理) → FastAPI (Python 后端) → SQLite (数据库)
```

- **后端**：Python FastAPI + Uvicorn
- **数据库**：SQLite，支持课程列表/筛选/搜索/详情查询
- **前端**：单页 HTML，通过 API 动态加载数据
- **部署**：阿里云服务器（Ubuntu 24.04），Nginx + systemd

## 数据来源

- `北大公选课数据_25-26第2学期.json` — 公选课（256 门）
- `北大通识课数据_25-26第2学期.json` — 通识课（143 门）
- `北大专业课数据_25-26第2学期.json` — 专业课（2066 门）

数据来源于 2026 年 3 月 20 日从北京大学选课系统 `elective.pku.edu.cn` 获取的课程数据，每门课程包含基本信息与详细信息两部分。

## 如果有能力的话欢迎赞助 弥补网站的运行费用
## 只要有人需要 每学期都会一直更新下去的！

<table align="center"><tr>
<td align="center"><img src="Images/wechat_sponsor.jpg" alt="微信赞助" width="150"><br>支付宝赞助码</td>
<td>&nbsp;&nbsp;&nbsp;&nbsp;</td>
<td align="center"><img src="Images/alipay_sponsor.jpg" alt="支付宝赞助" width="150"><br>微信赞助码</td>
</tr></table>

## 如果想要反馈BUG,或者希望有新的功能意见,欢迎微信联系,一定会尽快安排!
<p align="center"><img src="Images/MyWeChat.jpg" alt="微信联系方式" width="200"></p>


### 鸣谢赞助

感谢以下朋友的慷慨赞助，你们的支持是项目持续运营的动力！

| 赞助者 | 金额 |
|--------|------|
| 噬铁侠 | 100¥ |

### 鸣谢
感谢西米露老师提供的课程列表
