# 北京大学课程数据翻译

把两个 SQLite 数据库（本科 / 研究生）里的中文课程字段，批量翻译成 7 种语言（en / ja / ko / fr / de / es / ru），结果落到每个数据库自带的 `translations` 表，供前端按 `?lang=xx` 即时取用。

适用范围：`数据库/2026春季学期本科生课程.db` 和 `数据库/2026春季学期研究生课程.db`。脚本通过 `__file__` 解析路径，**从任意目录运行都可以**（无需切到仓库根）。

---

## 一、整体设计

### 1. 译文存储方式

每个数据库内都有一张同样 schema 的表：

```sql
CREATE TABLE translations (
    course_id INTEGER NOT NULL,   -- 对应 basic_info.id
    field     TEXT NOT NULL,      -- 比如 'course_name' / 'intro_cn' / 'syllabus'
    lang      TEXT NOT NULL,      -- 'en' | 'ja' | 'ko' | 'fr' | 'de' | 'es' | 'ru'
    text      TEXT NOT NULL,
    PRIMARY KEY (course_id, field, lang)
);
CREATE INDEX idx_trans_cid_field ON translations(course_id, field);
```

后端 `app.py` 的 `_apply_translations` 在 `lang!=zh` 时按 `(course_id, field, lang)` 把原始中文字段替换成译文。课程详情走全字段，课程列表只走可见字段（`course_name / classroom / notes`），把查询体积压到最小。

### 2. 翻译模型与服务

- **代理**：`https://api.qnaigc.com/v1`（兼容 OpenAI Chat Completions）
- **模型**：`deepseek/deepseek-v4-flash`
- **凭据**：脚本启动时读环境变量 `DEEPSEEK_API_KEY`，没设直接退出
- **响应格式**：长字段走 `response_format=json_object`，要求模型只输出 JSON；`translate_stubborn.py` 例外（见下）

### 3. 三个核心技巧

| 技巧 | 解决的问题 |
| --- | --- |
| **逐 `(course_id, field, lang)` 去重写入**（`INSERT OR REPLACE`） | 整个流程可断点续跑，跑挂了重启不会重复花钱 |
| **按源文本去重**（同样的 `'本研合上'`、`'物理学院'`、`'二教403'` 全库可能出现 N 次） | 缩到只对每个 unique 中文文本调一次 API，账单大头来自这一步 |
| **一次 API 调用批量请求 7 种语言**（一次返回 7 个键的 JSON） | 比每种语言一次调用便宜约 7×，且 1 次系统/上下文摊销到 7 个目标 |

---

## 二、三个脚本的分工

| 脚本 | 处理对象 | 调用方式 |
| --- | --- | --- |
| `translate_courses.py` | UG `detail_info.intro_cn`、GR `detail_info.intro`（统一存为 `intro_cn`）、GR `detail_info.extra_notes` | 一次性大头：所有课程简介，多线程 + 批量 7 语 |
| `translate_misc.py` | 其余所有含中文字段 ：短字段（课程名/备注/PNP/教室/专业/先修/通识系列/教材/听课对象/学期）+ 长字段（教学大纲/教学评估/参考书目） | 默认分两期跑：`--phase short` 和 `--phase long` |
| `translate_stubborn.py` | 上面两个脚本因为"7 语 JSON 太长被代理截断"而失败的长文本 | 退化为「单条文本、单种语言」直返，慢但稳 |

下面分别看一下。

### 2.1 `translate_courses.py`

```bash
# 全量跑（默认 10 线程）
python3 北京大学课程数据翻译/translate_courses.py

# 只跑某一档，便于试跑
python3 北京大学课程数据翻译/translate_courses.py --only ug_intro
python3 北京大学课程数据翻译/translate_courses.py --only gr_intro
python3 北京大学课程数据翻译/translate_courses.py --only gr_extra

# 试跑 N 条
python3 北京大学课程数据翻译/translate_courses.py --limit 5

# 调线程数
python3 北京大学课程数据翻译/translate_courses.py --workers 20
```

- 对 UG 简介，如果库里本来就有 `detail_info.intro_en`，会跳过 API 直接把它当作 `lang='en'` 入库
- 每条最多 retry 3 次，指数退避；最终失败的会在 stdout 末尾打印前 10 条原因
- 进度条每 10 条课程更新一次：`[done/total] elapsed=… rate=…/s eta=… in=in_tokens out=out_tokens errors=…`

### 2.2 `translate_misc.py`

```bash
# 跑短字段（推荐先跑这个）
python3 北京大学课程数据翻译/translate_misc.py --phase short

# 跑长字段（慢且贵）
python3 北京大学课程数据翻译/translate_misc.py --phase long

# 都跑
python3 北京大学课程数据翻译/translate_misc.py --phase all

# 默认 15 并发，可调
python3 北京大学课程数据翻译/translate_misc.py --phase short --workers 25
```

特点：
- 启动时先把 UG `detail_info.english_name` 当作 `course_name` 的 `lang='en'` 译文写进 `translations`（白嫖一波），后面的英文就不用走 API
- 对每个字段都带一句 hint 给 LLM，比如 `classroom` 是「Classroom or building+room (e.g. 二教403 = Building 2 Room 403)」，引导专业术语
- 同时跨两个数据库的 source 是按文本去重的（`'物理学院'` 在两个库里都翻一次太亏）

### 2.3 `translate_stubborn.py`

```bash
python3 北京大学课程数据翻译/translate_stubborn.py
```

固定处理三类：GR `intro`、GR `extra_notes`、UG `intro_cn`，把还差的 (course_id, lang) 全部补齐。每次 API 只翻一个文本到一种语言，不要求 JSON 输出，因此能扛住超长教学大纲 / 评估那种 >1500 字符的体量。慢、但配合上面两个脚本是兜底用的。

---

## 三、典型工作流

```bash
# 0. 准备：拿到 API key 并 export，不要写进文件
export DEEPSEEK_API_KEY=sk-xxxxx

# 1. 简介大头（占绝大多数 token）
python3 北京大学课程数据翻译/translate_courses.py

# 2. 短字段（课程名/备注/教室/...）
python3 北京大学课程数据翻译/translate_misc.py --phase short

# 3. 长字段（教学大纲/评估/参考书目）
python3 北京大学课程数据翻译/translate_misc.py --phase long

# 4. 兜底：补齐前面被截断/失败的长文本
python3 北京大学课程数据翻译/translate_stubborn.py
```

中途任何一步挂掉直接重跑同一条命令即可——所有写入都是 `INSERT OR REPLACE`，已经写进 `translations` 的 (cid, field, lang) 三元组在下次启动时会被 `fetch_pending_*` 自动跳过。

---

## 四、API 密钥与安全

- 所有脚本统一从 `DEEPSEEK_API_KEY` 环境变量取值。不要把 key 写进任何 `.py` 或 `.json`，否则会随提交泄露。
- 仓库 `.gitignore` 已经把 `.env` / `.env.*` / `*.api.key` / `secrets/` 列入忽略；本地可以放一个 `.env`：

  ```
  DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
  ```

  然后用 `set -a; source .env; set +a` 或 `export $(grep -v '^#' .env | xargs)` 加载。
- 如果 key 不慎入过 git 历史，需要在代理那边重新签发，并 `git filter-repo` 抹历史；只在工作区删除是不够的。

---

## 五、注意事项 / 已知坑

1. **重建 DB 会清空 `translations` 表**。`数据库构建脚本/build_undergrad_db.py` / `build_graduate_db.py` 是一次性 drop & rebuild，重建之后必须重新跑翻译。重要场合先 `cp 数据库/2026春季学期*.db /tmp/` 备份。
2. **代理偶尔截断 7 语 JSON**。`translate_misc.py --phase long` 跑大纲 / 评估时常见 `JSONDecodeError`，对应行会进 errors 列表；用 `translate_stubborn.py` 把它们补回来即可。
3. **并发上限**。代理对单 key 的并发不算友好，`--workers` 拉到 30 以上容易 5xx / 429，从 10–15 起步比较稳。
4. **`enable_thinking: False`**。所有调用都关闭了思考链以省 token，如果模型 / 代理升级把这字段拒了，会报 400，直接删掉这字段就行。
5. **课程号是字符串**。后端 `_parse_id` 把 `u123` / `g456` 拆 namespace + id，但翻译脚本和 `translations` 表都用裸 `course_id`，因为它们各自隔在 UG 库 / GR 库里，不需要 prefix。
