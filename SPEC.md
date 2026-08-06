# 题库规范（SPEC）

本文件是 MathNet 范式下题库的**唯一规范正本**。任何新题必须通过入库校验才算入库；
校验的唯一执行正本是 `bash scripts/lint.sh`（内部调用 `scripts/bank.py lint`，CI 跑的也是它），
必须输出 `LINT OK`。

历史交代：旧 164 题（官方 PDF 核验范式）已整体废弃，随 git tag `legacy-bank-v1.9` 存档可回溯；
在从 `problems/` 清退完成之前处于兼容期，本文标注「legacy」的条款仅为兼容期而保留。
新题一律从 MathNet 数据集（`candidates/mathnet.jsonl` 候选池）抽取，按本文件入库。

## 1. 编号政策

- 一题一文件：`problems/<category>/<ID>.md`，category ∈ `algebra` / `number-theory` / `combinatorics` / `geometry`。
- 题号 = 板块前缀 + 三位数字：`A-`（代数）/ `N-`（数论）/ `C-`（组合）/ `G-`（几何）。
- **各板块从 001 重新起号**，按序顺延、连号**不得跳号**（lint 强制）。不沿用 legacy 题号序列。
- **legacy 同号不同题**：`legacy-bank-v1.9` tag 里的 `A-001` 与现库的 `A-001` 是两道无关的题。
  题号不是跨代稳定标识——引用现库的题以 `mathnet_id` 为准；引用 legacy 的题必须带 tag
  （如「legacy-bank-v1.9 的 A-039」），不得裸用题号。
- 跨界题归入主要考点所在板块，在 `topics` 中并列第二板块的知识点。

## 2. Frontmatter 字段语义

新题模板（字段齐全、顺序照此）：

```yaml
---
id: G-001                    # 与文件名一致；编号政策见 §1
title: Tangent Circumcircles from Incircle Chord   # 英文短标题（题面语言即英文，见 §3）
category: geometry
contest: Bulgarian National Olympiad - Final Round # 原始赛名：照录 MathNet contest 字段，不做归一化
year: 2015                   # 可为 null（数据集无年份时）
source_ref: MathNet 03g0     # 固定格式 "MathNet <mathnet_id>"
difficulty: 4                # 1–5 整数，分级标准见 §4
difficulty_note: 需构造公共切点并串联螺旋相似与 Menelaus，突破口不常见  # 定级依据一句
topics: [圆与四点共圆, 共线共点定理]   # 1–4 个中文规范节点，正名见 taxonomy/registry.yml
verification: mathnet-reviewed
mathnet_id: 03g0             # 必填溯源字段：MathNet 数据集内的行 id（铁律 2）
review_ref: data/review/pilot-01/verdicts.json   # 评审凭证路径（铁律 3），lint 校验其真实覆盖本题
source_url: https://huggingface.co/datasets/ShadenA/MathNet
---
```

字段语义：

- `id` / `category`：见 §1。id 必须与文件名、板块前缀一致（lint 强制）。
- `title`：英文短标题，供检索与题卡显示；不是题面的一部分，可自拟。
- `contest` / `year`：数据集原始赛名与年份，**照录不改写**——它们是「数据集声称」（铁律 4），
  不承担核验职能，仅供检索与定级参考。
- `source_ref`：人读的溯源字符串，固定 `MathNet <mathnet_id>`。机器溯源以 `mathnet_id` 为准。
- `difficulty` / `difficulty_note`：定级结论与一句定级依据，标准见 §4。`difficulty_note` 新题必写。
- `topics`：中文规范节点列表；新词须先在 `taxonomy/registry.yml` 注册，否则 lint 告警。
- `verification`：**合法取值以 bank.py lint 为准**（本文不抄录枚举，防两处失同步）。各值语义：
  - `mathnet-reviewed`——新题唯一档位：Codex 逐题评审通过，凭证已落盘且 `review_ref` 指向之；
  - `sourced` / `independent-derivation`——legacy 档位，兼容期保留，新题不得使用。
- `mathnet_id`：必填溯源字段，MathNet 数据集中该题的行 id；与数据集 revision 共同构成
  溯源坐标（铁律 2）。
- `review_ref`：指向 `data/review/<batch>/verdicts.json` 的仓库相对路径。lint 校验该文件存在
  **且其中含有本题的 mathnet_id**——指向不覆盖本题的凭证 = 未评审（铁律 3、4）。
- `source_url`：数据集主页 `https://huggingface.co/datasets/ShadenA/MathNet`（新题固定值；
  精确溯源靠 mathnet_id + revision，不靠 URL）。
- `machine_check_ref`（**可选**）：指向 `data/verify/<batch>/results.json` 的机器核验台账路径。
  仅数值/闭式答案题适用（证明题不进本机制）；是 review_ref 之外的**补充凭证**，不改变
  verification 档位、不是入库门槛。挂了此字段的题，lint 校验台账存在、含本题且 status=pass；
  凭证保真由 CI 重跑核验脚本保证——机制正本在 `scripts/checks/run_checks.py` 头注。
- legacy 专用字段已随旧题整体清退；新题字段全集以上述模板及可选的
  `machine_check_ref` 为准。历史字段定义可从 `legacy-bank-v1.9` tag 回溯。

## 3. 正文各节语义

小节的**准入名单以代码白名单为契约正本**，本节只写语义、不抄名单：

- 必需节名单（lint 强制存在）：`scripts/bank.py` 的 `SECTIONS`；
- 允许节名单（spar 出卡解析白名单，防答案泄进题卡）：`scripts/spar_session.py` 的 `KNOWN_SECTIONS`。

想新增小节必须**先改代码白名单再入库**，否则 spar 拒绝解析该题——这是防泄答机制，不是官僚流程。

正文结构与各节语义（标题行 + 必需三节，顺序固定）：

```markdown
# <ID>｜<title>

## 题面
（MathNet problem_markdown 英文原文直用：忠实转录、不翻译、不改写、不「语义复原」。
小节标题保留中文，作为结构记号。依赖图形而无法文字复原的题不收，见铁律 5。）

## 答案
（数据集 final_answer 照录；证明题写「证明题」。）

## 解法要点
（MathNet 官方解 solutions_markdown 拼接，多解之间以分隔线相接；照录原文，
可截取但不做 AI 改写。评审发现的解答笔误可在文末以引注标明，不得静默修改正文。）
```

- 可选节：`## 提示阶梯`（spar hint 的提示源，按 `1.` `2.` 编号分级）；`## 核验`（legacy 核验记录）。
- `## 原文（English）` 是 legacy 专属小节（旧范式中英对照的产物）：**新题不再设此节**——
  题面本身就是英文原文。该节已随 legacy 题清退从允许名单与题卡名单中收回。

## 4. 难度分级（★1–★5）

跨初中到 IMO 的统一标尺，**以解法所需思维跨度定级，不以赛事名气定级**。

**学段范围（本节是正本）：本库只收初中与高中数学竞赛，下界是初中竞赛主体（★2）。**
★1 档保留在标尺里只为让定级有一个"低于准入线"的落点——**判为 ★1 即不予入库**，
不是"收进来标个一星"。上界不设限：IMO/ISL/RMM 属高中范畴，★5 照收。

| 星级 | 定位锚点 | 典型用时 | 关键特征 |
| --- | --- | --- | --- |
| ★1 | 小学/低龄档：华杯赛初赛、AMC 8 前中段、MATHCOUNTS 主体 | 2–5 分钟 | 单步套用公式或一次设元；**低于准入线，不入库** |
| ★2 | 初中竞赛主体、AMC 10/12 中前段、高联一试主体、HMMT November | 5–12 分钟 | 两三步组合，需一个不显然的转化 |
| ★3 | AIME、高联一试压轴/加试前两题、HMMT February、东南赛/西部赛、Balkan MO | 15–30 分钟 | 需调用具名工具或一个非显然引理，有明确答案可验算 |
| ★4 | 高联加试后两题、CMO 主体、USAMO/USAJMO、IMO P1/P4、APMO、EGMO | 45–90 分钟 | 完整证明题；主动构造辅助对象或不变量 |
| ★5 | IMO P3/P6、ISL 末位、CMO 压轴、RMM、TST、Putnam B6 | 90 分钟以上 | 多层结构叠加，解法本身即一个独立想法 |

定级规则：

- 输入有三：规则层估级 `difficulty_est`（赛名→底档映射见 `taxonomy/contest_tiers.yml`，
  它只管映射，星级语义以本节为正本）、Codex 评审 `difficulty_codex`、入库者自己读题的判断。
  **est 永远不是定级**；定稿写入 `difficulty`，依据一句写入 `difficulty_note`。
- 跨界题取较高星；同源系列题分别定级不继承；有疑义**就低不就高**并在 `difficulty_note` 注明「定级存疑」。
- 攻坚限时（★n ≤ 多少分钟）是训练契约，正本在 `scripts/spar_session.py` 的 `TIME_LIMIT`，
  与本表「典型用时」是两回事，不要混用。

## 5. 入库铁律 v2（不可协商）

1. **不编造题目。** 题面、答案、解法必须逐字来自 MathNet 数据集中实际存在的行；
   缺符号、缺句子的残缺文本**不得用 AI「按语义复原」**——legacy 审计抓出的两道实质错误题
   （不等号反向、凭空造出分式）全部源于这种复原（见 docs/archive/审计存档-旧题库核验真相-2026-08.md）。
   残缺就是不收，见第 5 条。
2. **来源 = MathNet 数据集坐标。** 每题的溯源单位是 `mathnet_id` + 数据集 revision，
   不是外部网页 URL。`mathnet_id` 必填（§2）；revision 记录在评审批次目录
   `data/review/<batch>/` 的批次文件中，题内不重复抄录。数据集之外的任何转述都不构成溯源。
3. **100% Codex 逐题评审，凭证落盘。** 每道新题入库前必须经 `scripts/mathnet_review.py`
   通道由 Codex 逐题评审并 `recommend: claim`；评审产出 `verdicts.json` 必须落盘在
   `data/review/<batch>/`，题内 `review_ref` 指向该文件且文件必须覆盖本题（lint 校验）。
   **没有落盘凭证 = 没有评审**，抽样评审不满足本条——是逐题，不是抽查。
4. **数据集声称 ≠ 已核验。** MathNet 自带的赛名、年份、难度、标签、答案都只是「声称」；
   只有经过第 3 条评审并留下凭证的字段才算核验过。legacy 审计的结构性教训：
   83% 的「已核验」自报没有证据链，**裸声明字段不再被信任**——本库不设任何
   「写个字段就算核验」的通道。
5. **依赖图形而无法文字复原的题不收；坏题宁缺勿滥。** 评审判定 `needs_figure: true`、
   `text_quality: broken` 或 `recommend: skip` 的题一律不收。拿不到干净可靠的题时，
   **少交付是可接受的结果，凑数不是**。

## 6. 来源与版权

- **项目原创软件代码**采用 GNU Affero General Public License v3.0 or later（AGPL-3.0-or-later），
  许可正本为仓库根目录 `LICENSE`。该许可允许商业使用，但要求修改版经网络向用户提供服务时，
  按许可向这些用户提供对应源码；它只覆盖项目有权许可的软件部分。
- **项目拥有权利的原创教学与文档内容**采用 CC BY-SA 4.0，适用范围与排除项以
  `LICENSE-CONTENT.md` 为准。它允许传播、改编和商业使用，但要求署名及相同方式共享；
  第三方材料、公共领域事实和项目无权许可的内容不进入该授权范围。
- 项目名称、Logo 与其他标识不随代码或内容许可授权；合理署名和描述来源除外。
- MathNet（`ShadenA/MathNet`，Hugging Face）数据集以 CC-BY-4.0 发布，但这是**混合权利**局面：
  数据集许可覆盖的是其编排与转录，**不等于底层竞赛题面与官方解的版权**——各赛事对题面
  另有各自的权利政策。两层权利须分开对待。
- 本仓库当前在 GitHub **公开可见**；公开可见不改变第三方材料的权利状态，也不表示项目有权
  重新许可底层竞赛题面与官方解。引用数据集时保留署名（source_url + mathnet_id）。
- 继续复制、再发布或扩大分发前，必须逐赛事核查题面版权政策，并重新审视数据集许可与
  底层权利的兼容性；仓库可访问性不能替代这项评估。
- 第三方署名、许可与排除说明集中记录在 `THIRD_PARTY_NOTICES.md`；该文件是告知与溯源清单，
  不替任何第三方权利人授予许可。
- 不对 AoPS 等社区站做批量爬取（robots/ToS 禁止，社区解答有作者版权）；
  本库供给链只有 MathNet 一条（决策①），不再维护官方 PDF 抓取路径。

## 7. 附录：规则归属表

每条规则只活在一个正本，其余位置只准放指针（引用不抄录）。改规则 = 改正本 + 检查引用处，
**新增规则必须同时在本表登记**。

| 规则 | 正本位置 | 引用/执行位置 |
| --- | --- | --- |
| 编号政策（前缀、001 起号、连号） | SPEC §1 | `bank.py lint`（前缀/连号校验） |
| frontmatter 字段语义 | SPEC §2 | `bank.py lint`（必填/类型/凭证校验） |
| verification 合法枚举 | `spar_session.py` `VALID_VERIFICATION` | `bank.py lint` 与 spar 出卡执行；SPEC §2 只写语义 |
| 必需小节名单 | `bank.py` `SECTIONS` | SPEC §3（语义） |
| 允许小节名单（防泄答白名单） | `spar_session.py` `KNOWN_SECTIONS` | SPEC §3（语义）；spar 解析时执行 |
| 题卡小节名单 | `spar_session.py` `CARD_SECTIONS` | spar 出卡与 web 训练台题面渲染执行 |
| 训练台页面防泄答（未解锁的提示/答案不出服务端） | `scripts/web_app.py`（渲染仅经 spar_session 白名单通道） | `tests/test_web.py` 锁定 |
| 训练台错误文案改写（网页上不出现任何命令行指令） | `scripts/web_app.py` `_web_err`（按 SparError 前缀改写，**不改 spar_session 正本**，CLI 仍用原文） | `tests/test_web.py::TestNoCliLeak` 锁定 |
| 训练台写操作的会话号校验（过期表单不得记到别的题上） | `scripts/web_app.py` `_require_session`（四个 POST 均带 sid 隐藏域） | `tests/test_web.py::TestSessionGuard` 锁定 |
| 训练台界面用语（大白话层） | `scripts/web_templates/` 与 `web_app.py` `RESULT_TEXT`/`STUCK_TEXT`/`TARGET_HINT` | 术语语义正本仍在 docs/ 两手册与 `spar_session.py`；界面只做展示层翻译 |
| 难度星级语义与锚点 | SPEC §4 | `taxonomy/contest_tiers.yml` 头注、`mathnet_review.py` 评审提示 |
| 学段范围（初中+高中，★1 不入库） | SPEC §4（语义）+ `bank.py` `MIN_DIFFICULTY`（阈值）+ `bank.py apply_grade_floor`（过滤器） | 硬闸：`mathnet_import.py below_floor`（写盘前准入）、`mathnet_review.py batch`、`candidates --gaps`、`bank.py lint`（最终门槛）；`bank.py candidates` 只作默认值（浏览工具允许显式看低档）；`docs/入库SOP-MathNet.md`、`contest_tiers.yml` 头注为引用 |
| 赛名→难度底档映射 | `taxonomy/contest_tiers.yml` | `mathnet_ingest.py` 估级管线 |
| 候选池时效与输入规则变更后的重建纪律 | `.claude/skills/ingest/SKILL.md`「实测坑」第 5 条 | `docs/入库SOP-MathNet.md` §1、`CLAUDE.md`「gitignore 与重建」 |
| 送审→入库转化率的排批校准 | `docs/入库SOP-MathNet.md` §2「排批量校准」 | `.claude/skills/ingest/SKILL.md`「实测坑」第 6 条 |
| 规则估级在低星段的系统性偏差 | `docs/入库SOP-MathNet.md` §2「低星段估级偏差」 | `.claude/skills/ingest/SKILL.md`「实测坑」第 7 条 |
| 知识点正名与别名 | `taxonomy/registry.yml`（含检索别名） | `bank.py lint` 告警、map/query |
| 知识点前置依赖图（教学建议边） | `taxonomy/prereq.yml`（语义见其头注） | `bank.py doclint`（端点/DAG 校验）、`bank.py map`（指示图前置 chips）、`student_profile.gap_queue`（补齐队列按上游优先） |
| 攻坚限时/提示冷却/复习间隔/毕业条件 | `spar_session.py` 契约常量（`TIME_LIMIT`/`HINT_COOLDOWN_MIN`/`INTERVALS`/`GRADUATE_STREAK`） | docs/ 两手册为方便两类读者而完整抄录，属刻意例外，由 `bank.py doclint` 数值校验兜底；`bank.py` coach/review、`web_app.py`（数值由服务端下发，前端不复制） |
| 机器核验凭证（machine_check_ref 与台账保真） | `scripts/checks/run_checks.py` 头注 | `bank.py lint`（凭证覆盖校验）、CI「机器核验」步骤（重跑保真）、SPEC §2（字段语义指针）、`data/verify/` 台账（必须提交） |
| 入库铁律 v2 | SPEC §5 | AGENTS.md（路由指针） |
| 版权边界 | SPEC §6 | AGENTS.md（路由指针） |
| lint 执行命令 | `scripts/lint.sh` | CI workflow、AGENTS.md |
| doclint 禁词表 | `scripts/bank.py` `DOCLINT_FORBIDDEN` | `scripts/bank.py` `doclint` 执行 |
| 外链纪律（死链处置与归档） | `docs/入库SOP-MathNet.md` 凭证纪律节 | `bank.py linkcheck`（执行）、`.github/workflows/linkcheck.yml`（月度 CI） |
| 训练日志 v2 数据契约 | `spar_session.py` 模块 docstring | `tests/test_learning_loop.py` 锁定 |
| 学生档案数据契约 / 能力图折算与阈值 | `student_profile.py` 模块 docstring（契约常量） | docs/教练手册.md（用法）；`tests/test_student_profile.py` 锁定 |
| 题面语言闸门（非英文拒收） | `mathnet_import.looks_english` | SPEC §3（语义）、`tests/test_import_pipeline.py` 锁定 |
| 规则归属表（本表） | SPEC §7 | ——（本文件自身也受本表约束） |
