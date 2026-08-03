# 奥林匹克数学竞赛题库

按「知识点 × 难度」双维组织的中文奥数题库 + 训练系统。当前 **164 道已核实真题**，覆盖小学/初中 → 高联/CMO → AMC-AIME-USAMO → IMO/ISL 四大体系。架构按 10000 题规模设计。训练用法见 `docs/教练手册.md`。

## 目录结构

```
problems/            一题一文件（YAML frontmatter + 题面/答案/解法要点）
  algebra/           A-001 ~ A-044
  number-theory/     N-001 ~ N-042
  combinatorics/     C-001 ~ C-038
  geometry/          G-001 ~ G-040
taxonomy/            四板块知识点树（二级子类 + 典型考法）
docs/                赛事地图与官方题源（30+ 赛事赛制/难度/题源链接）
scripts/             bank.py（lint/query/stats/plan + coach/log/review 教练闭环）、isl2024_*.py（程序验证）、browser_pdf_extract.js（官方 PDF 抽取管线）
SPEC.md              入库规范：字段定义、难度标准、入库铁律
```

## 常用命令

```bash
uv run python scripts/bank.py lint                       # 入库校验（CI 必跑）
uv run python scripts/bank.py stats                      # 难度 × 板块 / 体系分布
uv run python scripts/bank.py query --difficulty 4       # 按难度检索
uv run python scripts/bank.py query --topic 韦达         # 按知识点检索
uv run python scripts/bank.py query --contest IMO --category geometry
uv run python scripts/bank.py query --unverified         # 列出待二次复核的题

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
| 训练系统 | coach 周计划 / log 攻坚记录 / review 间隔复习闭环（docs/教练手册.md） | 攻坚数据（含提示用量）积累后出诊断报告与自适应计划 |

## 已知边界

- 高联加试/CMO/TST 无官方在线 PDF，补题需《中等数学》或 CNKI（付费），AoPS 转录交叉校验。
- `system` 字段按 contest 确定性归类（AMC 8 归小学/初中）；与旧版总览的体系统计口径略有差异。
- 华杯赛大陆赛事已停办、迎春杯已被认定违规——仅作训练素材，元数据已标注。
