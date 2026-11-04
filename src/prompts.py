"""Prompt 模板（docs/SPEC.md 第 4 章 + 第 5 章要求）。

提供两个函数，返回 **OpenAI messages 列表**（``[{"role": ..., "content": ...}, ...]``），
调用方直接把返回值丢给 ``llm.chat()`` 即可：

    build_will_prompt(metrics)   -> 代码遗嘱（SPEC 4.1）
    build_eulogy_prompt(metrics) -> 项目悼词（SPEC 4.2）

设计要点：
- 结构化指标**完整**喂进去（作者分布、bus factor、最后提交、趋势、依赖、issue、
  文档、TODO/FIXME、stale_core_files、hotspots、top_dirs ...）。
- 显式给出「可用文件路径清单」，并禁止模型编造清单之外的路径。
- 章节标题逐字给出，方便生成层做标题校验、以及降级时对齐同一套结构。
- 明确要求：只输出 Markdown 正文，不要解释、不要用三引号包裹。
"""

from __future__ import annotations

import json

# ── 章节标题常量（生成层做校验时复用，避免两处不一致）──────────────────────────
WILL_TITLE_PREFIX = "# 代码遗嘱 · "
WILL_HEADINGS = [
    "## 一、我留下了什么",
    "## 二、谁在养我",
    "## 三、雷区：千万别动",
    "## 四、我死后，接手的人按这个顺序做",
    "## 五、最后的话",
]

EULOGY_TITLE_PREFIX = "# 悼词 · "
EULOGY_HEADINGS = [
    "## 一、它的一生",
    "## 二、死因",
    "## 三、它留下了什么",
    "## 四、教训",
    "## 五、结语",
]

EULOGY_DEATH_BULLETS = [
    "- **直接原因**：",
    "- **根本原因**：",
    "- **加速因素**：",
]

BUS_FACTOR_ONE_SENTENCE = "这个人离开，项目即终止"

SEPARATOR = "---\n\n> 以下是它如果继续这样下去，最终会收到的悼词。"

MAX_LAST_WORDS_CHARS = 150
MAX_CLOSING_CHARS = 100


# ── 取值工具（对不完整 metrics 保持宽容）──────────────────────────────────────
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


def repo_name(metrics: dict) -> str:
    """仓库显示名：优先 ``owner/name``，退化为 ``name`` / ``input``。"""
    repo = _get(metrics, "repo", default={}) or {}
    owner = str(repo.get("owner") or "").strip()
    name = str(repo.get("name") or "").strip()
    if owner and name:
        return f"{owner}/{name}"
    if name:
        return name
    raw = str(repo.get("input") or "").strip().rstrip("/")
    if raw:
        return raw.split("/")[-1] or raw
    return "未知仓库"


def collect_file_paths(metrics: dict, limit: int = 12) -> list[str]:
    """从 stale_core_files → hotspots → size.top_dirs 依次收集**真实存在**的路径。

    只收集 metrics 里真的出现过的路径，绝不凭想象补。降级模板也用这个函数，
    保证「至少 3 个具体文件路径」的要求在 LLM 挂掉时同样成立。
    """
    paths: list[str] = []
    seen: set[str] = set()

    def push(path) -> None:
        text = str(path or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        paths.append(text)

    for item in _as_list(_get(metrics, "risks", "stale_core_files", default=[])):
        if isinstance(item, dict):
            push(item.get("path"))

    for item in _as_list(_get(metrics, "risks", "hotspots", default=[])):
        if isinstance(item, dict):
            push(item.get("path"))

    for item in _as_list(_get(metrics, "size", "top_dirs", default=[])):
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip().strip("/")
            if not name or name in {".", "./"}:
                continue
            # 目录名不是文件，但它是真实存在的路径，作为兜底比编造一个文件名诚实
            push(name.rstrip("/") + "/")

    return paths[:limit]


# ── 指标摘要（喂给 LLM 的正文）────────────────────────────────────────────────
def render_metrics_brief(metrics: dict) -> str:
    """把 metrics 拍成一段人类和模型都好读的结构化文本。"""
    metrics = metrics if isinstance(metrics, dict) else {}

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
    parts = _get(score, "parts", default={}) or {}

    lines: list[str] = []
    add = lines.append

    add("### 仓库")
    add(f"- 名称：{repo_name(metrics)}")
    add(f"- 原始输入：{repo.get('input') or '未知'}")
    add(f"- 托管平台：{repo.get('host') or '未知'}")
    add(f"- 是否本地路径：{'是' if repo.get('is_local') else '否'}")
    if metrics.get("sampled"):
        add(f"- 采样说明：{metrics.get('sample_note') or '因体量过大已采样分析'}")

    add("")
    add("### 生命周期")
    add(f"- 首次提交：{life.get('first_commit') or '未知'}")
    add(f"- 最后提交：{life.get('last_commit') or '未知'}")
    add(f"- 最后一条提交信息：{life.get('last_commit_msg') or '（无）'}")
    age_text = life.get("age_text") or ("%d 天" % _int(life.get("age_days")))
    add("- 仓库年龄：%s" % age_text)
    add(f"- 首次到最后一次提交跨度：{_int(life.get('span_days'))} 天")
    add(f"- 距今最后一次提交：{_int(life.get('days_since_last_commit'))} 天")
    add(f"- 本次分析范围内提交数：{_int(life.get('total_commits'))}")

    add("")
    add("### 代码规模")
    add(f"- 追踪文件数：{_int(size.get('tracked_files'))}")
    add(f"- 代码行数：{_int(size.get('loc'))}")
    add(f"- 模块数：{_int(size.get('modules'))}")
    langs = _as_list(size.get("languages"))
    if langs:
        text = "、".join(
            f"{item.get('name')}({_int(item.get('files'))} 文件)"
            for item in langs[:6]
            if isinstance(item, dict)
        )
        add(f"- 语言分布：{text}")
    dirs = _as_list(size.get("top_dirs"))
    if dirs:
        text = "、".join(
            f"{item.get('name')}({_int(item.get('files'))} 文件)"
            for item in dirs[:8]
            if isinstance(item, dict)
        )
        add(f"- 主要目录：{text}")

    add("")
    add("### 贡献者与 bus factor")
    bf = _int(authors.get("bus_factor"))
    add(f"- 贡献者总数：{_int(authors.get('total'))}")
    add(f"- bus factor：{bf}")
    if bf == 1:
        add(f"- 判定：bus factor = 1，唯一关键人，{BUS_FACTOR_ONE_SENTENCE}（第二章必须原句写出）")
    elif bf == 0:
        add("- 判定：无有效提交者信息")
    else:
        add(f"- 判定：bus factor = {bf}，前 {bf} 个人承担了 60% 以上提交")
    top = _as_list(authors.get("top"))
    if top:
        add("- Top 贡献者（按提交数）：")
        for index, person in enumerate(top[:6], 1):
            if not isinstance(person, dict):
                continue
            share = _float(person.get("share"))
            add(
                f"  {index}. {person.get('name') or '匿名'}"
                f" <{person.get('email') or '无邮箱'}>"
                f" — {_int(person.get('commits'))} 次提交，占比 {share * 100:.1f}%"
            )

    add("")
    add("### 提交活跃度趋势")
    add(f"- 最近 6 个月提交数：{_int(trend.get('recent_6m'))}")
    add(f"- 之前 6 个月提交数：{_int(trend.get('previous_6m'))}")
    add(f"- 比值 ratio：{_float(trend.get('ratio')):.2f}")
    add(f"- 历史不足 12 个月：{'是（趋势无法判断）' if trend.get('insufficient_history') else '否'}")

    add("")
    add("### 依赖")
    add(f"- 采集状态：{deps.get('status') or 'none'}（生态：{deps.get('ecosystem') or 'none'}）")
    add(f"- 依赖数量：{_int(deps.get('count'))}")
    add(f"- 最老依赖：{deps.get('oldest_pkg') or '无'}")
    oldest_days = deps.get("oldest_days")
    add(f"- 最老依赖距今天数：{'未知' if oldest_days is None else _int(oldest_days)}")
    dep_items = _as_list(deps.get("items"))
    if dep_items:
        sample = []
        for item in dep_items[:8]:
            if isinstance(item, dict):
                days = item.get("days")
                sample.append(
                    f"{item.get('name')}@{item.get('version') or '?'}"
                    f"({'未知' if days is None else str(_int(days)) + ' 天前'})"
                )
        if sample:
            add(f"- 依赖样例：{'、'.join(sample)}")

    add("")
    add("### Issue")
    add(f"- 状态：{issues.get('status') or 'unavailable'}")
    add(f"- 已关闭：{_int(issues.get('closed_count'))}，仍开启：{_int(issues.get('open_count'))}")
    median = issues.get("median_days")
    median_text = "无数据" if median is None else ("%.1f 天" % _float(median))
    add("- 关闭中位耗时：%s" % median_text)

    add("")
    add("### 文档")
    add(f"- 有 README：{'是' if docs.get('has_readme') else '否'}")
    add(f"- README 路径：{docs.get('readme_path') or '无'}")
    add(f"- README 字符数：{_int(docs.get('readme_chars'))}")
    add(f"- 有安装/使用说明：{'是' if docs.get('has_install') else '否'}")
    add(f"- 有代码示例：{'是' if docs.get('has_example') else '否'}")

    add("")
    add("### 风险信号")
    add(f"- TODO 数量：{_int(risks.get('todo_count'))}")
    add(f"- FIXME 数量：{_int(risks.get('fixme_count'))}")
    stale = _as_list(risks.get("stale_core_files"))
    if stale:
        add("- 长期未改动的核心文件（path / 未改动天数 / 历史提交数 / 行数）：")
        for item in stale[:8]:
            if isinstance(item, dict):
                add(
                    f"  - {item.get('path')}"
                    f" / {_int(item.get('days_untouched'))} 天"
                    f" / {_int(item.get('commits'))} 次提交"
                    f" / {_int(item.get('loc'))} 行"
                )
    hotspots = _as_list(risks.get("hotspots"))
    if hotspots:
        add("- 危险代码热点（path:line / 类型 / 原文）：")
        for item in hotspots[:8]:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip().replace("\n", " ")
                add(
                    f"  - {item.get('path')}:{_int(item.get('line'))}"
                    f" / {item.get('kind') or 'TODO'} / {text[:120]}"
                )

    add("")
    add("### 健康分")
    add(f"- 总分：{_int(score.get('total'))} / 100")
    add(f"- 模式：{score.get('mode') or '未知'}")
    for key in ("last_commit", "bus_factor", "trend", "deps", "issues", "docs"):
        part = parts.get(key)
        if isinstance(part, dict):
            add(
                f"- {part.get('label') or key}：{_int(part.get('score'))}"
                f"/{_int(part.get('max'))} — {part.get('detail') or ''}"
            )

    # 真实路径清单：让模型只能从这里挑，杜绝编造
    paths = collect_file_paths(metrics, limit=12)
    add("")
    add("### 可用文件路径清单（只能从这里选，禁止编造）")
    if paths:
        for path in paths:
            add(f"- {path}")
    else:
        add("- （本次分析未采集到任何文件级路径）")

    add("")
    add("### 原始 JSON（供交叉核对，可忽略）")
    try:
        trimmed = {
            "repo": repo,
            "life": life,
            "size": {"tracked_files": size.get("tracked_files"), "loc": size.get("loc"),
                     "modules": size.get("modules")},
            "authors": {"total": authors.get("total"), "bus_factor": authors.get("bus_factor")},
            "trend": trend,
            "deps": {"status": deps.get("status"), "ecosystem": deps.get("ecosystem"),
                     "count": deps.get("count"), "oldest_days": deps.get("oldest_days")},
            "issues": issues,
            "docs": docs,
            "score": {"total": score.get("total"), "mode": score.get("mode")},
        }
        add(json.dumps(trimmed, ensure_ascii=False, indent=2, default=str))
    except Exception:
        add("{}")

    return "\n".join(lines)


_SYSTEM_WILL = (
    "你是一位资深工程师，正在替一个真实的代码仓库写「代码遗嘱」——"
    "它还没死，但你需要把交接所需的一切写清楚，好让接手的人能活下去。\n"
    "风格要求：冷静、具体、可执行。像一个负责任的前任维护者在交接，"
    "不抒情、不写诗、不客套、不使用 emoji。\n"
    "所有数字和路径必须来自给定指标，禁止编造。\n"
    "只输出 Markdown 正文，不要任何解释性开场白，不要用 ``` 三引号包裹。"
)

_SYSTEM_EULOGY = (
    "你是一位资深工程师，正在替一个已经（或即将）死掉的代码仓库写「悼词」。\n"
    "风格要求：克制、精准、有信息量，像技术复盘，不是追悼会发言稿。"
    "不煽情、不用 emoji、不写排比句。\n"
    "所有数字和事实必须来自给定指标，禁止编造。\n"
    "只输出 Markdown 正文，不要任何解释性开场白，不要用 ``` 三引号包裹。"
)


def build_will_prompt(metrics: dict) -> list[dict]:
    """生成遗嘱的 messages（SPEC 4.1）。"""
    metrics = metrics if isinstance(metrics, dict) else {}
    name = repo_name(metrics)
    brief = render_metrics_brief(metrics)
    bf = _int(_get(metrics, "authors", "bus_factor"))
    paths = collect_file_paths(metrics, limit=12)

    path_block = "\n".join(f"{i}. {p}" for i, p in enumerate(paths, 1)) or "（无可用路径）"

    bus_rule = (
        f'⚠️ 本仓库 bus factor = 1。第二章必须**逐字**出现这一句：'
        f"「{BUS_FACTOR_ONE_SENTENCE}」，不得改写、不得省略。"
        if bf == 1
        else "本仓库 bus factor > 1，第二章要说明「几个人扛住了 60% 的提交」意味着什么。"
    )

    user = f"""为一个仓库写「代码遗嘱」。仓库名：{name}

## 结构化指标（唯一事实来源）

{brief}

## 输出格式（必须严格遵守）

第一行固定为：

{WILL_TITLE_PREFIX}{name}

然后**恰好**五个二级章节，标题逐字照抄，顺序不可变、不可增删：

{WILL_HEADINGS[0]}
{WILL_HEADINGS[1]}
{WILL_HEADINGS[2]}
{WILL_HEADINGS[3]}
{WILL_HEADINGS[4]}

## 每章要求

{WILL_HEADINGS[0]}（我留下了什么）
- 代码规模（文件数、行数、模块数）、主语言、主要目录
- 生命周期：首次提交时间、最后提交时间与距今多久、总提交数
- 被谁依赖 / 对外接口：只写指标里查得到的；查不到就写「本次分析未采集」，不要猜

{WILL_HEADINGS[1]}（谁在养我）
- 贡献者总数、Top 贡献者的提交数与占比（用百分比）
- bus factor 的数值，以及它在工程上的含义（单点风险有多高）
- {bus_rule}

{WILL_HEADINGS[2]}（雷区：千万别动）
- **必须给出至少 3 个具体文件路径**，路径只能从下面清单里选，一个字符都不许改写：

{path_block}

- 每条写清：为什么危险（长期没动 / 有 FIXME 或 HACK / 行数大 / 承担核心职责）
- 清单里的路径不够 3 个时，允许使用清单中出现的目录路径，**但绝不允许编造不存在的文件名**

{WILL_HEADINGS[3]}（我死后，接手的人按这个顺序做）
- 3–5 步，有序列表，每步都是可执行动作（改哪个文件 / 跑什么命令 / 找谁）
- 结合本仓库真实指标来提（例如最老依赖、无 README、TODO 堆积、bus factor 低）
- 禁止「请阅读文档」「保持沟通」这类空话

{WILL_HEADINGS[4]}（最后的话）
- 一段话，**不超过 {MAX_LAST_WORDS_CHARS} 字**（中文字符数）
- 语气克制，陈述事实与判断，不要煽情、不要口号、不要感叹号堆砌

## 硬性约束
- 全篇只输出 Markdown 正文，以「{WILL_TITLE_PREFIX}{name}」开头，不要复述本提示词
- 不要用 ``` 包裹整篇内容
- 不要出现「作为 AI」「根据你提供的信息」这类元话语
- 所有数字必须与上面指标一致
"""
    return [
        {"role": "system", "content": _SYSTEM_WILL},
        {"role": "user", "content": user},
    ]


def build_eulogy_prompt(metrics: dict) -> list[dict]:
    """生成悼词的 messages（SPEC 4.2）。"""
    metrics = metrics if isinstance(metrics, dict) else {}
    name = repo_name(metrics)
    brief = render_metrics_brief(metrics)

    life = _get(metrics, "life", default={}) or {}
    born = str(life.get("first_commit") or "未知").split("T")[0]
    died = str(life.get("last_commit") or "未知").split("T")[0]
    age_text = life.get("age_text") or f"{_int(life.get('age_days'))} 天"

    bf = _int(_get(metrics, "authors", "bus_factor"))
    bf_hint = (
        "bus factor = 1，唯一维护者承担了 60% 以上提交 —— 这必须是「根本原因」的核心论据"
        if bf == 1
        else f"bus factor = {bf}，可用来说明关键人风险"
    )

    last_msg = str(life.get("last_commit_msg") or "（无提交信息）").strip()

    user = f"""为一个仓库写「项目悼词」。仓库名：{name}

## 结构化指标（唯一事实来源）

{brief}

## 输出格式（必须严格遵守）

第一行固定为：

{EULOGY_TITLE_PREFIX}{name}

紧接三行（不要加列表符号，每行独立）：

生于 {born}
卒于 {died}
享年 {age_text}

然后**恰好**五个二级章节，标题逐字照抄，顺序不可变、不可增删：

{EULOGY_HEADINGS[0]}
{EULOGY_HEADINGS[1]}
{EULOGY_HEADINGS[2]}
{EULOGY_HEADINGS[3]}
{EULOGY_HEADINGS[4]}

## 每章要求

{EULOGY_HEADINGS[0]}（它的一生）
- 关键时间点：起始、活跃期、衰退期、终止
- 用真实的提交数与时间跨度支撑，不要空泛地抒情

{EULOGY_HEADINGS[1]}（死因）
- 这一章**必须**包含且只包含下面三个条目，格式逐字照抄（全角冒号，加粗项名）：

{EULOGY_DEATH_BULLETS[0]}...
{EULOGY_DEATH_BULLETS[1]}...
{EULOGY_DEATH_BULLETS[2]}...
- 直接原因：最后一条提交信息是「{last_msg}」，距今 {_int(life.get('days_since_last_commit'))} 天；
  以及依赖 / issue / 文档等直接失效信号
- 根本原因：{bf_hint}
- 加速因素：趋势下滑、TODO/FIXME 堆积、无 README 或文档、依赖老化等

{EULOGY_HEADINGS[2]}（它留下了什么）
- 仍可能被使用的部分（代码规模、语言、模块）
- 未完成的承诺：TODO {_int(_get(metrics, 'risks', 'todo_count'))} 个、
  FIXME {_int(_get(metrics, 'risks', 'fixme_count'))} 个
- 长期无人动过的核心文件、危险热点

{EULOGY_HEADINGS[3]}（教训）
- 2–3 条，具体、可操作，不说教、不写鸡汤
- 每条要能落到「下次怎么做」

{EULOGY_HEADINGS[4]}（结语）
- 一段话，**不超过 {MAX_CLOSING_CHARS} 字**（中文字符数）
- 克制收尾，不要抒情排比

## 硬性约束
- 全篇只输出 Markdown 正文，以「{EULOGY_TITLE_PREFIX}{name}」开头，不要复述本提示词
- 不要用 ``` 包裹整篇内容
- 不要出现「作为 AI」「根据你提供的信息」这类元话语
- 生于 / 卒于 / 享年三行必须在标题之后、第一章之前
- 所有数字必须与上面指标一致
"""
    return [
        {"role": "system", "content": _SYSTEM_EULOGY},
        {"role": "user", "content": user},
    ]


def build_repair_prompt(headings: list[str], missing: list[str], mode: str,
                        extra_rules: list[str] | None = None) -> dict:
    """校验不通过时的追加消息（重试一次）。"""
    kind = "代码遗嘱" if mode == "will" else "项目悼词"
    rules = "\n".join(f"- {rule}" for rule in (extra_rules or []))
    return {
        "role": "user",
        "content": (
            f"你上一次的输出不合格，缺少这些章节标题：{'、'.join(missing) or '（未知）'}。\n"
            f"请从头重新输出完整的{kind} Markdown，必须包含并逐字使用以下标题，一个都不能少：\n"
            + "\n".join(headings)
            + ("\n\n额外硬性要求：\n" + rules if rules else "")
            + "\n\n要求：只输出 Markdown 正文，不要解释，不要用 ``` 包裹。"
        ),
    }


__all__ = [
    "WILL_HEADINGS",
    "EULOGY_HEADINGS",
    "EULOGY_DEATH_BULLETS",
    "WILL_TITLE_PREFIX",
    "EULOGY_TITLE_PREFIX",
    "BUS_FACTOR_ONE_SENTENCE",
    "SEPARATOR",
    "build_will_prompt",
    "build_eulogy_prompt",
    "build_repair_prompt",
    "render_metrics_brief",
    "collect_file_paths",
    "repo_name",
]
