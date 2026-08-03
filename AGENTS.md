# AGENTS.md

给在本仓库工作的编码代理（Codex / Claude Code 等）的常驻说明。人类读者请先看 `README.md`。

## 这个仓库是什么

最终目的：**用 AI 促进数学教育，帮助学生高效学习数学**。题库与训练系统是实现路径，不是目的本身——一切取舍以"是否让学生学得更高效、更可信"为准。

按「知识点 × 难度」双维组织的中文奥数题库 + 训练系统。核心资产不是代码，而是**每一道题都可被追溯核实**这件事本身。

- `problems/<category>/<ID>.md` —— 一题一文件，YAML frontmatter + 固定小节的正文
- `taxonomy/` —— 四板块知识点树；`registry.yml` 是知识点正名注册表
- `docs/` —— 赛事地图、官方题源账本、教练手册
- `scripts/bank.py` —— 唯一的 CLI 入口（lint/query/stats/plan/coach/log/review/map）
- `SPEC.md` —— **入库规范，本仓库的最高约束**

## 唯一的硬门槛

```bash
uv run python scripts/bank.py lint
```

必须输出 `LINT OK`。CI 跑的就是这一条。lint 不过 = 工作未完成，不许 push。

辅助命令：

```bash
uv run python scripts/bank.py stats                 # 难度 × 板块 / 体系分布
uv run python scripts/bank.py query --difficulty 4  # 按难度检索
uv run python scripts/bank.py query --topic 韦达    # 按知识点检索
uv run python scripts/bank.py query --unverified    # 待二次复核的题
```

依赖由 `pyproject.toml` + `uv.lock` 声明，`uv run` 自动解决；无 uv 的环境退回 `python3` + 手动装 PyYAML 也能跑。

## 铁律（摘自 SPEC.md 第 5 节，不可协商）

1. **不编造题目。** 题面与答案必须来自你实际访问过的权威来源（imo-official.org 官方 PDF、赛事官方试卷、AoPS Wiki 单页），`source_url` 必须是你确实打开过的可访问链接。
2. **答案必须有出处。** 自行推导的设 `verification: independent-derivation` 并写 `verification_note`；能程序验证的优先写一次性脚本实证。
3. **依赖图形而无法用文字复原的题不收录。** 宁缺勿假。
4. **合规状态必须标注**（`compliance` 字段）。
5. **批量导入必须按 ≥15% 抽样逐字比对官方源**，抽样不合格整批退回。

> 拿不到可靠来源时，**少交付是可接受的结果，编造不是**。任何「凑数量」的要求都让位于这五条。

## 写题时的具体约定

- 题号按板块顺延，**不得跳号**：`A-`（代数）/ `N-`（数论）/ `C-`（组合）/ `G-`（几何）+ 三位数字。
- 正文小节顺序固定：`## 题面` → `## 原文（English）`（仅 `original_lang: en` 需要）→ `## 答案` → `## 解法要点`。
- `## 原文（English）` 必须**逐字照录**官方来源，不做任何改写——它是翻译质量的审计凭证。
- `## 解法要点` 只写 2–4 句关键突破口，不写完整证明。
- `topics` 取自 `taxonomy/`，1–4 个；新词须先在 `taxonomy/registry.yml` 注册，否则 lint 告警。
- 难度以**解法所需思维跨度**定级，不以赛事名气定级；有疑义就低不就高并注明「定级存疑」。
- 检索与叙述语言统一用中文；英文原题保留原文小节。
- 一次性程序验证脚本放 `scripts/verify/`，在 `verification_note` 中引用其路径作核验凭证；凭证脚本只增不改。`maps/` 是 `bank.py map` 的生成产物，不入库。

字段全集与难度分级表见 `SPEC.md` 第 2、4 节。

## 版权与文件卫生

- **赛事官方 PDF 随仓库管理。** `docs/sources/*.pdf` 与题库一同提交（远端为私有仓库），换机开箱即用；若日后转为公开仓库，须先把这些 PDF 移出版本库并恢复 gitignore。
- AoPS 只做逐题单页核验，**不做批量爬取**（robots/ToS 禁止，社区解答有作者版权）。
- 批量入库走官方 PDF 与开放数据集（NuminaMath、Omni-MATH 等）。
- 本库题面为中译转录；`SPEC.md` 第 6 节明确：**面向公开分发前需另行评估各赛事题面的版权政策**。默认按非公开仓库对待。
- 不要提交 `__pycache__/`、`*.pyc`、`.DS_Store`。

## 提交约定

- 提交信息用中文，一行主题概括这一批做了什么，正文列要点（参照 `git log` 既有风格：`架构升级 v1.9：……`、`ISL 2024 证明题批次 v1.8：……`）。
- 涉及新题的提交，正文里写清题号范围、来源、核验方式。

## Symphony 自动化

本仓库接入了 [Symphony](https://github.com/openai/symphony)：给 issue 打上 `symphony` 标签，编排器会克隆仓库、开 workspace、派 Codex 去做，产出 PR 后交回人工评审。

- 编排契约见 `WORKFLOW.md`（标签状态机、工作台格式、验收门槛）。
- 可复用的技能在 `.codex/skills/`：`commit` / `push` / `pull` / `land`。
- 启动：`scripts/symphony-start.sh`。

在 Symphony 会话里工作时，`WORKFLOW.md` 的要求覆盖本文件的一般性建议，但**铁律在任何情况下都不放松**。
