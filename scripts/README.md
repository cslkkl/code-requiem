# scripts/ — 运维脚本

| 脚本 | 做什么 | 副作用 | 何时跑 |
|---|---|---|---|
| `verify_live.py` | 跑三个真实仓库，验证三种模式 + 确定性，结果写 `docs/verification.json` | 消耗 GitHub API 配额（约 20-40 次）；写一个文件 | 改动分析层或生成层后 |
| `push_via_api.py` | `git push` 失败时，用 GitHub Git Data API 重放本地 commit | **改远端 ref**；本地需 `git fetch && git reset --hard` 对齐 | `git push` 报 `Failed to connect to github.com port 443` 时 |

## 用法

```powershell
# 验证（会自己找 token）
$env:GITHUB_TOKEN = gh auth token
.venv\Scripts\python.exe -u scripts\verify_live.py

# 推送（先 dry-run）
$env:GITHUB_TOKEN = gh auth token
.venv\Scripts\python.exe -u scripts\push_via_api.py --dry-run
.venv\Scripts\python.exe -u scripts\push_via_api.py
```

## 为什么需要 push_via_api.py

本机 `github.com:443` 间歇不可达（TCP 21 秒超时），`git push` 必然失败；
`api.github.com` 始终正常。决策记录见
[.agents/notes/2026-11-05-push-via-github-api.md](../.agents/notes/2026-11-05-push-via-github-api.md)。

## 约束与偏好

见 [AGENTS.md](AGENTS.md)。
