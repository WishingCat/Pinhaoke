# 归档/

旧的派生数据与构建脚本，2026 春季学期重构时移入此目录留档。**当前线上服务不依赖任何文件。**

## 文件清单

| 文件 | 大小 | 来源 / 用途 | 替代物 |
|---|---|---|---|
| `courses.db` | 20 MB | 旧的统一 SQLite（公选+通识+专业+研究生），由 `build_db.py` 从源 JSON 构建 | `数据库/2026春季学期本科生课程.db` + `数据库/2026春季学期研究生课程.db` |
| `courses.json` | 1.2 MB | 旧的 xlsx 解析产物，扁平 JSON | 直接读两个新 DB |
| `courses_data.js` | 636 KB | 旧的 xlsx 解析产物，压缩 JS（前端早期纯静态版本可直接 `<script>` 引入） | `/api/courses` 接口 |
| `build_db.py` | — | 旧的 JSON→单库构建脚本 | `数据库构建脚本/build_undergrad_db.py` + `build_graduate_db.py`（共用 `build_common.py`） |
| `build_data.py` | — | 更早的 xlsx→JSON 流水线；依赖的 `pku_*_course_schedule_spring_2026.xlsx` 文件已不存在 | 已废弃 |

## 回滚说明

若需要回到旧架构（不推荐）：

```bash
cp 归档/courses.db .
cp 归档/build_db.py .
git checkout HEAD~N -- app.py index.html   # 找到重构前的提交
```

但要注意旧 `app.py` 读 `courses.db`，新 schema 与之不兼容，需要一并回滚。
