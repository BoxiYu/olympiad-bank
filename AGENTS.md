# AGENTS.md

> **本文件不持有规则正本**，只做路由：每条规则的正本活在别处，这里只给指针。

## 定位

用 AI 促进数学教育：按「知识点 × 难度」组织的奥数题库 + 训练系统，题源为 MathNet 数据集。
核心资产是「每一道题都可被追溯核实」——一切取舍以学生学习效率与可信度为准。

## 唯一硬门槛

```bash
bash scripts/lint.sh
```

必须输出 `LINT OK`。这个脚本是 lint 的唯一执行正本，别处不要另写 lint 命令。

## 铁律（正本：SPEC.md 第 5 节，此处只列条目名）

1. 不编造题目
2. 答案必须有出处
3. 依赖图形而无法文字复原的题不收录
4. 合规状态必须标注
5. 批量导入必须过抽样核验

## 任务路由表

| 你要做的事 | 去读 |
| --- | --- |
| 写题 / 入库 | `docs/入库SOP-MathNet.md` |
| 训练（coach/spar/log/review/similar） | `docs/学生手册.md` + `docs/教练手册.md` |
| git 操作（commit/push/pull/land） | `.codex/skills/` 对应 skill |
| Symphony 编排会话 | `WORKFLOW.md` |
| CLI 用法 | `uv run python scripts/bank.py --help` |

MathNet 入库单走本地 `codex exec`，**不派 Symphony**（Symphony 只接文档/代码类工单）。
