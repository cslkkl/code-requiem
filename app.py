"""代码安魂曲 / Code Requiem —— Streamlit 界面层（app.py）。

只依赖 docs/CONTRACT.md 里约定的三个入口：

    src.analyzer.analyze_repo(url) -> dict
    src.narrative.generate_narrative(metrics) -> dict
    src.llm.provider_info() -> dict

设计原则：
  * 界面层只做「排版 + 容错」，不做任何判断逻辑（判断在 src/format.py 里，
    在那里可以脱离 streamlit 单测）。
  * 任何一层缺失 / 抛错都必须渲染成可读提示，页面绝不允许白屏。
  * 分数环、六维条、指标卡全部用原生 HTML/CSS 或 st.metric 实现，
    不引外部字体、不引 CDN，离线可用。

运行：
    .venv\\Scripts\\python.exe -u -m streamlit run app.py --server.headless true
"""

from __future__ import annotations

import html
import queue
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

# analyzer / narrative 内部用的是扁平导入（`import scoring`、`import prompts`），
# 只有在 src/ 位于 sys.path 时才能被当作包成员导入。这里补一条兼容路径，
# 既支持 `streamlit run app.py`，也支持 `python src/analyzer.py` 两种跑法。
_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from src import format as fmt  # noqa: E402

# 维度键 → 中文名。优先用 explain 层的定义，缺失时退回本地表，
# 保证「部分维度数据未获取」这条提示永远能说出人话。
try:
    from src.explain import DIM_LABELS as _DIM_LABELS  # noqa: E402
except Exception:  # pragma: no cover - 极端降级
    _DIM_LABELS = {
        "last_commit": "最后提交距今",
        "bus_factor": "Bus Factor",
        "trend": "提交活跃度趋势",
        "deps": "依赖健康度",
        "issues": "Issue 响应",
        "docs": "文档完整度",
    }

# ─────────────────────────────────────────────────────────────────────────────
# 依赖注入：三个模块可能还在并行开发中，缺失时不能让页面崩
# ─────────────────────────────────────────────────────────────────────────────

_IMPORT_ERRORS: dict[str, str] = {}

try:
    from src.analyzer import analyze_repo  # type: ignore[attr-defined]
except Exception as exc:  # noqa: BLE001 —— 缺文件、语法错、缺依赖都按「未就绪」处理
    analyze_repo = None  # type: ignore[assignment]
    _IMPORT_ERRORS["analyzer"] = f"{type(exc).__name__}: {exc}"

try:
    from src.narrative import generate_narrative  # type: ignore[attr-defined]
except Exception as exc:  # noqa: BLE001
    generate_narrative = None  # type: ignore[assignment]
    _IMPORT_ERRORS["narrative"] = f"{type(exc).__name__}: {exc}"

try:
    from src.llm import provider_info  # type: ignore[attr-defined]
except Exception as exc:  # noqa: BLE001
    provider_info = None  # type: ignore[assignment]
    _IMPORT_ERRORS["llm"] = f"{type(exc).__name__}: {exc}"


ENGINE_READY = analyze_repo is not None


def _accepts_progress(fn) -> bool:
    """analyzer 可能带 progress= 回调（当前实现有），也可能没有。"""
    if fn is None:
        return False
    try:
        import inspect

        return "progress" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


_ANALYZE_ACCEPTS_PROGRESS = _accepts_progress(analyze_repo)

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

APP_TITLE = "代码安魂曲 / Code Requiem"
GITHUB_URL = "https://github.com/cslkkl/code-requiem"

EXAMPLES = [
    {
        "tag": "存活样本",
        "label": "kornia/kornia",
        "url": "https://github.com/kornia/kornia",
        "note": "仍在高频提交，唯一短板是依赖",
        "score": "85",
    },
    {
        "tag": "承压样本",
        "label": "cslkkl/code-requiem",
        "url": "https://github.com/cslkkl/code-requiem",
        "note": "单人维护，bus factor 1",
        "score": "54",
    },
    {
        "tag": "已故样本",
        "label": "livid/v2ex-gae",
        "url": "https://github.com/livid/v2ex-gae",
        "note": "2017 年停更，只剩悼词",
        "score": "24",
    },
]

MAX_WAIT_ANALYZE = 240.0
MAX_WAIT_NARRATIVE = 180.0

# 界面预览用的假数据。
# 分析引擎还没落地时，用它把整套排版（分数环 / 六维条 / 指标卡 / 文档 / 解剖面板）
# 全部走一遍，保证 UI 本身可以被检查，而不是等别人写好才能看。
DEMO_METRICS: dict = {
    "ok": True,
    "error": None,
    "repo": {
        "input": "https://github.com/demo/aurora-registry",
        "host": "github",
        "owner": "demo",
        "name": "aurora-registry",
        "url": "https://github.com/demo/aurora-registry",
        "is_local": False,
    },
    "sampled": True,
    "sample_note": "仓库超过 500MB，只分析了最近 2000 个提交",
    "life": {
        "first_commit": "2019-03-11T09:12:00+00:00",
        "last_commit": "2026-09-28T17:41:00+00:00",
        "last_commit_msg": "chore: bump internal registry client",
        "total_commits": 2000,
        "age_days": 2795,
        "span_days": 2758,
        "days_since_last_commit": 37,
        "age_text": "7 年 8 个月",
    },
    "size": {
        "tracked_files": 1284,
        "loc": 184320,
        "modules": 26,
        "top_dirs": [
            {"name": "src", "files": 612},
            {"name": "tests", "files": 388},
        ],
        "languages": [{"name": "Python", "files": 1102}],
    },
    "authors": {
        "total": 23,
        "bus_factor": 2,
        "top": [
            {"name": "Lin Wei", "email": "lin@example.com", "commits": 968, "share": 0.484},
            {"name": "Ada Zhou", "email": "ada@example.com", "commits": 412, "share": 0.206},
        ],
    },
    "trend": {"recent_6m": 64, "previous_6m": 141, "ratio": 0.45, "insufficient_history": False},
    "deps": {
        "status": "ok",
        "ecosystem": "python",
        "count": 34,
        "oldest_pkg": "legacy-soap-client",
        "oldest_days": 812,
        "items": [{"name": "legacy-soap-client", "version": "0.9.1", "days": 812}],
    },
    "issues": {"status": "ok", "closed_count": 318, "open_count": 74, "median_days": 11.4},
    "docs": {
        "has_readme": True,
        "readme_path": "README.md",
        "readme_chars": 4820,
        "has_install": True,
        "has_usage": True,
        "has_example": False,
    },
    "risks": {
        "todo_count": 41,
        "fixme_count": 17,
        "stale_core_files": [
            {"path": "src/registry/resolver.py", "days_untouched": 940, "commits": 61, "loc": 812},
        ],
        "hotspots": [
            {"path": "src/registry/resolver.py", "line": 233, "kind": "FIXME", "text": "并发写回会丢更新"},
        ],
    },
    "score": {
        "total": 58,
        "mode": "critical",
        "parts": {
            "last_commit": {
                "score": 18,
                "max": 30,
                "label": "最后提交",
                "detail": "最后一次提交在 37 天前（≤90 天档）",
            },
            "bus_factor": {
                "score": 10,
                "max": 25,
                "label": "bus factor",
                "detail": "前 2 名贡献者合计 69% 提交，第二人一旦离开即单点",
            },
            "trend": {
                "score": 5,
                "max": 15,
                "label": "活跃趋势",
                "detail": "近 6 个月 64 次提交，之前 6 个月 141 次（0.45×），正在减速",
            },
            "deps": {
                "score": 5,
                "max": 15,
                "label": "依赖健康",
                "detail": "最老依赖 legacy-soap-client 已 812 天未更新（365–729 档以上）",
            },
            "issues": {
                "score": 6,
                "max": 10,
                "label": "Issue 响应",
                "detail": "已关闭 issue 中位响应 11 天（7–29 天档）",
            },
            "docs": {
                "score": 3,
                "max": 5,
                "label": "文档完整度",
                "detail": "有 README 且有安装/用法说明，但没有可跑示例",
            },
        },
    },
    "elapsed_sec": 35.4,
    "deterministic_key": "demo-preview-58",
}

DEMO_NARRATIVE: dict = {
    "ok": True,
    "degraded": False,
    "degraded_reason": None,
    "mode": "critical",
    "model": "Qwen/Qwen2.5-7B-Instruct（预览）",
    "markdown": (
        "# 代码遗嘱 · aurora-registry\n\n"
        "## 一、我留下了什么\n\n"
        "1284 个受跟踪文件、184,320 行代码、26 个模块，核心是 `src/registry/`。"
        "七年八个月里，2000 次提交把一块内部注册中心长成了别人上线前必须过的关卡。\n\n"
        "## 二、谁在养我\n\n"
        "23 位贡献者，但真正在养的只有两个人：Lin Wei（48.4%）与 Ada Zhou（20.6%）。"
        "**bus factor = 2** —— 这两个人里再走一个，项目就变成单点。\n\n"
        "## 三、雷区：千万别动\n\n"
        "- `src/registry/resolver.py`（940 天未被触碰，812 行，曾经历 61 次修改）\n"
        "- `src/registry/client.py` —— 依赖层解析与重试逻辑绕在一起\n"
        "- `src/registry/migrations/` —— 迁移脚本顺序敏感，重排即数据损坏\n\n"
        "> 17 处 FIXME 中有 6 处集中在 `resolver.py`，其中第 233 行写着：并发写回会丢更新。\n\n"
        "## 四、我死后，接手的人按这个顺序做\n\n"
        "1. 先把 `tests/` 跑起来，记录当前失败用例数，作为基线。\n"
        "2. 冻结 `migrations/` 顺序，加一条 CI 校验，防止有人重排。\n"
        "3. 给 `resolver.py` 补并发写回的回归测试，再动代码。\n"
        "4. 替掉 `legacy-soap-client`（812 天未更新），这是最老的依赖。\n"
        "5. 让第二位维护者独立完成一次发版，验证 bus factor 是否真的等于 2。\n\n"
        "## 五、最后的话\n\n"
        "我还活着，但活跃度已经掉到一年前的四成五。请不要等到某天发现没人能发版，"
        "才想起这份文件。\n"
    ),
    "sections": {"will": "# 代码遗嘱 · aurora-registry", "eulogy": ""},
    "elapsed_sec": 5.8,
}

# ─────────────────────────────────────────────────────────────────────────────
# 视觉：深色石碑 / 终端感
# ─────────────────────────────────────────────────────────────────────────────

_CSS = """
<style>
/* ═══════════════════════════════════════════════════════════════════════════
   代码安魂曲 —— 体检报告排版
   这份界面模仿的不是「AI 产品落地页」，而是纸质的化验单 / 尸检报告：
   左对齐、细分隔线、等宽数字、粗字重标题，没有卡片、没有药丸、没有衬线。
   ═══════════════════════════════════════════════════════════════════════════ */

:root{
  /* 纸与墨：冷调中性，不用米黄（那是「精品消费品牌」的套路） */
  --req-paper:#F4F4F2;
  --req-ink:#15140F;
  --req-ink-2:#3A3833;
  --req-ink-3:#6B6862;
  --req-ink-4:#96938C;
  --req-rule:#D8D5CE;      /* 主分隔线 */
  --req-rule-2:#E7E5E0;    /* 次分隔线 */

  /* 唯一强调色：氧化红。只出现在「分数」「当前状态」「链接」三处。 */
  --req-accent:#8E3223;

  /* 状态用墨色，不用糖果色 */
  --req-ok:#2F5D3A;
  --req-warn:#7A5310;
  --req-bad:#8E3223;

  --req-sans:'Segoe UI Variable Display','Segoe UI','PingFang SC',
             'Hiragino Sans GB','Microsoft YaHei','Noto Sans CJK SC',
             system-ui,sans-serif;
  --req-mono:'Cascadia Mono','JetBrains Mono','SF Mono',Consolas,
             'Liberation Mono',monospace;
}

/* ── 基底 ──────────────────────────────────────────────────────────────── */
.stApp{background:var(--req-paper);}
html,body,[class*="css"]{
  font-family:var(--req-sans);
  color:var(--req-ink);
  -webkit-font-smoothing:antialiased;
}
[data-testid="stHeader"]{background:transparent;height:0;}
[data-testid="stToolbar"]{display:none;}
footer{display:none;}
#MainMenu{visibility:hidden;}

.block-container{
  max-width:1140px;padding-top:2.6rem;padding-bottom:6rem;
  padding-left:2.4rem;padding-right:2.4rem;
}

/* Streamlit 原生 markdown 段落 */
[data-testid="stMarkdownContainer"] p{
  font-size:.94rem;line-height:1.72;color:var(--req-ink-2);
}
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3{color:var(--req-ink);letter-spacing:-.018em;}
[data-testid="stMarkdownContainer"] code{
  font-family:var(--req-mono);font-size:.86em;
  background:var(--req-rule-2);padding:1px 5px;border-radius:2px;
}
[data-testid="stMarkdownContainer"] a{color:var(--req-accent);text-decoration:none;}
[data-testid="stMarkdownContainer"] a:hover{text-decoration:underline;}
[data-testid="stMarkdownContainer"] hr{border:none;border-top:1px solid var(--req-rule);}
[data-testid="stMarkdownContainer"] ul,
[data-testid="stMarkdownContainer"] ol{padding-left:1.2rem;}
[data-testid="stMarkdownContainer"] li{
  font-size:.92rem;line-height:1.7;color:var(--req-ink-2);margin-bottom:5px;
}

/* ── 报头：左对齐的一行，像报告抬头的机构名 ────────────────────────────── */
.req-mast{
  display:flex;align-items:baseline;gap:14px;
  padding-bottom:13px;border-bottom:1.5px solid var(--req-ink);
  margin-bottom:30px;
}
.req-mast__title{
  font-family:var(--req-sans);font-size:1.72rem;font-weight:750;
  color:var(--req-ink);letter-spacing:-.045em;line-height:1;margin:0;
}
.req-mast__title span{color:var(--req-ink);}
.req-mast__latin{
  font-family:var(--req-mono);font-size:.66rem;letter-spacing:.19em;
  color:var(--req-ink-4);text-transform:uppercase;
}
.req-mast__note{
  margin-left:auto;font-family:var(--req-mono);font-size:.68rem;
  color:var(--req-ink-3);letter-spacing:.02em;
}

/* ── 输入行：贴着分隔线的一条，不要卡片 ────────────────────────────────── */
[data-testid="stTextInput"] input{
  font-family:var(--req-mono) !important;font-size:.88rem !important;
  background:transparent !important;border:none !important;
  border-bottom:1.5px solid var(--req-ink) !important;
  border-radius:0 !important;padding:9px 2px !important;color:var(--req-ink) !important;
}
[data-testid="stTextInput"] input:focus{
  box-shadow:none !important;border-bottom-color:var(--req-accent) !important;
}
[data-testid="stTextInput"] input::placeholder{color:var(--req-ink-4) !important;}
[data-testid="stTextInput"] > div{border:none !important;background:transparent !important;}
[data-testid="stTextInput"] label{display:none;}

/* Streamlit 现在把按钮渲染成 button[data-testid="stBaseButton-*"]。
   老写法 `.stButton>button` 已经匹配不上，会静默退回 Streamlit 默认的
   圆角白卡 —— 于是「侧栏示例」看起来又变回卡片，主按钮失去反白文字。
   这就是必须用真实 testid 前缀选择器的原因。 */
button[data-testid^="stBaseButton"]{
  font-family:var(--req-mono) !important;font-size:.76rem !important;
  letter-spacing:.08em !important;border-radius:0 !important;
  transition:background .12s ease,color .12s ease;
}
/* 关键：Streamlit 把按钮文案渲染成 markdown 段落，
   会被上面的 `stMarkdownContainer p{color:var(--req-ink-2)}` 覆盖成近黑色，
   于是深底按钮上出现深色文字（对比度不合格）。
   这里把按钮内部所有文字颜色拉回继承。 */
button[data-testid^="stBaseButton"] p,
button[data-testid^="stBaseButton"] span,
button[data-testid^="stBaseButton"] div{
  color:inherit !important;font-size:inherit !important;
  line-height:1.4 !important;margin:0 !important;
}
button[data-testid="stBaseButton-primaryFormSubmit"],
button[data-testid="stBaseButton-primary"]{
  background:var(--req-ink) !important;color:var(--req-paper) !important;
  border:1.5px solid var(--req-ink) !important;padding:10px 22px !important;
}
button[data-testid="stBaseButton-primaryFormSubmit"]:hover,
button[data-testid="stBaseButton-primary"]:hover{
  background:var(--req-accent) !important;border-color:var(--req-accent) !important;
  color:var(--req-paper) !important;
}
button[data-testid^="stBaseButton"]:active{transform:translateY(1px);}
button[data-testid^="stBaseButton"]:focus:not(:active){
  box-shadow:none !important;outline:1.5px solid var(--req-accent) !important;
  outline-offset:2px;
}

/* ── 侧栏：同样用分隔线组织，不放卡片 ──────────────────────────────────── */
[data-testid="stSidebar"]{background:var(--req-paper);border-right:1px solid var(--req-rule);}
[data-testid="stSidebar"] > div:first-child{padding-top:2.2rem;}
[data-testid="stSidebar"] .block-container{padding-left:1.3rem;padding-right:1.3rem;}
.req-side__title{
  font-family:var(--req-mono);font-size:.68rem;letter-spacing:.2em;
  color:var(--req-ink);text-transform:uppercase;
  padding-bottom:9px;border-bottom:1.5px solid var(--req-ink);margin-bottom:14px;
}
.req-side__desc{
  font-size:.83rem;line-height:1.72;color:var(--req-ink-2);margin-bottom:24px;
}
.req-side__desc b{font-weight:650;color:var(--req-ink);}
.req-side__tag{
  font-family:var(--req-mono);font-size:.62rem;letter-spacing:.17em;
  color:var(--req-ink-4);text-transform:uppercase;
  padding-bottom:7px;border-bottom:1px solid var(--req-rule);
  margin:0 0 11px;
}
[data-testid="stSidebar"] button[data-testid^="stBaseButton"]{
  width:100% !important;text-align:left !important;
  justify-content:flex-start !important;
  font-family:var(--req-mono) !important;font-size:.76rem !important;
  background:transparent !important;color:var(--req-ink-2) !important;
  border:none !important;border-bottom:1px solid var(--req-rule-2) !important;
  border-radius:0 !important;padding:8px 0 !important;
  letter-spacing:.01em !important;box-shadow:none !important;
}
[data-testid="stSidebar"] button[data-testid^="stBaseButton"]:hover{
  background:transparent !important;color:var(--req-accent) !important;
  border-bottom-color:var(--req-accent) !important;
}
[data-testid="stSidebar"] button[data-testid^="stBaseButton"]:focus:not(:active){
  background:transparent !important;color:var(--req-ink) !important;
  box-shadow:none !important;outline:none !important;
}
.req-ex__note{
  font-family:var(--req-mono);font-size:.64rem;line-height:1.55;
  color:var(--req-ink-4);margin:2px 0 13px;
}
.req-pill{
  display:inline-block;font-family:var(--req-mono);font-size:.66rem;
  letter-spacing:.05em;padding:3px 0;margin-bottom:3px;
  border:none;background:transparent;border-radius:0;
}
.req-pill__dot{
  display:inline-block;width:5px;height:5px;margin-right:6px;
  vertical-align:middle;background:currentColor;
}
.req-side__meta{
  font-family:var(--req-mono);font-size:.62rem;line-height:1.6;
  color:var(--req-ink-4);word-break:break-all;margin-bottom:20px;
}
.req-hint{font-family:var(--req-mono);font-size:.64rem;color:var(--req-ink-4);}

/* ── 空状态：左对齐的一段说明，不要居中的欢迎语 ────────────────────────── */
.req-empty{
  border-top:1px solid var(--req-rule);padding:44px 0 40px;
  max-width:62ch;
}
.req-empty__glyph{
  display:none;
}
.req-empty__title{
  font-family:var(--req-sans);font-size:1.06rem;font-weight:700;
  color:var(--req-ink);letter-spacing:-.02em;margin-bottom:10px;
}
.req-empty__detail{
  font-size:.9rem;line-height:1.75;color:var(--req-ink-3);
}
.req-empty__detail b{color:var(--req-ink);font-weight:650;}

/* ── 分数区：像化验单的抬头，巨大数字 + 右侧事实表 ────────────────────── */
.req-hero{
  display:grid;grid-template-columns:auto 1fr;gap:0 40px;
  border-top:1.5px solid var(--req-ink);
  border-bottom:1px solid var(--req-rule);
  padding:26px 0 24px;
}
.req-hero__score{
  font-family:var(--req-mono);font-size:4.6rem;font-weight:600;
  line-height:.86;letter-spacing:-.045em;color:var(--req-ink);
  font-variant-numeric:tabular-nums;
}
.req-hero__score.is-bad{color:var(--req-bad);}
.req-hero__score.is-warn{color:var(--req-warn);}
.req-hero__denom{
  font-family:var(--req-mono);font-size:.72rem;color:var(--req-ink-4);
  letter-spacing:.06em;display:block;margin-top:9px;
}
.req-hero__body{min-width:0;padding-top:2px;}
.req-hero__repo{
  font-family:var(--req-sans);font-size:1.24rem;font-weight:700;
  color:var(--req-ink);letter-spacing:-.025em;line-height:1.2;
  margin-bottom:7px;word-break:break-all;
}
.req-hero__repo a{color:inherit;text-decoration:none;}
.req-hero__repo a:hover{color:var(--req-accent);}
.req-hero__state{
  font-family:var(--req-mono);font-size:.72rem;letter-spacing:.1em;
  text-transform:uppercase;margin-bottom:11px;
}
.req-hero__verdict{
  font-size:.95rem;line-height:1.72;color:var(--req-ink-2);margin-bottom:16px;
  max-width:60ch;
}
.req-hero__foot{
  font-family:var(--req-mono);font-size:.64rem;letter-spacing:.02em;
  color:var(--req-ink-4);margin-top:13px;
}
.req-hero__scorewrap{display:flex;flex-direction:column;justify-content:flex-end;}

/* 事实表：键值对，用点线引导，像检验单的项目行 */
.req-facts{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
  gap:0;border-top:1px solid var(--req-rule-2);
}
.req-fact{padding:9px 0 8px;border-bottom:1px solid var(--req-rule-2);}
.req-fact__k{
  font-family:var(--req-mono);font-size:.6rem;letter-spacing:.13em;
  color:var(--req-ink-4);text-transform:uppercase;margin-bottom:3px;
}
.req-fact__v{
  font-family:var(--req-mono);font-size:.82rem;color:var(--req-ink);
  font-variant-numeric:tabular-nums;
}

/* ── 区块标题：一行文字 + 右侧批注，下面紧跟内容，不套容器 ──────────────── */
.req-sec{
  display:flex;align-items:baseline;gap:12px;
  margin:44px 0 0;padding-bottom:8px;
  border-bottom:1.5px solid var(--req-ink);
}
.req-sec__label{
  font-family:var(--req-sans);font-size:.98rem;font-weight:700;
  color:var(--req-ink);letter-spacing:-.015em;
}
.req-sec__hint{
  margin-left:auto;font-family:var(--req-mono);font-size:.68rem;
  color:var(--req-ink-4);text-align:right;
}

/* ── 三个实际问题：编号列表，整宽单列，不做三等分卡 ────────────────────── */
.req-answers{border-bottom:1px solid var(--req-rule);}
.req-answer{
  display:grid;grid-template-columns:34px 1fr;gap:0 16px;
  padding:20px 0 19px;border-top:1px solid var(--req-rule-2);
}
.req-answer:first-child{border-top:none;}
.req-answer__n{
  font-family:var(--req-mono);font-size:.72rem;color:var(--req-ink-4);
  padding-top:3px;font-variant-numeric:tabular-nums;
}
.req-answer__q{
  font-family:var(--req-sans);font-size:.92rem;font-weight:650;
  color:var(--req-ink);margin-bottom:6px;letter-spacing:-.008em;
}
.req-answer__a{
  font-family:var(--req-sans);font-size:1.06rem;font-weight:700;
  color:var(--req-ink);letter-spacing:-.02em;margin-bottom:7px;
}
.req-answer__a.is-ok{color:var(--req-ok);}
.req-answer__a.is-warn{color:var(--req-warn);}
.req-answer__a.is-bad{color:var(--req-bad);}
.req-answer__d{
  font-size:.88rem;line-height:1.75;color:var(--req-ink-2);max-width:66ch;
}

/* ── 六维：表格行，不是卡片列表 ────────────────────────────────────────── */
.req-dims{border-bottom:1px solid var(--req-rule);}
.req-dim{
  display:grid;grid-template-columns:1fr auto;gap:0 20px;
  padding:16px 0 15px;border-top:1px solid var(--req-rule-2);
}
.req-dim:first-child{border-top:none;}
.req-dim__top{
  grid-column:1 / -1;display:flex;align-items:baseline;gap:9px;
  margin-bottom:8px;
}
.req-dim__name{
  font-family:var(--req-sans);font-size:.94rem;font-weight:650;
  color:var(--req-ink);letter-spacing:-.008em;
}
.req-dim__term{
  font-family:var(--req-mono);font-size:.62rem;font-style:normal;
  color:var(--req-ink-4);letter-spacing:.03em;
}
.req-dim__score{
  margin-left:auto;font-family:var(--req-mono);font-size:.9rem;
  font-weight:600;font-variant-numeric:tabular-nums;
}
.req-dim__score i{
  font-style:normal;font-size:.68rem;color:var(--req-ink-4);font-weight:400;
}
.req-dim__track{
  grid-column:1 / -1;height:2px;background:var(--req-rule-2);
  margin-bottom:9px;position:relative;overflow:hidden;
}
.req-dim__fill{height:100%;}
.req-dim__detail{
  grid-column:1 / -1;font-size:.85rem;line-height:1.72;color:var(--req-ink-2);
  max-width:74ch;
}
.req-dims__note{
  font-family:var(--req-mono);font-size:.66rem;color:var(--req-ink-4);
  padding:9px 0 0;letter-spacing:.02em;
}

/* ── 指标卡：改成紧凑的键值网格，不要八个大方块 ────────────────────────── */
[data-testid="stMetric"]{padding:0;}
[data-testid="stMetricLabel"] p{
  font-family:var(--req-mono) !important;font-size:.6rem !important;
  letter-spacing:.11em !important;color:var(--req-ink-4) !important;
  text-transform:uppercase;
}
[data-testid="stMetricValue"]{
  font-family:var(--req-mono) !important;font-size:1.02rem !important;
  font-weight:600 !important;color:var(--req-ink) !important;
  font-variant-numeric:tabular-nums;
}
[data-testid="stHorizontalBlock"]{gap:0 !important;}

/* ── 折叠区：像报告的附录，朴素 ────────────────────────────────────────── */
[data-testid="stExpander"]{
  border:1px solid var(--req-rule) !important;border-radius:0 !important;
  background:transparent !important;margin:14px 0 0 !important;
}
[data-testid="stExpander"] summary{
  font-family:var(--req-mono) !important;font-size:.74rem !important;
  color:var(--req-ink-2) !important;padding:10px 13px !important;
  letter-spacing:.02em;
}
[data-testid="stExpander"] summary:hover{color:var(--req-accent) !important;}
[data-testid="stExpander"] > div{border-top:1px solid var(--req-rule);}

/* ── 提示条：左侧竖线，不是圆角卡片 ────────────────────────────────────── */
.req-notice{
  font-size:.86rem;line-height:1.72;color:var(--req-ink-2);
  padding:11px 0 11px 14px;margin:14px 0;
  border-left:2.5px solid var(--req-ink-4);background:transparent;
}
.req-notice b{
  font-family:var(--req-mono);font-size:.68rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--req-ink);margin-right:7px;
}
.req-notice code{
  font-family:var(--req-mono);font-size:.86em;background:var(--req-rule-2);
  padding:1px 4px;
}
.req-notice.is-warn{border-left-color:var(--req-warn);}
.req-notice.is-bad{border-left-color:var(--req-bad);}
.req-notice.is-ok{border-left-color:var(--req-ok);}
.req-notice.is-ghost{border-left-color:var(--req-rule);}

/* ── 文档区：正文像排版过的手册，标题用字重不用颜色 ────────────────────── */
.req-doc-head{
  display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
  padding-bottom:9px;border-bottom:1px solid var(--req-rule);margin-bottom:18px;
}
.req-doc-head__t{
  font-family:var(--req-sans);font-size:.92rem;font-weight:700;
  color:var(--req-ink);letter-spacing:-.01em;
}
.req-doc-head__m{
  margin-left:auto;font-family:var(--req-mono);font-size:.66rem;
  color:var(--req-ink-4);letter-spacing:.02em;
}
.req-doc{
  font-size:.92rem;line-height:1.82;color:var(--req-ink-2);
}
.req-doc h2{
  font-family:var(--req-sans);font-size:1.02rem;font-weight:700;
  color:var(--req-ink);letter-spacing:-.018em;
  margin:30px 0 11px;padding-bottom:7px;
  border-bottom:1px solid var(--req-rule-2);
}
.req-doc h3{
  font-size:.94rem;font-weight:650;color:var(--req-ink);
  margin:19px 0 7px;
}
.req-doc p{margin:0 0 11px;}
.req-doc ul,.req-doc ol{margin:0 0 13px;padding-left:1.25rem;}
.req-doc li{margin-bottom:5px;line-height:1.78;}
.req-doc strong{color:var(--req-ink);font-weight:650;}
.req-doc table{
  width:100%;border-collapse:collapse;margin:13px 0;font-size:.86rem;
}
.req-doc th{
  font-family:var(--req-mono);font-size:.66rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--req-ink-4);font-weight:500;
  text-align:left;padding:7px 10px 7px 0;
  border-bottom:1.5px solid var(--req-rule);
}
.req-doc td{
  padding:8px 10px 8px 0;border-bottom:1px solid var(--req-rule-2);
  color:var(--req-ink-2);vertical-align:top;
}

/* ── 页脚 ──────────────────────────────────────────────────────────────── */
.req-foot{
  margin-top:52px;padding-top:13px;border-top:1.5px solid var(--req-ink);
  font-family:var(--req-mono);font-size:.64rem;letter-spacing:.04em;
  color:var(--req-ink-4);display:flex;gap:18px;flex-wrap:wrap;
}

/* ── 进度 / 加载 ───────────────────────────────────────────────────────── */
[data-testid="stStatusWidget"]{
  font-family:var(--req-mono) !important;font-size:.72rem !important;
  color:var(--req-ink-3) !important;
}
.stSpinner>div{border-color:var(--req-ink-4) transparent transparent transparent !important;}
[data-testid="stProgress"] > div > div > div{background:var(--req-ink) !important;}

/* ── 标签页（解剖面板）──────────────────────────────────────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"]{
  gap:0;border-bottom:1.5px solid var(--req-ink);background:transparent;
}
[data-testid="stTabs"] [data-baseweb="tab"]{
  font-family:var(--req-mono);font-size:.74rem;color:var(--req-ink-3);
  background:transparent;border:none;border-radius:0;
  padding:8px 14px 8px 0;letter-spacing:.03em;
}
[data-testid="stTabs"] [aria-selected="true"]{
  color:var(--req-ink) !important;font-weight:600;
  border-bottom:2px solid var(--req-accent) !important;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stTabs"] [data-baseweb="tab-border"]{display:none;}

/* ── 数据表 ────────────────────────────────────────────────────────────── */
[data-testid="stDataFrame"]{border:1px solid var(--req-rule) !important;border-radius:0 !important;}

/* ── 选中文字 ──────────────────────────────────────────────────────────── */
::selection{background:var(--req-ink);color:var(--req-paper);}

/* ── 窄屏 ──────────────────────────────────────────────────────────────── */
@media (max-width:760px){
  .block-container{padding-left:1.1rem;padding-right:1.1rem;padding-top:1.6rem;}
  .req-hero{grid-template-columns:1fr;gap:16px 0;}
  .req-hero__score{font-size:3.2rem;}
  .req-mast{flex-wrap:wrap;gap:6px 12px;}
  .req-mast__note{margin-left:0;width:100%;}
}
</style>
"""


# ─────────────────────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────────────────────


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def notice(tag: str, body: str, kind: str = "") -> str:
    cls = f"req-notice req-notice--{kind}" if kind else "req-notice"
    return f'<div class="{cls}"><span class="req-notice__tag">{esc(tag)}</span>{body}</div>'


def section(label: str, hint: str = "") -> None:
    hint_html = f'<span class="req-sec__hint">{esc(hint)}</span>' if hint else ""
    st.markdown(
        f'<div class="req-sec"><span class="req-sec__label">{esc(label)}</span>'
        f'<span class="req-sec__rule"></span>{hint_html}</div>',
        unsafe_allow_html=True,
    )


def _legend_html() -> str:
    """三档状态图例：让评委一眼看懂 70/30 两条线。"""
    items = []
    for mode in ("alive", "critical", "dead"):
        meta = fmt.mode_meta(mode)
        items.append(
            '<span class="req-legend__item">'
            f'<span class="req-legend__bar" style="background:{meta["tone"]}"></span>'
            f'{esc(meta["label"])} <span class="req-legend__range">{esc(meta["band"])}</span>'
            f" · {esc(meta['doc'])}</span>"
        )
    return '<div class="req-legend">' + "".join(items) + "</div>"


def _provider_snapshot() -> dict:
    """provider_info() 带 60 秒会话缓存；任何异常都退化为「未配置」。"""
    now = time.time()
    cached = st.session_state.get("_provider")
    if isinstance(cached, dict) and now - float(cached.get("_ts", 0)) < 60:
        return cached.get("info") or {}

    if provider_info is None:
        info = {
            "configured": False,
            "source": "missing",
            "model": "—",
            "endpoint": "",
            "_err": _IMPORT_ERRORS.get("llm", "src/llm.py 尚未就绪"),
        }
    else:
        try:
            raw = provider_info()
            info = raw if isinstance(raw, dict) else {}
        except Exception as exc:  # noqa: BLE001
            info = {
                "configured": False,
                "source": "error",
                "model": "—",
                "endpoint": "",
                "_err": f"{type(exc).__name__}: {exc}",
            }
    st.session_state["_provider"] = {"_ts": now, "info": info}
    return info


def _call_with_ticker(
    fn,
    *,
    label: str,
    bar,
    base: float,
    span: float,
    est: float,
    max_wait: float,
    step=None,
    stages: list | None = None,
    prefix: str = "",
):
    """在工作线程里跑阻塞调用，主线程按秒刷新进度条，返回 (结果, 耗时)。

    analyze_repo 内部是单次阻塞（克隆 + 采集），不拆线程就只能给用户
    一个静止的 spinner。这里保证：
      * 进度条每秒推进（带秒表，用户知道没死）；
      * analyzer 通过 progress= 回调吐出的阶段文本实时显示；
      * 即使调用卡死，也在 max_wait 后报错返回。
    """
    box: queue.Queue[tuple[str, object]] = queue.Queue()

    def _worker() -> None:
        try:
            box.put(("ok", fn()))
        except BaseException as exc:  # noqa: BLE001
            box.put(("err", exc))

    thread = threading.Thread(target=_worker, name="requiem-worker", daemon=True)
    started = time.perf_counter()
    thread.start()

    shown = ""
    while thread.is_alive():
        elapsed = time.perf_counter() - started
        if elapsed > max_wait:
            break
        if stages:
            latest = str(stages[-1] or "")
            if latest and latest != shown:
                shown = latest
                if step is not None:
                    step.markdown(f"`{prefix}` {latest}")
                bar.progress(min(0.995, base + span * min(0.95, elapsed / max(est, 1.0))), text=latest)
        fraction = min(0.95, elapsed / max(est, 1.0))
        bar.progress(min(0.995, base + span * fraction), text=f"{label} … {elapsed:4.1f}s")
        thread.join(0.18)

    thread.join(0.4)
    elapsed = time.perf_counter() - started
    if box.empty():
        raise TimeoutError(f"{label} 超过 {max_wait:.0f} 秒仍未返回")

    kind, payload = box.get()
    if kind == "err":
        raise payload  # type: ignore[misc]
    return payload, elapsed


# ─────────────────────────────────────────────────────────────────────────────
# 页面骨架
# ─────────────────────────────────────────────────────────────────────────────


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def render_masthead() -> None:
    st.markdown(
        '<div class="req-mast">'
        '<h1 class="req-mast__title">代码<span>安魂曲</span></h1>'
        '<span class="req-mast__latin">code requiem</span>'
        '<span class="req-mast__note">仓库生命体征检查 · 交接风险盘点</span>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown('<div class="req-side__title">CODE REQUIEM</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="req-side__desc">'
            "输入一个公开 Git 仓库，我们读出它的生命体征：六维健康分、bus factor、"
            "还有谁在养它。活着的交出<b>遗嘱</b>，断气的领走<b>悼词</b>。"
            "</div>",
            unsafe_allow_html=True,
        )

        st.markdown('<div class="req-side__tag">示例仓库</div>', unsafe_allow_html=True)
        for index, example in enumerate(EXAMPLES):
            # 按钮在前、说明在后：说明属于这个按钮，不是下一个。
            # 按钮用「标签 + 分数」，说明用「档位 · 依据」，避免侧栏出现两行长句。
            if st.button(
                f"{example['label']}　{example['score']}",
                key=f"example_{index}",
                help=f"{example['tag']} · {example['note']}",
                type="secondary",
                width="stretch",
            ):
                st.session_state["queued_url"] = example["url"]
                st.session_state["autorun"] = True
                st.rerun()
            st.markdown(
                f'<div class="req-ex__note">{esc(example["tag"])} · {esc(example["note"])}</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div class="req-side__tag">AI 叙事</div>', unsafe_allow_html=True)
        info = _provider_snapshot()
        badge = fmt.llm_badge(info)
        st.markdown(
            f'<div class="req-pill" style="color:{esc(badge["tone"])};'
            f'background:var(--req-surface-2)">'
            f'<span class="req-pill__dot"></span>{esc(badge["text"])}</div>'
            f'<div class="req-side__meta">{esc(badge["detail"])}</div>',
            unsafe_allow_html=True,
        )
        if not ENGINE_READY:
            st.markdown(
                '<div class="req-side__meta" style="color:#9F2F2D">'
                "分析引擎尚未就绪（src/analyzer.py 未加载）</div>",
                unsafe_allow_html=True,
            )
            if st.button("预览界面（假数据）", key="demo_preview"):
                st.session_state["demo"] = True

        st.markdown('<div class="req-side__tag">关于</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="req-side__meta">分析层完全确定性，不含任何 AI 参与打分；'
            f"AI 只负责把指标写成叙事。</div>"
            f'<div class="req-side__meta" style="margin-top:10px">'
            f'<a href="{GITHUB_URL}" target="_blank" rel="noopener">'
            f"github.com/cslkkl/code-requiem</a></div>",
            unsafe_allow_html=True,
        )


def render_top_alerts(result: dict | None) -> None:
    """页面顶部的常驻提示：采样、LLM 降级、引擎缺失。"""
    if not ENGINE_READY:
        st.markdown(
            notice(
                "engine offline",
                "<b>分析引擎尚未就绪。</b> src/analyzer.py 还没有出现在工作区里 —— "
                "界面可以正常浏览，但暂时无法真的分析仓库。等引擎落地后刷新即可。",
                "ghost",
            ),
            unsafe_allow_html=True,
        )

    if not result:
        return

    metrics = result.get("metrics") or {}
    narrative = result.get("narrative") or {}

    if metrics.get("sampled"):
        note = metrics.get("sample_note") or "体量超过阈值，只分析了最近 2000 个提交。"
        st.markdown(
            notice("sampled", f"<b>已采样</b> · {esc(note)}"),
            unsafe_allow_html=True,
        )

    quality = metrics.get("data_quality") or {}
    if quality and not quality.get("complete", True):
        dims = quality.get("unavailable_dimensions") or []
        if dims:
            st.markdown(
                notice(
                    "incomplete",
                    "<b>部分维度数据未获取</b> · "
                    f"{esc('、'.join(_DIM_LABELS.get(d, d) for d in dims))} 本次拿不到外部数据，"
                    "按 SPEC 取了保守分，因此总分可能比数据完整时偏低。"
                    "配置 <code>GITHUB_TOKEN</code> 可消除大部分限制。",
                    "warn",
                ),
                unsafe_allow_html=True,
            )

    # 规则性修正：停更项目被总分封顶时必须明说，不能让读者以为这是自然得出的分数
    for reason in metrics.get("overrides") or []:
        st.markdown(
            notice("score capped", f"<b>总分已封顶</b> · {esc(reason)}", "warn"),
            unsafe_allow_html=True,
        )

    if narrative.get("degraded"):
        reason = narrative.get("degraded_reason") or "LLM 不可达"
        st.markdown(
            notice(
                "degraded",
                f"<b>AI 叙事已降级</b> · {esc(reason)}。下方文档由结构化指标直接渲染，数字与评分不受影响。",
                "warn",
            ),
            unsafe_allow_html=True,
        )


def render_input() -> tuple[str, bool]:
    with st.form("requiem_form", clear_on_submit=False, border=False, enter_to_submit=True):
        col_input, col_button = st.columns([0.79, 0.21], vertical_alignment="bottom")
        with col_input:
            url = st.text_input(
                "仓库地址",
                value=st.session_state.get("current_url", ""),
                placeholder="https://github.com/owner/repo   ·   git@gitlab.com:group/repo.git",
                label_visibility="collapsed",
            )
        with col_button:
            submitted = st.form_submit_button("为其判读", type="primary", width="stretch")
    return (url or "").strip(), bool(submitted)


def _fallback_doc(metrics: dict) -> str:
    """叙事层缺失时的兜底文档：只用确定性指标排版，保证页面永远有输出。"""
    repo = fmt.repo_label(metrics)
    total = fmt.score_int(metrics)
    mode = str(fmt.safe_get(metrics, "score.mode") or fmt.mode_from_total(total))
    meta = fmt.mode_meta(mode)
    lines = [
        f"# {meta['doc']} · {repo}",
        "",
        f"> {fmt.verdict(metrics)}",
        "",
    ]
    for row in fmt.dim_rows(metrics):
        lines.append(f"## {row['name']} · {row['score']}/{row['max']}")
        lines.append(row["detail"])
        lines.append("")
    lines.append("> ⚠️ AI 叙事生成不可用，以上为结构化指标。")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 渲染：结果
# ─────────────────────────────────────────────────────────────────────────────


def render_score_hero(metrics: dict, narrative: dict, elapsed_total: float, *, cached: bool = False) -> None:
    """分数区，模仿化验单抬头：左侧巨大等宽数字，右侧仓库名 + 状态 + 结论 + 事实表。

    之前的做法是一个 conic-gradient 圆环 + 淡色药丸徽章，那是 AI 落地页的默认形状。
    化验单不会把指标画成圆环 —— 它直接把数字排得很大，让你一眼看到异常值。
    """
    total = fmt.score_int(metrics)
    mode = str(fmt.safe_get(metrics, "score.mode") or fmt.mode_from_total(total))
    meta = fmt.mode_meta(mode)
    shown = "—" if total is None else str(total)

    verdict = fmt.verdict(metrics)
    repo = fmt.repo_label(metrics)
    url = fmt.repo_url(metrics)
    generated = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M")

    model = ""
    if isinstance(narrative, dict):
        model = str(narrative.get("model") or "").strip()
    if not model:
        model = "确定性指标 · 无 LLM"

    # 分数颜色只表达状态，不做装饰
    tone_cls = ""
    if mode == "dead":
        tone_cls = " is-bad"
    elif mode == "critical":
        tone_cls = " is-warn"

    life = metrics.get("life") or {}
    authors = metrics.get("authors") or {}
    risks = metrics.get("risks") or {}
    days = life.get("days_since_last_commit")
    bf = authors.get("bus_factor")
    n_authors = authors.get("total")

    repo_html = f'<a href="{esc(url)}" target="_blank" rel="noreferrer">{esc(repo)}</a>' if url else esc(repo)

    facts = [
        ("最后提交", fmt.human_days(days)),
        ("bus factor", str(bf) if bf is not None else "—"),
        ("贡献者", str(n_authors) if n_authors is not None else "—"),
        ("提交总数", fmt.fmt_count(life.get("total_commits"))),
        ("享年", str(life.get("age_text") or "—")),
        ("风险点", str(risks.get("todo_count", 0) + risks.get("fixme_count", 0))),
    ]
    facts_html = "".join(
        f'<div class="req-fact"><div class="req-fact__k">{esc(k)}</div>'
        f'<div class="req-fact__v">{esc(v)}</div></div>'
        for k, v in facts
    )

    elapsed = fmt.elapsed_text(elapsed_total) + ("（会话缓存）" if cached else "")

    st.markdown(
        '<div class="req-hero">'
        f'<div class="req-hero__scorewrap">'
        f'<div class="req-hero__score{tone_cls}">{shown}</div>'
        '<span class="req-hero__denom">/ 100</span>'
        "</div>"
        '<div class="req-hero__body">'
        f'<div class="req-hero__repo">{repo_html}</div>'
        f'<div class="req-hero__state" style="color:{meta["tone"]}">'
        f"{esc(meta['label'])} · {esc(meta['band'])} 档 · {esc(meta['doc'])}模式</div>"
        f'<p class="req-hero__verdict">{esc(verdict)}</p>'
        f'<div class="req-facts">{facts_html}</div>'
        '<div class="req-hero__foot">'
        f"{esc(generated)}　·　耗时 {esc(elapsed)}　·　{esc(str(model))}"
        "</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_dims(metrics: dict) -> None:
    """六维明细：默认一行一维度，只给「问题 / 分数 / 一句话」。

    之前每项下面固定铺「事实 + 为什么 + 对你」三行，六项就是 18 行文字，
    读者扫不动。改成一行一维度，展开才看理由 —— 结论先给，细节按需。
    """
    rows = fmt.dim_rows(metrics)
    if not rows:
        st.markdown(
            notice("dimensions", "六维明细缺失：分析层没有返回 score.parts。", "ghost"),
            unsafe_allow_html=True,
        )
        return

    terms: dict = {}
    try:
        from src.explain import DIM_TERMS as _T

        terms = _T
    except Exception:
        pass

    chunks = ['<div class="req-dims">']
    for row in rows:
        width = max(0, min(100, row["pct"]))
        color = "#346538"
        if row["ratio"] < 0.34:
            color = "#9F2F2D"
        elif row["ratio"] < 0.67:
            color = "#8A6209"

        # 一行摘要：不用 explain 的三段，用最紧的那句事实
        one_line = row.get("fact") or row.get("detail") or ""

        chunks.append(
            '<div class="req-dim">'
            '<div class="req-dim__top">'
            f'<span class="req-dim__name">{esc(row["name"])}'
            f'<em class="req-dim__term">{esc(terms.get(row["key"], ""))}</em></span>'
            f'<span class="req-dim__score" style="color:{color}">'
            f"{row['score']}<i>/{row['max']}</i></span>"
            "</div>"
            '<div class="req-dim__track">'
            f'<div class="req-dim__fill" style="width:{width}%;background:{color}"></div>'
            "</div>"
            f'<div class="req-dim__detail">{esc(one_line)}</div>'
            "</div>"
        )
    chunks.append("</div>")
    st.markdown("".join(chunks), unsafe_allow_html=True)

    # 理由放进折叠：想看的人点开，不想看的人不被挡住
    with st.expander("每一维为什么是这个分（规则 + 对你的影响）", expanded=False):
        for row in rows:
            if not (row.get("mechanism") or row.get("impact")):
                continue
            st.markdown(f"**{row['name']} · {row['score']}/{row['max']}**")
            if row.get("mechanism"):
                st.markdown(f"- 规则：{row['mechanism']}")
            if row.get("impact"):
                st.markdown(f"- 对你：{row['impact']}")
            st.markdown("")


def render_metric_cards(metrics: dict) -> None:
    cards = fmt.key_metrics(metrics)
    for start in (0, 4):
        columns = st.columns(4, gap="small")
        for column, card in zip(columns, cards[start : start + 4], strict=False):
            with column:
                st.metric(
                    label=card["label"],
                    value=card["value"],
                    delta=card["sub"],
                    delta_color="off",
                    help=card["help"],
                    border=True,
                )


def render_answers(metrics: dict) -> None:
    """三个实际问题 —— 结果页最重要的区块。

    读者不关心我们的六维模型，他关心：这东西还能用吗、出事了找谁、我先做什么。
    这三条答案直接摆在分数下面，六维指标退到它后面当证据。

    排版：编号列表（01 / 02 / 03），整宽单列。
    之前是三等分的淡色卡片，那是 AI 落地页最典型的「三张特征卡」形状，
    而且把三条有先后关系的问题排成并列，读者不知道该先看哪条。
    """
    answers = metrics.get("answers") or []
    if not answers:
        return

    tone_cls = {"ok": " is-ok", "warn": " is-warn", "bad": " is-bad", "neutral": ""}

    chunks = ['<div class="req-answers">']
    for index, item in enumerate(answers, 1):
        cls = tone_cls.get(str(item.get("tone")), "")
        chunks.append(
            '<div class="req-answer">'
            f'<div class="req-answer__n">{index:02d}</div>'
            "<div>"
            f'<div class="req-answer__q">{esc(item.get("question", ""))}</div>'
            f'<div class="req-answer__a{cls}">{esc(item.get("answer", ""))}</div>'
            f'<div class="req-answer__d">{esc(item.get("detail", ""))}</div>'
            "</div></div>"
        )
    chunks.append("</div>")
    st.markdown("".join(chunks), unsafe_allow_html=True)


def render_document(metrics: dict, narrative: dict) -> None:
    markdown = ""
    if isinstance(narrative, dict):
        markdown = str(narrative.get("markdown") or "")
    if not markdown.strip():
        markdown = _fallback_doc(metrics)

    mode = str(fmt.safe_get(metrics, "score.mode") or fmt.mode_from_total(fmt.score_int(metrics)))
    meta = fmt.mode_meta(mode)

    degraded = bool((narrative or {}).get("degraded"))
    if degraded:
        model = "模板渲染 · 未经 LLM"
    else:
        model = str((narrative or {}).get("model") or "结构化指标（无 LLM）")
    elapsed = fmt.elapsed_text((narrative or {}).get("elapsed_sec"))
    sections = (narrative or {}).get("sections") or {}
    # 章数按正文里的「## 」二级标题数算，而不是按 sections 字典的键数 ——
    # 后者只是 will/eulogy 两段，用来显示「几章」会误导读者。
    markdown_text = str((narrative or {}).get("markdown") or "")
    section_count = len([ln for ln in markdown_text.splitlines() if ln.strip().startswith("## ")])
    if not section_count and isinstance(sections, dict):
        # 极端降级：没有 markdown 时退回按段落算，至少不为 0
        section_count = len([k for k, v in sections.items() if str(v or "").strip()])

    with st.container(border=True):
        st.markdown(
            '<div class="req-doc-head">'
            '<span class="req-doc-head__kicker">document</span>'
            f'<span class="req-doc-head__title">{esc(meta["doc"])}</span>'
            f'<span class="req-doc-head__meta">{esc(model)} · {esc(elapsed)}'
            + (f" · {section_count} 章" if section_count else "")
            + "</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(markdown)

    repo = fmt.repo_label(metrics).replace("/", "-")
    col_a, col_b, col_c = st.columns([0.22, 0.22, 0.56], vertical_alignment="center")
    with col_a:
        st.download_button(
            "下载 Markdown",
            data=markdown,
            file_name=f"{repo}-requiem.md",
            mime="text/markdown",
            width="stretch",
            key="dl_doc",
        )
    with col_b:
        if st.button("重新分析", width="stretch", key="rerun_analysis"):
            st.session_state["force_refresh"] = True
            st.session_state["autorun"] = True
            st.rerun()
    with col_c:
        st.markdown(
            '<div class="req-hint" style="text-align:right;margin:0">'
            f"字符数 {len(markdown):,} · {esc(meta['doc'])}</div>",
            unsafe_allow_html=True,
        )


def render_forensics(metrics: dict, result: dict) -> None:
    rows = fmt.dim_rows(metrics)
    with st.expander("解剖 · 原始指标与打分依据", expanded=False):
        tab_score, tab_json, tab_run = st.tabs(["打分依据", "完整 JSON", "运行信息"])

        with tab_score:
            body = [
                '<table class="req-table"><thead><tr>'
                "<th>维度</th><th>得分</th><th>依据</th></tr></thead><tbody>"
            ]
            for row in rows:
                body.append(
                    "<tr>"
                    f'<td class="dim">{esc(row["name"])}</td>'
                    f'<td class="num">{row["score"]} / {row["max"]}</td>'
                    f"<td>{esc(row['detail'])}</td>"
                    "</tr>"
                )
            body.append("</tbody></table>")
            st.markdown("".join(body), unsafe_allow_html=True)
            st.caption(
                "打分规则见 docs/SPEC.md 第 3 章：最后提交 30 / bus factor 25 / "
                "趋势 15 / 依赖 15 / issue 10 / 文档 5。全程无 LLM 参与。"
            )

        with tab_json:
            st.json(metrics, expanded=False)

        with tab_run:
            info = result.get("timings") or {}
            st.markdown(
                '<table class="req-table"><tbody>'
                f'<tr><td class="dim">分析耗时</td><td class="num">{esc(fmt.elapsed_text(info.get("analyze")))}</td></tr>'
                f'<tr><td class="dim">叙事耗时</td><td class="num">{esc(fmt.elapsed_text(info.get("narrative")))}</td></tr>'
                f'<tr><td class="dim">确定性指纹</td><td>{esc(fmt.safe_get(metrics, "deterministic_key", "—"))}</td></tr>'
                f'<tr><td class="dim">分析层耗时</td><td class="num">{esc(fmt.elapsed_text(fmt.safe_get(metrics, "elapsed_sec")))}</td></tr>'
                "</tbody></table>",
                unsafe_allow_html=True,
            )


def render_error(error: dict) -> None:
    panel = fmt.error_panel(error)
    if panel["kind"] == "empty":
        st.markdown(
            '<div class="req-empty">'
            f'<div class="req-empty__glyph">{esc(panel["ghost"])}</div>'
            f'<div class="req-empty__title">{esc(panel["title"])}</div>'
            f'<div class="req-empty__detail">{esc(panel["detail"])}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        notice(
            panel["kind"],
            f"<b>{esc(panel['title'])}</b><br>{esc(panel['detail'])}",
            "error",
        ),
        unsafe_allow_html=True,
    )
    if panel.get("raw"):
        st.caption(f"原始信息：{panel['raw']}")


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def analyze_phase(url: str) -> tuple[dict, dict, dict | None]:
    """阶段一：接入仓库 + 采集指标。返回 (metrics, timings, error)。

    status / 进度条在自己的 with 块里开、自己收 —— 不把后面的排版套进容器。
    """
    timings: dict[str, float] = {}
    forced = bool(st.session_state.pop("force_refresh", False))

    status = st.status("正在接入仓库…", expanded=True)
    with status:
        step = st.empty()
        bar = st.progress(0.02, text="准备中…")
        step.markdown("`①` 接入仓库 · `②` 采集指标")
        bar.progress(0.05, text="① 接入仓库…")

        analysis_cache = st.session_state.setdefault("analysis_cache", {})
        metrics = None if forced else analysis_cache.get(url)

        if metrics is None:
            stages: list[str] = []

            def _stage(message: str) -> None:
                # 运行在 analyse 线程里：只写列表，界面更新交给主线程轮询。
                stages.append(str(message))

            if _ANALYZE_ACCEPTS_PROGRESS:

                def _analyze() -> dict:
                    return analyze_repo(url, progress=_stage)  # type: ignore[misc]

            else:

                def _analyze() -> dict:
                    return analyze_repo(url)  # type: ignore[misc]

            metrics, elapsed = _call_with_ticker(
                _analyze,
                label="① 接入 + 采集指标",
                bar=bar,
                base=0.05,
                span=0.6,
                est=28.0,
                max_wait=MAX_WAIT_ANALYZE,
                step=step,
                stages=stages,
                prefix="②",
            )
            timings["analyze"] = elapsed
            if isinstance(metrics, dict) and metrics.get("ok"):
                analysis_cache[url] = metrics
        else:
            timings["analyze"] = safe_float((metrics or {}).get("elapsed_sec"))
            bar.progress(0.65, text="① 命中本次会话缓存")

        if not isinstance(metrics, dict):
            raise TypeError(f"analyze_repo 返回了 {type(metrics).__name__}，契约要求 dict")

        if not metrics.get("ok"):
            step.markdown("`×` 分析未完成")
            bar.progress(1.0, text="未完成")
            status.update(label="分析未完成", state="error", expanded=True)
            return metrics, timings, metrics.get("error")

        bar.progress(1.0, text="② 指标采集完成")
        step.markdown("`②` 采集指标 · 完成")
        status.update(
            label=f"指标就绪 · {fmt.elapsed_text(timings.get('analyze'))}",
            state="complete",
            expanded=False,
        )

    return metrics, timings, None


def _degraded_narrative(reason: str, metrics: dict) -> dict:
    return {
        "ok": False,
        "degraded": True,
        "degraded_reason": reason,
        "mode": fmt.safe_get(metrics, "score.mode"),
        "model": None,
        "markdown": "",
        "sections": {},
        "elapsed_sec": 0.0,
    }


def narrative_phase(metrics: dict, *, forced: bool = False) -> tuple[dict, float, bool]:
    """阶段二：调用生成层。返回 (narrative, 耗时, 是否命中缓存)。"""
    key = str(metrics.get("deterministic_key") or fmt.repo_label(metrics))
    cache = st.session_state.setdefault("narrative_cache", {})

    if generate_narrative is None:
        return _degraded_narrative("叙事层模块尚未就绪（src/narrative.py）", metrics), 0.0, False

    if not forced and key in cache:
        cached = cache[key]
        return cached, safe_float((cached or {}).get("elapsed_sec")), True

    status = st.status("正在生成叙事文档 —— 分数已经算完了，这里只等措辞", expanded=False)
    with status:
        bar = st.progress(0.05, text="③ 生成叙事…")
        try:
            narrative, elapsed = _call_with_ticker(
                lambda: generate_narrative(metrics),  # type: ignore[misc]
                label="③ 生成叙事",
                bar=bar,
                base=0.05,
                span=0.9,
                est=40.0,
                max_wait=MAX_WAIT_NARRATIVE,
            )
            bar.progress(1.0, text="完成")
            if isinstance(narrative, dict):
                cache[key] = narrative
            status.update(
                label=f"叙事完成 · {fmt.elapsed_text(elapsed)}",
                state="complete",
                expanded=False,
            )
            return (narrative if isinstance(narrative, dict) else {}), elapsed, False
        except Exception as exc:  # noqa: BLE001 —— 叙事失败不影响指标展示
            status.update(label="叙事生成失败，已降级为结构化指标", state="error", expanded=False)
            return _degraded_narrative(f"{type(exc).__name__}: {exc}", metrics), 0.0, False


def render_result_head(metrics: dict, narrative: dict, timings: dict, *, cached: bool = False) -> None:
    """结果页上半部分，按「先结论、后证据」排序。

    顺序就是读者的问题顺序：
      1. 多少分、什么状态（总分卡）
      2. 所以我该关心什么（三个实际问题的答案）
      3. 凭什么这么判（六维，每维一行）
      4. 细节数字（指标卡，折叠）
    """
    render_score_hero(metrics, narrative, sum(timings.values()), cached=cached)

    answers = metrics.get("answers") or []
    if answers:
        section("所以，我该关心什么", "三句话回答接手前真正会问的事")
        render_answers(metrics)

    section("凭什么这么判", fmt.band_note(metrics))
    render_dims(metrics)

    with st.expander("原始数字（8 项关键指标）", expanded=False):
        render_metric_cards(metrics)

    section("交接清单", fmt.doc_kicker(metrics))


def render_result(result: dict) -> None:
    metrics = result.get("metrics") or {}
    narrative = result.get("narrative") or {}
    timings = result.get("timings") or {}

    if result.get("error") or not metrics.get("ok"):
        render_error(result.get("error") or metrics.get("error") or {})
        return

    render_result_head(metrics, narrative, timings, cached=bool(result.get("cached")))
    render_document(metrics, narrative)
    render_forensics(metrics, result)


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="⚰",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css()

    st.session_state.setdefault("current_url", "")
    if "queued_url" in st.session_state:
        st.session_state["current_url"] = st.session_state.pop("queued_url")

    render_sidebar()
    render_masthead()

    result = st.session_state.get("result")
    render_top_alerts(result)

    url, submitted = render_input()

    st.markdown(
        '<div class="req-hint">GitHub / GitLab · 公开仓库 · 回车或点按钮均可触发'
        "　|　分析层不含 AI，六维打分完全确定性</div>",
        unsafe_allow_html=True,
    )

    autorun = bool(st.session_state.pop("autorun", False))
    should_run = (submitted or autorun) and bool(url)

    if should_run:
        st.session_state["current_url"] = url
        forced = bool(st.session_state.get("force_refresh"))

        # ── 阶段一：指标。50 秒左右，进度条按 analyzer 的真实阶段推进。
        try:
            metrics, timings, error = analyze_phase(url)
        except Exception as exc:  # noqa: BLE001 —— 顶层兜底，页面绝不白屏
            metrics, timings, error = (
                {"ok": False},
                {},
                {"kind": "unknown", "message": f"{type(exc).__name__}: {exc}"},
            )

        if error or not (metrics or {}).get("ok"):
            st.session_state["result"] = {
                "metrics": metrics or {"ok": False},
                "narrative": {},
                "timings": timings,
                "error": error or (metrics or {}).get("error") or {"kind": "unknown"},
            }
            st.rerun()

        # ── 先把分数画出来：这一刻用户已经看到 80 分了，不必等 LLM。
        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
        render_result_head(metrics, {}, timings)
        st.markdown(
            '<div class="req-hint" style="margin:-2px 0 0 2px">数字是最终结果。下面是叙事文档，正在写…</div>',
            unsafe_allow_html=True,
        )

        # ── 阶段二：叙事。失败也只是降级，不影响上面的数字。
        try:
            narrative, narr_elapsed, narr_cached = narrative_phase(metrics, forced=forced)
        except Exception as exc:  # noqa: BLE001
            narrative, narr_elapsed, narr_cached = (
                _degraded_narrative(f"{type(exc).__name__}: {exc}", metrics),
                0.0,
                False,
            )

        timings["narrative"] = narr_elapsed
        st.session_state["result"] = {
            "metrics": metrics,
            "narrative": narrative,
            "timings": timings,
            "cached": narr_cached,
            "error": None,
        }
        st.session_state["force_refresh"] = False
        # 重跑一次：让「已采样 / AI 降级」这类横幅出现在页面真正顶部，
        # 而不是等下一次交互才补上。结果已在 session_state 里，重跑是瞬时的。
        st.rerun()

    if st.session_state.get("demo"):
        st.markdown(
            notice(
                "preview",
                "<b>界面预览</b> · 下面是演示数据，只用来检查排版；真实分析需要 src/analyzer.py 就绪。",
                "ghost",
            ),
            unsafe_allow_html=True,
        )
        if st.button("退出预览", key="demo_exit"):
            st.session_state["demo"] = False
            st.rerun()

        render_score_hero(DEMO_METRICS, DEMO_NARRATIVE, 41.2)
        section("六维拆解", fmt.band_note(DEMO_METRICS))
        render_dims(DEMO_METRICS)
        section("关键指标", "点击标签可看口径")
        render_metric_cards(DEMO_METRICS)
        section("生成文档", fmt.doc_kicker(DEMO_METRICS))
        render_document(DEMO_METRICS, DEMO_NARRATIVE)
        render_forensics(
            DEMO_METRICS,
            {"metrics": DEMO_METRICS, "timings": {"analyze": 35.4, "narrative": 5.8}},
        )
        return

    if result:
        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
        render_result(result)
    else:
        st.markdown(
            '<div class="req-empty">'
            '<div class="req-empty__glyph">awaiting first reading</div>'
            '<div class="req-empty__title">还没有待检的仓库</div>'
            '<div class="req-empty__detail">'
            "在上面贴一个公开仓库地址，或从左侧挑一个样本。<br>"
            "一次检查大约 10–60 秒：接入 → 采集指标 → 生成叙事。"
            "</div>" + _legend_html() + "</div>",
            unsafe_allow_html=True,
        )

    if result and (result.get("metrics") or {}).get("ok"):
        metrics = result["metrics"]
        st.markdown(
            '<div class="req-hero__foot" style="margin-top:26px;justify-content:center">'
            f"<span>{esc(fmt.repo_url(metrics))}</span><i>·</i>"
            "<span>分析层确定性 · 无 LLM 参与打分</span>"
            "</div>",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
