# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

项目定位、硬门槛、铁律条目与任务路由见下（本仓库的路由正本，勿在此重复）：

@AGENTS.md

以下只记「不说就会做错」的操作细节。**任何规则的正本都在别处**——SPEC.md §7 有全量归属表，
改规则 = 改正本 + 检查引用处；把规则抄进第二个文件是本仓库明令禁止的反模式。

## uv 依赖组：用错就 ImportError

| 场景 | 命令 |
| --- | --- |
| lint（唯一写法） | `bash scripts/lint.sh` |
| 测试 | `uv run --group dev pytest -q` |
| 读 MathNet 数据集的一切（ingest / import / export / review batch） | `uv run --group mathnet python ...` |
| embedding 索引 | `uv run --group similar python scripts/similar_index.py ...` |
| 候选池全量索引（两组叠加） | `uv run --group similar --group mathnet python ...` |
| 重跑历史 `verify/` 核验脚本 | `uv run --with sympy python scripts/verify/...`（sympy 刻意不设组；`scripts/verify/` 仅为退役史料） |
| 新核验 / 持续机器核验（CI 同款） | `uv run --with sympy python scripts/checks/run_checks.py`（新核验写入 `scripts/checks/check_<batch>.py`，细则见 `docs/入库SOP-MathNet.md` §6.5） |

`mathnet_review.py` 的三步依赖组不对称：`batch` 要 `--group mathnet`（读数据集），
`dispatch` / `merge` 不要。其余 `bank.py` 子命令用 `uv run python scripts/bank.py ...` 即可。

## gitignore 与重建：clone 后这些目录不存在

- `candidates/`（含候选池与 `simindex/`）→ `uv run --group mathnet python scripts/mathnet_ingest.py`
- 改过 `taxonomy/mathnet_map.yml` 的映射/召回规则，或 `taxonomy/contest_tiers.yml` 的赛名难度规则后必须重建候选池；排查实例正本见 `.claude/skills/ingest/SKILL.md`「实测坑」第 5 条
- `candidates/simindex/` → `uv run --group similar python scripts/similar_index.py build --bank-only`
- `maps/` → `uv run python scripts/bank.py map`
- `mathnet-full/`（全文导出，供人工检索选题）→ `uv run --group mathnet python scripts/mathnet_export.py`
- `data/sessions/` 是 spar 运行态，**不要手动删**（会毁掉进行中的攻坚会话）

`mathnet-full/` 存在时 `bank.py doclint` 会连它一起扫（md 文件数 ~300 → ~28000，耗时 0.3s → 5s）。
这是本地现象，CI 上该目录不存在故不受影响；嫌慢就删掉它，随时可重建。

候选池缺失时**不要凭记忆或联网重建题目数据**——按 WORKFLOW.md 那是合法阻塞，如实上报即可。
以上重建都依赖本地 HuggingFace 缓存里的 `ShadenA/MathNet`。

## `data/review/` 必须提交（与常规直觉相反）

评审凭证 `verdicts.json` 是入库题的核验依据，lint 会打开 `review_ref` 指向的文件、
并检查其中确实包含该题的 `mathnet_id`。凭证不入库 = 题不可信 = lint 红。
`data/hints/`、`data/plan.json` 同理入库。

`data/attempts.jsonl`（训练日志）也必须提交：它是学生能力图与复习调度的唯一数据来源，
且只存在于本机——不入库就随时可能丢掉全部训练史。别和 `data/sessions/`（gitignore 的
运行态）搞混：前者是长期资产，后者是进行中的会话。

## 契约正本在代码里，不在 SPEC

- `verification` 合法枚举 → `scripts/spar_session.py` 的 `VALID_VERIFICATION`
- 必需正文小节 → `scripts/bank.py` 的 `SECTIONS`
- 允许小节白名单（防泄答）→ `scripts/spar_session.py` 的 `KNOWN_SECTIONS`
- doclint 禁词表 → `scripts/bank.py` 的 `DOCLINT_FORBIDDEN`

要加一种新小节或新 verification 档，**先改代码常量**再谈文档；SPEC 只写语义、故意不抄枚举。

## 题目内容的红线

题面/答案一律逐字照录 MathNet 原文。**丢失的符号绝不"按语义复原"**——旧题库的历史审计
（`docs/archive/` 有存档）证实这一做法制造过两道实质错误题：不等号方向抄反、差式被写成商式。
拿不准就不收：少交付是可接受的结果，凑数不是。

题号不是跨代稳定标识：`legacy-bank-v1.9` 里的 `A-001` 与当前 `A-001` 不是同一道题，
引用旧题必须带 tag，引用现库用 `mathnet_id`。

## 提交与分支

- 提交信息用**中文**，不用 Conventional Commits 前缀（无 `feat:` / `fix:`）。
  风格：`<主题>：<做了什么>（<为什么或来源>）`，涉及题目的提交须写清来源与核验方式。
- 基线分支是 `master`（写错分支名会被 doclint 禁词拦下）。
- PR 按 `.github/pull_request_template.md`：核验记录三项 + 粘贴 `bash scripts/lint.sh` 输出。
