# scripts/ — 规则层

继承根规则，见 [../AGENTS.md](../AGENTS.md)。

## 本目录特有约束

- 脚本是**运维工具**，不进 Streamlit 运行路径，HF Spaces 也不需要它们
- 涉及网络的脚本必须自己处理 token（环境变量优先，`gh auth token` 兜底），**绝不写死密钥**
- `push_via_api.py` 会改远端 ref：运行前先 `--dry-run` 确认待推送 commit 清单
- `verify_live.py` 会消耗 GitHub API 配额（每个仓库 6-14 次请求），不要放进循环里反复跑
- 新增脚本在 [README.md](README.md) 登记用途与副作用
