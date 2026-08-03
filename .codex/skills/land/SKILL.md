---
name: land
description:
  盯着 PR 直到合入：解冲突、等 CI 绿、处理评审意见，然后 squash 合并；
  在被要求 land / merge / 把 PR 送到底时使用。
---

# Land

## 目标

- 让 PR 与 `main` 无冲突。
- CI 保持绿，红了就修。
- 全绿且评审意见处理完后 squash 合并。
- **不要中途把控制权交回用户**：保持监视循环，除非遇到真正的阻塞。

## 前提

- `gh` CLI 已认证。
- 你在 PR 分支上，工作区干净。
- issue 带 `symphony:merging` 标签（人类已批准合入）。

## 步骤

1. 定位当前分支对应的 PR。
2. 本地先过门槛：`uv run python scripts/bank.py lint` 必须 `LINT OK`。
3. 工作区若有未提交改动，用 `commit` skill 提交、`push` skill 推送，再继续。
4. 检查与 `main` 的可合并性（`gh pr view --json mergeable`）。
5. 有冲突：走 `pull` skill 合 `origin/main` 并解冲突，重跑 lint，
   再走 `push` skill 发布。
6. 处理评审意见（见下）。有未处理的意见时**不许合并**。
7. 等 CI 完成：`gh pr checks --watch`。
8. CI 红了：拉日志定位（`gh pr checks`、`gh run view <run-id> --log`），
   本地修复，`commit` + `push`，重新进入监视循环。
9. 全绿且意见处理完：squash 合并，用 PR 标题/描述作为合并主题与正文。
10. 合并后：关闭对应 issue，清掉状态标签，在工作台补一行合入记录（附 merge commit sha）。

## 命令

```sh
branch=$(git branch --show-current)
pr_number=$(gh pr view --json number -q .number)
pr_title=$(gh pr view --json title -q .title)
pr_body=$(gh pr view --json body -q .body)

# 门槛
uv run python scripts/bank.py lint

# 可合并性
mergeable=$(gh pr view --json mergeable -q .mergeable)
# CONFLICTING → 走 pull skill 解冲突，再走 push skill

# 等 CI
if ! gh pr checks --watch; then
  gh pr checks
  gh run list --branch "$branch"
  # gh run view <run-id> --log
  exit 1
fi

# squash 合并
gh pr merge --squash --subject "$pr_title" --body "$pr_body"
```

## 评审意见处理

- 顶层讨论：`gh api repos/{owner}/{repo}/issues/<pr_number>/comments`
- 行内评审意见：`gh api repos/{owner}/{repo}/pulls/<pr_number>/comments`
- 评审结论：`gh pr view --json reviews`

规则：

- 本代理产生的所有 GitHub 评论一律加 `[codex]` 前缀。
- **先回复、后改代码**：对每条意见先表明处理方式（接受 / 澄清 / 反驳），再动手。
- 行内意见要用行内回复：
  ```sh
  gh api -X POST /repos/{owner}/{repo}/pulls/<pr_number>/comments \
    -f body='[codex] <回应>' -F in_reply_to=<comment_id>
  ```
  `in_reply_to` 必须是数字型 review comment id（如 `2710521800`），
  不是 GraphQL node id；路径必须带 PR 编号。
- 每条意见归类为：正确性 / 设计 / 风格 / 澄清 / 范围。
- **正确性问题必须给出具体验证**（命令、输出或推理）才能关闭。
  涉及题面或答案正确性的意见，验证方式必须回到官方来源逐字比对——
  不许用「看起来没问题」结案。
- 反驳时给出理由 + 替代方案；接受时补一行动机说明。
- 一批修改后发一条汇总的根级 `[codex]` 评论，不要发很多条碎片更新。

## 失败处理

- 会判断 flake（例如只在某一个环境超时）；确属 flake 可以继续。
- 可合并性是 `UNKNOWN` 时等待后重查。
- **不要开启 auto-merge**：本仓库的 lint 检查必须真实跑过。
- 远端分支因你自己之前的 force-push 而前进时，不要重复合并，
  必要时本地重跑后 `git push --force-with-lease`。

## 护栏

- 合并前 PR 标题与描述必须覆盖整条分支的全部范围，而不只是最后一次修复。
- 评审要求扩大范围时，决定「现在做」还是「另开 issue」，
  并在根级 `[codex]` 更新里说明理由。
- 不要为了让 CI 变绿而放宽 `bank.py lint` 的规则或删改校验逻辑。
  lint 报错说明数据有问题，要修数据，不是修尺子。
