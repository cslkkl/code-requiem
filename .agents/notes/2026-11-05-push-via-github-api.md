# 决策：github.com 不可达时改用 GitHub Git Data API 推送 (2026-11-05)

状态：生效

## 问题

本机 `github.com:443` 间歇性不可达（TCP 连接 21 秒后超时），`git push` 必然失败。
但 `api.github.com` 与 `codeload.github.com` 始终正常。

这直接影响交付：仓库必须能持续推送，否则「真实提交历史」这项验收做不到。

## 决策

`scripts/push_via_api.py` 用 GitHub Git Data API（blob → tree → commit → 更新 ref）
重放本地相对远端多出的 commit。只推送差异文件，不重传整个仓库。

`git push` 仍然是首选；只有在 `git push` 失败时用这个脚本兜底。

## 替代方案（强制）

- **等网络恢复后重试 `git push`**：不可控，且七天排期里每天都要提交，等待会阻塞节奏。
- **用 `gh repo sync`**：只支持从上游仓库同步，不支持把本地未推送的 commit 推上去。
- **换 SSH 协议**：`github.com:22` 同样不可达（同一个域名），换协议不解决。
- **改远端为镜像站**：会破坏 Devpost 要求的 `https://github.com/cslkkl/code-requiem` 链接。

## 影响

- 推送后本地与远端 sha 会不一致（API 创建的 commit sha 与本地不同），必须
  `git fetch origin && git reset --hard origin/main` 对齐，否则下次 diff 基准错。
- 脚本只做快进推送（`force: False`），远端有本地没有的 commit 时会明确报错而不是覆盖。
- 大文件走 base64 上传，单文件超过 40 MB 会失败（GitHub Git Data API 限制）。本仓库无此风险。
