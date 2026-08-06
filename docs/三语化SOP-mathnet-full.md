# MathNet 全量语料三语化 SOP

本文只说明 `mathnet-full/` 的本地操作顺序。译文目录、JSONL 字段、哈希、直通条件与保真要求的
唯一正本是 [译文契约](译文契约-mathnet-full.md)，这里不复述；依赖组的唯一约定见
[CLAUDE.md](../CLAUDE.md)「uv 依赖组：用错就 ImportError」。

## 1｜前置条件

- 所有命令都从仓库根目录运行，并使用仓库约定的 `uv` 环境。
- 本地必须已有 `mathnet-full/`。该目录不随 clone 分发；缺失时按
  [CLAUDE.md](../CLAUDE.md)「gitignore 与重建」中的 `mathnet-full/` 重建项处理。
- `run` 会自动寻找本机的 `codex-companion`；找不到时会在调用模型前退出并给出错误。
- `mathnet-full/`、翻译工作目录和译文都是 gitignore 派生物，真实全量运行及断点文件只留在本机。

脚本参数以当前分支的帮助输出为准。升级脚本后先重新核对：

```bash
uv run python scripts/mathnet_translate.py export --help
uv run python scripts/mathnet_translate.py run --help
uv run python scripts/mathnet_translate.py apply --help
uv run --group mathnet python scripts/mathnet_export.py --help
uv run python scripts/bank.py mathnet-search --help
uv run python scripts/checks/check_translation_contract.py --help
```

## 2｜先做小批试跑

首次运行或模型、提示词、脚本有变化时，先让 10 题通过严格模式。这里沿用默认工作目录
`mathnet-full/.mathnet-translate-run/`，后续全量会复用已完成结果。

```bash
uv run python scripts/mathnet_translate.py run \
  --root mathnet-full \
  --limit 10 \
  --batch-size 5 \
  --concurrency 1 \
  --strict
```

`--strict` 适合烟雾测试：首个批次或题级失败便停止，避免错误扩散。命令成功后刷新索引，再检查
产物契约与仓库硬门槛：

```bash
uv run python scripts/mathnet_export.py --out mathnet-full --refresh-index
uv run python scripts/checks/check_translation_contract.py --sample 10
bash scripts/lint.sh
```

还应分别查看英文、中文失败项和过期项。试跑刚成功时，下列查询都应显示 `共 0 题`：

```bash
uv run python scripts/bank.py mathnet-search --lang en --coverage failed --limit 20
uv run python scripts/bank.py mathnet-search --lang zh --coverage failed --limit 20
uv run python scripts/bank.py mathnet-search --coverage stale --limit 20
```

任一检查失败时先处理第 5、6 节，不进入全量。

## 3｜全量运行

小批通过后去掉 `--limit` 与 `--strict`。默认值本来就是每批 25 题、4 个并发，这里显式写出，
方便运行日志与手册互相核对：

```bash
uv run python scripts/mathnet_translate.py run \
  --root mathnet-full \
  --batch-size 25 \
  --concurrency 4
```

全量模式会继续处理其他批次，并在最后以退出码 1 汇总任何失败。标准输出持续给出批次、题目、
题目×目标语言三层进度以及 ETA；最终退出码为 0 才表示本轮没有遗留失败。

日常全量流程优先使用 `run`，因为它负责生成语言图并串起 export、模型调用与 apply。需要审计
中间 JSONL 或接入外部批处理时，可单独调用两端；输入输出结构仍只查译文契约：

```bash
uv run python scripts/mathnet_translate.py export \
  --root mathnet-full \
  --out mathnet-full/.mathnet-translate-run/manual-export.jsonl \
  --source-lang-map mathnet-full/.mathnet-translate-run/source-lang-map.json

uv run python scripts/mathnet_translate.py apply \
  --root mathnet-full \
  --in mathnet-full/.mathnet-translate-run/manual-translations.jsonl \
  --failures mathnet-full/.mathnet-translate-run/manual-apply-failures.jsonl
```

第二条命令要求上游已经按译文契约生成 `manual-translations.jsonl`，不能把 export 文件原样当作
apply 输入。

## 4｜断点续跑

收到中断信号或进程异常退出后，直接重跑第 3 节的同一条 `run` 命令。默认工作目录中的关键状态是：

- `.translate-progress.json`：题目与批次两层进度；
- `.translate-failures.jsonl`：尚未消除的批次或目标语言失败；
- `source-lang-map.json`：以原文哈希为键复用的语言检测结果；
- `batches/`：可复用或重新核对的批次暂存文件。

驱动会跳过已经有效的目标译文、重新核对中断批次，并只补剩余工作。不要为了“从断点继续”更换
`--work-dir`、`--progress` 或 `--failures` 路径；显式使用这些参数时，续跑也必须保持相同路径。

## 5｜失败清单

默认失败清单是：

```text
mathnet-full/.mathnet-translate-run/.translate-failures.jsonl
```

先按每行的 `scope`、`mathnet_id`、`target_lang` 与 `error` 判断是整批调用失败，还是单题写回/
保真失败。临时服务错误可直接重跑第 3 节命令；驱动只重新导出无效目标，成功后会从失败清单
清除对应记录。只想重试少数题时，从失败清单取得 id，并用 `run --only ID`；`--only` 可重复。

如果同一错误稳定复现，先修原文之外的调用或译文生成问题，再重跑。`index.md` 是输入，不通过
手工改原文来让译文检查变绿；原文与译文边界见第 8 节。

## 6｜发现并重译过期译文

原文变化后，先刷新索引，再查 `stale`：

```bash
uv run python scripts/mathnet_export.py --out mathnet-full --refresh-index
uv run python scripts/bank.py mathnet-search --coverage stale --limit 20
```

`stale` 非零表示译文记录的 `source_sha256` 与当前 `index.md` 不一致。直接重跑第 3 节命令；驱动会
丢弃相应旧进度，把过期目标重新 export、重新翻译并 apply。完成后再次刷新索引并重复查询，直到
`共 0 题`。若只处理清单中的个别 id，使用可重复的 `--only ID`。

apply 也会拒绝写入原文哈希已经变化的旧批次；不要绕过拒绝，重新 export 才能得到当前输入。

## 7｜全量收尾与校验

翻译结束后先重建 `index.jsonl` 和 README 的三语状态投影：

```bash
uv run python scripts/mathnet_export.py --out mathnet-full --refresh-index
```

当前全量是 27,817 题。用不小于该题数的样本上限执行全覆盖契约与保真检查，并确认输出中的
“抽样 已检查/已发现”两数相等：

```bash
uv run python scripts/checks/check_translation_contract.py --sample 27817
```

随后确认两种语言都没有 failed/missing，且没有 stale：

```bash
uv run python scripts/bank.py mathnet-search --lang en --coverage failed --limit 20
uv run python scripts/bank.py mathnet-search --lang zh --coverage failed --limit 20
uv run python scripts/bank.py mathnet-search --lang en --coverage missing --limit 20
uv run python scripts/bank.py mathnet-search --lang zh --coverage missing --limit 20
uv run python scripts/bank.py mathnet-search --coverage stale --limit 20
bash scripts/lint.sh
```

五个覆盖查询都应显示 `共 0 题`，契约检查应输出 `TRANSLATION CHECK OK`，最后一条必须输出
`LINT OK`。若数据集题数以后变化，以 `mathnet-full/index.jsonl` 的实际题数为准调整 `--sample`，
并仍要求检查数等于发现数。

## 8｜成本、耗时与题源边界

当前量级为 27,817 题，预计约 1,800 万输入 token。约 84% 的英文原文在生成英文版时走
passthrough，因此该目标语言不调用模型；中文版仍需翻译。实际费用取决于当时模型单价，运行前
据此 token 量另算，不在本文固化价格。

这不是分钟级任务。应预留数小时到过夜的本地运行窗口；模型延迟、限流、重试与并发设置都会
改变总耗时，以 `run` 的实时 ETA 和进度文件为准。

`index.zh.md`、`index.en.md` 是机器生成、未经人工核验的派生产物，只用于本地检索与筛选。
`problems/` 入库题的题面和答案仍须逐字照录 MathNet 原文，不得引用译文作为题源；入库规则正本
见 [SPEC.md](../SPEC.md) 第 5 节。
