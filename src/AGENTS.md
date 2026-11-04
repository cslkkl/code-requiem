# src/ — 规则层

继承根规则，见 [../AGENTS.md](../AGENTS.md)。

## 本目录特有约束

- **分析层禁止调用 LLM、禁止随机数**：`scoring.py` / `git_metrics.py` / `deps.py` / `github_api.py`
  必须是确定性的。只有 `llm.py` / `narrative.py` 允许走网络请求 AI。
- **任何新增网络调用必须三件套**：超时 + 缓存 + 失败降级。缺一个就是 bug。
- 新增模块必须在 [README.md](README.md) 的分层职责表登记，否则下一个人找不到它。
- 中文 Markdown 模板保留 `%` 格式化（ruff `UP031` 已忽略，理由见
  [.agents/notes/2026-11-05-ignore-up031.md](../.agents/notes/2026-11-05-ignore-up031.md)）。
- 双导入块（`try: from . import x / except ImportError: import x`）不改写成单一风格。
