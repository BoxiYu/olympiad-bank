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
uv run python scripts/mathnet_translate.py reindex --help
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

`--strict` 适合烟雾测试：首个批次或题级失败便停止，避免错误扩散。`run` 成功写回后会自动把
相应题目的三语状态增量刷新到 `index.jsonl`；下面的覆盖率查询正是读取这些字段。正常流程无需
手动 reindex，接着检查产物契约与仓库硬门槛：

```bash
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

小批通过后去掉 `--limit` 与 `--strict`。`--help` 中的默认值仍是每批 25 题、4 个并发；按第 8 节
的实测标定，全量推荐每批 100 题、6～8 个并发。下面取并发 8：

```bash
uv run python scripts/mathnet_translate.py run \
  --root mathnet-full \
  --batch-size 100 \
  --concurrency 8
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
清除对应记录。

逐字相同不再天然等于失败。纯符号最终答案必须原样保留；源小节已经是目标语言时无需改写；
`en/*` 源生成英文目标且模型判定无需改动时，也可按已核验的 `translated` 结果落盘。只有派单前
满足译文契约直通条件的英文版本才记为 `passthrough`。混合语言小节中仍残留非目标语言散文，
则仍属于真正的 `untranslated`，不能用整篇源语言标签绕过。

因此，清单中值得人工看的题级记录主要是稳定复现的非目标语言散文、数学/图片/章节骨架漂移、
纯符号答案被改动、空译文或缺译文，以及模型自述等污染。`scope=batch` 的超时、服务错误或非法
批次输出先交给幂等续跑；只有多轮仍稳定复现，才按错误类型检查生成结果或调用链。

失败清单含 `scope=batch` 时，必须重跑第 3 节的原全量命令，让驱动以原批次 ID 清除批次记录；
不要拆成逐题重试。只有清单仅剩 `scope=target` 记录时，才从中取得 id，并用 `run --only ID`
重试少数题；`--only` 可重复。

如果同一错误稳定复现，先修原文之外的调用或译文生成问题，再重跑。`index.md` 是输入，不通过
手工改原文来让译文检查变绿；原文与译文边界见第 8 节。

## 6｜发现并重译过期译文

`mathnet-search --coverage ...` 读取的是 `index.jsonl` 中的三语字段。正常的 `run` 与 `apply`
写回后会自动增量 reindex，不需要额外命令。若手工拆开执行过 `apply`（尤其用了
`--no-reindex`），或进程中途被 kill、无法确认增量收尾完整，先显式重建全量视图，再查
`stale`：

```bash
uv run python scripts/mathnet_translate.py reindex --root mathnet-full --all
uv run python scripts/bank.py mathnet-search --coverage stale --limit 20
```

`stale` 非零表示译文记录的 `source_sha256` 与当前 `index.md` 不一致。直接重跑第 3 节命令；驱动会
丢弃相应旧进度，把过期目标重新 export、重新翻译并 apply。完成后自动增量刷新索引，再重复查询，
直到 `共 0 题`。若只需手动刷新个别 id 的视图，使用
`uv run python scripts/mathnet_translate.py reindex --root mathnet-full --only <id>`；`--only` 可重复。

刷新 `mathnet-search --coverage ...` 视图只需上述 `reindex`；不要误跑未带 `--refresh-index` 的
`mathnet_export.py`，普通 export 会从 Hugging Face 数据集全量重铺语料目录。`--refresh-index` 是
例外：它不读数据集，只按现有 `index.md` / `translation.json` 刷新索引与 README，供第 7 节收尾使用。

apply 也会拒绝写入原文哈希已经变化的旧批次；不要绕过拒绝，重新 export 才能得到当前输入。

## 7｜全量收尾与校验

翻译结束时，正常 `run` 已自动增量刷新 `index.jsonl`。全量收尾仍先显式重建一次索引视图，以覆盖
曾被 kill、手工 apply 或断点恢复留下的任何投影缺口；再用 `--refresh-index` 快速路径同步 README
中的三语覆盖率表。后一个命令不读 Hugging Face 数据集：

```bash
uv run python scripts/mathnet_translate.py reindex --root mathnet-full --all
uv run --group mathnet python scripts/mathnet_export.py --out mathnet-full --refresh-index
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

全量实测共 27,817 题，源语言分布如下：

| 源语言 | 题数 |
| --- | ---: |
| `en` | 21,139（76.0%） |
| `und` | 1,966（7.1%） |
| `pt` | 985 |
| `fr` | 875 |
| `es` | 789 |
| `it` | 675 |
| `sl` | 577 |
| `de` | 546 |
| `ru` | 149 |
| `zh` | 116 |

英文版有 19,812 题（71.2%）走 passthrough，零模型调用；不是早期抽样估计的 84%。中文版需真翻
27,817 份，源文 55.7 MB；英文版需真翻 8,005 份，源文 15.7 MB。合计需生成 35,822 份译文，
源文输入约 2,340 万 token。若按 25 题一批，就是 1,113 个中文批次加 321 个英文批次，共
1,434 批派单。实际费用取决于运行时模型单价，本文不固化价格。

批大小标定使用真实 Codex、并发 2，三个实测点为：

| 批大小 | 耗时 |
| ---: | ---: |
| 25 | 12m19s |
| 50 | 14m08s |
| 100 | 17m20s |

线性拟合为 `t = 639s + 4.01s × N`；用 `N=50` 回代为 840 秒，实测 848 秒，误差约 1%。也就是
每批固定开销约 10.6 分钟，每题只增加约 4 秒；批 25 时，固定开销占总耗时约 86%，因此加大批量
能显著减少总派单时间。

对全量 35,822 份真翻，串行总时可按 `22.89M/N + 143.6k` 秒估算：

| 批大小 | 串行 | 并发 4 | 并发 8 |
| ---: | ---: | ---: | ---: |
| 25 | 294 h | 74 h | 37 h |
| 50 | 167 h | 42 h | 21 h |
| **100** | **103 h** | **26 h** | **13 h** |
| 200（外推） | 72 h | 18 h | 9 h |
| 理论下限 | 40 h | 10 h | 5 h |

推荐配置是批 100、并发 6～8，预计约 13～17 小时。批 100 是实测点；批 200 只是外推，而且
200 题一批约 340 KB，会塞进单个 Codex 上下文，质量与上下文风险尚未验证，收益却只是在并发 8
时把约 13 小时降到约 9 小时，因此不作为推荐值。

当前校验语义下的真实失败率为 14/187，即 7.5%；此前 8% 与 17.3% 均来自仍会误拒正确结果的
旧版本，不能横向比较。`run` 幂等，失败项会在下一轮自动重派；安排全量窗口时，在上述估算上
再留约 10% 时间余量。模型延迟、限流与重试仍会改变实耗，以 `run` 的实时 ETA 和进度文件为准。

`index.zh.md`、`index.en.md` 是机器生成、未经人工核验的派生产物，只用于本地检索与筛选。
`problems/` 入库题的题面和答案仍须逐字照录 MathNet 原文，不得引用译文作为题源；入库规则正本
见 [SPEC.md](../SPEC.md) 第 5 节。
