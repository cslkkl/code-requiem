# src/ — 核心模块手册

## 分层职责

| 文件 | 职责 | 关键导出 | 被谁依赖 |
|---|---|---|---|
| `analyzer.py` | 编排全流程、错误分类、CLI 入口 | `analyze_repo(url, now, token, progress)` | `app.py`、`scripts/verify_live.py` |
| `scoring.py` | 六维打分的**纯函数**，SPEC 第 3 章逐条实现 | `score_last_commit`、`bus_factor`、`score_trend`、`score_deps`、`score_issues`、`score_docs`、`decide_mode` | `analyzer.py`、`tests/test_scoring.py` |
| `git_metrics.py` | 克隆、提交历史、文件级风险扫描 | `clone_repo`、`collect_commits`、`count_loc`、`analyze_readme`、`scan_risks`、`stale_core_files`、`human_age` | `analyzer.py` |
| `deps.py` | 依赖健康度（PyPI / npm），带磁盘缓存 | `analyze_deps`、`detect_manifest` | `analyzer.py` |
| `github_api.py` | GitHub REST：元信息、提交计数、issue、配额感知 | `repo_meta`、`commit_count`、`analyze_issues`、`rate_limit_status`、`has_token` | `analyzer.py`、`issues.py` |
| `issues.py` | 兼容转出层 | 转出 `github_api` 的三个函数 | 旧代码 |
| `net.py` | TCP 可达性探测（3 秒超时，60 秒缓存） | `github_git_available`、`tcp_open` | `fetch_api.py`、`analyzer.py` |
| `fetch_api.py` | clone 不可用时的 API 兜底（tarball + commits） | `fetch_commits`、`repo_meta`、`download_tarball`、`file_last_commit_days` | `analyzer.py` |
| `llm.py` | OpenAI 兼容客户端，三级 provider 解析 | `chat`、`provider_info` | `narrative.py` |
| `prompts.py` | 遗嘱 / 悼词 prompt 模板 + 章节标题常量 | `build_will_prompt`、`build_eulogy_prompt`、`WILL_HEADINGS`、`EULOGY_HEADINGS` | `narrative.py` |
| `narrative.py` | 三模式串联、降级模板 | `generate_narrative(metrics, mode)` | `app.py`、`scripts/*` |
| `format.py` | 界面层格式化工具 | 见文件内 docstring | `app.py` |

## 变更影响路由

| 改这里 | 必须同步 |
|---|---|
| `scoring.py` 任何阈值 | `tests/test_scoring.py` 对应断言 + `docs/SPEC.md` 第 3 章 + 根 AGENTS.md 验证快照 |
| `analyze_repo` 返回结构 | [docs/CONTRACT.md](../docs/CONTRACT.md) 第 2 节 + `src/format.py` + `app.py` |
| `narrative.generate_narrative` 返回结构 | [docs/CONTRACT.md](../docs/CONTRACT.md) 第 4 节 + `app.py` |
| 新增打分维度 | 同上 + `analyzer._build_metrics` 的 `parts` 字典（六维变七维要改 max 和展示） |
| 新增网络调用 | 必须有超时 + 缓存 + 失败降级，见 [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) |
| LLM 相关 | 必须同时实现降级模板，且降级输出与 LLM 输出同章节结构 |

## 必跑测试

```powershell
# 改打分：这个必须全绿
.venv\Scripts\python.exe -u -m unittest discover -s tests -p "test_scoring.py"

# 改边界处理 / 错误分类：这个必须全绿
.venv\Scripts\python.exe -u -m unittest discover -s tests -p "test_boundary.py"

# 改生成层：三个模式都要实测
.venv\Scripts\python.exe -u scripts\test_three_modes.py
```

## 双导入约定

`analyzer.py` / `issues.py` / `fetch_api.py` 同时支持包内导入与脚本模式导入：

```python
try:
    from . import scoring
except ImportError:
    import scoring
```

原因：`streamlit run app.py` 与 `python src/analyzer.py` 走不同的 `sys.path`。
不要改成单一风格，除非同时改掉 README、测试与 HF Spaces 的入口方式。

## 工作约束

见 [AGENTS.md](AGENTS.md)。
