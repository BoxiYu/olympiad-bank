---
name: ingest
description: 从 MathNet 候选池筛题、派 Codex 评审、入库到 problems/。当用户要「补题」「入库」「扩充题库」「补某个知识点/板块的题」时使用。策略正本是 docs/入库SOP-MathNet.md，本技能只管执行顺序、依赖组与检查点。
---

# MathNet 入库执行清单

策略、字段含义、凭证纪律的正本是 `docs/入库SOP-MathNet.md`（先读它，本文不复制其内容）。
本技能只解决「命令怎么敲、每步该看什么、哪里容易翻车」。

## 前提检查

1. 候选池在不在：`ls candidates/mathnet.jsonl`。不在就重建（gitignore，可随时重建）：
   `uv run --group mathnet python scripts/mathnet_ingest.py`
   —— 若本地 HF 缓存也没有 `ShadenA/MathNet`，这是合法阻塞，如实上报，**不要凭记忆或联网编造题目**。
2. Codex 通道在不在：入库第 3 步要派本地 codex-cli。不可用时不要跳过评审直接入库——
   无 `verdicts.json` 不入库是铁律，不是流程建议。

## 五步执行

**① 选题**（先看缺口再定条件，别拍脑袋）
```bash
uv run python scripts/bank.py candidates --gaps                     # 知识点缺口采购单
uv run python scripts/bank.py candidates --category geometry --difficulty 2-3 --limit 20
```

**② 出批次**（注意：batch 要 `--group mathnet`）
```bash
uv run --group mathnet python scripts/mathnet_review.py batch \
    --category geometry --difficulty 2-3 --n 12 --out data/review/geo-01
```
批次目录名一经 dispatch 就会被 `review_ref` 永久引用，**不要事后改名**。

**③ 派 Codex 评审**（不要 `--group mathnet`）
```bash
uv run python scripts/mathnet_review.py dispatch --dir data/review/geo-01
```
数分钟。产物 `verdicts.json` 就是这批题的核验凭证。

**④ 回填与人工定夺**（不要 `--group mathnet`）
```bash
uv run python scripts/mathnet_review.py merge --dir data/review/geo-01
```
读输出里的分歧明细：`recommend: skip` 的题不入库；难度分歧 ≥2 档的标 `needs_review`，
交人来定，**不要为凑数把它们塞进去**。

**⑤ 入库并验收**（import 要 `--group mathnet`）
```bash
uv run --group mathnet python scripts/mathnet_import.py --dir data/review/geo-01 --dry-run
uv run --group mathnet python scripts/mathnet_import.py --dir data/review/geo-01
bash scripts/lint.sh
```
`--dry-run` 先看会写哪些题。import 幂等（同 `mathnet_id` 拒重），题面逐字照录、含 `## ` 行的题会被拒收。

## 收尾

- 新题没有提示阶梯，学生卡住时无梯可下。补上：
  `uv run python scripts/hint_backfill.py batch --out data/hints/round-NN` → `dispatch` → `apply`
- `data/review/<batch>/` 与 `data/hints/<round>/` **必须随提交入库**（凭证，不是临时产物）。
- 提交按仓库风格（中文、非 Conventional Commits），正文写清批次、评审结论与 skip 原因。

## 护栏

- 无 `verdicts.json` 不入库；`review_ref` 必须指向真实存在且覆盖该题 `mathnet_id` 的凭证文件。
- 题面/答案逐字照录，**丢失的符号不得"按语义复原"**（历史上这样制造过实质错题）。
- 评审判 skip、或难度分歧未定夺的题，宁可少收也不入库。
- 入库前后都要 `bash scripts/lint.sh` 绿；lint 红了修数据，不要改尺子。
