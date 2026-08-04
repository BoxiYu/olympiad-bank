# 奥林匹克数学竞赛题库

> **项目使命：用 AI 促进数学教育，帮助学生高效地学习数学。**
> AI 教学的可靠性取决于题目与答案的可靠性——先建成每道题都可溯源核实的题库，再在其上生长训练系统。

**现状一句话**：题库供给自 MathNet 数据集，经 Codex 逐题评审通过后入库；规模与分布一律以 `uv run python scripts/bank.py stats` 的输出为准，本文不手写任何数字。

## 读者路由

| 你是 | 从这里开始 |
| --- | --- |
| 学生（要训练） | [docs/学生手册.md](docs/学生手册.md) |
| 教练（要设计训练） | [docs/教练手册.md](docs/教练手册.md) |
| AI 代理（要干活） | [AGENTS.md](AGENTS.md) |
| 查规范（字段/难度/铁律） | [SPEC.md](SPEC.md)——唯一规范正本，本文不复述规则 |

## 常用命令

```bash
# 训练闭环（流程与语义见 docs/学生手册.md）
uv run python scripts/bank.py spar next      # 开无答案题卡（复习到期优先于周计划）
uv run python scripts/bank.py spar hint      # 逐级解锁提示
uv run python scripts/bank.py spar reveal    # 亮出解法要点（之后须合卷复述）
uv run python scripts/bank.py spar finish    # 登记结果，追加训练日志
uv run python scripts/bank.py coach --target IMO --save   # 出周计划并写入 data/plan.json
uv run python scripts/bank.py similar <题号>  # 相似题检索（首次使用先建索引，见 docs/教练手册.md）

# 库况与校验
uv run python scripts/bank.py stats          # 题库规模与分布
bash scripts/lint.sh                         # 入库校验唯一执行正本，必须输出 LINT OK
```

依赖由 `pyproject.toml` + `uv.lock` 声明，装好 [uv](https://docs.astral.sh/uv/) 后 `uv run` 自动解决，无需手动装包。

## 许可与第三方材料

- 项目原创的软件代码及软件配套文件采用 [GNU AGPL v3.0 or later](LICENSE)：允许个人、学校与企业使用和商用；修改后通过网络向用户提供服务时，须按许可证向这些用户提供对应源码。
- 项目拥有权利的原创教学与文档内容采用 [CC BY-SA 4.0](LICENSE-CONTENT.md)：允许传播、改编和商用，但必须署名并以相同或兼容许可分享改编内容。
- `problems/`、`candidates/`、`data/`、`docs/archive/` 及其他第三方材料不因上述许可而被重新授权；具体来源与边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [SPEC §6](SPEC.md#6-来源与版权)。
- 项目名称、Logo 与其他标识不随代码或内容许可授权，但合理署名和描述来源不受影响。
