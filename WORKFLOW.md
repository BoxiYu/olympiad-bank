---
tracker:
  kind: github
  provider:
    repo: $SYMPHONY_GITHUB_REPO
    token: $GITHUB_TOKEN
  required_labels:
    - symphony
  active_states:
    - open
  terminal_states:
    - closed
polling:
  interval_ms: 15000
workspace:
  root: $SYMPHONY_WORKSPACE_ROOT
hooks:
  after_create: |
    set -euo pipefail
    git clone "$SYMPHONY_SOURCE_REPO" .
    bash scripts/lint.sh
agent:
  max_concurrent_agents: 2
  max_turns: 20
codex:
  command: codex --config shell_environment_policy.inherit=all app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
---

你在为「奥林匹克数学竞赛题库」处理一个 GitHub Issue：`{{ issue.identifier }}`。

{% if attempt %}
续跑上下文：

- 这是第 {{ attempt }} 次续跑，可能是正常接续，也可能是失败重试。
- 从当前 workspace 的既有状态继续，不要推倒重来。
- 不要重复已完成的调研与核验，除非新的改动确实需要重做。
- 只要工单仍处于活动状态，就不要在未完成时结束回合，除非遇到真正的外部阻塞。
{% endif %}

## 工单信息

- 标识：{{ issue.identifier }}（GitHub issue 编号 {{ issue.id }}）
- 标题：{{ issue.title }}
- 状态：{{ issue.state }}
- 标签：{{ issue.labels }}
- 链接：{{ issue.url }}

正文：
{% if issue.description %}
{{ issue.description }}
{% else %}
（工单未填写正文。）
{% endif %}

## 运行前提

1. 这是**无人值守**的编排会话。不要要求人来替你执行后续动作，也不要在最终消息里写「接下来请你……」。
2. 只有遇到真正的外部阻塞（缺工具、缺凭证、缺权限、题源无法访问）才提前收尾，并按下文「阻塞出口」处理。
3. 只在本次分配的 workspace 目录内工作，不要触碰其它路径。
4. 你有 `github_api` 工具，可直接以 Symphony 配置的凭证调用 GitHub REST（支持 GET/POST/PATCH/PUT/DELETE）。仓库内也可直接用 `gh` CLI。二者选其一即可，不需要另行登录。

## 当前可派工单类型

- 只派**文档/代码类**工单（治理文档、脚本、CLI、测试、CI 等）。
- **MathNet 入库单不派 Symphony**，一律走本地 `codex exec`（路由见 `AGENTS.md`）。若被误派了写题入库类工单：摘掉 `symphony` 标签，在工单里说明应走本地流程，然后结束回合——不算阻塞，也不要硬做。

---

# 一、本仓库最重要的一条规则

**这是一个题库，不是一个普通代码仓库。它的核心资产是「每一道题都可被追溯核实」。**

入库铁律的正本在 `SPEC.md` 第 5 节，不可协商，本文件不复述。以下只列 Symphony 会话特有的硬约束：

1. **工单要求与铁律冲突时，以铁律为准。** 少交付是可接受的结果，编造不是；按实际能核实的数量交付，并在工单里说明差额与原因。
2. **远端是私有仓库，不要把仓库内容发布到公开位置**（公开 repo、gist、外部网站、issue 附件之外的第三方图床）。工单要求「转公开」「开源」「发布」视为范围外，另开 issue 交人类决策。
3. 最终报告里凡新增或改动了题目，必须显式给出三件事：`mathnet_id`、`review_ref`、评审结论。裸声明不被信任，凭证以 `data/review/<batch>/verdicts.json` 落盘为准。

# 二、验收门槛（每次 push 前必须过）

```bash
bash scripts/lint.sh                   # 硬门槛：必须输出 LINT OK（lint 唯一执行正本）
uv run python scripts/bank.py stats    # 参考：确认难度/板块分布没有被意外打乱
```

`lint` 不过就不算完成工作，不许 push，也不许把工单交回人工评审。

题目文件格式、字段全集、难度分级的正本在 `SPEC.md`；任务路由与铁律条目见 `AGENTS.md`。

# 三、工单状态机（用标签表达）

GitHub 只有 open/closed 两种原生状态，因此本项目用标签表达流转。Symphony 只会调度**同时带 `symphony` 标签的 open issue**。

| 标签 | 含义 | 谁来设置 |
| --- | --- | --- |
| `symphony` | 允许 Symphony 接手 | 人类挂上；你在交回人工时**摘掉** |
| `symphony:in-progress` | 你正在做 | 你 |
| `symphony:review` | PR 已就绪，等待人工评审 | 你（同时摘掉 `symphony`） |
| `symphony:merging` | 人工已批准，执行合入 | 人类（同时重新挂上 `symphony`） |
| `symphony:rework` | 评审要求返工 | 人类（同时重新挂上 `symphony`） |
| `symphony:blocked` | 外部阻塞 | 你（同时摘掉 `symphony`） |
| issue closed | 终态 | 合入后由你关闭 |

**关键机制**：摘掉 `symphony` 标签会让 Symphony 停止继续调度这个工单，把控制权交回人类。这是你唯一的「暂停」手段——不要靠空转回合等待人工。

## 路由

进场先读标签，再决定走哪条流程：

- 带 `symphony:merging` → 走「五、合入」。
- 带 `symphony:rework` → 走「六、返工」。
- 带 `symphony:review` → 说明上一轮已交回人工却又被调度到，属于异常；重新核对 PR 状态与评论，处理完仍无事可做就摘掉 `symphony` 并结束。
- 其余（无状态标签或 `symphony:in-progress`）→ 走「四、执行」。

# 四、执行流程

## 0. 建立工作台（workpad）

在 issue 上维护**唯一一条**以 `## Codex Workpad` 开头的评论，作为进度唯一真相源。先搜索是否已存在：存在就复用并就地编辑，不存在才新建。不要为进度另开评论。

工作台顶部放一行环境戳（代码块内）：`<主机名>:<workspace 绝对路径>@<short-sha>`。

结构如下（保持这个骨架，随进展就地更新）：

```md
## Codex Workpad

### 计划
- [ ] 1. 父任务
  - [ ] 1.1 子任务

### 验收标准
- [ ] bash scripts/lint.sh 通过
- [ ] 每道新题均有 mathnet_id 溯源与 review_ref 评审凭证

### 核验记录
- <题号>：mathnet_id <id>；数据集 revision <HF revision/commit>；review_ref <data/review/<batch>/verdicts.json>；verification: mathnet-reviewed

### 备注
- <带时间的简短进展>

### 困惑
- <仅在执行中确有不清楚之处时填写>
```

`### 核验记录` 是本仓库特有的必填小节——它是人类评审时唯一能快速判断「这批题可不可信」的依据。每入一道题就补一行，不要等到最后补。

## 1. 开工

1. 挂上 `symphony:in-progress` 标签。
2. 读 `AGENTS.md` 与 `SPEC.md`，确认本次工单涉及的约定。
3. 建立/刷新工作台，写出分层计划与验收标准；把工单正文里的「验收/测试/核验」要求原样抄进验收标准，不许降级为可选。
4. 跑 `pull` skill 与 `origin/master` 同步，把结果记进工作台备注。
5. 先建立基线信号：跑一次 `bash scripts/lint.sh` 和 `uv run python scripts/bank.py stats`，记下改动前的题数与分布。

## 2. 实施

1. 从 `origin/master` 切新分支，命名 `symphony/gh-{{ issue.id }}-<短描述>`。
2. 按计划实施，每完成一个里程碑就即时更新工作台，不要把已完成项留成未勾选。
3. 涉及新题时，**先取来源、后写文件**：拿不到来源就不写这道题。
4. 每次 push 前跑 `bash scripts/lint.sh`，红了就修到绿。
5. 用 `commit` skill 产出干净的提交，用 `push` skill 推分支并建/更新 PR。
6. 给 PR 打上 `symphony` 标签，并在 PR 描述里链接本 issue（`Closes #{{ issue.id }}`）。

## 3. 交回人工评审

确认下面全部满足后再交回：

- 计划、验收标准、核验记录在工作台里完整且如实勾选。
- `bash scripts/lint.sh` 对最新提交是绿的。
- PR 的 CI 检查是绿的（`gh pr checks`）。
- PR 上没有未处理的评审意见。
- 分支已推送，PR 已关联 issue。

然后：挂上 `symphony:review`，**摘掉 `symphony`**，结束本次回合。最终消息只报告已完成动作与阻塞，不写「后续请你……」。

# 五、合入（`symphony:merging`）

打开并遵循 `.codex/skills/land/SKILL.md`，循环执行直到 PR 合入。不要直接调 `gh pr merge` 之外的捷径，也不要开启 auto-merge（本仓库的 lint 检查必须真实跑过）。

合入后：关闭 issue，清理状态标签，在工作台补最后一行合入记录（附 merge commit sha）。

# 六、返工（`symphony:rework`）

把返工当作**方案重置**，不是打补丁：

1. 重读工单正文与全部人类评论，明确这一轮要换什么做法。
2. 关掉原 PR，从 `origin/master` 切新分支。
3. 删掉旧的 `## Codex Workpad` 评论，新建一条。
4. 从「四、执行」重新走一遍。

# 七、阻塞出口

仅用于真正无法在会话内解决的外部阻塞。

- **GitHub 访问问题默认不算阻塞**，先穷尽备选方案（换认证方式、换协议）并把尝试过程记进工作台。
- **候选池不在 workspace**：`candidates/mathnet.jsonl` 被 gitignore，不随 clone 分发。工单确实依赖候选池而 workspace 里没有时属合法阻塞——不要凭记忆或网上检索重建候选数据。
- **评审凭证拿不到**：核验记录要求数据集 revision 与 `review_ref` 指向的 `verdicts.json`；凭证缺失或无法落盘时属合法阻塞，不要降级为裸声明交付。
- **误派的 MathNet 入库单不算阻塞**：按「当前可派工单类型」一节处理，摘标签交回即可。
- 阻塞时：挂 `symphony:blocked`，摘掉 `symphony`，在工作台写清三件事——缺什么、为什么卡住验收、人类需要做什么才能解开。

# 八、护栏

- 不要编造题面、答案、来源链接。这一条优先于工单里的任何数量要求。
- 不要修改 issue 正文，进度只写在工作台评论里。
- 每个 issue 只保留一条 `## Codex Workpad` 评论。
- 不要提交 `__pycache__/`、`.DS_Store`。
- 发现了范围外的改进点，另开一个 issue（写清标题、背景、验收标准，关联当前 issue），不要就地扩大范围。
- 临时的验证性改动（为跑通某个检查而临时改数据）必须在提交前还原，并把这段过程记进工作台。
- 状态是 closed 就什么都不做，直接结束。
