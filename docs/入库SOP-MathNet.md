# MathNet 入库 SOP（端到端正本）

> 本文是 MathNet 候选题从筛选到入库的**流程唯一正本**。题文件格式以 `SPEC.md` 为正本，
> lint 执行以 `scripts/lint.sh` 为正本，转向缘由见 `docs/决策-2026-08-MathNet转向.md`——本文只写流程，不复制它们的内容。

## 凭证纪律（置顶，先于一切步骤）

**数据集声称 ≠ 已核验；无 verdicts.json 不入库。**

- MathNet 自带的 status/难度/标签只是候选池元数据，不构成核验。每道入库题必须经过本文第 3–4 步的
  Codex 逐题评审，评审产物 `verdicts.json` 落盘在 `data/review/<batch>/` 并**随仓库提交**。
- 入库题 frontmatter 的 `review_ref` 必须指向该 verdicts.json（写法见第 5 步）。指不到凭证的题即为未核验，lint 应拒绝、评审时应退回。
- 教训出处：旧库 83% sourced 题为裸声明、2 道实质错题（`docs/archive/审计存档-旧题库核验真相-2026-08.md`）。裸声明字段不再被信任。

## 流程总览

```
candidates 筛选 → batch 出批次 → dispatch 派 Codex → merge 回填+人工定夺 → import 转格式入库 → lint
   （第 1 步）      （第 2 步）      （第 3 步）         （第 4 步）          （第 6 步）      （第 7 步）
                                                    verdicts.json 归档 + review_ref（第 5 步）
```

## 1｜候选池与筛选

候选池 `candidates/mathnet.jsonl`（27k+ 行，status=ok）由 `scripts/mathnet_ingest.py` 从 HF 本地缓存的
ShadenA/MathNet 确定性构建，**gitignore、可随时重建**：

```bash
uv run --group mathnet python scripts/mathnet_ingest.py              # 全量重建
uv run --group mathnet python scripts/mathnet_ingest.py --selfcheck  # 只校验两张映射表自洽
```

筛选浏览走 `bank.py candidates`（默认排除带图题、置信度 ≥mid）：

```bash
uv run python scripts/bank.py candidates --stats                                    # 难度×板块分布
uv run python scripts/bank.py candidates --gaps                                     # 知识点缺口采购单
uv run python scripts/bank.py candidates --category geometry --difficulty 2-3 \
    --node 圆与四点共圆 --limit 20                                                   # 按板块/估级/节点筛
uv run python scripts/bank.py candidates --grep Ramsey                              # 题面正则旁路召回
```

**学段下界（语义正本 SPEC §4，阈值正本 `bank.py` 的 `MIN_DIFFICULTY`）：本库只收初中与高中，
★1 是小学/低龄档，不予入库。** 这条不靠自觉，四道执行点各自拦一次：

| 环节 | 执行点 | 行为 |
| --- | --- | --- |
| 选池 | `bank.py candidates` | 默认只出 est ★2–5；`--difficulty` 给了更低下限会被抬回并打印提示 |
| 评审池 | `mathnet_review.py batch` | est ★1 的候选不进批次，打印跳过条数（不浪费评审预算） |
| 入库准入 | `mathnet_import.py` 的 `below_floor` | `min(est, codex) < 下界` 即跳过，**写盘之前**，不占题号与板块配额 |
| 最终门槛 | `bank.py lint` | `difficulty: 1` 判红 |

第三道是关键：`needs_review` 只查 est 与 Codex 的**分歧幅度**，est★2 + Codex★1 这类分歧仅
1 档的组合能整个躲过它，而「就低不就高」定稿正是 ★1。所以下界必须是独立一道判定。

## 2｜batch：出评审批次

```bash
uv run --group mathnet python scripts/mathnet_review.py batch \
    --category geometry --difficulty 2-3 --node 共线共点定理 --n 12 --out data/review/geo-01
```

产物：`data/review/<batch>/batch.json`（题面+官方解摘录+规则层估级）与 `task.md`（评审提示词）。
批次目录名建议 `<板块缩写>-<序号>`（如 `geo-01`、`nt-02`），一经 dispatch 不再改名——它会被 review_ref 永久引用。

## 3｜dispatch：派给本地 Codex

```bash
uv run python scripts/mathnet_review.py dispatch --dir data/review/geo-01
```

需本机 codex-cli 已登录；companion 路径由脚本 glob 自动发现（取插件最高版本）。产物：同目录 `verdicts.json`。

### 评审输出 schema（每题一个对象；`scripts/mathnet_review.py` 的 PROMPT 是其可执行渲染，改字段先改本表）

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `mathnet_id` | 字符串 | 溯源主键，对应 batch.json 条目 |
| `short_title` | ≤8 词英文短标题 | 名词短语概括题目核心对象与性质；入库时直接用作 `title` |
| `difficulty_codex` | 1–5 整数 | Codex 独立定级（标尺同 SPEC 第 4 节；有疑义就低不就高） |
| `difficulty_reason` | 一句话 | 关键突破口与思维跨度 |
| `topics_verdict` | agree / partial / wrong | 我方知识点标签是否贴切 |
| `topics_comment` | 一句话 | 不贴切时说明该往哪个方向 |
| `text_quality` | clean / minor_issues / broken | 转录质量：LaTeX 完整性、OCR 残缺、题意自洽性 |
| `text_comment` | 一句话 | 题面/解答质量分别如实记录 |
| `needs_figure` | true / false | 是否依赖图形且无法文字复原（铁律：此类不收录） |
| `figure_comment` | 一句话 | 判断依据 |
| `recommend` | claim / skip | 收录建议 |
| `recommend_reason` | 一句话 | 结论 |

## 4｜merge：回填候选池 + 分歧定夺

```bash
uv run python scripts/mathnet_review.py merge --dir data/review/geo-01
```

把评审结果回填候选池，并打印分歧报告。自动标 `needs_review` 的情形：难度分歧 ≥2 档、
`topics_verdict: wrong`、`text_quality: broken`、`needs_figure: true`。

**入库准入线**：只有 `recommend: claim` 且非 `needs_review` 的题可直接进第 6 步；
needs_review 的题须人工逐题定夺，裁定意见写进该题入库文件的 `difficulty_note`（或放弃收录）。
正式 `difficulty` 始终由入库者按 SPEC 第 4 节裁定，`difficulty_codex` 与规则层估级只是两路参考。

## 5｜verdicts.json 归档与 review_ref 写法

- **归档位置**：`data/review/<batch>/verdicts.json`，与 batch.json 同目录，**必须提交进仓库**（`data/review/` 不在 gitignore，这是刻意的）。归档后只增不改；如需重评，另开新批次目录。
- **review_ref 写法**：仓库相对路径，逐字指向凭证文件：

```yaml
verification: mathnet-reviewed
mathnet_id: 03g0
review_ref: data/review/geo-01/verdicts.json
```

一个批次的 verdicts.json 被该批全部入库题共同引用；按 `mathnet_id` 在其中查到对应评审对象即为该题凭证。

## 6｜import：转格式入库

```bash
uv run --group mathnet python scripts/mathnet_import.py --dir data/review/geo-01
```

（参数以脚本 `--help` 为准。）题文件字段全集、正文小节、编号规则**一律以 SPEC.md 为正本**，本文只规定评审产物到字段的取值映射：

| 入库字段 | 取自 |
| --- | --- |
| `title` | verdicts 的 `short_title` |
| `difficulty` / `difficulty_note` | 入库者裁定（参考 `difficulty_codex` 与估级，分歧裁定写 note） |
| `verification` | 固定 `mathnet-reviewed` |
| `mathnet_id` | batch/verdicts 的 `mathnet_id`（必填溯源字段） |
| `review_ref` | 第 5 步写法 |
| `source_ref` | `MathNet <mathnet_id>` |
| `source_url` | `https://huggingface.co/datasets/ShadenA/MathNet` |
| 题面 / 答案 / 解法要点 | MathNet 原文英文直用 / final_answer（证明题写「证明题」）/ 官方解拼接 |

## 7｜lint：唯一硬门槛

```bash
bash scripts/lint.sh
```

`scripts/lint.sh` 是 lint 的唯一执行正本（本地与 CI 同一入口），必须输出 `LINT OK` 才算入库完成。
