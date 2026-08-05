# 奥林匹克数学竞赛题库

> **项目使命：用 AI 促进数学教育，帮助学生高效地学习数学。**
> AI 教学的可靠性取决于题目与答案的可靠性——先建成每道题都可溯源核实的题库，再在其上生长训练系统。

**现状一句话**：题库供给自 MathNet 数据集，经 Codex 逐题评审通过后入库；2026-08-06 的
`bash scripts/lint.sh` 基线为 193 题，之后的实时规模与分布以该命令和
`uv run python scripts/bank.py stats` 的输出为准。

## 先做一次准备

只需做一次的准备：

1. **安装 uv**（Python 环境管家）：按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)
   选择 macOS、Windows 或 Linux 的步骤。
2. **下载本项目**：仓库页绿色 Code 按钮 → Download ZIP，解压到任意位置；熟悉 Git 也可直接克隆。

准备好后，按身份只走下面一条路径。

## 5 分钟快速上手

### 学生：直接开始练题

三选一启动浏览器训练台：

- macOS 双击 [开始.command](开始.command)（首次被系统拦截时：右键 → 打开）；
- Windows 双击 [开始.bat](开始.bat)；
- 终端：`uv run python scripts/bank.py web`。

训练台会显示今日队列，并完成开卡计时、提示与收卷登记。首次运行会自动下载 Python 环境与
依赖（联网等几分钟）；无浏览器环境可用 `uv run python scripts/menu.py`。开始前读一遍
[学生手册](docs/学生手册.md)，之后日常只需回到训练台。

### 教练：生成题单并查看能力图

不想用命令行时，启动同一个训练台，在「教练工具」里生成本周计划。命令行等价入口是：

```bash
uv run python scripts/bank.py plan --target IMO --n 10
uv run python scripts/bank.py coach --target IMO --save
uv run python scripts/bank.py web
```

`IMO` 可换成学生目标赛事；第一条先预览单周配额，并显示当前库存缺口，第二条再保存周计划。
更多解释以及建档、测评波次与能力图入口见 [教练手册](docs/教练手册.md)。

### 维护者：检查库况，再进入补题流程

```bash
bash scripts/lint.sh
uv run python scripts/bank.py stats
```

确认基线正常后再读 [MathNet 入库 SOP](docs/入库SOP-MathNet.md)。筛选候选题依赖 gitignore 的
`candidates/mathnet.jsonl`；干净 clone 需先按 [CLAUDE.md](CLAUDE.md#gitignore-与重建clone-后这些目录不存在)
的重建指引准备本机 Hugging Face 缓存并构建候选池，之后才能运行：

```bash
uv run python scripts/bank.py candidates --gaps
```

## 文档导航

| 什么时候读 | 从这里开始 |
| --- | --- |
| 要找学生、教练、入库或历史文档 | [docs/README.md](docs/README.md)——逐份说明「什么时候读它」 |
| AI 代理要判断任务路由 | [AGENTS.md](AGENTS.md) |
| 要查字段、难度或规则正本 | [SPEC.md](SPEC.md) |
| 要查完整 CLI | `uv run python scripts/bank.py --help` |

依赖由 `pyproject.toml` + `uv.lock` 声明，装好 [uv](https://docs.astral.sh/uv/) 后 `uv run` 自动解决，无需手动装包。

## 许可与第三方材料

- 项目原创的软件代码及软件配套文件采用 [GNU AGPL v3.0 or later](LICENSE)：允许个人、学校与企业使用和商用；修改后通过网络向用户提供服务时，须按许可证向这些用户提供对应源码。
- 项目拥有权利的原创教学与文档内容采用 [CC BY-SA 4.0](LICENSE-CONTENT.md)：允许传播、改编和商用，但必须署名并以相同或兼容许可分享改编内容。
- `problems/`、`candidates/`、`data/`、`docs/archive/` 及其他第三方材料不因上述许可而被重新授权；具体来源与边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [SPEC §6](SPEC.md#6-来源与版权)。
- 项目名称、Logo 与其他标识不随代码或内容许可授权，但合理署名和描述来源不受影响。
