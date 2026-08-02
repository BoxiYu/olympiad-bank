# 题库规范（SPEC）

本文件是全库的**入库标准**。任何新题必须通过 `python3 scripts/bank.py lint` 才算入库。

## 1. 一题一文件

- 路径：`problems/<category>/<ID>.md`，category ∈ `algebra` / `number-theory` / `combinatorics` / `geometry`
- 题号前缀：A- / N- / C- / G-，三位数字，按序顺延，**不得跳号**
- 跨界题选主要考点所在板块，在 `topics` 中并列第二板块关键词

## 2. Frontmatter 字段

```yaml
---
id: A-018            # 必填，与文件名一致
title: 三分式根号不等式  # 必填，简短题名
category: algebra    # 必填
contest: IMO         # 规范化赛事名：IMO/ISL/USAMO/USAJMO/AIME/AMC 8/AMC 10/AMC 12/CMO/高联/华杯赛/…
original_lang: en    # 原文语言：en ｜ zh。en 的题正文必须含「## 原文（English）」小节
year: 2001           # 可为 null（出处无年份时）
system: IMO/ISL      # 备赛体系：小学/初中｜高联/CMO｜AMC体系｜IMO/ISL
source_ref: IMO 2001 Problem 2   # 必填，原始出处字符串
difficulty: 4        # 必填，1–5 整数，标准见第 4 节
topics: [不等式, Hölder, 幂平均]  # 必填，1–4 个，取自 taxonomy/ 知识点树
verification: sourced             # 必填：sourced ｜ independent-derivation
verification_note: ""             # independent-derivation 时必写说明
source_url: https://...           # 必填，可访问链接
compliance: ""       # 非白名单/停办/违规赛事须注明状态
---
```

## 3. 正文结构

必须包含三个小节，顺序固定：

```markdown
# <ID>｜<题名>

## 题面
（忠实中译，LaTeX 行内 $...$；依赖图形的题必须用文字把构造关系交代清楚）

## 原文（English）
（仅 original_lang: en 的题必填：逐字照录官方来源的英文题面，不做任何改写——
它是翻译质量的审计凭证，也供高阶训练者直接阅读原题。原文为中文的题省略此节。）

## 答案
（填空选择给答案；证明题给结论）

## 解法要点
（2–4 句，只点关键突破口，不写完整证明）
```

阶段二起可选增加 `## 完整解答`、`## 多解对比`、`## 同源题链` 小节（★4/★5 优先补齐）。

## 4. 难度分级（★1–★5）

跨小学到 IMO 的统一标尺，**以解法所需思维跨度定级，不以赛事名气定级**。

| 星级 | 定位锚点 | 典型用时 | 关键特征 |
| --- | --- | --- | --- |
| ★1 | 华杯赛初赛、AMC 8 前中段 | 2–5 分钟 | 单步套用公式或一次设元 |
| ★2 | 初中竞赛主体、AMC 10/12 中前段、高联预赛 | 5–12 分钟 | 两三步组合，需一个不显然的转化 |
| ★3 | AIME、高联一试压轴/加试前两题 | 15–30 分钟 | 需调用具名工具，有明确答案可验算 |
| ★4 | CMO 主体、USAMO/USAJMO、IMO P1/P4 | 45–90 分钟 | 完整证明题；主动构造辅助对象或不变量 |
| ★5 | IMO P3/P6、ISL 高难、CMO 压轴、Putnam B6 | 90 分钟以上 | 多层结构叠加，解法本身即一个独立想法 |

定级规则：跨界题取较高星；同构系列题分别定级不继承；有疑义就低不就高并注明「定级存疑」。

## 5. 入库铁律

1. **不编造题目**。每题必须能在权威来源核实原文（AoPS Wiki/Collections、imo-official.org 官方 PDF、赛事官方试卷），附可访问链接。
2. **答案必须有出处**；自行推导的设 `verification: independent-derivation` 并写 `verification_note`。
3. **依赖图形而无法文字复原的题不收录**，宁缺勿假。
4. **赛事合规状态必须标注**（`compliance` 字段）：华杯赛大陆已停办、迎春杯已违规等。
5. **批量导入的题必须过抽样核验**：每批次按 ≥15% 抽样比对官方源，抽样不合格整批退回。

## 6. 来源与版权边界

- 批量入库走：官方 PDF（imo-official / APMO / EGMO / Putnam 等）+ 开放数据集（NuminaMath、Omni-MATH 等）导入。
- AoPS 仅做**逐题单页核验**，不做批量爬取（robots/ToS 禁止；社区解答有作者版权）。
- 本库题面为中译转录，解法要点为原创压缩表述；面向公开分发前需另行评估各赛事题面的版权政策。
