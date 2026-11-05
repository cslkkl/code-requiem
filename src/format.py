"""界面层格式化助手 —— 纯函数，不 import streamlit。

这样做的原因：
  1. 所有「数字怎么变成人话」的规则可以脱离界面单测（verifier 也能直接调）；
  2. app.py 只负责排版，不负责判断，页面逻辑不会被格式化分支淹没；
  3. 契约（docs/CONTRACT.md 第 2 节）里的任何字段缺失都只能退化成 "—"，
     绝不允许把 KeyError 抛到页面上。

用法：
    from src import format as fmt
    fmt.verdict(metrics)          # 一句结论
    fmt.dim_rows(metrics)         # 六维分数条
    fmt.key_metrics(metrics)      # 八张关键指标卡
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = [
    "DIM_ORDER",
    "MODE_META",
    "MODE_UNKNOWN",
    "safe_get",
    "mode_from_total",
    "mode_meta",
    "score_int",
    "fmt_int",
    "fmt_count",
    "human_days",
    "human_span",
    "human_days_precise",
    "fmt_ratio",
    "fmt_chars",
    "fmt_when",
    "repo_label",
    "repo_url",
    "dim_rows",
    "key_metrics",
    "verdict",
    "doc_kicker",
    "band_note",
    "llm_badge",
    "source_label",
    "error_panel",
    "elapsed_text",
    "first_author",
]

# ─────────────────────────────────────────────────────────────────────────────
# 常量：模式 → 视觉与文案
# ─────────────────────────────────────────────────────────────────────────────

# 语义色：淡底 + 深字。tone 是用于**文字与细线**的深色，tone_soft 是用于**底块**的淡色。
# 暖白主题下不能用大色块，所以 status_bg / status_ink 成对出现。
MODE_META: dict[str, dict[str, str]] = {
    "alive": {
        "label": "存活",
        "doc": "代码遗嘱",
        "band": "70 – 100",
        "tone": "#346538",  # 深苔绿（用于文字与分数环）
        "tone_soft": "rgba(52,101,56,0.08)",
        "status_bg": "#EDF3EC",
        "status_ink": "#346538",
        "hint": "仍在呼吸：交接优先，悼词暂不触发。",
    },
    "critical": {
        "label": "承压",
        "doc": "临终关怀",
        "band": "30 – 69",
        "tone": "#8A6209",  # 深琥珀
        "tone_soft": "rgba(138,98,9,0.08)",
        "status_bg": "#FBF3DB",
        "status_ink": "#8A6209",
        "hint": "体征不稳：遗嘱 + 预演悼词同时输出。",
    },
    "dead": {
        "label": "已故",
        "doc": "项目悼词",
        "band": "0 – 29",
        "tone": "#9F2F2D",  # 深砖红
        "tone_soft": "rgba(159,47,45,0.08)",
        "status_bg": "#FDEBEC",
        "status_ink": "#9F2F2D",
        "hint": "已停止更新：只出具悼词，不再假设它会活。",
    },
}

MODE_UNKNOWN: dict[str, str] = {
    "label": "未知",
    "doc": "文档",
    "band": "—",
    "tone": "#787774",
    "tone_soft": "rgba(120,119,116,0.08)",
    "status_bg": "#F7F6F3",
    "status_ink": "#787774",
    "hint": "健康分缺失。",
}

# 与 CONTRACT 第 2 节 score.parts 的键一一对应，顺序即展示顺序。
DIM_ORDER: list[tuple[str, str, int]] = [
    ("last_commit", "最后提交", 30),
    ("bus_factor", "bus factor", 25),
    ("trend", "活跃趋势", 15),
    ("deps", "依赖健康", 15),
    ("issues", "Issue 响应", 10),
    ("docs", "文档完整度", 5),
]

_DEP_STATUS = {
    "ok": "依赖清单已解析",
    "timeout": "依赖接口超时，按中性分计",
    "none": "没有依赖清单",
    "unsupported": "生态暂无公开 API",
}

_ISSUE_STATUS = {
    "ok": "GitHub issue 已统计",
    "unavailable": "issue 数据未获取",
    "not_github": "非 GitHub 仓库，不统计 issue",
}

_DASH = "—"


# ─────────────────────────────────────────────────────────────────────────────
# 基础设施：安全取值
# ─────────────────────────────────────────────────────────────────────────────


def safe_get(data, path: str, default=None):
    """按 "a.b.c" 取嵌套字典值，任何一层缺失都返回 default。"""
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def _num(value, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


def _int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None if value is None else int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ─────────────────────────────────────────────────────────────────────────────
# 模式
# ─────────────────────────────────────────────────────────────────────────────


def mode_from_total(total) -> str:
    """SPEC 3.7：≥70 存活 / 30–69 承压 / <30 已故。"""
    t = _int(total)
    if t is None:
        return "critical"
    if t >= 70:
        return "alive"
    if t >= 30:
        return "critical"
    return "dead"


def mode_meta(mode: str | None) -> dict[str, str]:
    return MODE_META.get(str(mode or ""), MODE_UNKNOWN)


def score_int(metrics: dict) -> int | None:
    return _int(safe_get(metrics, "score.total"))


# ─────────────────────────────────────────────────────────────────────────────
# 数字 → 人话
# ─────────────────────────────────────────────────────────────────────────────


def fmt_int(value) -> str:
    """1234 → 1,234；缺失 → —。"""
    n = _int(value)
    return _DASH if n is None else f"{n:,}"


def fmt_count(value) -> str:
    """大数字压缩：12345 → 1.2 万，1.2e8 → 1.2 亿。"""
    n = _int(value)
    if n is None:
        return _DASH
    if n < 10_000:
        return f"{n:,}"
    if n < 100_000_000:
        return f"{n / 10_000:.1f} 万".replace(".0 ", " ")
    return f"{n / 100_000_000:.1f} 亿".replace(".0 ", " ")


def human_days(days) -> str:
    """距今天数 → 1 天 / 3 个月 / 2 年 3 个月 / 9 年。"""
    d = _int(days)
    if d is None:
        return _DASH
    d = max(0, d)
    if d < 60:
        return f"{d} 天"
    if d < 365:
        return f"{max(1, round(d / 30))} 个月"
    years, rem = divmod(d, 365)
    months = rem // 30
    if years and months:
        return f"{years} 年 {months} 个月"
    return f"{years} 年"


def human_span(days) -> str:
    """总跨度，用词更重：9 年 4 个月。"""
    return human_days(days)


def human_days_precise(days) -> str:
    """小数天数：0.3 天 / 5.6 天 / 3 个月。

    中位响应这类指标经常小于一天，直接取整会变成刺眼的 "0 天"。
    """
    if days is None:
        return _DASH
    try:
        value = float(days)
    except (TypeError, ValueError):
        return _DASH
    if value < 0:
        return _DASH
    if value < 1:
        return f"{value:.1f} 天"
    if value < 10 and abs(value - round(value)) > 0.05:
        return f"{value:.1f} 天"
    return human_days(int(round(value)))


def fmt_ratio(value) -> str:
    r = _num(value, -1.0)
    if r < 0:
        return _DASH
    return f"{r:.2f}×"


def fmt_chars(value) -> str:
    n = _int(value)
    if n is None:
        return _DASH
    if n <= 0:
        return "无 README"
    return f"{n:,} 字符"


def fmt_when(iso: str | None) -> str:
    """ISO8601 → YYYY-MM-DD；解析不了就原样截断。"""
    if not iso or not isinstance(iso, str):
        return _DASH
    text = iso.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return text[:10]


def elapsed_text(seconds) -> str:
    s = _num(seconds, -1.0)
    if s < 0:
        return _DASH
    if s < 1:
        return f"{s * 1000:.0f} ms"
    if s < 60:
        return f"{s:.1f} s"
    return f"{int(s // 60)} 分 {int(s % 60)} 秒"


# ─────────────────────────────────────────────────────────────────────────────
# 仓库标识
# ─────────────────────────────────────────────────────────────────────────────


def repo_label(metrics: dict) -> str:
    owner = str(safe_get(metrics, "repo.owner", "") or "").strip()
    name = str(safe_get(metrics, "repo.name", "") or "").strip()
    if owner and name:
        return f"{owner}/{name}"
    if name:
        return name
    return str(safe_get(metrics, "repo.input", "") or "未知仓库")


def repo_url(metrics: dict) -> str:
    return str(safe_get(metrics, "repo.url", "") or safe_get(metrics, "repo.input", "") or "")


def first_author(metrics: dict) -> dict:
    top = safe_get(metrics, "authors.top", []) or []
    if isinstance(top, list) and top and isinstance(top[0], dict):
        return top[0]
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 六维分数条
# ─────────────────────────────────────────────────────────────────────────────


def dim_rows(metrics: dict) -> list[dict]:
    """返回六维（按契约顺序）以及契约里多出来的维度。每项：
    {key, name, score, max, ratio, detail, pct, fact, mechanism, impact}

    fact / mechanism / impact 来自 src/explain.py：说清「观察到什么、为什么扣分、
    对要接手的人意味着什么」。explain 缺失时这三项为空串，界面只显示 detail。
    """
    parts = safe_get(metrics, "score.parts", {}) or {}
    if not isinstance(parts, dict):
        parts = {}
    explain = safe_get(metrics, "explain", {}) or {}
    if not isinstance(explain, dict):
        explain = {}
    meta = mode_meta(safe_get(metrics, "score.mode") or mode_from_total(score_int(metrics)))
    rows: list[dict] = []
    seen: set[str] = set()

    def _explained(key: str, part: dict) -> dict:
        e = explain.get(key)
        e = e if isinstance(e, dict) else {}
        return {
            "fact": str(e.get("fact") or ""),
            "mechanism": str(e.get("mechanism") or ""),
            "impact": str(e.get("impact") or ""),
            # 兜底：explain 没给就退回一段式 detail
            "detail": str(e.get("fact") or part.get("detail") or "该项未提供说明（分析层降级）"),
        }

    for key, fallback_name, default_max in DIM_ORDER:
        seen.add(key)
        part = parts.get(key)
        part = part if isinstance(part, dict) else {}
        score = _int(part.get("score")) or 0
        mx = _int(part.get("max")) or default_max
        ratio = _clamp01(score / mx) if mx else 0.0
        ex = _explained(key, part)
        rows.append(
            {
                "key": key,
                # 优先用「中文问法」（label_plain），读者不需要先懂术语。
                # label 是 SPEC 里的正式名称，留在解剖面板里备查。
                "name": str(part.get("label_plain") or part.get("label") or fallback_name),
                "spec_name": str(part.get("label") or fallback_name),
                "score": score,
                "max": mx,
                "ratio": ratio,
                "pct": round(ratio * 100),
                "detail": ex["detail"],
                "fact": ex["fact"],
                "mechanism": ex["mechanism"],
                "impact": ex["impact"],
                "tone": meta["tone"],
            }
        )

    for key, part in parts.items():
        if key in seen or not isinstance(part, dict):
            continue
        score = _int(part.get("score")) or 0
        mx = _int(part.get("max")) or 0
        ratio = _clamp01(score / mx) if mx else 0.0
        ex = _explained(key, part)
        rows.append(
            {
                "key": str(key),
                "name": str(part.get("label_plain") or part.get("label") or key),
                "spec_name": str(part.get("label") or key),
                "score": score,
                "max": mx,
                "ratio": ratio,
                "pct": round(ratio * 100),
                "detail": ex["detail"],
                "fact": ex["fact"],
                "mechanism": ex["mechanism"],
                "impact": ex["impact"],
                "tone": meta["tone"],
            }
        )
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 关键指标卡（八张）
# ─────────────────────────────────────────────────────────────────────────────


def key_metrics(metrics: dict) -> list[dict]:
    """返回 8 张卡：{label, value, sub, help}。全部容错。"""
    sampled = bool(safe_get(metrics, "sampled", False))
    life_commits = safe_get(metrics, "life.total_commits")
    days_last = safe_get(metrics, "life.days_since_last_commit")
    span = safe_get(metrics, "life.span_days")

    bf = _int(safe_get(metrics, "authors.bus_factor"))
    total_authors = _int(safe_get(metrics, "authors.total"))
    top = first_author(metrics)
    top_share = _num(top.get("share"), -1.0) if top else -1.0

    loc = _int(safe_get(metrics, "size.loc"))
    files = _int(safe_get(metrics, "size.tracked_files"))
    modules = _int(safe_get(metrics, "size.modules"))

    oldest_days = safe_get(metrics, "deps.oldest_days")
    oldest_pkg = safe_get(metrics, "deps.oldest_pkg")
    deps_status = str(safe_get(metrics, "deps.status", "") or "")
    deps_count = _int(safe_get(metrics, "deps.count"))

    issues_status = str(safe_get(metrics, "issues.status", "") or "")
    median = safe_get(metrics, "issues.median_days")
    closed = _int(safe_get(metrics, "issues.closed_count"))
    opened = _int(safe_get(metrics, "issues.open_count"))

    readme_chars = _int(safe_get(metrics, "docs.readme_chars"))
    has_readme = bool(safe_get(metrics, "docs.has_readme", False))
    doc_part = safe_get(metrics, "score.parts.docs", {}) or {}
    doc_score = _int(doc_part.get("score")) if isinstance(doc_part, dict) else None
    doc_max = _int(doc_part.get("max")) if isinstance(doc_part, dict) else None

    cards: list[dict] = []

    # 1 bus factor
    cards.append(
        {
            "label": "BUS FACTOR",
            "value": _DASH if bf is None else f"{bf}",
            "sub": (f"前 {bf} 人承担 ≥60% 提交" if bf not in (None, 0) else "无法计算（提交数为 0）"),
            "help": "把贡献者按提交数排序，累计到 60% 时需要几个人。等于 1 就是单点。",
        }
    )

    # 2 最后提交距今
    days_int = _int(days_last)
    if days_int == 0:
        last_value, last_sub = "今天", "0 天"
    elif days_int is not None and days_int == 1:
        last_value, last_sub = "昨天", "1 天"
    else:
        last_value, last_sub = human_days(days_last), (_DASH if days_int is None else f"{days_int} 天")
    cards.append(
        {
            "label": "最后提交距今",
            "value": last_value,
            "sub": last_sub,
            "help": "默认分支最后一次提交的 committer date。",
        }
    )

    # 3 作者数
    cards.append(
        {
            "label": "作者数",
            "value": _DASH if total_authors is None else f"{total_authors} 人",
            "sub": (f"主导者占 {top_share * 100:.0f}%" if top_share >= 0 else "无作者信息"),
            "help": "按 commit author email 去重，同一个人多个名字算一个人。",
        }
    )

    # 4 提交数
    cards.append(
        {
            "label": "提交数",
            "value": fmt_int(life_commits),
            "sub": "已采样" if sampled else f"跨度 {human_span(span)}",
            "help": "本次分析范围内的提交数量；体量大时只取最近 2000 个。",
        }
    )

    # 5 代码行数
    cards.append(
        {
            "label": "代码行数",
            "value": fmt_count(loc),
            "sub": (
                f"{fmt_int(files)} 文件" + (f" / {modules} 模块" if modules else "")
                if files is not None
                else _DASH
            ),
            "help": "受跟踪文件的物理行数总和（含空行与注释）。",
        }
    )

    # 6 依赖最老天数
    dep_value = human_days(oldest_days) if oldest_days is not None else "未获取"
    dep_sub = _DEP_STATUS.get(deps_status, "依赖状态未知")
    if oldest_pkg and oldest_days is not None:
        dep_sub = f"最老：{str(oldest_pkg)[:22]}"
    elif deps_count is not None and deps_status == "ok":
        dep_sub = f"{deps_count} 个依赖"
    cards.append(
        {
            "label": "依赖最老天数",
            "value": dep_value,
            "sub": dep_sub,
            "help": "依赖里最久没更新那个包的年龄。最弱环节决定这一项得分。",
        }
    )

    # 7 issue 中位响应
    if median is None:
        issue_value = "不可用"
    else:
        issue_value = human_days_precise(median)
    issue_sub = _ISSUE_STATUS.get(issues_status, "issue 状态未知")
    if closed is not None or opened is not None:
        issue_sub = f"{fmt_int(closed or 0)} 已关闭 / {fmt_int(opened or 0)} 开放"
    cards.append(
        {
            "label": "Issue 中位响应",
            "value": issue_value,
            "sub": issue_sub,
            "help": "已关闭 issue 从创建到关闭的中位天数，代表维护者的应答速度。",
        }
    )

    # 8 README 字符数
    cards.append(
        {
            "label": "README 字符数",
            "value": fmt_chars(readme_chars if has_readme else 0),
            "sub": (
                f"文档分 {doc_score}/{doc_max}"
                if doc_score is not None and doc_max
                else ("有 README" if has_readme else "无文档")
            ),
            "help": "README 长度与是否包含安装 / 用法 / 示例，决定文档完整度分。",
        }
    )

    return cards


# ─────────────────────────────────────────────────────────────────────────────
# 一句结论
# ─────────────────────────────────────────────────────────────────────────────


def verdict(metrics: dict) -> str:
    """用指标拼一句结论。确定性，不走 LLM —— 保证 LLM 挂了也有话说。"""
    total = score_int(metrics)
    mode = str(safe_get(metrics, "score.mode") or mode_from_total(total))
    days_last = safe_get(metrics, "life.days_since_last_commit")
    bf = _int(safe_get(metrics, "authors.bus_factor"))
    authors = _int(safe_get(metrics, "authors.total"))
    top = first_author(metrics)
    share = _num(top.get("share"), -1.0) if top else -1.0
    top_name = str(top.get("name") or "").strip()

    when = human_days(days_last)
    if days_last is not None and _int(days_last) == 0:
        when_phrase = "最后一次提交就在今天"
    elif days_last is None:
        when_phrase = "最后一次提交时间未知"
    else:
        when_phrase = f"最后一次提交在 {when}前"

    if not (safe_get(metrics, "score.parts") or {}) and safe_get(metrics, "score.total") is None:
        return "没有拿到有效的六维指标，这个仓库暂时无法判读。"

    if mode == "alive":
        parts = [when_phrase]
        if authors:
            parts.append(f"{authors} 位贡献者共同维持")
        if bf is not None and bf >= 3:
            parts.append(f"bus factor = {bf}，交接风险可控")
        elif bf is not None:
            parts.append(f"但 bus factor 只有 {bf}，交接前必须先补第二个人")
        return "，".join(parts) + "。"

    if mode == "critical":
        risk = _worst_dim(metrics)
        tail = f"短板是 {risk[0]}（{risk[1]}/{risk[2]}）"
        if bf == 1:
            who = f"（{top_name} 一人写下 {share * 100:.0f}% 的提交）" if top_name and share >= 0 else ""
            return f"{when_phrase}，bus factor = 1{who}，{tail}——还有人愿意接手，现在就是最后窗口。"
        return f"{when_phrase}，{tail}——体征还在，但已经不允许再放两年不管。"

    # dead
    detail = ""
    if authors == 1 and top_name:
        detail = f"，而全部提交来自 {top_name} 一人"
    elif bf == 1 and top_name and share >= 0:
        detail = f"，而其中 {share * 100:.0f}% 出自 {top_name} 一人"
    elif bf == 1:
        detail = "，且只有一个维护者"
    score_text = total if total is not None else _DASH
    return f"{when_phrase}{detail}，{score_text} 分的体征已经不允许它再站起来。"


def _worst_dim(metrics: dict) -> tuple[str, int, int]:
    rows = dim_rows(metrics)
    if not rows:
        return ("健康分", 0, 0)
    worst = min(rows, key=lambda r: (r["ratio"], r["max"]))
    return (worst["name"], worst["score"], worst["max"])


def doc_kicker(metrics: dict) -> str:
    mode = str(safe_get(metrics, "score.mode") or mode_from_total(score_int(metrics)))
    return mode_meta(mode)["doc"]


def band_note(metrics: dict) -> str:
    mode = str(safe_get(metrics, "score.mode") or mode_from_total(score_int(metrics)))
    meta = mode_meta(mode)
    return f"{meta['label']}档 {meta['band']} · {meta['hint']}"


# ─────────────────────────────────────────────────────────────────────────────
# LLM 状态
# ─────────────────────────────────────────────────────────────────────────────


def llm_badge(info: dict | None) -> dict:
    """provider_info() → 徽标文案。

    在线："AI 叙事：在线（模型名）"；离线："AI 叙事：离线（将降级为结构化指标）"
    """
    info = info if isinstance(info, dict) else {}
    configured = bool(info.get("configured"))
    model = str(info.get("model") or "").strip()
    source = str(info.get("source") or "").strip()
    endpoint = str(info.get("endpoint") or "").strip()

    if configured:
        text = f"AI 叙事：在线（{model or '未命名模型'}）"
        return {
            "online": True,
            "text": text,
            "tone": "#346538",
            "detail": f"来源 {source or '未知'}" + (f" · {endpoint}" if endpoint else ""),
        }
    return {
        "online": False,
        "text": "AI 叙事：离线（将降级为结构化指标）",
        "tone": "#8A6209",
        "detail": f"来源 {source or '未配置'}"
        + (f" · {endpoint}" if endpoint else "")
        + "；没有 key 时仍会输出结构化文档，只是没有叙事文本。",
    }


def source_label(info: dict | None) -> str:
    info = info if isinstance(info, dict) else {}
    mapping = {
        "env": "显式环境变量",
        "huggingface": "Hugging Face Router",
        "hf": "Hugging Face Router",
        "local": "本地开发网关",
        "none": "未配置",
        "missing": "模块缺失",
    }
    key = str(info.get("source") or "").strip()
    if key in mapping:
        return mapping[key]
    return key or "未配置"


# ─────────────────────────────────────────────────────────────────────────────
# 错误面板
# ─────────────────────────────────────────────────────────────────────────────

_ERROR_TEXT = {
    "not_found": ("仓库不存在或不可见", "无法访问该仓库，请确认它是公开的，并且地址拼写正确。"),
    "private": ("这是私有仓库", "无法访问该仓库，请确认它是公开的。私有仓库不上传凭据，我们看不到它。"),
    "empty": ("这个项目还没有出生", "0 次提交 —— 连第一行代码都还不存在，没有可供安葬的东西。"),
    "network": ("网络没有接通", "克隆或接口请求失败。检查网络后重试，或换一个仓库地址。"),
    "invalid": ("这不像一个 Git 仓库地址", "请输入 GitHub / GitLab 的仓库链接，或本机的仓库路径。"),
}

_EMPTY_GLYPH = "· · ·"


def error_panel(error: dict | None) -> dict:
    """analyzer 的 error 对象 → 展示用文案。返回 {title, detail, kind, ghost}。"""
    err = error if isinstance(error, dict) else {}
    kind = str(err.get("kind") or "unknown")
    raw = str(err.get("message") or "").strip()

    # 未认证的 GitHub API 每小时 60 次。限流时后端常把它报成 not_found，
    # 界面上必须说清楚是「额度用完了」而不是「仓库不存在」，否则演示时误导人。
    lowered = raw.lower()
    if "rate limit" in lowered or "速率" in raw or "403" in raw or "额度" in raw:
        return {
            "title": "GitHub 接口限流",
            "detail": (
                "未认证的 GitHub API 每小时只有 60 次额度，现在用完了。"
                "等整点过后再试，或给后端配一个 GITHUB_TOKEN。"
                "分析层本身没有问题，换成本地路径 / 其他仓库也能继续演示。"
            ),
            "kind": "rate_limit",
            "raw": raw,
            "ghost": "",
        }

    title, detail = _ERROR_TEXT.get(kind, ("分析中断", "分析没有完成。下面的原始信息通常能说明原因。"))
    if kind == "empty":
        return {
            "title": title,
            "detail": detail,
            "kind": kind,
            "raw": raw,
            "ghost": _EMPTY_GLYPH,
        }
    return {"title": title, "detail": detail, "kind": kind, "raw": raw, "ghost": ""}
