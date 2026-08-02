# 奥林匹克数学竞赛题库

按「知识点 × 难度」双维组织的中文奥数题库。当前 **131 道已核实真题**，覆盖小学/初中 → 高联/CMO → AMC-AIME-USAMO → IMO/ISL 四大体系。架构按 10000 题规模设计。

## 目录结构

```
problems/            一题一文件（YAML frontmatter + 题面/答案/解法要点）
  algebra/           A-001 ~ A-035
  number-theory/     N-001 ~ N-033
  combinatorics/     C-001 ~ C-032
  geometry/          G-001 ~ G-031
taxonomy/            四板块知识点树（二级子类 + 典型考法）
docs/                赛事地图与官方题源（30+ 赛事赛制/难度/题源链接）
scripts/             bank.py（lint/query/stats）、migrate.py（历史迁移，一次性）
SPEC.md              入库规范：字段定义、难度标准、入库铁律
```

## 常用命令

```bash
python3 scripts/bank.py lint                       # 入库校验（CI 必跑）
python3 scripts/bank.py stats                      # 难度 × 板块 / 体系分布
python3 scripts/bank.py query --difficulty 4       # 按难度检索
python3 scripts/bank.py query --topic 韦达         # 按知识点检索
python3 scripts/bank.py query --contest IMO --category geometry
python3 scripts/bank.py query --unverified         # 列出待二次复核的题
```

依赖：Python 3 + PyYAML（`pip install pyyaml`）。

## 四维建设路线

| 维度 | 现状 | 下一步 |
| --- | --- | --- |
| 核验可信度 | 131 题全部溯源；6 题标记独立推导待复核 | 复核 6 题；lint 加入链接可达性检查 |
| 覆盖广度 | 131 题 | 阶段一 500 题：优先补 ISL 近十年、高联加试、APMO/EGMO |
| 解答深度 | 解法要点（2–4 句） | ★4/★5 逐步补「完整解答 + 多解对比 + 同源题链」 |
| 训练系统 | query 脚本 | 按目标赛事生成刷题清单；错题记录与间隔复习 |

## 已知边界

- 高联加试/CMO/TST 无官方在线 PDF，补题需《中等数学》或 CNKI（付费），AoPS 转录交叉校验。
- `system` 字段按 contest 确定性归类（AMC 8 归小学/初中）；与旧版总览的体系统计口径略有差异。
- 华杯赛大陆赛事已停办、迎春杯已被认定违规——仅作训练素材，元数据已标注。
