---
name: push
description:
  把当前分支推到 origin 并创建/更新对应的 PR；在被要求 push、发布更新或开 PR 时使用。
---

# Push

## 前提

- `gh` CLI 已安装且在 `PATH` 中。
- `gh auth status` 对本仓库可用。

## 目标

- 安全地把当前分支推到 `origin`。
- 分支没有 PR 就创建，有就更新。
- 远端已前进时保持历史干净。

## 相关技能

- `pull`：push 被拒（non-fast-forward、分支落后、有冲突风险）时使用。
- `commit`：工作区尚有未提交改动时使用。

## 步骤

1. 确认当前分支与远端状态。
2. **推之前先过门槛**：`bash scripts/lint.sh` 必须 `LINT OK`。
   红了就修到绿再推，不要带着红的 lint 推分支。
3. 用现有的 remote URL 推分支，必要时建立 upstream 跟踪。
4. push 不干净时：
   - 属于 non-fast-forward / 落后：走 `pull` skill 合 `origin/master`、解冲突、重跑 lint，再推。
   - 只有在确实改写过历史时才用 `--force-with-lease`。
   - 属于认证、权限或 workflow 限制：**停下来把原始错误如实报出**，
     不要改 remote、不要换协议绕过。
5. 确保分支有对应 PR：
   - 没有就创建；有且是 open 就更新。
   - 分支绑定的 PR 已 closed/merged：另开新分支 + 新 PR。
   - PR 标题要说清这次改动的结果；分支更新时重新审视标题是否还匹配当前范围。
6. 按 `.github/pull_request_template.md` 写/刷新 PR 描述：
   - 每一节都填具体内容，替换掉所有 `<!-- ... -->` 占位注释。
   - 保留模板的清单结构。
   - **「核验记录」一节是本仓库特有的必填项**：MathNet 入库题逐题写清
     `mathnet_id`、`review_ref`、评审结论；非入库改动写「不涉及」。
     这一节留空的 PR 不允许交人工评审。
   - 分支更新时刷新描述，使其覆盖**整条分支的全部范围**，而不只是最新几个提交；
     不要沿用过时的旧描述。
7. 在 PR 描述里写上 Linear 工单链接（工单板是 Linear，不是 GitHub issue）。
   分支名带工单标识（`symphony/<标识小写>-...`）时，Linear 会自动把 PR 关联回工单。
8. 回复 `gh pr view` 给出的 PR URL。

## 命令

```sh
branch=$(git branch --show-current)

# 门槛
bash scripts/lint.sh

# 首次推送：沿用当前 origin
git push -u origin HEAD

# 若因远端前进而失败，先走 pull skill，重跑 lint，再推：
git push -u origin HEAD

# 仅在本地改写过历史时：
git push --force-with-lease origin HEAD

# 确保 PR 存在（不存在才创建）
pr_state=$(gh pr view --json state -q .state 2>/dev/null || true)
if [ "$pr_state" = "MERGED" ] || [ "$pr_state" = "CLOSED" ]; then
  echo "当前分支绑定的 PR 已关闭；请另开分支与 PR。" >&2
  exit 1
fi

pr_title="<说清本次改动结果的标题>"
if [ -z "$pr_state" ]; then
  gh pr create --title "$pr_title" --body-file /tmp/pr_body.md
else
  gh pr edit --title "$pr_title" --body-file /tmp/pr_body.md
fi

gh pr view --json url -q .url
```

## 注意

- 不要用 `--force`；`--force-with-lease` 也只作为最后手段。
- 区分两类失败：
  - 同步问题（non-fast-forward、分支落后）→ 走 `pull` skill。
  - 认证 / 权限 / workflow 限制 → 直接如实上报，不做绕过。
