# 26. The CLI records the head commit, merge-base, and base branch

Status: accepted. Refines ADR-0007.

## Context

ADR-0007 picks the newest completed experiment at the PR's merge-base commit, else the newest on `main`. The server has no git repository, so it can't compute a merge-base. Phase 3's runner records `GITHUB_SHA` as the commit, which on a `pull_request` event is GitHub's temporary merge commit: neither the PR head nor anything on the base branch. And not every repo's base branch is `main`.

## Decision

- On a pull request, the CLI records the PR head SHA, read from the event payload at `GITHUB_EVENT_PATH`, as `git_commit`. Elsewhere it keeps Phase 3's behavior.
- The CLI computes the merge-base of the recorded commit (the PR head, not the checked-out merge commit, whose merge-base would be the base branch's tip) with `git merge-base <commit> origin/<base>`, and records it as `git_merge_base`, along with `git_base_branch`. Outside GitHub, `trayce eval --base <branch>` does the same. The GitHub Action unshallows the checkout and fetches the base branch, so the history is there. Outside a PR, both are empty.
- The server selects a baseline from `(project, eval.yaml hash, git_merge_base, git_base_branch)`: newest completed experiment whose `git_commit` is the merge-base, else newest completed on the base branch, else none.

## Consequences

- An experiments migration adds `git_merge_base` and `git_base_branch`.
- Merge-base baselines exist only if the base branch runs evals on every push; the Action does that without gating.
- A shallow checkout gives no merge-base; the CLI then falls back to the base branch and says so.
