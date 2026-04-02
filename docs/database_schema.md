# Database Schema Documentation (OpenAlex Parser)

本文档说明 `openalex_parser.py` 输出的 SQLite 数据库结构、表关系和数据流，方便后续维护与二次开发。

- 默认数据库文件：`openalex_citations.db`
- 可通过 CLI `--db` 或配置文件字段 `db` 自定义路径

---

## 1. 总体设计

数据库采用「原始数据 + 规范化数据」双层模型：

1. **原始层（Raw）**
   - `works_raw`：完整保存 OpenAlex work JSON（可追溯、可重放）。
2. **规范化层（Normalized）**
   - `works`：论文主表
   - `citations`：引用边（有向）
   - `work_funders`：资助机构
   - `work_awards`：资助/奖项明细
3. **运行状态层（Run/Log）**
   - `crawl_runs`：任务级状态
   - `journal_cursors`：期刊级游标断点
   - `fetch_log`：分页抓取日志

---

## 2. ER 关系（逻辑）

```text
crawl_runs (1) -------- (N) journal_cursors
crawl_runs (1) -------- (N) fetch_log

works (1) ------------ (1) works_raw
works (1) ------------ (N) citations (as src_work_id)
works (1) ------------ (N) work_funders
works (1) ------------ (N) work_awards

citations.dst_work_id 可能指向 works.work_id（若目标论文已抓取）
```

> 说明：当前 schema 未显式声明 FOREIGN KEY 约束，但通过主键/唯一键和写入逻辑维持关系一致性。

---

## 3. 表定义说明

## 3.1 `crawl_runs`

任务运行主表，一次 `crawl` 命令对应一条记录。

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | 运行 ID |
| `started_at` | TEXT NOT NULL | 开始时间（UTC ISO） |
| `finished_at` | TEXT | 结束时间 |
| `journals_json` | TEXT NOT NULL | 本次任务 journal 列表（JSON 字符串） |
| `status` | TEXT NOT NULL | `running/completed/failed` |
| `notes` | TEXT | 错误信息或备注 |

---

## 3.2 `journal_cursors`

保存每个 journal 在当前 run 下的分页游标，支持断点续跑。

| 字段 | 类型 | 含义 |
|---|---|---|
| `run_id` | INTEGER NOT NULL | 对应 `crawl_runs.id` |
| `journal_id` | TEXT NOT NULL | OpenAlex source ID |
| `next_cursor` | TEXT | 下次请求游标 |
| `completed` | INTEGER NOT NULL DEFAULT 0 | 0/1 是否完成 |
| `updated_at` | TEXT NOT NULL | 最近更新时间 |

**主键：** `(run_id, journal_id)`

---

## 3.3 `fetch_log`

分页请求级日志，用于追踪每页抓取情况。

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | 日志 ID |
| `run_id` | INTEGER NOT NULL | 对应 `crawl_runs.id` |
| `journal_id` | TEXT NOT NULL | OpenAlex source ID |
| `cursor_in` | TEXT | 本页请求使用的 cursor |
| `cursor_out` | TEXT | 本页返回的 next cursor |
| `page_results` | INTEGER NOT NULL | 本页结果数 |
| `fetched_at` | TEXT NOT NULL | 抓取时间 |
| `cost_usd` | REAL | 预留字段（当前实现未写入） |

---

## 3.4 `works_raw`

原始 work JSON 存储（审计、回放、字段补抽取的权威来源）。

| 字段 | 类型 | 含义 |
|---|---|---|
| `work_id` | TEXT PK | OpenAlex work ID（如 `https://openalex.org/W...`） |
| `fetched_at` | TEXT NOT NULL | 最近抓取时间 |
| `payload_json` | TEXT NOT NULL | work 原始 JSON |
| `payload_hash` | INTEGER NOT NULL | payload 哈希（用于快速变化检测） |

---

## 3.5 `works`

论文主表（规范化基础字段 + 全量 record_json 备份）。

| 字段 | 类型 | 含义 |
|---|---|---|
| `work_id` | TEXT PK | OpenAlex work ID |
| `title` | TEXT | 标题 |
| `display_name` | TEXT | 展示标题 |
| `doi` | TEXT | DOI |
| `publication_year` | INTEGER | 发表年份 |
| `publication_date` | TEXT | 发表日期 |
| `work_type` | TEXT | work 类型 |
| `language` | TEXT | 语言 |
| `cited_by_count` | INTEGER | 被引次数 |
| `source_id` | TEXT | 期刊/来源 ID |
| `source_display_name` | TEXT | 期刊/来源名称 |
| `updated_date` | TEXT | OpenAlex 更新时间 |
| `created_date` | TEXT | OpenAlex 创建时间 |
| `record_json` | TEXT NOT NULL | 当前完整 work JSON |
| `last_seen_at` | TEXT NOT NULL | 最近见到时间 |

---

## 3.6 `citations`

引用边表，表示 `src_work_id -> dst_work_id`。

| 字段 | 类型 | 含义 |
|---|---|---|
| `src_work_id` | TEXT NOT NULL | 引用发出方（当前抓取到的 work） |
| `dst_work_id` | TEXT NOT NULL | 被引用方（可能尚未入库） |
| `discovered_at` | TEXT NOT NULL | 发现该边时间 |

**主键：** `(src_work_id, dst_work_id)`

---

## 3.7 `work_funders`

work 对应的资助机构信息（来自 `funders`）。

| 字段 | 类型 | 含义 |
|---|---|---|
| `work_id` | TEXT NOT NULL | 对应 `works.work_id` |
| `funder_id` | TEXT | 资助机构 ID |
| `funder_display_name` | TEXT | 资助机构名 |
| `funder_country_code` | TEXT | 国家码 |
| `funder_type` | TEXT | 类型 |
| `raw_json` | TEXT NOT NULL | 原始 funder JSON |

**主键：** `(work_id, funder_id)`

---

## 3.8 `work_awards`

work 对应的资助/奖项明细（来自 `awards`）。

| 字段 | 类型 | 含义 |
|---|---|---|
| `work_id` | TEXT NOT NULL | 对应 `works.work_id` |
| `award_id` | TEXT | 奖项 ID |
| `funder_id` | TEXT | 资助方 ID |
| `funder_display_name` | TEXT | 资助方名称 |
| `funder_award_id` | TEXT | 资助方奖项编号 |
| `award_doi` | TEXT | 奖项 DOI |
| `raw_json` | TEXT NOT NULL | 原始 award JSON |

**主键：** `(work_id, award_id, funder_award_id)`

---

## 4. 数据写入流程

每抓取一页 works 后，程序在事务内执行：

1. `upsert` 到 `works_raw`
2. `upsert` 到 `works`
3. `insert ignore` 到 `citations`
4. `upsert` 到 `work_funders`
5. `upsert` 到 `work_awards`
6. 记录 `fetch_log`
7. 更新 `journal_cursors`

任务结束后更新 `crawl_runs.status` 为 `completed`；异常则置 `failed` 并写入 `notes`。

---

## 5. 常见查询示例

### 5.1 某次 run 的抓取进度

```sql
SELECT journal_id, completed, next_cursor, updated_at
FROM journal_cursors
WHERE run_id = ?;
```

### 5.2 某个 work 的引用出边数量

```sql
SELECT COUNT(*) AS out_degree
FROM citations
WHERE src_work_id = ?;
```

### 5.3 某期刊抓取到的论文数

```sql
SELECT source_id, COUNT(*) AS n_works
FROM works
GROUP BY source_id
ORDER BY n_works DESC;
```

### 5.4 funding 覆盖率

```sql
SELECT
  COUNT(DISTINCT w.work_id) AS total_works,
  COUNT(DISTINCT f.work_id) AS works_with_funders,
  ROUND(1.0 * COUNT(DISTINCT f.work_id) / COUNT(DISTINCT w.work_id), 4) AS funder_coverage
FROM works w
LEFT JOIN work_funders f ON w.work_id = f.work_id;
```

---

## 6. 维护建议

- 后续如果要增强一致性，可显式开启/声明 FOREIGN KEY。
- 建议增加索引（如 `works.source_id`, `citations.dst_work_id`）以提升分析查询速度。
- 若数据量大，可把 `works_raw` 迁移到对象存储，仅保留 hash/指针在 SQLite。
