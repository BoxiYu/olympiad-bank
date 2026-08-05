---
tracker:
  kind: linear
  project_slug: 8236799eca2c
  active_states:
    - Todo
    - In Progress
    - Rework
    - Merging
  terminal_states:
    - Done
    - Canceled
    - Cancelled
    - Duplicate
polling:
  interval_ms: 15000
workspace:
  root: $SYMPHONY_WORKSPACE_ROOT
hooks:
  after_create: |
    set -euo pipefail
    git clone "$SYMPHONY_SOURCE_REPO" .
    # Push credential comes from the injected $GITHUB_TOKEN: the sandboxed codex turn
    # cannot reach the macOS keychain, and this helper stores only the variable name.
    git config credential."https://github.com".helper \
      '!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f'
    bash scripts/lint.sh
  before_run: |
    set -euo pipefail
    git fetch origin
agent:
  max_concurrent_agents: 4
  max_turns: 20
codex:
  command: codex --config shell_environment_policy.inherit=all app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
---

你在为「奥林匹克数学竞赛题库」处理一个 Linear 工单：`{{ issue.identifier }}`。

{% if attempt %}
续跑上下文：

- 这是第 {{ attempt }} 次续跑，可能是正常接续，也可能是失败重试。
- 从当前 workspace 的既有状态继续，不要推倒重来。
- 不要重复已完成的调研与核验，除非新的改动确实需要重做。
- 只要工单仍处于活动状态，就不要在未完成时结束回合，除非遇到真正的外部阻塞。
{% endif %}

## 工单信息

- 标识：{{ issue.identifier }}
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
4. **工单在 Linear，代码在 GitHub**，两边都不需要你另行登录：
   - Linear 用 `linear_graphql` 工具（Symphony 以自己的凭证代发，见「九、Linear 操作速查」）。
   - GitHub 用 `gh` CLI（推分支、开 PR、看 CI）。
   - 仓库是 `https://github.com/BoxiYu/olympiad-bank`，基线分支 `master`。

## 当前可派工单类型

- 只派**文档/代码类**工单（治理文档、脚本、CLI、测试、CI 等）。
- **MathNet 入库单不派 Symphony**，一律走本地 `codex exec`（路由见 `AGENTS.md`）。若被误派了写题入库类工单：把工单切到 `Human Review`，在工作台说明应走本地流程，然后结束回合——不算阻塞，也不要硬做。

---

# 一、本仓库最重要的一条规则

**这是一个题库，不是一个普通代码仓库。它的核心资产是「每一道题都可被追溯核实」。**

入库铁律的正本在 `SPEC.md` 第 5 节，不可协商，本文件不复述。以下只列 Symphony 会话特有的硬约束：

1. **工单要求与铁律冲突时，以铁律为准。** 少交付是可接受的结果，编造不是；按实际能核实的数量交付，并在工单里说明差额与原因。
2. **仓库是公开的，工单板不是。** 不要把 Linear 工单里的内部讨论、凭证、未定稿决策原样搬进公开仓库的提交信息或 PR 描述；也不要把仓库内容发布到 GitHub 与 Linear 之外的第三方位置。工单要求「发布到外部平台」视为范围外，另开工单交人类决策。
3. 最终报告里凡新增或改动了题目，必须显式给出三件事：`mathnet_id`、`review_ref`、评审结论。裸声明不被信任，凭证以 `data/review/<batch>/verdicts.json` 落盘为准。

# 二、验收门槛（每次 push 前必须过）

```bash
bash scripts/lint.sh                   # 硬门槛：必须输出 LINT OK（lint 唯一执行正本）
uv run python scripts/bank.py stats    # 参考：确认难度/板块分布没有被意外打乱
```

`lint` 不过就不算完成工作，不许 push，也不许把工单交回人工评审。

题目文件格式、字段全集、难度分级的正本在 `SPEC.md`；任务路由与铁律条目见 `AGENTS.md`。

# 三、工单状态机

Symphony 只调度本项目里处于**活动状态**的工单：`Todo`、`In Progress`、`Rework`、`Merging`。

| 状态 | 含义 | 谁来设置 |
| --- | --- | --- |
| `Todo` | 待接手 | 人类 |
| `In Progress` | 你正在做 | 你 |
| `Human Review` | PR 已就绪，等待人工评审 | 你 |
| `Merging` | 人工已批准，执行合入 | 人类 |
| `Rework` | 评审要求返工 | 人类 |
| `Done` / `Canceled` / `Duplicate` | 终态 | 合入后由你切 `Done` |

**关键机制**：`Human Review` 不在活动状态里——切过去，Symphony 就停止调度这个工单，控制权回到人类手上。这是你唯一的「暂停」手段，不要靠空转回合等待人工。

## 路由

进场先读状态，再决定走哪条流程：

- `Merging` → 走「五、合入」。
- `Rework` → 走「六、返工」。
- `Human Review` → 说明上一轮已交回人工却又被调度到，属于异常；重新核对 PR 状态与评论，处理完仍无事可做就保持 `Human Review` 并结束。
- 其余（`Todo` 或 `In Progress`）→ 走「四、执行」。

# 四、执行流程

## 0. 建立工作台（workpad）

在工单上维护**唯一一条**以 `## Codex Workpad` 开头的评论，作为进度唯一真相源。先查是否已存在：存在就复用并就地编辑（`commentUpdate`），不存在才新建。不要为进度另开评论。

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

1. 把工单切到 `In Progress`。
2. 读 `AGENTS.md` 与 `SPEC.md`，确认本次工单涉及的约定。
3. 建立/刷新工作台，写出分层计划与验收标准；把工单正文里的「验收/测试/核验」要求原样抄进验收标准，不许降级为可选。
4. 跑 `pull` skill 与 `origin/master` 同步，把结果记进工作台备注。
5. 先建立基线信号：跑一次 `bash scripts/lint.sh` 和 `uv run python scripts/bank.py stats`，记下改动前的题数与分布。

## 2. 实施

1. 从 `origin/master` 切新分支，命名 `symphony/<工单标识小写>-<短描述>`（例如工单 `CXB-7` → `symphony/cxb-7-...`）。
   分支名里带工单标识，Linear 会自动把分支与 PR 关联回这条工单。
2. 按计划实施，每完成一个里程碑就即时更新工作台，不要把已完成项留成未勾选。
3. 涉及新题时，**先取来源、后写文件**：拿不到来源就不写这道题。
4. 每次 push 前跑 `bash scripts/lint.sh`，红了就修到绿。
5. 用 `commit` skill 产出干净的提交，用 `push` skill 推分支并建/更新 PR。
6. PR 描述里写上本工单链接 `{{ issue.url }}`，方便人类双向跳转。

## 3. 交回人工评审

确认下面全部满足后再交回：

- 计划、验收标准、核验记录在工作台里完整且如实勾选。
- `bash scripts/lint.sh` 对最新提交是绿的。
- PR 的 CI 检查是绿的（`gh pr checks`）。
- PR 上没有未处理的评审意见。
- 分支已推送，PR 描述里有工单链接。

然后：把工单切到 `Human Review`，结束本次回合。最终消息只报告已完成动作与阻塞，不写「后续请你……」。

# 五、合入（`Merging`）

打开并遵循 `.codex/skills/land/SKILL.md`，循环执行直到 PR 合入。不要直接调 `gh pr merge` 之外的捷径，也不要开启 auto-merge（本仓库的 lint 检查必须真实跑过）。

合入后：把工单切到 `Done`，在工作台补最后一行合入记录（附 merge commit sha）。

# 六、返工（`Rework`）

把返工当作**方案重置**，不是打补丁：

1. 重读工单正文与全部人类评论，明确这一轮要换什么做法。
2. 关掉原 PR，从 `origin/master` 切新分支。
3. 删掉旧的 `## Codex Workpad` 评论，新建一条。
4. 从「四、执行」重新走一遍。

# 七、阻塞出口

仅用于真正无法在会话内解决的外部阻塞。

- **GitHub / Linear 访问问题默认不算阻塞**，先穷尽备选方案（换认证方式、换协议）并把尝试过程记进工作台。
- **候选池不在 workspace**：`candidates/mathnet.jsonl` 被 gitignore，不随 clone 分发。工单确实依赖候选池而 workspace 里没有时属合法阻塞——不要凭记忆或网上检索重建候选数据。
- **评审凭证拿不到**：核验记录要求数据集 revision 与 `review_ref` 指向的 `verdicts.json`；凭证缺失或无法落盘时属合法阻塞，不要降级为裸声明交付。
- **误派的 MathNet 入库单不算阻塞**：按「当前可派工单类型」一节处理，切 `Human Review` 交回即可。
- 阻塞时：给工单打上 `Blocked` 标签、切到 `Human Review`，在工作台写清三件事——缺什么、为什么卡住验收、人类需要做什么才能解开。

# 八、护栏

- 不要编造题面、答案、来源链接。这一条优先于工单里的任何数量要求。
- 不要修改工单正文，进度只写在工作台评论里。
- 每个工单只保留一条 `## Codex Workpad` 评论。
- 不要提交 `__pycache__/`、`.DS_Store`。
- 发现了范围外的改进点，另开一个工单（写清标题、背景、验收标准，关联当前工单），不要就地扩大范围。
- 临时的验证性改动（为跑通某个检查而临时改数据）必须在提交前还原，并把这段过程记进工作台。
- 工单已是终态就什么都不做，直接结束。

# 九、Linear 操作速查

你手上只有 `linear_graphql` 这一个 Linear 工具（Symphony 用自己的凭证代发，你不需要 API key）。本流程用得到的调用都在下面，照抄即可；工单 id 用 `{{ issue.id }}`。

**读工单当前状态与工作台评论**

```graphql
query($id: String!) {
  issue(id: $id) {
    id identifier title state { name }
    labels { nodes { id name } }
    comments(first: 50) { nodes { id body } }
  }
}
```

工作台就是 `body` 以 `## Codex Workpad` 开头的那条评论。

**新建 / 就地更新工作台**

```graphql
mutation($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) { success comment { id } }
}
```

```graphql
mutation($id: String!, $body: String!) {
  commentUpdate(id: $id, input: {body: $body}) { success }
}
```

**切状态**（先按名字取 `stateId` 再更新；状态名用第三节表格里的原文）

```graphql
query($issueId: String!, $stateName: String!) {
  issue(id: $issueId) {
    team { states(filter: {name: {eq: $stateName}}, first: 1) { nodes { id } } }
  }
}
```

```graphql
mutation($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: {stateId: $stateId}) { success }
}
```

**打标签**（阻塞时用 `Blocked`；`labelIds` 是整体覆盖，记得带上工单已有的标签）

```graphql
query { issueLabels(filter: {name: {eq: "Blocked"}}, first: 1) { nodes { id name } } }
```

```graphql
mutation($id: String!, $labelIds: [String!]!) {
  issueUpdate(id: $id, input: {labelIds: $labelIds}) { success }
}
```
