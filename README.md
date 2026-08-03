# 奥林匹克数学竞赛题库

> **项目使命：用 AI 促进数学教育，帮助学生高效地学习数学。** 本仓库是这一目标的地基——AI 教学的可靠性取决于题目与答案的可靠性，所以先建成一个每道题都可溯源核实的题库，再在其上生长训练系统（教练闭环、诊断报告、自适应计划）。

按「知识点 × 难度」双维组织的中文奥数题库 + 训练系统。当前 **164 道已核实真题**，覆盖小学/初中 → 高联/CMO → AMC-AIME-USAMO → IMO/ISL 四大体系。架构按 10000 题规模设计。训练用法见 `docs/学生手册.md`（学生一页流程）与 `docs/教练手册.md`（设计原理）。

## 目录结构

```
problems/            一题一文件（YAML frontmatter + 题面/答案/解法要点）
  algebra/           A-001 ~ A-044
  number-theory/     N-001 ~ N-042
  combinatorics/     C-001 ~ C-038
  geometry/          G-001 ~ G-040
taxonomy/            四板块知识点树 + registry.yml 正名注册表 + MathNet/赛事分层映射表
docs/                赛事地图与官方题源（30+ 赛事）、学生手册、教练手册、sources/ 官方 PDF 弹药库
scripts/             bank.py（唯一 CLI 入口：lint/query/stats/plan/map/candidates + coach/spar/log/review/similar 训练闭环）
  spar_session.py    spar 会话流程与训练日志 v2 契约（bank.py 的辅助模块）
  similar_index.py   相似度索引：embedding + 公式指纹（bank.py similar 的后端；build 需直接调用）
  mathnet_ingest.py / mathnet_review.py   MathNet 候选池构建与 Codex 第二意见评审
  browser_pdf_extract.js                  官方 PDF 抽取管线
  verify/            一次性程序验证脚本（题目 verification_note 引用的核验凭证，只增不改）
  archive/           已完成使命的一次性脚本（如初始迁移 migrate.py）
data/attempts.jsonl  攻坚记录（追加式 JSONL，spar finish 自动写入；入 git）
data/plan.json       本周题单（入 git）
data/sessions/       spar 攻坚会话目录（无答案题卡/逐级提示/复盘；gitignore 不入库）
data/similar/        相似边台账 edges.jsonl（duplicate/near_isomorphic/same_method/related，人工确认；入 git）
data/review/         Codex 跨模型评审通道的批次与裁决记录
maps/                bank.py map 生成的交互式知识点指示图（生成产物，不入库）
SPEC.md              入库规范：字段定义、难度标准、入库铁律（本仓库最高约束）
WORKFLOW.md          Symphony 派单代理配置：按 GitHub Issue 自动开工作区跑批
AGENTS.md            给编码代理（Claude Code / Codex）的常驻工作说明
```

## 常用命令

```bash
uv run python scripts/bank.py lint                       # 入库校验（CI 必跑）
uv run python scripts/bank.py stats                      # 难度 × 板块 / 体系分布
uv run python scripts/bank.py query --difficulty 4       # 按难度检索
uv run python scripts/bank.py query --topic 韦达         # 按知识点检索
uv run python scripts/bank.py query --contest IMO --category geometry
uv run python scripts/bank.py query --unverified         # 列出待二次复核的题

# 训练闭环（学生流程见 docs/学生手册.md）
uv run python scripts/bank.py spar next                  # 开无答案题卡（复习到期 > 周计划；指定题：spar start A-037）
uv run python scripts/bank.py spar hint                  # 逐级解锁提示（写 hints/hint-N.md）
uv run python scripts/bank.py spar reveal                # 亮出解法要点（写 solution.md，之后须合卷复述）
uv run python scripts/bank.py spar finish                # 登记结果（四分＋卡点标签），追加 data/attempts.jsonl
uv run --group similar python scripts/similar_index.py build --bank-only   # 建相似索引（similar 查询的前置，建一次即可）
uv run python scripts/bank.py similar A-037              # top-k 相似候选（关系/置信度/依据）；--confirm --relation 登记确认边

# MathNet 候选池（外部筛题索引，不属正式题库；入库须官方源核验）
uv run --group mathnet python scripts/mathnet_ingest.py  # 构建候选池（读两张 taxonomy 映射表）
uv run python scripts/bank.py candidates --category geometry --difficulty 2-3 --node 共线共点定理
uv run python scripts/bank.py candidates --gaps          # 45 节点缺口采购单：库内 vs 候选
```

依赖由 `pyproject.toml` + `uv.lock` 声明（Python ≥3.11 + PyYAML），装好 [uv](https://docs.astral.sh/uv/) 后 `uv run` 会自动解决，无需手动装包。

## 四维建设路线

| 维度 | 现状 | 下一步 |
| --- | --- | --- |
| 核验可信度 | 164 题全部溯源（题面/答案官方 sourced）；ISL 2024 题面与答案终审关闭；新入题解法要点为官方证明压缩 | lint 加入链接可达性检查；教练梗概逐步扩充为完整证明 |
| 覆盖广度 | 164 题；ISL 2024 A1–A8、C1–C8、G1–G8、N1–N7 全部入库（31 条相关条目，含 IMO P1–P6 全六题） | 阶段一 500 题：2015–2023 逐年跑抽取管线、高联加试、APMO/EGMO |
| 解答深度 | 解法要点 + ISL 2024 全系三级提示阶梯 | ★4/★5 逐步补「完整解答 + 多解对比 + 同源题链」 |
| 训练系统 | coach 周计划 / spar 攻坚会话（四分结果＋卡点标签）/ review 间隔复习与毕业闭环（docs/学生手册.md、docs/教练手册.md） | attempts.jsonl ≥50 条后出诊断报告与自适应计划；similar 相似边台账支撑变式训练 |

## 已知边界

- 高联加试/CMO/TST 无官方在线 PDF，补题需《中等数学》或 CNKI（付费），AoPS 转录交叉校验。
- `system` 字段按 contest 确定性归类（AMC 8 归小学/初中）；与旧版总览的体系统计口径略有差异。
- 华杯赛大陆赛事已停办、迎春杯已被认定违规——仅作训练素材，元数据已标注。
