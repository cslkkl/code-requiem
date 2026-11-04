# 模块契约（内部接口约定）

> 本文件是所有并行开发模块之间的唯一接口事实来源。三个模块必须严格按此约定实现，
> 改动契约必须先改本文件并在 commit message 里写明。

## 1. 分析层（src/analyzer.py）

唯一公开入口：

```python
def analyze_repo(url: str, *, now: datetime | None = None, token: str | None = None) -> dict:
    """分析任意公开 Git 仓库。永远返回 dict，永不抛出未捕获异常。"""
```

出错时不抛异常，返回 `{"ok": False, "error": {...}, "repo": {...}}`。

## 2. 分析结果 schema

```python
{
  "ok": True,
  "error": None,                      # 或 {"kind": "not_found|private|empty|network|invalid", "message": str}
  "repo": {
      "input": str,                   # 用户原始输入
      "host": str,                    # "github" | "gitlab" | "local" | "other"
      "owner": str,
      "name": str,
      "url": str,
      "is_local": bool,
  },
  "sampled": bool,                    # 是否因体量采样
  "sample_note": str | None,          # 面向用户的中文说明
  "life": {
      "first_commit": str,            # ISO8601
      "last_commit": str,
      "last_commit_msg": str,
      "total_commits": int,           # 本次分析范围内
      "age_days": int,
      "span_days": int,
      "days_since_last_commit": int,
      "age_text": str,                # "3 年 2 个月"
  },
  "size": {
      "tracked_files": int,
      "loc": int,
      "modules": int,
      "top_dirs": [{"name": str, "files": int}],
      "languages": [{"name": str, "files": int}],
  },
  "authors": {
      "total": int,
      "bus_factor": int,
      "top": [{"name": str, "email": str, "commits": int, "share": float}],
  },
  "trend": {
      "recent_6m": int,
      "previous_6m": int,
      "ratio": float,
      "insufficient_history": bool,
  },
  "deps": {
      "status": str,                  # "ok" | "timeout" | "none" | "unsupported"
      "ecosystem": str,               # "python" | "node" | "go" | "none"
      "count": int,
      "oldest_pkg": str | None,
      "oldest_days": int | None,
      "items": [{"name": str, "version": str, "days": int | None}],
  },
  "issues": {
      "status": str,                  # "ok" | "unavailable" | "not_github"
      "closed_count": int,
      "open_count": int,
      "median_days": float | None,
  },
  "docs": {
      "has_readme": bool,
      "readme_path": str | None,
      "readme_chars": int,
      "has_install": bool,
      "has_usage": bool,
      "has_example": bool,
  },
  "risks": {
      "todo_count": int,
      "fixme_count": int,
      "stale_core_files": [{"path": str, "days_untouched": int, "commits": int, "loc": int}],
      "hotspots": [{"path": str, "line": int, "kind": str, "text": str}],
  },
  "score": {
      "total": int,                   # 0-100 整数
      "mode": str,                    # "alive" | "critical" | "dead"
      "parts": {
          "last_commit": {"score": int, "max": 30, "label": str, "detail": str},
          "bus_factor":  {"score": int, "max": 25, "label": str, "detail": str},
          "trend":       {"score": int, "max": 15, "label": str, "detail": str},
          "deps":        {"score": int, "max": 15, "label": str, "detail": str},
          "issues":      {"score": int, "max": 10, "label": str, "detail": str},
          "docs":        {"score": int, "max": 5,  "label": str, "detail": str},
      },
  },
  "elapsed_sec": float,
  "deterministic_key": str,           # 用于验证确定性的指纹
}
```

## 3. 模式判定

| total | mode | 输出 |
|---|---|---|
| >= 70 | `alive` | 遗嘱 |
| 30-69 | `critical` | 临终关怀（遗嘱 + 悼词） |
| < 30 | `dead` | 悼词 |

## 4. 生成层（src/narrative.py）

```python
def generate_narrative(metrics: dict, *, mode: str | None = None) -> dict:
    """
    返回:
    {
      "ok": bool,
      "degraded": bool,        # True = LLM 不可用，已降级
      "degraded_reason": str | None,
      "mode": str,             # alive | critical | dead
      "model": str | None,
      "markdown": str,         # 最终要渲染的完整 Markdown（降级时也必须有内容）
      "sections": {"will": str, "eulogy": str},  # 分章 Markdown，可为空串
      "elapsed_sec": float,
    }
    """
```

约束：
- `markdown` **任何情况下都不为空**。LLM 挂了必须降级为「仅结构化指标」的 Markdown，
  顶部带 `> ⚠️ AI 叙事生成不可用，以下是结构化指标。` 提示。
- 降级时 `sections` 里的内容用模板从 metrics 直接渲染（确定性，不走 LLM）。
- 不允许抛出未捕获异常。

## 5. LLM 配置（src/llm.py）

优先级：
1. `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`（显式覆盖，任意 OpenAI 兼容网关）
2. `HF_TOKEN` → `https://router.huggingface.co/v1` + `Qwen/Qwen2.5-7B-Instruct`
3. 本地开发网关探测：`http://127.0.0.1:8317/v1` + `CPA_API_KEY`（读 `~/.dsh/.credentials.yaml`，仅本地开发用，绝不写入仓库）

```python
def chat(messages: list[dict], *, max_tokens: int = 2048, temperature: float = 0.4,
         timeout: int = 60, json_mode: bool = False) -> dict:
    """返回 {"ok": bool, "text": str, "model": str, "error": str|None, "source": str}"""
def provider_info() -> dict:
    """返回 {"configured": bool, "source": str, "model": str, "endpoint": str}"""
```

## 6. 界面层（app.py）

- 单输入框 + 分析按钮 + 结果渲染（`st.markdown`）
- 顶部显示 LLM 状态徽标；降级时显示提示条
- 任何异常都要捕获并渲染成友好提示
- 侧边栏放三个「示例仓库」快捷按钮
- 结果页展示：总分环形/进度、六维分数条、关键指标表、生成的 Markdown 文档
- 必须支持 `streamlit run app.py` 直接跑通，不需要额外参数

## 7. 文件归属（避免并行写冲突）

| 路径 | 归属 |
|---|---|
| `src/analyzer.py`, `src/scoring.py`, `src/git_metrics.py`, `src/deps.py`, `src/issues.py`, `src/schema.py` | lead |
| `src/llm.py`, `src/narrative.py`, `src/prompts.py` | narrator |
| `app.py`, `src/format.py`, `.streamlit/config.toml` | ui |
| `README.md`, `docs/DEVPOST.md`, `docs/DEMO.md`, `requirements.txt` | docs |
| `tests/**` | verifier |
