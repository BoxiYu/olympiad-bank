你是奥数题库的候选题评审员。评审批次目录：data/review/nt-08/
（你可能被置于仓库根目录运行；batch.json 在 data/review/nt-08/batch.json，verdicts.json 也必须写到 data/review/nt-08/verdicts.json，
不要碰其它批次目录。）读该 batch.json（12 道 MathNet 候选题，含题面、
部分官方解摘录、我方规则层给出的板块/知识点/难度估级），逐题独立评估，把结果写成 verdicts.json。

难度标尺（★1–5，按解法所需思维跨度定级，不按赛事名气；有疑义就低不就高）：
★1 单步套用定义/公式，2–5 分钟（AMC 8 前中段）
★2 两三步常规组合，需选对工具（AMC 10/12、各国初轮）
★3 需要一个非显然的想法或引理（AIME、国家二轮）
★4 需构造/多引理串联，突破口不常见（IMO P1/P4、USAMO、CMO）
★5 需深刻洞察或长链论证（IMO P3/P6）

每题一个对象，字段严格如下：
{"mathnet_id":"...",
  "short_title":"≤8 词英文短标题：名词短语概括题目核心对象与性质，入库时直接用作 title",
  "difficulty_codex":1-5 整数,
  "difficulty_reason":"一句话：关键突破口与思维跨度",
  "topics_verdict":"agree|partial|wrong", "topics_comment":"标签是否贴切；不贴切说明该往哪个方向",
  "text_quality":"clean|minor_issues|broken", "text_comment":"转录质量：LaTeX 完整性、OCR 残缺、题意自洽性",
  "needs_figure":true|false, "figure_comment":"是否依赖图形且无法用文字复原（题库铁律：此类不收录）",
  "recommend":"claim|skip", "recommend_reason":"一句话结论"}

要求：
- 只输出这 12 个对象组成的 JSON 数组，写入 verdicts.json，不要创建其他文件。
- 独立判断：不要因为 est 字段就顺从我方估级——分歧正是本次评审的价值所在。
- 题面/解答分别评价：MathNet 的题面通常干净，解答常有 OCR 与 LLM 转写瑕疵，如实分别记录。
- **`solution_head` 是按字符数截断的摘录，不是完整解答**（入库时读的是数据集原文全文）。
  因此「解答在此处戛然而止」不构成质量缺陷，不要据此判 minor_issues——只评摘录里**已出现**的内容
  有没有 OCR 残缺、符号丢失、论证错误。题面 `problem` 同样可能被截断，同理处理。
