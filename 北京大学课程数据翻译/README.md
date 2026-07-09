# 北京大学课程数据翻译

把 SQLite 数据库里的中文课程字段批量翻译成 7 种语言（`en` / `ja` / `ko` / `fr` / `de` / `es` / `ru`），结果写入各数据库自带的 `translations` 表，供前端通过 `?lang=xx` 即时切换。缺少译文时，后端保留原始中文文本。

当前覆盖：

| 数据库 | 课程数 | translations 行数 |
|---|---:|---:|
| `数据库/2026秋季学期本科生课程.db` | 3032 | 124734 |
| `数据库/2026秋季学期研究生课程.db` | 1611 | 0 |
| `数据库/2026春季学期本科生课程.db` | 2465 | 100156 |
| `数据库/2026春季学期研究生课程.db` | 1379 | 39445 |
| `数据库/2026暑期本科生课程.db` | 194 | 10010 |

脚本通过 `__file__` 解析项目根目录，从任意工作目录运行都可以。

## 一、译文存储方式

每个数据库内都有同样 schema 的表：

```sql
CREATE TABLE translations (
    course_id INTEGER NOT NULL,
    field     TEXT NOT NULL,
    lang      TEXT NOT NULL,
    text      TEXT NOT NULL,
    PRIMARY KEY (course_id, field, lang)
);
CREATE INDEX idx_trans_cid_field ON translations(course_id, field);
```

后端 `app.py` 的 `_apply_translations` 在 `lang != zh` 时按 `(course_id, field, lang)` 把原始中文字段替换成译文。课程列表只查卡片可见字段（`course_name` / `classroom` / `notes`），课程详情查 `TRANSLATABLE_FIELDS` 中的完整字段。

注意：`translations.course_id` 使用各自数据库内的裸整数 id，不带前端的 `a` / `r` / `u` / `g` / `s` 前缀。前缀只存在于 API 和 URL 层。

## 二、翻译服务

- API 代理：`https://api.qnaigc.com/v1`（兼容 OpenAI Chat Completions）
- 模型：`deepseek/deepseek-v4-flash`
- 凭据：环境变量 `DEEPSEEK_API_KEY`
- 长字段默认要求模型返回 JSON；兜底脚本 `translate_stubborn.py` 改为单语言直返，避免超长 JSON 被代理截断

不要把 API key 写进仓库文件。可以本地放 `.env`，但必须保持忽略：

```text
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

加载示例：

```bash
set -a; source .env; set +a
```

## 三、脚本分工

| 脚本 | 覆盖范围 | 常用命令 |
|---|---|---|
| `translate_courses.py` | 春季本科 `intro_cn`、暑期本科 `intro_cn`、秋季本科 `intro_cn`、春季研究生 `intro`（统一存为 `intro_cn`）、春季研究生 `extra_notes` | `python3 北京大学课程数据翻译/translate_courses.py` |
| `translate_misc.py` | 春季本科、暑期本科、秋季本科及春季研究生的其余短字段与长字段：课程名、备注、PNP、教室、专业、先修、通识系列、教材、参考书、教学大纲、教学评估等 | `python3 北京大学课程数据翻译/translate_misc.py --phase short` / `--phase long` |
| `translate_stubborn.py` | 单语言兜底补齐：春季研究生 intro/extra、春季本科 intro、暑期 intro/syllabus/evaluation、秋季本科 intro/syllabus/evaluation/reference_book | `python3 北京大学课程数据翻译/translate_stubborn.py` |

所有脚本都可断点续跑。已写入的 `(course_id, field, lang)` 会被跳过或覆盖写入，不需要手工清理。

## 四、典型工作流

```bash
export DEEPSEEK_API_KEY=sk-xxxxx

# 1. 简介大头
python3 北京大学课程数据翻译/translate_courses.py

# 2. 短字段：课程名/备注/教室/专业/先修等
python3 北京大学课程数据翻译/translate_misc.py --phase short

# 3. 长字段：教学大纲/教学评估/参考书等
python3 北京大学课程数据翻译/translate_misc.py --phase long

# 4. 兜底：补齐被截断或失败的长文本
python3 北京大学课程数据翻译/translate_stubborn.py
```

试跑或限定范围：

```bash
python3 北京大学课程数据翻译/translate_courses.py --limit 5
python3 北京大学课程数据翻译/translate_courses.py --only summer_intro
python3 北京大学课程数据翻译/translate_courses.py --only fall_intro
python3 北京大学课程数据翻译/translate_misc.py --phase short --workers 10
python3 北京大学课程数据翻译/translate_misc.py --phase long --workers 10
python3 北京大学课程数据翻译/translate_stubborn.py --db fall --field intro --workers 10
```

建议并发从 10 到 15 起步。代理偶尔会对 30 以上并发返回 5xx 或 429。

## 五、当前字段覆盖

### 简介字段

- 春季本科：`detail_info.intro_cn` → `translations.field='intro_cn'`
- 暑期本科：`detail_info.intro_cn` → `translations.field='intro_cn'`
- 秋季本科：`detail_info.intro_cn` → `translations.field='intro_cn'`
- 春季研究生：`detail_info.intro` → `translations.field='intro_cn'`
- 春季研究生：`detail_info.extra_notes` → `translations.field='extra_notes'`

本科形态数据库如果 `detail_info.intro_en` 已有英文简介，脚本会直接写入 `lang='en'`，不再调用 API。秋季研究生库目前暂未纳入翻译脚本，前端会显示原始中文，后续扩展脚本后可直接写入其 `translations` 表。

### 其他字段

短字段包括：

```text
course_name, notes, pnp, classroom, major,
prerequisites, ge_series, textbook,
audience, term
```

长字段包括：

```text
syllabus, evaluation, reference_book
```

春季本科、暑期本科和秋季本科 schema 相同；研究生 schema 不同，因此脚本内部分别列任务。

## 六、校验

查看各库翻译行数：

```bash
sqlite3 "数据库/2026秋季学期本科生课程.db" "select count(*) from translations;"
sqlite3 "数据库/2026秋季学期研究生课程.db" "select count(*) from translations;"
sqlite3 "数据库/2026春季学期本科生课程.db" "select count(*) from translations;"
sqlite3 "数据库/2026春季学期研究生课程.db" "select count(*) from translations;"
sqlite3 "数据库/2026暑期本科生课程.db" "select count(*) from translations;"
```

检查某个字段是否缺少语言：

```bash
sqlite3 "数据库/2026暑期本科生课程.db" "
select course_id, field, count(*) as langs
from translations
group by course_id, field
having langs < 7
limit 20;
"
```

## 七、注意事项

1. **重建 DB 会清空 `translations` 表。** `数据库构建脚本/build_undergrad_db.py`、`build_graduate_db.py`、`北京大学选课网数据抓取/build_summer_db.py`、`build_undergrad_2627_fall_db.py`、`build_graduate_2627_fall_db.py` 都会重建库。重建前先备份，或重建后重新跑翻译。
2. **代理可能截断长 JSON。** `translate_misc.py --phase long` 对超长教学大纲/评估更容易失败；失败后运行 `translate_stubborn.py`。
3. **源文本去重只在单次运行内生效。** 大量重复短文本会被缓存减少 API 调用；中断后重跑仍会跳过已写入的三元组。
4. **`enable_thinking: False` 是省 token 设置。** 如果代理或模型升级后拒绝该字段，删除脚本请求体中的这个字段即可。
5. **前端短枚举仍在 `index.html`。** 院系、类别、成绩方式、语言、星期等有限字典主要由前端 `dataI18n` 翻译；数据库 `translations` 负责自由文本。
