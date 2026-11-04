"""生成层对外唯一入口（docs/CONTRACT.md 第 4 节 + docs/SPEC.md 第 4 章）。

    generate_narrative(metrics, *, mode=None) -> dict

三种模式：

    alive    -> 只生成遗嘱
    dead     -> 只生成悼词
    critical -> 遗嘱 + 分隔线 + 悼词（SPEC 4.3）

**降级是硬要求**：LLM 未配置 / 超时 / 返回空 / 返回缺章节的垃圾，一律退回
模板渲染，``markdown`` 永远非空，顶部带
``> ⚠️ AI 叙事生成不可用，以下是结构化指标。``，且降级文档同样具备 SPEC 要求的
完整 5 章结构（用 metrics 里的真实数字填充）。

**绝不抛出未捕获异常**：整个函数体外层只有一个 try/except，最差也会返回模板结果。
"""

from __future__ import annotations

import time

try:  # 作为包导入（from src.narrative import ...）
    from . import llm, prompts
except ImportError:  # 兜底：把 src 直接塞进 sys.path 的调用方式
    import llm  # type: ignore
    import prompts  # type: ignore

MODES = ("alive", "critical", "dead")
DEGRADED_BANNER = "> ⚠️ AI 叙事生成不可用，以下是结构化指标。"

# ── 时间预算（可被环境变量覆盖，方便按部署环境调）─────────────────────────────
# 整体 LLM 阶段硬上限：超了立刻降级输出模板，绝不等外层超时。
# critical 模式两篇**并发**跑，关键路径 ≈ 单篇耗时，不是两篇相加。
TOTAL_BUDGET_SEC = 55.0
# 单次请求上限（lead 要求 45s）
CALL_TIMEOUT_SEC = 45
# 仅「结构校验不合格」时才重试；网络超时 / 连接失败不重试
RETRY_TIMEOUT_SEC = 30
# 热路径先发小额度请求，拿到合格章节就返回；不合格再用大额度重试一次。
# 1200 会让「遗嘱」第五章被截断，1600 才稳定装得下完整 5 章（实测）。
FAST_MAX_TOKENS = 1600
RETRY_MAX_TOKENS = 2400
# 每章正文的最短长度：低于这个值基本就是被 token 上限截断了
MIN_SECTION_CHARS = 30


def _budget_total() -> float:
    """总预算，允许用 LLM_BUDGET_SEC 覆盖（测试 / 展示用）。"""
    try:
        import os

        return max(10.0, float(os.environ.get("LLM_BUDGET_SEC") or TOTAL_BUDGET_SEC))
    except Exception:
        return TOTAL_BUDGET_SEC


def _budget_call() -> tuple[int, int]:
    """(首次 timeout, 重试 timeout)，允许用 LLM_CALL_TIMEOUT_SEC 覆盖。"""
    try:
        import os

        call = int(os.environ.get("LLM_CALL_TIMEOUT_SEC") or CALL_TIMEOUT_SEC)
    except Exception:
        call = CALL_TIMEOUT_SEC
    call = max(8, min(call, CALL_TIMEOUT_SEC))
    return call, min(RETRY_TIMEOUT_SEC, call)


# ── 取值工具 ────────────────────────────────────────────────────────────────
def _get(mapping, *keys, default=None):
    cur = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _text(value, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _short_date(value) -> str:
    return _text(value, "未知").split("T")[0].split(" ")[0]


def _clip(text: str, limit: int) -> str:
    """按字符数硬截断，保证长度约束在任何 metrics 下都成立。"""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip("，、；： ") + "。"


# ── 降级模板：遗嘱 ──────────────────────────────────────────────────────────
def _risk_lines(metrics: dict) -> list[str]:
    """第三章的雷区条目，路径全部来自 metrics 真实数据。"""
    lines: list[str] = []
    stale = _as_list(_get(metrics, "risks", "stale_core_files", default=[]))
    for item in stale:
        if not isinstance(item, dict):
            continue
        path = _text(item.get("path"))
        if not path:
            continue
        lines.append(
            "`%s` — 已 %d 天没人碰过，历史提交 %d 次，%d 行。"
            "它仍然承担核心职责，改动前先把调用方摸清。"
            % (
                path,
                _int(item.get("days_untouched")),
                _int(item.get("commits")),
                _int(item.get("loc")),
            )
        )

    hotspots = _as_list(_get(metrics, "risks", "hotspots", default=[]))
    seen_paths = {line.split("`")[1] for line in lines if "`" in line}
    for item in hotspots:
        if not isinstance(item, dict):
            continue
        path = _text(item.get("path"))
        if not path:
            continue
        kind = _text(item.get("kind"), "TODO")
        text = _text(item.get("text"), "（无原文）").replace("\n", " ")[:80]
        if path in seen_paths:
            lines.append(
                "  - 同一文件内 %s:%d 处有 `%s`：%s"
                % (path, _int(item.get("line")), kind, text)
            )
        else:
            lines.append(
                "`%s` — 第 %d 行有 `%s`：%s。这是已知的坑，别当它是待办。"
                % (path, _int(item.get("line")), kind, text)
            )
        seen_paths.add(path)

    # 路径不足 3 个时，用 size.top_dirs 的真实目录补齐（不编造文件名）
    if len(seen_paths) < 3:
        for item in _as_list(_get(metrics, "size", "top_dirs", default=[])):
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name")).strip("/")
            if not name or name in seen_paths:
                continue
            lines.append(
                "`%s/` — 该目录承载 %d 个文件，是仓库主干之一；"
                "没有文件级风险数据，先从这里的入口文件读起。"
                % (name, _int(item.get("files")))
            )
            seen_paths.add(name)
            if len(seen_paths) >= 3:
                break

    return lines


def _takeover_steps(metrics: dict) -> list[str]:
    """第四章：可执行步骤，按仓库真实指标拼。"""
    deps = _get(metrics, "deps", default={}) or {}
    docs = _get(metrics, "docs", default={}) or {}
    risks = _get(metrics, "risks", default={}) or {}
    bf = _int(_get(metrics, "authors", "bus_factor"))
    stale = _as_list(risks.get("stale_core_files"))
    hotspot_list = _as_list(risks.get("hotspots"))
    oldest_pkg = _text(deps.get("oldest_pkg"))
    oldest_days = deps.get("oldest_days")

    steps: list[str] = []

    if oldest_pkg and oldest_days is not None:
        steps.append(
            "先冻结依赖：`%s` 已经 %d 天没有新版本，把它在依赖清单里钉死到当前版本，"
            "再单独开一次升级分支试跑。不要一上手就全量升级。" % (oldest_pkg, _int(oldest_days))
        )
    elif _text(deps.get("status")) in ("timeout", "unsupported", "none"):
        steps.append(
            "先把依赖清单摸清：本次没拿到依赖时间信息，手工核对每个依赖的最后发布时间，"
            "把超过 24 个月没更新的记下来当作重点排查对象。"
        )
    else:
        steps.append("先在干净环境里跑通一次构建，把实际的依赖版本固化成锁文件。")

    if stale:
        first = stale[0] if isinstance(stale[0], dict) else {}
        steps.append(
            "读 `%s`：它 %d 天没改动却仍是核心，先把它的调用方全部列出来再动手。"
            % (_text(first.get("path"), "仓库主干文件"), _int(first.get("days_untouched")))
        )
    elif hotspot_list:
        first = hotspot_list[0] if isinstance(hotspot_list[0], dict) else {}
        steps.append(
            "处理 `%s:%d` 这条 `%s`：它是公认的坑，先加一条回归用例再改。"
            % (_text(first.get("path"), "核心文件"), _int(first.get("line")), _text(first.get("kind"), "TODO"))
        )
    else:
        steps.append("从仓库入口文件（主模块 / CLI 入口）读起，画出实际的调用链再改。")

    todo = _int(risks.get("todo_count"))
    fixme = _int(risks.get("fixme_count"))
    steps.append(
        "清点遗留标记：TODO %d 处、FIXME %d 处。逐条判断「已过期」还是「真问题」，"
        "过期的删掉，真问题的转成 issue 并写清复现路径。" % (todo, fixme)
    )

    if not docs.get("has_readme"):
        steps.append("补一个最小 README：安装方式、跑起来的命令、一个可复制的示例。这是接手成本最大的一块。")
    elif not docs.get("has_install") or not docs.get("has_example"):
        steps.append(
            "补齐 README 缺失的部分：%s。别人照着它能不能在三分钟内跑起来，是唯一验收标准。"
            % (
                "安装/使用说明"
                if not docs.get("has_install")
                else "可直接复制的代码示例"
            )
        )

    if bf <= 1:
        steps.append(
            "找一个第二维护者：bus factor = 1，你现在就是那个 1。至少让另一个人能独立发布一次版本。"
        )
    else:
        steps.append(
            "确认维护者分工：bus factor = %d，把发布权限和部署步骤写进仓库文档，别只存在人脑里。" % bf
        )

    return steps[:5]


def render_will_template(metrics: dict) -> str:
    """纯模板遗嘱：确定性，走 metrics 真实数字，5 章齐全。"""
    metrics = metrics if isinstance(metrics, dict) else {}
    name = prompts.repo_name(metrics)

    repo = _get(metrics, "repo", default={}) or {}
    life = _get(metrics, "life", default={}) or {}
    size = _get(metrics, "size", default={}) or {}
    authors = _get(metrics, "authors", default={}) or {}
    trend = _get(metrics, "trend", default={}) or {}
    deps = _get(metrics, "deps", default={}) or {}
    issues = _get(metrics, "issues", default={}) or {}
    docs = _get(metrics, "docs", default={}) or {}
    risks = _get(metrics, "risks", default={}) or {}
    score = _get(metrics, "score", default={}) or {}

    langs = _as_list(size.get("languages"))
    lang_text = (
        "、".join(
            "%s（%d 个文件）" % (_text(i.get("name"), "unknown"), _int(i.get("files")))
            for i in langs[:5]
            if isinstance(i, dict)
        )
        or "未采集"
    )
    dirs = _as_list(size.get("top_dirs"))
    dir_text = (
        "、".join(
            "%s（%d 个文件）" % (_text(i.get("name"), "?"), _int(i.get("files")))
            for i in dirs[:6]
            if isinstance(i, dict)
        )
        or "未采集"
    )

    bf = _int(authors.get("bus_factor"))
    total_authors = _int(authors.get("total"))

    out: list[str] = []
    add = out.append

    add("# 代码遗嘱 · %s" % name)
    add("")

    # ── 一
    add(prompts.WILL_HEADINGS[0])
    add("")
    add("- 代码规模：%d 个追踪文件、%d 行代码、%d 个模块"
        % (_int(size.get("tracked_files")), _int(size.get("loc")), _int(size.get("modules"))))
    add("- 主要语言：%s" % lang_text)
    add("- 主要目录：%s" % dir_text)
    add("- 出生于 %s，最后一次提交是 %s（距今 %d 天）"
        % (_short_date(life.get("first_commit")), _short_date(life.get("last_commit")),
           _int(life.get("days_since_last_commit"))))
    add("- 已有寿命：%s；本次分析范围内共 %d 次提交"
        % (_text(life.get("age_text"), "%d 天" % _int(life.get("age_days"))),
           _int(life.get("total_commits"))))
    add("- 托管平台：%s；地址：%s" % (_text(repo.get("host"), "未知"), _text(repo.get("url"), "未知")))
    if metrics.get("sampled"):
        add("- 说明：%s" % _text(metrics.get("sample_note"), "仓库体量较大，已采样分析"))
    add("- 对外接口 / 被依赖情况：本次分析未采集该维度，接手时需自行 grep 导出的符号。")
    add("")

    # ── 二
    add(prompts.WILL_HEADINGS[1])
    add("")
    add("- 贡献者总数：%d 人" % total_authors)
    add("- 趋势：近 6 个月 %d 次提交，前 6 个月 %d 次提交（ratio %.2f）%s"
        % (_int(trend.get("recent_6m")), _int(trend.get("previous_6m")), _float(trend.get("ratio")),
           "；历史不足 12 个月，趋势参考价值有限" if trend.get("insufficient_history") else ""))
    add("")
    top = _as_list(authors.get("top"))
    if top:
        add("| 排名 | 贡献者 | 提交数 | 占比 |")
        add("|---|---|---|---|")
        for index, person in enumerate(top[:6], 1):
            if not isinstance(person, dict):
                continue
            add("| %d | %s | %d | %.1f%% |"
                % (index, _text(person.get("name"), "匿名"),
                   _int(person.get("commits")), _float(person.get("share")) * 100))
        add("")
    if bf == 1:
        add("bus factor = 1。**%s**" % prompts.BUS_FACTOR_ONE_SENTENCE)
        add("")
        add("这个数字的含义很直接：所有核心知识、发布权限、部署步骤都压在一个人身上，"
            "没有任何冗余。这不是风险提示，是已经发生的事实。")
    elif bf <= 0:
        add("bus factor 无法计算（没有有效提交者信息）。")
    else:
        add("bus factor = %d：排名前 %d 的贡献者合计承担了 60%% 以上的提交。"
            "关键人风险中等偏低，但上面这张表里每个人的离开都会带走一部分上下文。" % (bf, bf))
    add("")

    # ── 三
    add(prompts.WILL_HEADINGS[2])
    add("")
    risk_lines = _risk_lines(metrics)
    if risk_lines:
        counter = 0
        for line in risk_lines:
            if line.startswith("  - "):
                add(line)
            else:
                counter += 1
                add("%d. %s" % (counter, line))
    else:
        add("本次分析未采集到文件级风险数据，只能给出目录级判断：%s。"
            "接手时请先自己跑一遍 `git log --stat` 找长期无人改动的核心文件。" % dir_text)
    add("")
    add("补充信号：TODO %d 处、FIXME %d 处；最老依赖 %s（%s 天未更新）。"
        % (_int(risks.get("todo_count")), _int(risks.get("fixme_count")),
           _text(deps.get("oldest_pkg"), "无"),
           "未知" if deps.get("oldest_days") is None else str(_int(deps.get("oldest_days")))))
    add("")

    # ── 四
    add(prompts.WILL_HEADINGS[3])
    add("")
    for index, step in enumerate(_takeover_steps(metrics), 1):
        add("%d. %s" % (index, step))
    add("")

    # ── 五
    add(prompts.WILL_HEADINGS[4])
    add("")
    closing = (
        "这个仓库现在还能跑，但它把风险都集中在了少数文件和少数人身上。"
        "接手不需要重构，需要先读懂 %d 个核心文件和 %d 个依赖。"
        "按上面的顺序做，别跳步。"
        % (max(1, len(_as_list(risks.get("stale_core_files"))) or 1), _int(deps.get("count")))
    )
    closing = _clip(closing, 150)
    add(closing)
    add("")

    # 分数脚注（真实数据，不参与叙事）
    parts = _get(score, "parts", default={}) or {}
    part_bits = []
    for key in ("last_commit", "bus_factor", "trend", "deps", "issues", "docs"):
        part = parts.get(key)
        if isinstance(part, dict):
            part_bits.append("%s %d/%d" % (_text(part.get("label"), key), _int(part.get("score")),
                                           _int(part.get("max"))))
    if part_bits:
        add("---")
        add("")
        add("> 健康分 %d/100（%s）。%s"
            % (_int(score.get("total")), _text(score.get("mode"), "未知"), "；".join(part_bits)))
        add("")

    return "\n".join(out).rstrip() + "\n"


# ── 降级模板：悼词 ──────────────────────────────────────────────────────────
def render_eulogy_template(metrics: dict) -> str:
    """纯模板悼词：确定性，5 章齐全，第二章三条死因必填。"""
    metrics = metrics if isinstance(metrics, dict) else {}
    name = prompts.repo_name(metrics)

    life = _get(metrics, "life", default={}) or {}
    size = _get(metrics, "size", default={}) or {}
    authors = _get(metrics, "authors", default={}) or {}
    trend = _get(metrics, "trend", default={}) or {}
    deps = _get(metrics, "deps", default={}) or {}
    issues = _get(metrics, "issues", default={}) or {}
    docs = _get(metrics, "docs", default={}) or {}
    risks = _get(metrics, "risks", default={}) or {}
    score = _get(metrics, "score", default={}) or {}

    bf = _int(authors.get("bus_factor"))
    last_msg = _text(life.get("last_commit_msg"), "（无提交信息）")
    gap = _int(life.get("days_since_last_commit"))

    out: list[str] = []
    add = out.append

    add("# 悼词 · %s" % name)
    add("")
    add("生于 %s" % _short_date(life.get("first_commit")))
    add("卒于 %s" % _short_date(life.get("last_commit")))
    add("享年 %s" % _text(life.get("age_text"), "%d 天" % _int(life.get("age_days"))))
    add("")

    # ── 一
    add(prompts.EULOGY_HEADINGS[0])
    add("")
    add("- %s 第一次提交，项目开始。" % _short_date(life.get("first_commit")))
    add("- 存续 %s，累计 %d 次提交、%d 个文件、%d 行代码。"
        % (_text(life.get("age_text"), "%d 天" % _int(life.get("age_days"))),
           _int(life.get("total_commits")), _int(size.get("tracked_files")), _int(size.get("loc"))))
    add("- 衰退期：近 6 个月 %d 次提交，前 6 个月 %d 次提交，比值为 %.2f。"
        % (_int(trend.get("recent_6m")), _int(trend.get("previous_6m")), _float(trend.get("ratio"))))
    add("- %s 发出最后一条提交，此后 %d 天没有任何动静。"
        % (_short_date(life.get("last_commit")), gap))
    add("- 最终健康分 %d/100。" % _int(score.get("total")))
    add("")

    # ── 二
    add(prompts.EULOGY_HEADINGS[1])
    add("")
    add("- **直接原因**：最后一条提交信息是「%s」，之后 %d 天再没有新的提交；"
        "最老依赖 %s 已 %s 天没有更新，构建环境随时可能失效。"
        % (last_msg[:80], gap, _text(deps.get("oldest_pkg"), "无"),
           "未知" if deps.get("oldest_days") is None else str(_int(deps.get("oldest_days")))))
    if bf == 1:
        add("- **根本原因**：bus factor = 1。%s——所有上下文、发布权限和部署步骤都在一个人手里，"
            "这个人停下来，项目就没有第二条腿。" % prompts.BUS_FACTOR_ONE_SENTENCE)
    else:
        add("- **根本原因**：bus factor = %d，贡献集中在 %d 人身上；"
            "核心维护动力耗尽后，剩余的贡献者没有接手的动力。" % (bf, max(bf, 1)))
    readme_state = (
        "README 缺失，新来的人连怎么启动都不知道"
        if not docs.get("has_readme")
        else "README 缺少安装说明或可运行示例"
    )
    add("- **加速因素**：%s；%d 处 TODO、%d 处 FIXME 长期未清；issue 中位关闭耗时 %s；%s。"
        % (
            readme_state,
            _int(risks.get("todo_count")), _int(risks.get("fixme_count")),
            "无数据" if issues.get("median_days") is None
            else "%d 天" % int(_float(issues.get("median_days"))),
            "趋势已连续两期下滑" if _float(trend.get("ratio")) < 0.5 else "活跃度缓慢衰减",
        ))
    add("")

    # ── 三
    add(prompts.EULOGY_HEADINGS[2])
    add("")
    add("- 仍在被使用的部分：%d 个模块、%d 行代码，主语言 %s。"
        % (_int(size.get("modules")), _int(size.get("loc")),
           "、".join(_text(i.get("name"), "?") for i in _as_list(size.get("languages"))[:3]
                    if isinstance(i, dict)) or "未采集"))
    add("- 未完成的承诺：TODO %d 个、FIXME %d 个，它们是被写下来但永远不会被兑现的计划。"
        % (_int(risks.get("todo_count")), _int(risks.get("fixme_count"))))
    stale = _as_list(risks.get("stale_core_files"))
    if stale:
        names = "、".join("`%s`" % _text(i.get("path"), "?") for i in stale[:3] if isinstance(i, dict))
        add("- 最难接手的是那几个长期没人动过的核心文件：%s。" % names)
    top_list = [p for p in _as_list(authors.get("top")) if isinstance(p, dict)]
    if _int(authors.get("total")):
        if top_list:
            add("- 留下了 %d 位贡献者的一段历史，其中 %s 一人占了 %.1f%% 的提交。"
                % (_int(authors.get("total")), _text(top_list[0].get("name"), "第一贡献者"),
                   _float(top_list[0].get("share")) * 100))
        else:
            add("- 留下了 %d 位贡献者的一段历史。" % _int(authors.get("total")))
    add("")

    # ── 四
    add(prompts.EULOGY_HEADINGS[3])
    add("")
    add("1. 关键人不是风险提示，是倒计时。bus factor = %d 的仓库，应该在还能提交的时候就把发布权限、"
        "部署步骤、密钥位置写进仓库文档，而不是留在一个人的脑子里。" % max(bf, 1))
    add("2. 提交停止之前，信号早就出现了：趋势比值 %.2f、%d 处 FIXME 没有下降。"
        "把这些当指标看，比事后写悼词有用。"
        % (_float(trend.get("ratio")), _int(risks.get("fixme_count"))))
    lesson_doc = (
        "3. README 缺位就是最高优先级的技术债：在一个没人维护的仓库里，文档是唯一的交接物。"
        if not docs.get("has_readme")
        else "3. 文档写给自己人看是没用的：README 只有 3 分钟能跑通的示例才算交接材料。"
    )
    add(lesson_doc)
    add("")

    # ── 五
    add(prompts.EULOGY_HEADINGS[4])
    add("")
    closing = (
        "它没有做错什么，只是没人继续做了。%d 行代码还在那里，能读懂的人不多。"
        "如果哪天要复活它，先找到第二个愿意维护的人。" % _int(size.get("loc"))
    )
    closing = _clip(closing, 100)
    add(closing)
    add("")

    return "\n".join(out).rstrip() + "\n"


# ── 校验 ────────────────────────────────────────────────────────────────────
_DANGLING_TAIL = "，、：；,:'\"（(【[《"


def _missing_headings(text: str, headings: list[str]) -> list[str]:
    return [h for h in headings if h not in text]


def _chapter_bodies(text: str, headings: list[str]) -> dict[str, str | None]:
    """{标题: 正文}；标题不存在时值为 None。"""
    out: dict[str, str | None] = {}
    for index, heading in enumerate(headings):
        start = text.find(heading)
        if start == -1:
            out[heading] = None
            continue
        body_start = start + len(heading)
        body_end = len(text)
        for later in headings[index + 1:]:
            found = text.find(later)
            if found != -1:
                body_end = found
                break
        out[heading] = text[body_start:body_end].strip()
    return out


def _truncation_problems(text: str, headings: list[str]) -> list[str]:
    """截断检测：章节正文过短，或全文停在半句话上。"""
    problems: list[str] = []
    bodies = _chapter_bodies(text, headings)
    for heading, body in bodies.items():
        if body is None:
            continue  # 缺标题由 _missing_headings 报
        if len(body) < MIN_SECTION_CHARS:
            problems.append("章节「%s」正文只有 %d 字，疑似被截断" % (heading, len(body)))
    tail = text.rstrip()
    if tail and tail[-1] in _DANGLING_TAIL:
        problems.append("全文停在半句话上（疑似被 max_tokens 截断）")
    return problems


def _validate_will(text: str, metrics: dict) -> list[str]:
    """返回不合格项列表（空列表 = 合格）。"""
    problems: list[str] = []
    if not text or len(text.strip()) < 120:
        problems.append("内容过短或为空")
        return problems
    missing = _missing_headings(text, prompts.WILL_HEADINGS)
    if missing:
        problems.append("缺少章节：" + "、".join(missing))
    problems.extend(_truncation_problems(text, prompts.WILL_HEADINGS))
    bf = _int(_get(metrics, "authors", "bus_factor"))
    if bf == 1 and prompts.BUS_FACTOR_ONE_SENTENCE not in text:
        problems.append("bus factor=1 但缺少原句「%s」" % prompts.BUS_FACTOR_ONE_SENTENCE)
    return problems


def _validate_eulogy(text: str) -> list[str]:
    problems: list[str] = []
    if not text or len(text.strip()) < 120:
        problems.append("内容过短或为空")
        return problems
    missing = _missing_headings(text, prompts.EULOGY_HEADINGS)
    if missing:
        problems.append("缺少章节：" + "、".join(missing))
    problems.extend(_truncation_problems(text, prompts.EULOGY_HEADINGS))
    for bullet in prompts.EULOGY_DEATH_BULLETS:
        if bullet not in text:
            problems.append("第二章缺少条目「%s」" % bullet.strip())
    return problems


def _clip_sentence(body: str, limit: int) -> str:
    """把一段话压到 limit 字以内，优先在句号处断开。"""
    cleaned = " ".join(body.split())
    if len(cleaned) <= limit:
        return cleaned
    head = cleaned[:limit]
    cut = max(head.rfind("。"), head.rfind("！"), head.rfind("？"))
    if cut >= limit // 2:
        return head[: cut + 1]
    return head[: limit - 1].rstrip("，、；： ") + "。"


def _enforce_length_limits(text: str, headings: list[str], kind: str) -> str:
    """把末章长度压回 SPEC 要求（遗嘱 ≤150 字 / 悼词 ≤100 字）。

    LLM 经常写超；与其再发一次请求，不如按句末截断，结构性要求必须成立。
    """
    limit = prompts.MAX_LAST_WORDS_CHARS if kind == "will" else prompts.MAX_CLOSING_CHARS
    last = headings[-1]
    try:
        bodies = _chapter_bodies(text, headings)
        body = bodies.get(last)
        if not body or len(" ".join(body.split())) <= limit:
            return text
        start = text.find(last) + len(last)
        # 末章后面不应再有内容；保险起见只替换到文档结尾
        new_body = _clip_sentence(body, limit)
        return text[:start] + "\n\n" + new_body + "\n"
    except Exception:
        return text


# ── 章节拼接（把 LLM 可用部分补进模板）───────────────────────────────────────
def _partition(text: str, headings: list[str]) -> tuple[str, dict[str, str]]:
    """按标题切段，返回 (首个标题之前的文本, {标题: 正文})。"""
    positions: list[tuple[int, str]] = []
    for heading in headings:
        index = text.find(heading)
        if index != -1:
            positions.append((index, heading))
    positions.sort()
    if not positions:
        return text, {}

    prefix = text[: positions[0][0]]
    bodies: dict[str, str] = {}
    for order, (start, heading) in enumerate(positions):
        body_start = start + len(heading)
        body_end = positions[order + 1][0] if order + 1 < len(positions) else len(text)
        bodies[heading] = text[body_start:body_end]
    return prefix, bodies


def _merge_into_template(template: str, llm_text: str, headings: list[str]) -> str:
    """把 LLM 输出里合格的小节替换进模板，保证结构完整。"""
    if not llm_text:
        return template
    try:
        _, llm_bodies = _partition(llm_text, headings)
        if not llm_bodies:
            return template
        prefix, tpl_bodies = _partition(template, headings)
        if not tpl_bodies:
            return template

        merged: dict[str, str] = {}
        for heading in headings:
            tpl_body = tpl_bodies.get(heading, "\n")
            llm_body = llm_bodies.get(heading, "")
            # 只采纳「有实质内容」的小节，短于 40 字符的当作没写
            if len(llm_body.strip()) >= 40:
                merged[heading] = "\n" + llm_body.strip() + "\n\n"
            else:
                merged[heading] = tpl_body

        out = [prefix.rstrip(), ""]
        for heading in headings:
            out.append(heading)
            out.append(merged.get(heading, "\n").strip("\n"))
            out.append("")
        return "\n".join(out).rstrip() + "\n"
    except Exception:
        return template


# ── 带硬墙的单次调用 ────────────────────────────────────────────────────────
def _call_with_deadline(
    messages: list[dict], *, max_tokens: int, temperature: float, timeout: int, deadline: float
) -> dict:
    """在硬 deadline 内完成一次 chat 调用。

    ``requests`` 的 timeout 只是**读空闲**超时：只要服务端一直在缓慢吐 token，
    一次调用可以跑得比 timeout 久得多，整体预算会被悄悄突破。
    这里再加一道墙——超时就放弃这次调用，直接走模板。
    """
    from concurrent.futures import ThreadPoolExecutor

    remaining = deadline - time.time()
    if remaining <= 3:
        return {"ok": False, "text": "", "model": "", "error": "整体预算已耗尽", "source": ""}

    budget = max(3.0, min(float(timeout), remaining))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(
            llm.chat,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=int(budget),
        )
        try:
            return future.result(timeout=budget + 2)
        except Exception:
            future.cancel()
            return {
                "ok": False,
                "text": "",
                "model": "",
                "error": "超出总预算 %.0fs，已放弃本次请求" % budget,
                "source": "",
            }
    finally:
        # 不等被放弃的线程：它自己的 read timeout 会兜住，结果丢弃即可
        pool.shutdown(wait=False)


# ── 单篇生成 ────────────────────────────────────────────────────────────────
def _repair_rules(kind: str, metrics: dict) -> list[str]:
    """重试时的额外硬性要求。"""
    if kind == "will":
        rules = [
            "第三章必须给出至少 3 个具体文件路径，只能从提示词里的路径清单选，不得编造",
            "第五章不超过 %d 字" % prompts.MAX_LAST_WORDS_CHARS,
        ]
        if _int(_get(metrics, "authors", "bus_factor")) == 1:
            rules.append("第二章必须逐字出现：「%s」" % prompts.BUS_FACTOR_ONE_SENTENCE)
        return rules
    return [
        "标题之后必须依次有三行：生于 / 卒于 / 享年",
        "第二章必须包含 %s 三个条目（全角冒号）" % "、".join(b.strip() for b in prompts.EULOGY_DEATH_BULLETS),
        "第五章不超过 %d 字" % prompts.MAX_CLOSING_CHARS,
    ]


def _generate_one(kind: str, metrics: dict, deadline: float) -> dict:
    """kind: "will" | "eulogy"。返回 {"text","used_llm","model","reason"}。

    策略（时间敏感，因为分析层已经吃掉几十秒）：
      1. 热路径先用 **1600 max_tokens** 发一次（1200 会让遗嘱末章被截断），
         能拿到合格章节就立刻返回；
      2. 失败分类处理：
         - 网络超时 / 连接失败 → **不重试**，立刻用模板（重试只会再等一遍）；
         - 拿到文本但结构不合格 / 被截断 → 用更大额度重试 **1 次**；
      3. 每次调用前都检查整体预算，剩余不足直接走模板。
    """
    template = render_will_template(metrics) if kind == "will" else render_eulogy_template(metrics)
    headings = prompts.WILL_HEADINGS if kind == "will" else prompts.EULOGY_HEADINGS
    call_timeout, retry_timeout = _budget_call()

    def validate(text: str) -> list[str]:
        if kind == "will":
            return _validate_will(text, metrics)
        return _validate_eulogy(text)

    def degraded(reason_text: str, llm_text: str = "", model: str = "") -> dict:
        return {
            "text": _merge_into_template(template, llm_text, headings),
            "used_llm": False,
            "model": model,
            "reason": reason_text,
        }

    messages = (
        prompts.build_will_prompt(metrics) if kind == "will" else prompts.build_eulogy_prompt(metrics)
    )

    remaining = deadline - time.time()
    if remaining <= 6:
        return degraded("LLM 预算不足，直接使用模板")

    # ── 热路径：小额快速请求
    first = _call_with_deadline(
        messages,
        max_tokens=FAST_MAX_TOKENS,
        temperature=0.4,
        timeout=int(min(call_timeout, max(6, remaining))),
        deadline=deadline,
    )

    first_text = (first.get("text") or "").strip()
    first_model = first.get("model") or ""

    if not first.get("ok"):
        # 传输层失败：不重试，直接模板
        return degraded("LLM 调用失败：%s" % (first.get("error") or "未知错误"), "", first_model)

    problems = validate(first_text)
    if not problems:
        return {
            "text": _enforce_length_limits(first_text, headings, kind) + "\n",
            "used_llm": True,
            "model": first_model,
            "reason": "",
        }

    # ── 结构不合格：唯一一次重试（更大 token 额度）
    remaining = deadline - time.time()
    if remaining <= 10:
        return degraded(
            "LLM 输出结构不合格（%s），预算不足不再重试" % "；".join(problems[:3]),
            first_text,
            first_model,
        )

    retry_messages = list(messages)
    missing = _missing_headings(first_text, headings) if first_text else list(headings)
    if first_text:
        retry_messages.append({"role": "assistant", "content": first_text[:1200]})
    retry_messages.append(
        prompts.build_repair_prompt(
            headings, missing, "will" if kind == "will" else "eulogy", _repair_rules(kind, metrics)
        )
    )

    second = _call_with_deadline(
        retry_messages,
        max_tokens=RETRY_MAX_TOKENS,
        temperature=0.3,
        timeout=int(min(retry_timeout, max(6, remaining))),
        deadline=deadline,
    )

    if not second.get("ok"):
        return degraded(
            "LLM 输出结构不合格（%s）；重试失败（%s）"
            % ("；".join(problems[:2]), second.get("error") or "未知错误"),
            first_text,
            second.get("model") or first_model,
        )

    second_text = (second.get("text") or "").strip()
    second_model = second.get("model") or first_model
    second_problems = validate(second_text)
    if not second_problems:
        return {
            "text": _enforce_length_limits(second_text, headings, kind) + "\n",
            "used_llm": True,
            "model": second_model,
            "reason": "",
        }

    return degraded(
        "LLM 输出结构不合格（%s）" % "；".join((problems + second_problems)[:3]),
        second_text or first_text,
        second_model,
    )


def _run_parallel(jobs: list[str], metrics: dict, deadline: float) -> dict[str, dict]:
    """并发跑多个生成任务；线程不可用或抛错时退回串行，保证结果不丢。"""
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {job: pool.submit(_generate_one, job, metrics, deadline) for job in jobs}
            out: dict[str, dict] = {}
            for job, future in futures.items():
                try:
                    out[job] = future.result(timeout=max(5, deadline - time.time() + 15))
                except Exception as exc:
                    out[job] = {
                        "text": (render_will_template(metrics) if job == "will"
                                 else render_eulogy_template(metrics)),
                        "used_llm": False,
                        "model": "",
                        "reason": "并发生成异常：%s" % type(exc).__name__,
                    }
            return out
    except Exception:
        return {job: _generate_one(job, metrics, deadline) for job in jobs}


# ── 指标附录（降级时附在文末）──────────────────────────────────────────────
def _metrics_appendix(metrics: dict) -> str:
    metrics = metrics if isinstance(metrics, dict) else {}
    life = _get(metrics, "life", default={}) or {}
    size = _get(metrics, "size", default={}) or {}
    authors = _get(metrics, "authors", default={}) or {}
    trend = _get(metrics, "trend", default={}) or {}
    deps = _get(metrics, "deps", default={}) or {}
    issues = _get(metrics, "issues", default={}) or {}
    docs = _get(metrics, "docs", default={}) or {}
    risks = _get(metrics, "risks", default={}) or {}
    score = _get(metrics, "score", default={}) or {}

    lines = [
        "## 附录：结构化指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 健康分 | %d/100 |" % _int(score.get("total")),
        "| 首次提交 | %s |" % _short_date(life.get("first_commit")),
        "| 最后提交 | %s（距今 %d 天） |" % (_short_date(life.get("last_commit")),
                                        _int(life.get("days_since_last_commit"))),
        "| 提交总数 | %d |" % _int(life.get("total_commits")),
        "| 追踪文件 / 代码行 | %d / %d |" % (_int(size.get("tracked_files")), _int(size.get("loc"))),
        "| 贡献者数 / bus factor | %d / %d |" % (_int(authors.get("total")), _int(authors.get("bus_factor"))),
        "| 近 6 月 / 前 6 月提交 | %d / %d（ratio %.2f） |"
        % (_int(trend.get("recent_6m")), _int(trend.get("previous_6m")), _float(trend.get("ratio"))),
        "| 依赖数 / 最老依赖天数 | %d / %s |"
        % (_int(deps.get("count")),
           "未知" if deps.get("oldest_days") is None else str(_int(deps.get("oldest_days")))),
        "| issue 中位关闭耗时 | %s |"
        % ("无数据" if issues.get("median_days") is None
           else "%.1f 天" % _float(issues.get("median_days"))),
        "| README | %s |" % ("有" if docs.get("has_readme") else "无"),
        "| TODO / FIXME | %d / %d |" % (_int(risks.get("todo_count")), _int(risks.get("fixme_count"))),
        "",
    ]
    return "\n".join(lines)


# ── 对外入口 ────────────────────────────────────────────────────────────────
def generate_narrative(metrics: dict, *, mode: str | None = None) -> dict:
    """把结构化指标变成最终要渲染的 Markdown。永不抛异常。"""
    started = time.time()
    deadline = started + _budget_total()

    result = {
        "ok": True,
        "degraded": False,
        "degraded_reason": None,
        "mode": "critical",
        "model": None,
        "markdown": "",
        "sections": {"will": "", "eulogy": ""},
        "elapsed_sec": 0.0,
    }

    try:
        if not isinstance(metrics, dict):
            metrics = {}

        # 模式解析
        resolved = mode if isinstance(mode, str) else None
        resolved = (resolved or "").strip().lower() or None
        if resolved not in MODES:
            resolved = _text(_get(metrics, "score", "mode"), "").lower() or None
        if resolved not in MODES:
            resolved = "critical"  # 兜底：两篇都出，保证有内容
        result["mode"] = resolved

        want_will = resolved in ("alive", "critical")
        want_eulogy = resolved in ("dead", "critical")

        reasons: list[str] = []
        models: list[str] = []

        jobs = []
        if want_will:
            jobs.append("will")
        if want_eulogy:
            jobs.append("eulogy")

        generated: dict[str, dict] = {}
        if len(jobs) == 2:
            # critical 模式：两篇**并发**，关键路径 ≈ 单篇耗时，而不是两篇相加
            generated = _run_parallel(jobs, metrics, deadline)
        else:
            for job in jobs:
                generated[job] = _generate_one(job, metrics, deadline)

        if want_will:
            will = generated.get("will") or {}
            result["sections"]["will"] = will.get("text") or ""
            if will.get("model"):
                models.append(will["model"])
            if not will.get("used_llm"):
                reasons.append("遗嘱：" + (will.get("reason") or "已降级"))

        if want_eulogy:
            eulogy = generated.get("eulogy") or {}
            result["sections"]["eulogy"] = eulogy.get("text") or ""
            if eulogy.get("model"):
                models.append(eulogy["model"])
            if not eulogy.get("used_llm"):
                reasons.append("悼词：" + (eulogy.get("reason") or "已降级"))

        will_text = (result["sections"].get("will") or "").strip()
        eulogy_text = (result["sections"].get("eulogy") or "").strip()

        # 极端兜底：模板理论上不会空，但空了一定要补
        if want_will and not will_text:
            will_text = render_will_template(metrics).strip()
            result["sections"]["will"] = will_text
            reasons.append("遗嘱：模板兜底")
        if want_eulogy and not eulogy_text:
            eulogy_text = render_eulogy_template(metrics).strip()
            result["sections"]["eulogy"] = eulogy_text
            reasons.append("悼词：模板兜底")

        if want_will and want_eulogy:
            markdown = "%s\n\n%s\n\n%s\n" % (will_text, prompts.SEPARATOR, eulogy_text)
        elif want_will:
            markdown = will_text + "\n"
        else:
            markdown = eulogy_text + "\n"

        result["degraded"] = bool(reasons)
        if reasons:
            result["degraded_reason"] = "；".join(reasons)
            markdown = "%s\n\n%s\n%s" % (
                DEGRADED_BANNER,
                markdown,
                _metrics_appendix(metrics),
            )

        result["model"] = models[0] if models else None
        result["markdown"] = markdown
        result["ok"] = bool(markdown.strip())

    except Exception as exc:  # 最后一道网：无论如何都要有内容
        try:
            fallback_metrics = metrics if isinstance(metrics, dict) else {}
            fallback_mode = result.get("mode") or "critical"
            parts = []
            if fallback_mode in ("alive", "critical"):
                parts.append(render_will_template(fallback_metrics).strip())
            if fallback_mode in ("dead", "critical"):
                parts.append(render_eulogy_template(fallback_metrics).strip())
            body = ("\n\n%s\n\n" % prompts.SEPARATOR).join(parts) if len(parts) > 1 else \
                (parts[0] if parts else "")
            if not body.strip():
                body = "# 代码遗嘱 · 未知仓库\n\n%s\n\n## 一、我留下了什么\n\n指标不可用。\n" % DEGRADED_BANNER
            result["markdown"] = "%s\n\n%s\n" % (DEGRADED_BANNER, body.strip())
            result["ok"] = True
        except Exception:
            result["markdown"] = "%s\n\n## 一、我留下了什么\n\n指标不可用。\n" % DEGRADED_BANNER
            result["ok"] = False
        result["degraded"] = True
        result["degraded_reason"] = "生成层异常：%s: %s" % (type(exc).__name__, exc)
        if not result.get("sections"):
            result["sections"] = {"will": "", "eulogy": ""}

    try:
        result["elapsed_sec"] = round(time.time() - started, 3)
    except Exception:
        result["elapsed_sec"] = 0.0

    return result


__all__ = ["generate_narrative", "render_will_template", "render_eulogy_template", "MODES",
           "DEGRADED_BANNER"]
