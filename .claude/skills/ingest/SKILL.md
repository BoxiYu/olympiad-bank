---
name: ingest
description: 从 MathNet 候选池筛题、派 Codex 评审、入库到 problems/。当用户说「补题」「入库」「扩充题库」「补某个板块/知识点的题」「题库太单薄」时使用，即使他没点名入库流程也该用。策略正本是 docs/入库SOP-MathNet.md，本技能只补文档里没写、但一定会绊人的执行细节。
---

# MathNet 入库执行清单

策略、字段含义、凭证纪律的正本是 `docs/入库SOP-MathNet.md`；铁律正本是 `SPEC.md` §5。
**本文不复述它们**——只写实测中真正会翻车的地方。

## 一眼看全的五步

```bash
# ① 选题：先看缺口，别拍脑袋
uv run python scripts/bank.py candidates --gaps
uv run python scripts/bank.py candidates --category geometry --difficulty 2-3 --limit 20

# ② 出批次        —— 要 --group mathnet（读数据集）
uv run --group mathnet python scripts/mathnet_review.py batch \
    --category geometry --difficulty 2-3 --n 12 --out data/review/geo-01

# ③ 派 Codex 评审  —— 不要 --group（几分钟）
uv run python scripts/mathnet_review.py dispatch --dir data/review/geo-01

# ④ 回填定夺       —— 不要 --group
uv run python scripts/mathnet_review.py merge --dir data/review/geo-01

# ⑤ 入库验收       —— 要 --group mathnet；--per-category 必须显式给
uv run --group mathnet python scripts/mathnet_import.py --dir data/review/geo-01 \
    --per-category 12 --dry-run
uv run --group mathnet python scripts/mathnet_import.py --dir data/review/geo-01 --per-category 12
bash scripts/lint.sh
```

## 四个实测坑（这才是本技能存在的理由）

**1. 依赖组不对称，三步各不相同。** `batch` 与 `import` 读 HF 数据集，必须 `--group mathnet`，
不加必 ImportError；`dispatch`/`merge` 不读，加了是照抄不理解。实测有人在这里翻过车。

**2. `--per-category` 默认只有 5，会静默丢题。** 单板块批次尤其致命：出 12 题的几何批次，
默认值会让 7 道被判「geometry 配额已满」丢掉——而这个理由和质量原因混在同一份输出里打印，
极易被读成「这题不合格」。**永远显式写 `--per-category`**，让准入线由质量而非配额决定。

**3. 人工要放弃某题，用 `--exclude` 在入库前生效。**
```bash
uv run --group mathnet python scripts/mathnet_import.py --dir data/review/geo-01 \
    --per-category 12 --exclude 0g2e 01wa
```
lint 强制题号连号，事后删题就得重排编号——实测这一步会写出重复文件。裁定要前置。

**4. 批次目录名一经 dispatch 就被 `review_ref` 永久引用，不要事后改名。**

## 收尾（少了这几步就是半成品）

- **补提示阶梯**：新题一律没有，学生卡住时无梯可下。
  `hint_backfill.py batch --out data/hints/round-NN` → `dispatch` → `apply`
- **凭证必须随提交**：`data/review/<batch>/` 与 `data/hints/<round>/` 不是临时产物，
  不提交 = 题不可信 = lint 红。
- 提交信息按仓库风格（中文、非 Conventional Commits），写清批次、评审结论与排除原因。

## 会自动挡住你的闸门（知道它们存在，别绕）

`mathnet_import.py` 自己会拒收：评审判 skip、`needs_review`（难度分歧 ≥2 档等）、题面非英文
（SPEC §3：新题不设译文节，翻译即改写）、题面或官方解为空、原文含 `## ` 行、同 `mathnet_id` 重复。
被拒不是脚本出错，是准入线在工作——**少交付可接受，凑数不可**。想收就得先解决根因，
不是改闸门。
