# 北京大学课程数据翻译

本目录把课程自由文本翻译为 `en`、`ja`、`ko`、`fr`、`de`、`es`、`ru`，写入五个 SQLite 数据库各自的 `translations` 表。有限枚举值仍由 `index.html` 的 `dataI18n` 负责。

**自动测试、部署和文档任务绝不自动运行付费 API。** 只有用户明确批准费用、数据库和字段范围后，才能执行翻译命令。

## 五库矩阵

仓库数据库快照（2026-07-11）：

| 选择名 | 数据库 | 课程行数 | translations 行数 |
|---|---|---:|---:|
| `ug` | `数据库/2026春季学期本科生课程.db` | 2465 | 100156 |
| `gr` | `数据库/2026春季学期研究生课程.db` | 1379 | 39445 |
| `summer` | `数据库/2026暑期本科生课程.db` | 194 | 10010 |
| `fall` | `数据库/2026秋季学期本科生课程.db` | 3032 | 159138 |
| `fall_gr` | `数据库/2026秋季学期研究生课程.db` | 1611 | 52430 |

这些数字是表内译文行数，不代表所有非空源字段都具备七种译文。春季长字段仍可能缺少 `syllabus`、`evaluation`、`reference_book` 或研究生 `reference_book`；其他库也应按脚本 pending 扫描结果判断缺口，不能从总行数推断全覆盖。

缺少目标语言译文或译文只含空白时，`app.py` 会**回退原始中文**。因此可以分批补译，不完整译文不会让课程字段变空。

## 存储契约

五库使用相同表结构：

```sql
CREATE TABLE translations (
    course_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    lang TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (course_id, field, lang)
);
CREATE INDEX idx_trans_cid_field ON translations(course_id, field);
```

`course_id` 是各数据库内部的整数，不带 API 的 `a/r/u/g/s` 前缀。翻译写入通过 `INSERT OR REPLACE` 按三元组断点续跑。空白 API 结果由 `clean_translation()` 拒绝，不写入数据库。

## 配置

凭据只从环境变量读取：

```bash
export DEEPSEEK_API_KEY=sk-xxxxx
```

可选配置：

```bash
export DEEPSEEK_API_URL=https://api.qnaigc.com/v1/chat/completions
export DEEPSEEK_MODEL=deepseek/deepseek-v4-flash
```

不要把 key 写入仓库。三个脚本在导入和 `--help` 时不会读取密钥；只有 pending 任务真正调用 API 时才要求 `DEEPSEEK_API_KEY`。`certifi` 是可选依赖，缺少时使用系统证书上下文。

## 脚本与选择参数

### `translate_courses.py`

负责本科简介、研究生简介和研究生详情备注。使用 `--only` 精确选一项：

| `--only` | 数据库与字段 |
|---|---|
| `ug_intro` | 春季本科 `intro_cn` |
| `gr_intro` | 春季研究生 `intro` -> `intro_cn` |
| `gr_extra` | 春季研究生 `extra_notes` |
| `summer_intro` | 暑期本科 `intro_cn` |
| `fall_intro` | 秋季本科 `intro_cn` |
| `fall_gr_intro` | 秋季研究生 `intro` -> `intro_cn` |
| `fall_gr_extra` | 秋季研究生 `extra_notes` |

示例：

```bash
python3 北京大学课程数据翻译/translate_courses.py --only fall_intro --workers 10
python3 北京大学课程数据翻译/translate_courses.py --only fall_gr_intro --limit 5
```

不传 `--only` 会扫描表中全部七项；执行前必须确认这是预期范围。

### `translate_misc.py`

负责其余字段，使用 `--db` 选库，使用 `--phase` 选字段组：

- `--db ug|gr|summer|fall|fall_gr|all`
- `--phase short|long|all`
- `--allow-non-cn`：也处理纯英文等不含中文字符的源文本
- `--limit N`：限制 pending 行数，适合小规模试跑

短字段任务包括课程名、备注、PNP、教室、专业、先修课程、通识系列、教材、修读对象和开课学期。长字段任务包括教学大纲、教学评估和参考书；秋季研究生还包括 `syllabus`。

```bash
python3 北京大学课程数据翻译/translate_misc.py --db fall --phase short --workers 10
python3 北京大学课程数据翻译/translate_misc.py --db ug --phase long
python3 北京大学课程数据翻译/translate_misc.py --db gr --phase long
```

春季必须分别使用 `--db ug` 和 `--db gr`。推荐先用 `--help` 核对 choices：

```bash
python3 北京大学课程数据翻译/translate_misc.py --help
```

### `translate_stubborn.py`

长文本 JSON 返回被截断或单一语言持续缺失时，使用单语言直返兜底：

- `--db gr|ug|summer|fall|fall_gr|all`
- `--field intro|extra_notes|syllabus|evaluation|reference_book|all`
- `--workers N`、`--limit N`

```bash
python3 北京大学课程数据翻译/translate_stubborn.py --db ug --field syllabus --workers 10
python3 北京大学课程数据翻译/translate_stubborn.py --db fall_gr --field reference_book --limit 5
```

任务矩阵包含春季本科 `syllabus`、`evaluation`、`reference_book` 和春季研究生 `reference_book`，用于补齐审计发现的长字段缺口。

## 推荐流程

每次只处理一个明确选择：

1. 备份目标数据库，记录 `translations` 行数和文件 SHA-256。
2. 用 `--help` 核对选择参数。
3. 用 `--limit 5` 小规模试跑并检查内容质量与费用。
4. 对简介运行 `translate_courses.py --only ...`。
5. 对其他字段依次运行 `translate_misc.py --db ... --phase short` 和 `--phase long`。
6. 用 `translate_stubborn.py --db ... --field ...` 处理剩余长字段。
7. 检查失败状态、缺失语言、数据库完整性和最终哈希，再决定是否提交数据库。

不要直接运行三个脚本的默认 `all` 范围来“看看还有多少”，因为 pending 记录会立即发起付费请求。需要无费用检查时使用只读 SQL 或测试提供的 mock。

## 重试与失败语义

- 网络和 API 错误由调用层有限退避重试。
- API 成功后，译文先在内存中清洗，再单独写库。
- 遇到 SQLite **数据库锁**或 busy 状态，只重试写入；锁重试不会再次调用付费 API。
- 同一次 `translate_misc.py` 运行按源文本去重，相同文本只调用一次 API，再写入多个课程三元组。
- worker 或任务存在未处理失败时，进程以非零状态退出。
- `--only` / `--db` 只初始化、扫描和写入选中的数据库，不得触碰其他库。

## 校验

查看五库译文行数：

```bash
for db in 数据库/*.db; do
  printf '%s: ' "$db"
  sqlite3 "$db" 'select count(*) from translations;'
done
```

查看某字段少于七种语言的记录：

```bash
sqlite3 "数据库/2026春季学期本科生课程.db" "
select course_id, field, count(*) as languages
from translations
group by course_id, field
having languages < 7
order by field, course_id
limit 50;
"
```

无密钥回归测试不会发起网络请求：

```bash
env -u DEEPSEEK_API_KEY python3 -m unittest tests.test_translation_scripts -v
```

## 数据保护

五个建库脚本会重建 `translations` 为空表。建库前必须备份译文数据库，或接受重新翻译的费用与时间。翻译作业优先在仓库外的数据库副本完成，验收通过后再一次性原子导入，避免持续改写受 Git LFS 管理的大型数据库。
