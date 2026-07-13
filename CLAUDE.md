# CLAUDE.md

拼好课 V2 由 FastAPI、两个无构建步骤的 HTML 页面、五个课程 SQLite 数据库和一个树洞评测数据库组成。生产应用只读数据，课程抓取必须复用已登录的 Chrome 页面，翻译任务不得自动产生 API 费用。

本仓库的工程事实、API、网页设计契约、操作边界和验证命令统一维护在 [AGENTS.md](AGENTS.md)；用户功能与文档入口见 [README.md](README.md)。

开始工作前请完整阅读 `AGENTS.md`，并以代码、数据库契约和测试为最终事实来源。不要创建重复说明文档。
