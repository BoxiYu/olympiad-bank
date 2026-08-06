# MathNet 全量导出译文契约

本文是 `mathnet-full/` 三语译文产物的**唯一契约正本**。其他文档若需要说明三语产物，只引用本文，
不复制字段、布局或不变量。

本契约只覆盖由 `scripts/mathnet_export.py` 生成的全文视图；正式题库 `problems/` 的入库规范仍以
`SPEC.md` 为准。

## 1. 目录与原文不变量

每道题的真实目录固定为：

```text
by-topic/<板块>/<知识点>/<id>/
├── index.md
├── index.en.md
├── index.zh.md
├── translation.json
└── attached_image_*.png
```

- `index.md` 是 MathNet 逐字原文，路径和内容均不可修改；`index.jsonl.path` 始终指向它。
- `index.en.md`、`index.zh.md` 与原文同构：H1 不丢失，元信息条目数量与顺序不变，
  `## 题面`、`## 解法 N`（无解资料时可为 `## 解法`）、`## 最终答案` 的标题、顺序与数量不变。
- 当前骨架只翻译上述 H2 小节的正文。H1 与元信息区逐字复制，所以 `mathnet_id`、赛事原名、
  候选池 `⚠️` 标记及其他溯源元信息不会漂移；将来若翻译展示层元信息，必须另行扩展本契约。
- 是否存在译文文件不是有效性依据。只有 `translation.json.source_sha256` 等于当前 `index.md`
  的 SHA-256，且目标文件 SHA-256 等于对应 variant 的 `sha256`，该译文才有效。

## 2. 永不翻译的内容

以下内容在导出单元里替换为 `{{MNT_0001}}` 形式的不可译占位，apply 时逐字恢复：

- `$...$`、`$$...$$`、`\begin{...}...\end{...}` 数学环境；
- 行内代码；
- `![](attached_image_N.png)` 图片引用；
- `## 最终答案` 中的纯符号答案，例如 `n ≡ 1 (mod 3)` 或 MCQ 的 `D`。

译文必须让每个占位恰好出现一次且顺序不变，不能增删、复制、改名。纯符号答案单元整体不可翻译。
图片占位因而仍位于同一小节内的同一文本位置，文件名和编号均不变化。

## 3. `translation.json`

成功翻译或直通后的完整形态如下：

```json
{
  "mathnet_id": "0ekm",
  "source_sha256": "<index.md 的 sha256>",
  "source_lang": "sl",
  "source_lang_confidence": "high",
  "variants": {
    "en": {
      "mode": "translated",
      "model": "<模型标识>",
      "generated_at": "<带时区的 ISO8601>",
      "sha256": "<index.en.md 的 sha256>"
    },
    "zh": {
      "mode": "translated",
      "model": "<模型标识>",
      "generated_at": "<带时区的 ISO8601>",
      "sha256": "<index.zh.md 的 sha256>"
    }
  }
}
```

`mode` 只有三种：

- `passthrough`：原文就是目标语言。目标文件逐字复制 `index.md`，`model` 必须为 `null`；
- `translated`：由 `model` 指定的模型生成，必须有完整单元译文；
- `failed`：本次生成失败，不写目标 Markdown。该 variant 记录 `model`、`generated_at`、`error`，
  不记录 `sha256`。

`source_lang` 与 `source_lang_confidence` 在本阶段由外部映射传入或分别使用 `und`、`unknown`
占位；源语言检测由后续模块提供，不在本契约骨架中猜测。

## 4. export JSONL

```bash
uv run python scripts/mathnet_translate.py export \
  --root mathnet-full --out /tmp/mathnet-translate.jsonl
```

一行一题，字段契约为：

```json
{
  "mathnet_id": "0ekm",
  "path": "by-topic/algebra/不等式/0ekm/index.md",
  "source_sha256": "...",
  "source_lang": "sl",
  "source_lang_confidence": "high",
  "units": [
    {
      "id": "statement",
      "section": "题面",
      "source": "... {{MNT_0001}} ...",
      "protected": {"{{MNT_0001}}": "$x^2$"},
      "translatable": true
    }
  ],
  "targets": ["en", "zh"]
}
```

单元 id 固定为 `statement`、`solution_N`、`final_answer`。`targets` 只列仍缺失或已经失效的语言。
有效 target 默认跳过，因此重复 export 幂等。以下任一情况都会重新列入 target：

- `source_sha256` 与当前原文不符；
- variant 不存在或为 `failed`；
- 目标 Markdown 不存在；
- 目标 Markdown 的 SHA-256 与 variant 不符。

`--limit N` 限制题数，`--only ID` 可重复指定小批 id。`--source-lang-map FILE` 接受
`{"id":"en"}`，也接受带 `source_lang`、`source_lang_confidence` 的对象值，给后续语言检测模块留接口。

## 5. apply JSONL

apply 输入沿用 export 的题级字段，把待落盘内容放入 `variants`：

```json
{
  "mathnet_id": "0ekm",
  "path": "by-topic/algebra/不等式/0ekm/index.md",
  "source_sha256": "...",
  "source_lang": "sl",
  "source_lang_confidence": "high",
  "variants": {
    "zh": {
      "mode": "translated",
      "model": "gpt-example",
      "generated_at": "2026-08-06T12:00:00Z",
      "units": {
        "statement": "求 ... {{MNT_0001}} ...",
        "solution_1": "...",
        "final_answer": "{{MNT_0001}}"
      }
    },
    "en": {
      "mode": "failed",
      "model": "gpt-example",
      "generated_at": "2026-08-06T12:00:00Z",
      "error": "provider timeout"
    }
  }
}
```

```bash
uv run python scripts/mathnet_translate.py apply \
  --root mathnet-full --in /tmp/mathnet-translations.jsonl
```

apply 的写入纪律：

- 先重算并核对 `source_sha256`，过期批次拒绝写回；
- 先在内存中验证该题所有 variant、全部单元和占位，任一单元缺失时题目目录零写入；
- `passthrough` 只允许目标语言等于源语言，且直接复制原文字节，不接收模型输出；
- 每个文件均在目标同目录写临时文件、flush、fsync，再以 rename 原子替换；内容不变时不写；
- 重复执行同一输入得到逐字相同的 Markdown 与 JSON；
- 失败逐行写入 `<输入>.failures.jsonl`（可用 `--failures` 改路径），有失败时退出码为 1。

本阶段不执行源语言检测、译文语义保真校验、`index.jsonl` 扩字段或并发模型调用；这些模块只消费或
补充上述 JSONL，不得绕过原文哈希、完整单元与不可译占位三道校验。
