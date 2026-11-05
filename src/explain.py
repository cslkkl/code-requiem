"""解释层 —— 把每一个分数翻译成「为什么扣分、这对要接手的人意味着什么」。

为什么单独一层，而不是塞进 scoring.py：
  * `scoring.py` 是 SPEC 第 3 章阈值表的逐条实现，只负责产生**数字**。
    它必须能被单元测试逐条比对，输出越纯越好。
  * 本模块只负责产生**人话**，不做任何判断、不改分数。
  * 两者分开后：分数永远可追溯到 SPEC 的哪一行；理由永远可追溯到具体观测值。

写作规则（每个维度都必须满足）：
  1. **先给事实**：具体数字、具体名字、具体日期。不写「较低」「偏高」这种没有信息量的词。
  2. **再给机制**：为什么这条会被扣分 —— 说出规则本身，让人能自己复核。
  3. **最后给影响**：对你（要接手的人）意味着什么，以及现在该做什么。

这三段合起来才是「有理由」，缺任何一段就退化成「机器人报数」。

本模块是纯函数、确定性、不访问网络。
"""

from __future__ import annotations

from datetime import datetime

# 维度键 → 面向用户的名字。
# 每个维度都用「中文问句」而不是术语：读者不需要先懂 bus factor 才能读懂分数。
DIM_LABELS = {
    "last_commit": "还在维护吗",
    "bus_factor": "靠几个人撑着",
    "trend": "提交在变多还是变少",
    "deps": "依赖还在维护吗",
    "issues": "提问有人回吗",
    "docs": "有说明书吗",
}

# 维度键 → 术语原文（给需要对照 SPEC 的读者），界面用小字附在中文名后。
DIM_TERMS = {
    "last_commit": "last commit",
    "bus_factor": "bus factor",
    "trend": "commit trend",
    "deps": "dependencies",
    "issues": "issue response",
    "docs": "documentation",
}

# 维度键 → 满分（与 SPEC 3.1-3.6 一致）
DIM_MAX = {
    "last_commit": 30,
    "bus_factor": 25,
    "trend": 15,
    "deps": 15,
    "issues": 10,
    "docs": 5,
}


def _g(d: dict | None, *keys, default=None):
    """安全逐层取字典字段。"""
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _short_date(iso: str | None) -> str:
    if not iso or not isinstance(iso, str):
        return "未知日期"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except Exception:
        return iso[:10]


# ── 逐维解释 ────────────────────────────────────────────────────────────────
def _explain_last_commit(m: dict) -> dict:
    life = m.get("life") or {}
    days = _int(life.get("days_since_last_commit"))
    last = _short_date(life.get("last_commit"))
    msg = (life.get("last_commit_msg") or "").strip()
    part = _g(m, "score", "parts", "last_commit", default={}) or {}
    got = _int(part.get("score"))

    if days <= 7:
        fact = f"最后一次提交是 {last}，就在 {days} 天前。"
        mech = "规则把「一周内还有动作」算作最高档（30/30）——这是项目还在被维护的最强信号。"
        impact = "有问题可以直接找维护者问，不用自己从零猜设计意图。"
    elif days <= 30:
        fact = f"最后一次提交是 {last}，距今 {days} 天。"
        mech = "规则把「一个月内还有动作」算第二档（25/30），扣 5 分是因为节奏已经放慢。"
        impact = "维护者大概率还在，但响应可能变慢，提 issue 要留出等待时间。"
    elif days <= 90:
        fact = f"最后一次提交是 {last}，距今 {days} 天。"
        mech = "规则把「三个月内还有动作」算第三档（18/30），扣 12 分：三个月足以让上下文散掉。"
        impact = "接手前先确认维护者是否还在这个项目上，否则你会拿到一份没人能解释的代码。"
    elif days <= 180:
        fact = f"最后一次提交是 {last}，距今 {days} 天。"
        mech = "规则把「半年内还有动作」算第四档（10/30），扣 20 分：半年不动通常意味着人已经转走了。"
        impact = "当作「无人维护」来接手：把外部依赖全部锁版本，不要指望上游修 bug。"
    elif days <= 365:
        fact = f"最后一次提交是 {last}，距今 {days} 天。"
        mech = "规则把「一年内还有动作」算第五档（5/30），扣 25 分：一年不动基本可以判定停更。"
        impact = "按「已归档」对待。先跑一遍现有测试，确认它在你手上还能启动。"
    else:
        fact = f"最后一次提交是 {last}，距今 {days} 天，已经超过一年。"
        mech = "超过 365 天直接记 0/30 —— 这是规则里最重的一刀，因为「一年没人碰」几乎等价于弃养。"
        impact = "不要在这个项目上做增量开发，除非你准备好接管它的全部依赖链。"

    if msg:
        fact += f" 最后一条提交信息是「{msg[:60]}」。"
    return {"score": got, "max": 30, "fact": fact, "mechanism": mech, "impact": impact}


def _explain_bus_factor(m: dict) -> dict:
    authors = m.get("authors") or {}
    bf = _int(authors.get("bus_factor"))
    total = _int(authors.get("total"))
    top = authors.get("top") or []
    top1 = top[0] if top else {}
    n1 = (top1.get("name") or "主要维护者").strip()
    s1 = _float(top1.get("share")) * 100
    part = _g(m, "score", "parts", "bus_factor", default={}) or {}
    got = _int(part.get("score"))
    life = m.get("life") or {}
    bots = _int(life.get("bot_commits"))

    who = f"共 {total} 位人类贡献者"
    if bots:
        who += f"（已排除 {bots} 次机器人提交，它们不会离职，不该算进分母）"

    if bf <= 0:
        return {
            "score": got,
            "max": 25,
            "fact": f"{who}，但样本里没有可识别的人工提交。",
            "mechanism": "bus factor 需要按提交占比累加才能算，没有人工提交时无法判定。",
            "impact": "用完整的提交历史重跑一次，或者直接问团队谁在维护。",
        }
    if bf == 1:
        fact = f"{who}，但 {n1} 一个人就占了 {s1:.0f}% 的提交，累计到 60% 只用到 1 个人 —— bus factor = 1。"
        mech = (
            "规则按邮件去重、按提交数降序累加，占比首次达到 60% 时数到几个人就是几。"
            "结果是 1，说明项目只有一根支柱。"
        )
        impact = "这个人离开，项目即终止。交接必须从他手上把设计意图、未写完的分支、"
        impact += "外部账号和部署方式全部问出来，不要只看代码。"
        return {"score": got, "max": 25, "fact": fact, "mechanism": mech, "impact": impact}
    if bf == 2:
        fact = f"{who}，累计到 60% 需要 2 个人，{n1} 占 {s1:.0f}%。"
        mech = "规则要求累计 60% 才算安全，2 个人就凑够 60% 说明重心高度集中（10/25）。"
        impact = "这两个人只要走一个，剩下的那个就是新的单点。交接要同时覆盖两人，不只找负责人。"
        return {"score": got, "max": 25, "fact": fact, "mechanism": mech, "impact": impact}
    if bf == 3:
        fact = f"{who}，累计到 60% 需要 3 个人，{n1} 占 {s1:.0f}%。"
        mech = "3 个人分担 60% 已经不算单点，但仍是小圈子（17/25）。"
        impact = "交接名单至少覆盖这 3 个人，别只写「由 A 负责」。"
        return {"score": got, "max": 25, "fact": fact, "mechanism": mech, "impact": impact}

    fact = f"{who}，累计到 60% 需要 {bf} 个人，最高的 {n1} 也只占 {s1:.0f}%。"
    mech = "4 人以上才能凑够 60%，说明知识分散在多个人手上，这是健康结构（25/25 满分）。"
    impact = "人员流动不会立刻造成真空。仍然建议按模块列一份「谁最熟这块」的对照表。"
    return {"score": got, "max": 25, "fact": fact, "mechanism": mech, "impact": impact}


def _explain_trend(m: dict) -> dict:
    trend = m.get("trend") or {}
    recent = _int(trend.get("recent_6m"))
    prev = _int(trend.get("previous_6m"))
    ratio = _float(trend.get("ratio"))
    insufficient = bool(trend.get("insufficient_history"))
    part = _g(m, "score", "parts", "trend", default={}) or {}
    got = _int(part.get("score"))
    quality = m.get("data_quality") or {}
    degraded = "trend" in (quality.get("unavailable_dimensions") or [])

    if insufficient:
        return {
            "score": got,
            "max": 15,
            "fact": f"最近半年 {recent} 次提交，再往前半年 {prev} 次，但仓库本身还不满 12 个月。",
            "mechanism": "规则规定：历史不足 12 个月时无法判断趋势，统一按 10/15 中性分。",
            "impact": "这条现在既不加分也不扣分。等项目满一年再看趋势才有意义。",
        }

    base = f"最近半年 {recent} 次提交，之前半年 {prev} 次，比值 {ratio:.2f}。"
    if degraded:
        base += "（趋势窗口由 GitHub API 补齐；本次该接口不可用，数值取自本地提交历史，可能偏差。）"

    if ratio >= 1.0:
        mech = "比值 ≥ 1 说明提交速度没有下降，算增长或稳定，给满 15 分。"
        impact = "项目仍在上升期，现在加入不会接手烂摊子；但也说明代码还在快速变化，文档可能滞后。"
    elif ratio >= 0.5:
        mech = "比值在 0.5–0.99 之间：还在动，但速度减半，给 10/15。"
        impact = "减速通常是维护者精力转移的早期信号。接手时问清「下一个版本还要做什么」。"
    elif ratio >= 0.1:
        mech = "比值在 0.1–0.49 之间：提交量掉到不足原来一半的零头，给 5/15。"
        impact = "已经进入维护末期。别指望新功能，只评估它现在能不能用。"
    else:
        mech = "比值 < 0.1：基本停止提交，给 0/15。"
        impact = "事实性停更。按「遗产项目」处理：冻结功能，只做安全修补。"
    return {"score": got, "max": 15, "fact": base, "mechanism": mech, "impact": impact}


def _explain_deps(m: dict) -> dict:
    deps = m.get("deps") or {}
    status = deps.get("status") or "none"
    part = _g(m, "score", "parts", "deps", default={}) or {}
    got = _int(part.get("score"))
    oldest = deps.get("oldest_pkg")
    days = deps.get("oldest_days")
    scope = deps.get("oldest_scope") or "runtime"
    rt_pkg = deps.get("oldest_runtime_pkg")
    rt_days = deps.get("oldest_runtime_days")
    eco = deps.get("ecosystem") or "none"
    count = _int(deps.get("count"))
    file = deps.get("file") or "依赖清单"

    if status == "none":
        return {
            "score": got,
            "max": 15,
            "fact": "没有找到依赖清单（requirements.txt / pyproject.toml / package.json 都不存在）。",
            "mechanism": "没有依赖清单就无法评估依赖是否还在维护，规则给中性分 8/15。",
            "impact": "顺手补一份依赖清单并锁版本，这是接手成本最低、收益最高的一件事。",
        }
    if status in ("timeout", "error"):
        return {
            "score": got,
            "max": 15,
            "fact": f"识别到 {eco} 生态的依赖清单（{file}，{count} 个依赖），但包仓库接口本次没查通。",
            "mechanism": "规则规定网络失败或超时按中性分 8/15，不让外部服务的抖动决定你的分数。",
            "impact": "这一项本次没有真实结论。数据完整时会重新计算，参考价值以下次为准。",
        }
    if status == "unsupported":
        return {
            "score": got,
            "max": 15,
            "fact": f"依赖清单是 {eco} 生态（{file}），该生态没有稳定的公开发布时间接口。",
            "mechanism": "规则明确跳过 Go 等无稳定公开 API 的生态，按中性分 8/15。",
            "impact": "这一项不反映真实风险。自己检查一下 go.mod 里有没有停更多年的模块。",
        }

    if days is None:
        fact = f"依赖清单 {file} 里有 {count} 个依赖，但没有取到任何发布时间。"
        return {
            "score": got,
            "max": 15,
            "fact": fact,
            "mechanism": "所有依赖都查询失败，按中性分 8/15 处理。",
            "impact": "这一项本次没有真实结论。",
        }

    # 判分口径：只看运行时依赖。dev 工具单独说明，不参与打分 ——
    # 理由见 .agents/notes/2026-11-05-deps-runtime-only.md
    scored_days = rt_days if rt_days is not None else days
    scored_pkg = rt_pkg if rt_days is not None else oldest
    scored_scope = "运行时依赖" if rt_days is not None else "全部依赖"

    d = _int(scored_days)
    if d >= 730:
        band = "超过 730 天（两年）"
        mech = f"规则取{scored_scope}里最老的那个作为最弱环节：两年没更新就给 0/15。"
    elif d >= 365:
        band = "365–729 天（一至两年）"
        mech = f"规则取{scored_scope}里最老的：一年以上没更新给 5/15。"
    elif d >= 180:
        band = "180–364 天（半年到一年）"
        mech = f"规则取{scored_scope}里最老的：半年以上没更新给 10/15。"
    else:
        band = "不到 180 天"
        mech = f"规则取{scored_scope}里最老的：全部在半年内更新过，给满 15/15。"

    fact = (
        f"清单 {file} 里 {count} 个依赖；{scored_scope}里最老的是 {scored_pkg}，"
        f"已经 {d} 天没发布新版本（{band}）。"
    )

    # dev 依赖不参与打分，但仍然要如实告知它是技术债
    if scope == "dev" and rt_pkg and _int(days) >= 365:
        fact += f" 另有开发/文档工具 {oldest} 已经 {_int(days)} 天没更新，不计入分数。"
        impact = (
            f"运行时风险不大：{rt_pkg} 才 {_int(scored_days)} 天。"
            f"但构建链上的 {oldest} 停更超过一年，某天可能装不上，建议锁版本或换掉。"
        )
    elif d < 180:
        impact = "运行时依赖都在半年内更新过，这一项没有风险。出问题时有上游可修。"
    elif d < 365:
        impact = "有运行时依赖半年以上没更新。它不会立刻坏，但安全补丁可能不再跟进，接手后留意。"
    elif d < 730:
        impact = (
            f"{scored_pkg} 是运行时依赖且一年多没更新：出安全问题时上游大概率不修，"
            "建议自己 fork 一份锁死版本。"
        )
    else:
        impact = (
            f"{scored_pkg} 是运行时依赖且两年以上没更新，这是个定时炸弹："
            "先确认它是否还在关键路径上，能替就替，不能替就 fork 锁死。"
        )

    return {"score": got, "max": 15, "fact": fact, "mechanism": mech, "impact": impact}


def _explain_issues(m: dict) -> dict:
    iss = m.get("issues") or {}
    status = iss.get("status") or "unavailable"
    median = iss.get("median_days")
    closed = _int(iss.get("closed_count"))
    open_n = _int(iss.get("open_count"))
    part = _g(m, "score", "parts", "issues", default={}) or {}
    got = _int(part.get("score"))
    host = _g(m, "repo", "host", default="other")
    quality = m.get("data_quality") or {}
    degraded = "issues" in (quality.get("unavailable_dimensions") or [])

    if host != "github":
        return {
            "score": got,
            "max": 10,
            "fact": f"这是 {host} 仓库，issue 数据不在 GitHub API 覆盖范围内。",
            "mechanism": "规则只对 GitHub 计算 issue 响应时间，其他托管平台按 1/10 处理。",
            "impact": "这一项不代表项目不响应，只是我们拿不到数据。去项目自己的 issue 页面看一眼更准。",
        }
    if status == "unavailable" or degraded:
        return {
            "score": got,
            "max": 10,
            "fact": "本次没能取到 issue 列表（接口不可用或配额用尽）。",
            "mechanism": "规则把「无法获取」按 1/10 处理 —— 这是保守取值，不代表项目真的不响应。",
            "impact": "这一项的分数是假低：它反映的是我们拿不到数据，不是项目不响应。配置 GITHUB_TOKEN 后重跑，或者自己去 issue 页看最近回复时间。",
        }
    if median is None:
        return {
            "score": got,
            "max": 10,
            "fact": f"最近 100 条 issue 里没有已关闭的（当前 {open_n} 条打开）。",
            "mechanism": "没有已关闭样本就算不出响应中位数，规则按 1/10 处理。",
            "impact": (
                "如果 issue 数很少，这通常说明项目小、问题靠私聊解决，不是坏事；"
                "如果打开的一堆没人关，那就是真的没人管。"
            ),
        }

    d = _float(median)
    fact = f"已关闭的 {closed} 条 issue 里，从提出到关闭的中位数是 {d:.1f} 天（PR 已排除，只算真 issue）。"
    if d < 7:
        mech = "中位数小于 7 天，给满 10/10。"
        impact = "维护者看得见反馈。接手后遇到不明确的实现，直接开 issue 问是最省时间的做法。"
    elif d < 30:
        mech = "中位数 7–29 天，给 6/10：会回，但不快。"
        impact = "别把 issue 当同步沟通渠道，急事要找人或自己读代码。"
    elif d < 90:
        mech = "中位数 30–89 天，给 3/10：响应以月为单位。"
        impact = "指望上游修 bug 不现实。接手时把已知问题列成清单，自己排优先级。"
    else:
        mech = "中位数 ≥ 90 天，给 1/10：issue 基本处于无人处理状态。"
        impact = "这个项目的 issue 区不是支持渠道，是历史遗留问题的堆放处。别在这里等答复。"
    return {"score": got, "max": 10, "fact": fact, "mechanism": mech, "impact": impact}


def _explain_docs(m: dict) -> dict:
    docs = m.get("docs") or {}
    has = bool(docs.get("has_readme"))
    chars = _int(docs.get("readme_chars"))
    path = docs.get("readme_path") or "README"
    has_install = bool(docs.get("has_install"))
    has_example = bool(docs.get("has_example"))
    part = _g(m, "score", "parts", "docs", default={}) or {}
    got = _int(part.get("score"))

    if not has:
        return {
            "score": got,
            "max": 5,
            "fact": "仓库根目录没有 README。",
            "mechanism": "规则里没有 README 记 0/5。",
            "impact": "接手成本最高的情形：你得先跑通它才能知道它做什么。第一件事就是补 README。",
        }
    if got == 5:
        return {
            "score": got,
            "max": 5,
            "fact": f"{path} 有 {chars} 字符，里面能找到安装/使用说明和代码示例。",
            "mechanism": "三项齐全（README + 安装说明 + 可跑示例）给满 5/5。",
            "impact": "文档可用。但注意：示例能跑不等于覆盖了边界，仍然要读核心模块的实现。",
        }

    missing = []
    if not has_install:
        missing.append("安装/使用说明")
    if not has_example:
        missing.append("可跑的代码示例")
    fact = f"{path} 有 {chars} 字符"
    fact += "，但缺少" + "、".join(missing) + "。" if missing else "。"
    mech = f"规则按完整度分档：三项齐全 5 分；只有 README 且 ≥500 字符 3 分；更短 1 分。这条给 {got}/5。"
    impact = (
        "缺示例意味着你得靠读代码猜用法，通常要多花半天。"
        "接手第一周就把你踩到的坑写成示例补进 README —— 下一个人会感谢你。"
    )
    return {"score": got, "max": 5, "fact": fact, "mechanism": mech, "impact": impact}


_EXPLAINERS = {
    "last_commit": _explain_last_commit,
    "bus_factor": _explain_bus_factor,
    "trend": _explain_trend,
    "deps": _explain_deps,
    "issues": _explain_issues,
    "docs": _explain_docs,
}


# ── 汇总 ────────────────────────────────────────────────────────────────────
def explain_dimensions(metrics: dict) -> dict[str, dict]:
    """每个维度返回 {score, max, fact, mechanism, impact}。任何异常都降级为空壳。"""
    out: dict[str, dict] = {}
    for key, fn in _EXPLAINERS.items():
        try:
            out[key] = fn(metrics)
        except Exception as exc:  # 解释层不允许拖垮分析
            out[key] = {
                "score": _int(_g(metrics, "score", "parts", key, "score", default=0)),
                "max": DIM_MAX.get(key, 0),
                "fact": "该维度的解释生成失败。",
                "mechanism": f"解释生成抛错：{type(exc).__name__}: {exc}",
                "impact": "分数本身不受影响，见打分依据表。",
            }
    return out


def top_deductions(metrics: dict, limit: int = 2) -> list[dict]:
    """扣分最多的几个维度 —— 回答「为什么不是满分」。"""
    explain = metrics.get("explain") or explain_dimensions(metrics)
    parts = _g(metrics, "score", "parts", default={}) or {}
    rows = []
    for key, part in parts.items():
        got = _int(part.get("score"))
        mx = _int(part.get("max")) or DIM_MAX.get(key, 0)
        lost = mx - got
        if lost <= 0:
            continue
        info = explain.get(key) or {}
        rows.append(
            {
                "key": key,
                "label": DIM_LABELS.get(key, key),
                "lost": lost,
                "score": got,
                "max": mx,
                "reason": info.get("fact", ""),
                "impact": info.get("impact", ""),
            }
        )
    rows.sort(key=lambda r: (-r["lost"], r["key"]))
    return rows[:limit]


def verdict(metrics: dict) -> str:
    """一句话总判断：说清这个项目现在处于什么状态、为什么。"""
    mode = _g(metrics, "score", "mode", default="dead")
    life = metrics.get("life") or {}
    authors = metrics.get("authors") or {}
    days = _int(life.get("days_since_last_commit"))
    bf = _int(authors.get("bus_factor"))
    top = top_deductions(metrics, limit=1)

    if mode == "alive":
        head = f"这个项目还活着：{days} 天前还有人提交"
        tail = "，现在接手不会孤立无援。"
    elif mode == "critical":
        head = f"这个项目在承压：{days} 天前还有提交"
        tail = "，关键指标已经在退，接手前先把风险点看清。"
    else:
        head = f"这个项目已经停了：{days} 天没提交"
        tail = "，现在接手等于做考古。"

    if bf == 1:
        mid = "，而且就一个人在撑着"
    elif bf <= 0:
        mid = ""
    else:
        mid = f"，{bf} 个人分担了大部分提交"

    base = f"{head}{mid}{tail}"

    # 满分时「最大失分项」是空话，必须换一种说法
    if not top:
        return base + "六项全部满分 —— 没有任何可改进的维度，这在真实项目里少见，值得复核数据来源。"

    worst = top[0]
    # 扣分很少时也别说「最大失分项」，那会误导读者以为项目有问题
    if worst["lost"] <= 3:
        return base + f"六项几乎满分（只在「{worst['label']}」上少 {worst['lost']} 分），不需要特别处理。"

    return base + f"当前最需要关注的是「{worst['label']}」（少 {worst['lost']} 分）。"


# ── 三个实际问题 ────────────────────────────────────────────────────────────
# 前面所有维度都在回答这三个问题。把答案直接摆出来，读者不必自己拼。
# 每条都是 {question, answer, detail, tone}，tone 取 ok / warn / bad / neutral。
def practical_answers(metrics: dict) -> list[dict]:
    """把六维指标翻译成接手前真正会问的三件事。"""
    life = metrics.get("life") or {}
    authors = metrics.get("authors") or {}
    docs = metrics.get("docs") or {}
    deps = metrics.get("deps") or {}
    score = metrics.get("score") or {}

    days = _int(life.get("days_since_last_commit"))
    bf = _int(authors.get("bus_factor"))
    n_authors = _int(authors.get("total"))
    top = authors.get("top") or []
    top1 = top[0] if top else {}
    top1_name = (top1.get("name") or "").strip()
    top1_share = _float(top1.get("share")) * 100
    mode = score.get("mode") or "dead"

    out: list[dict] = []

    # ① 还能用吗 ── 关心的是「现在接手会不会踩在废墟上」
    if days <= 30:
        answer, tone = "可以用，作者还在", "ok"
        detail = f"最后一次提交在 {days} 天前。"
    elif days <= 180:
        answer, tone = "能用，但没人更新了", "warn"
        detail = f"最后一次提交在 {days} 天前，半年内没有新动作。"
    elif days <= 365:
        answer, tone = "能用，但基本等于冻结", "warn"
        detail = f"最后一次提交在 {days} 天前，接近一年没动。"
    else:
        answer, tone = "不要指望它继续演进", "bad"
        detail = f"最后一次提交在 {days} 天前，超过一年。它现在是一个静态遗产。"
    if mode == "dead":
        detail += " 建议只把它当参考实现，不要在其上做新功能。"
    out.append({"question": "这东西我还能用吗", "answer": answer, "detail": detail, "tone": tone})

    # ② 出事了找谁 ── 关心的是「关键人依赖」，这是本产品的核心价值
    # 但项目已经停更时，「风险分散」是种误导：没有人可以被找到，分散也就没有意义。
    if mode == "dead":
        who_now = "；".join(
            f"{a.get('name')}（{_float(a.get('share')) * 100:.0f}%）" for a in top[:2] if a.get("name")
        )
        answer, tone = "现在没人可以找", "bad"
        detail = f"最后一次提交在 {days} 天前，维护者已经离场。" + (
            f"历史上主要是 {who_now} 在改这份代码 —— 只能从提交记录里找线索，别指望回信。" if who_now else ""
        )
    elif bf <= 0:
        answer, tone = "查不到维护者", "neutral"
        detail = "样本里没有可识别的人工提交，需要直接问团队谁在管。"
    elif bf == 1:
        who = f"{top1_name}（{top1_share:.0f}%）" if top1_name else "同一个人"
        answer, tone = "只有一个能改的人", "bad"
        detail = f"全部维护压在 {who} 身上。这个人离开，项目即终止 —— 先把他手上的隐性约定问出来。"
    elif bf == 2:
        answer, tone = "靠两个人对撑", "warn"
        detail = f"两人合计超过 60% 提交（第一人 {top1_share:.0f}%）。走掉一个，另一个就是新的单点。"
    elif bf == 3:
        answer, tone = "三个人分担，仍是小圈子", "warn"
        detail = f"累计到 60% 需要 3 个人，第一人占 {top1_share:.0f}%。交接名单至少要覆盖这三人。"
    else:
        answer, tone = "风险分散，不是单点", "ok"
        detail = (
            f"{n_authors} 位贡献者，凑够 60% 需要 {bf} 个人，第一人只占 {top1_share:.0f}%。"
            "人员流动不会立刻造成真空。"
        )
    out.append({"question": "出事了该找谁", "answer": answer, "detail": detail, "tone": tone})

    # ③ 我现在先做什么 ── 直接给一个动作，不给清单
    first_action = ""
    if deps.get("status") == "ok" and _int(deps.get("oldest_days")) >= 365:
        scope = deps.get("oldest_scope") or "runtime"
        if scope == "runtime":
            first_action = (
                f"先处理依赖：{deps.get('oldest_pkg')} 已经 "
                f"{_int(deps.get('oldest_days'))} 天没更新，它是运行时依赖，是个定时炸弹。"
            )
        else:
            rt = deps.get("oldest_runtime_pkg")
            first_action = (
                f"依赖里最老的是文档工具 {deps.get('oldest_pkg')}（{_int(deps.get('oldest_days'))} 天），"
                f"不影响运行；真正要盯的是运行时依赖 {rt or '（未采集）'}。先跑通测试再动代码。"
            )
    elif bf == 1:
        first_action = (
            f"先做知识转移：让 {top1_name or '主要维护者'} 把核心模块的设计意图、"
            "未完成分支、部署方式讲一遍并录下来。这比读代码快十倍。"
        )
    elif not docs.get("has_readme"):
        first_action = "先补一份 README：现在连它做什么、怎么跑都得靠猜。你自己跑通一遍就是最好的文档。"
    elif _int(days) > 365:
        first_action = "先跑一遍现有测试，确认它在你手上还能启动。跑不起来的话，先修环境再谈业务。"
    else:
        first_action = "先跑通测试建立基线，再从「雷区」里挑体量最大的文件读一遍 —— 那是改动风险最高的地方。"
    out.append(
        {
            "question": "我接手后先做什么",
            "answer": "一个动作，不是一张清单",
            "detail": first_action,
            "tone": "neutral",
        }
    )

    return out
