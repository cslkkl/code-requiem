"""代码安魂曲 / Code Requiem —— Streamlit 界面层（app.py）。

只依赖 docs/CONTRACT.md 里约定的三个入口：

    src.analyzer.analyze_repo(url) -> dict
    src.narrative.generate_narrative(metrics) -> dict
    src.llm.provider_info() -> dict

设计原则：
  * 界面层只做「排版 + 容错」，不做任何判断逻辑（判断在 src/format.py 里，
    在那里可以脱离 streamlit 单测）。
  * 任何一层缺失 / 抛错都必须渲染成可读提示，页面绝不允许白屏。
  * 分数、六维条、指标全部用原生 HTML/CSS 或 st.metric 实现。
  * Geist 与 Geist Mono 以 WOFF2 自托管，运行时不请求字体 CDN。

运行：
    .venv\\Scripts\\python.exe -u -m streamlit run app.py --server.headless true
"""

from __future__ import annotations

import base64
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

try:
    # 只为侧栏显示配额状态，不参与打分，所以单独 try 且允许缺失
    from src import github_api  # type: ignore[attr-defined]
except Exception as exc:  # noqa: BLE001
    github_api = None  # type: ignore[assignment]
    _IMPORT_ERRORS["github_api"] = f"{type(exc).__name__}: {exc}"


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
/* 代码安魂曲：一份放在桌面上的检验报告。
   桌面是灰的，纸是白的，内容全部装在纸里；
   章节有编号，规格表用竖线分格 —— 它看起来应该像一份打印出来的文档，
   而不是一个网页仪表盘。 */
:root{
  --req-desk:#E9E9E6;        /* 桌面 */
  --req-paper:#FDFDFC;       /* 纸张 */
  --req-ink:#1B1A17;
  --req-ink-2:#42413D;
  --req-ink-3:#66655F;
  --req-ink-4:#77756E;
  --req-rule:#DCDCD6;
  --req-rule-2:#E9E9E4;
  --req-accent:#8A3324;
  --req-ok:#2F5D3A;
  --req-warn:#78520D;
  --req-bad:#8A3324;
  --req-sans:'Geist',system-ui,-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei','Noto Sans CJK SC','Source Han Sans SC','WenQuanYi Micro Hei',sans-serif;
  --req-mono:'Geist Mono',ui-monospace,'Cascadia Mono','JetBrains Mono','SF Mono',Consolas,'Liberation Mono','DejaVu Sans Mono',monospace;
}
.stApp{background:var(--req-desk);font-synthesis-weight:none;}
html,body,[class*="css"]{font-family:var(--req-sans);color:var(--req-ink);-webkit-font-smoothing:antialiased;}
[data-testid="stHeader"]{height:0;background:transparent;}
[data-testid="stToolbar"],footer,#MainMenu{display:none;}
.block-container{max-width:1240px;padding:2.6rem 2.8rem 6rem;}
[data-testid="stMain"] .stVerticalBlock{gap:0;}
::selection{background:var(--req-ink);color:var(--req-paper);}
a{color:var(--req-accent);text-decoration:none;}
a:hover{text-decoration:underline;}
[data-testid="stMarkdownContainer"] p{font-size:.95rem;line-height:1.78;color:var(--req-ink-2);}
[data-testid="stMarkdownContainer"] code{font-family:var(--req-mono);font-size:.86em;background:var(--req-rule-2);padding:1px 4px;border-radius:0;}
[data-testid="stMarkdownContainer"] ul,[data-testid="stMarkdownContainer"] ol{padding-left:1.25rem;}
[data-testid="stMarkdownContainer"] li{font-size:.92rem;line-height:1.76;color:var(--req-ink-2);margin-bottom:4px;}
[data-testid="stMarkdownContainer"] hr{border:0;border-top:1px solid var(--req-rule);}
[data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] p{font-size:.74rem;color:var(--req-ink-3);}

/* ── 纸张：所有内容装进来 ─────────────────────────────────────────────── */
.st-key-sheet{
  max-width:940px;margin:0 auto 3rem;
  background:var(--req-paper);
  border:1px solid var(--req-rule);
  padding:0 0 34px;
}
.st-key-sheet{padding-left:58px;padding-right:58px;}

/* ── 报告抬头：印在纸上沿 ─────────────────────────────────────────────── */
.req-mast{
  display:flex;align-items:baseline;gap:16px;
  padding:46px 0 14px;margin-bottom:0;
  border-bottom:1.5px solid var(--req-ink);
}
.req-mast__title{font-size:1.62rem;font-weight:700;letter-spacing:-.045em;line-height:1;margin:0;color:var(--req-ink);}
.req-mast__latin{font-family:var(--req-mono);font-size:.66rem;letter-spacing:.16em;text-transform:uppercase;color:var(--req-ink-4);}
.req-mast__note{margin-left:auto;font-family:var(--req-mono);font-size:.7rem;color:var(--req-ink-3);}

/* ── 输入区：纸内第一格 ───────────────────────────────────────────────── */
.req-inputzone{padding:20px 0 6px;}
[data-testid="stForm"]{border:0 !important;padding:0 !important;}
[data-testid="stTextInput"] > div{border:0 !important;background:transparent !important;}
[data-testid="stTextInput"] label{display:none;}
[data-testid="stTextInput"] input{font-family:var(--req-mono) !important;font-size:.88rem !important;background:transparent !important;border:0 !important;border-bottom:1.5px solid var(--req-ink) !important;border-radius:0 !important;padding:11px 2px !important;color:var(--req-ink) !important;box-shadow:none !important;}
[data-testid="stTextInput"] input:focus{border-bottom-color:var(--req-accent) !important;box-shadow:none !important;}
[data-testid="stTextInput"] input::placeholder{color:var(--req-ink-4) !important;}
button[data-testid^="stBaseButton"]{font-family:var(--req-mono) !important;font-size:.78rem !important;letter-spacing:.035em !important;border-radius:0 !important;box-shadow:none !important;transition:background .12s,color .12s,border-color .12s;}
button[data-testid^="stBaseButton"] p,button[data-testid^="stBaseButton"] span,button[data-testid^="stBaseButton"] div{color:inherit !important;font-size:inherit !important;line-height:1.4 !important;margin:0 !important;}
button[data-testid="stBaseButton-primaryFormSubmit"],button[data-testid="stBaseButton-primary"]{background:var(--req-ink) !important;color:var(--req-paper) !important;border:1.5px solid var(--req-ink) !important;padding:11px 24px !important;}
button[data-testid="stBaseButton-primaryFormSubmit"]:hover,button[data-testid="stBaseButton-primary"]:hover{background:var(--req-accent) !important;border-color:var(--req-accent) !important;color:var(--req-paper) !important;}
button[data-testid^="stBaseButton"]:active{transform:translateY(1px);}
button[data-testid^="stBaseButton"]:focus-visible{outline:2px solid var(--req-accent) !important;outline-offset:2px;}
[data-testid="stDownloadButton"] button,[data-testid="stBaseButton-secondary"]{background:transparent !important;color:var(--req-ink) !important;border:1px solid var(--req-rule) !important;padding:9px 16px !important;}
[data-testid="stDownloadButton"] button:hover,[data-testid="stBaseButton-secondary"]:hover{border-color:var(--req-ink) !important;color:var(--req-accent) !important;background:transparent !important;}
.req-hint{font-family:var(--req-sans);font-size:.74rem;line-height:1.65;color:var(--req-ink-3);}

/* ── 侧栏：桌面上的一列批注，无框 ─────────────────────────────────────── */
[data-testid="stSidebar"]{background:var(--req-desk);border-right:0;}
[data-testid="stSidebar"] > div:first-child{padding-top:2.2rem;}
[data-testid="stSidebar"] .block-container{padding-left:1.6rem;padding-right:1.6rem;}
.req-side__title{font-family:var(--req-mono);font-size:.68rem;letter-spacing:.17em;text-transform:uppercase;color:var(--req-ink);padding-bottom:10px;border-bottom:1.5px solid var(--req-ink);margin-bottom:16px;}
.req-side__desc{font-size:.85rem;line-height:1.76;color:var(--req-ink-2);margin-bottom:26px;}
.req-side__desc b{font-weight:650;color:var(--req-ink);}
.req-side__tag{font-family:var(--req-sans);font-size:.7rem;font-weight:600;color:var(--req-ink);padding-bottom:8px;border-bottom:1px solid var(--req-rule);margin:0 0 10px;}
[data-testid="stSidebar"] button[data-testid^="stBaseButton"]{width:100% !important;text-align:left !important;justify-content:flex-start !important;font-family:var(--req-mono) !important;font-size:.76rem !important;background:transparent !important;color:var(--req-ink-2) !important;border:0 !important;border-bottom:1px solid var(--req-rule-2) !important;padding:8px 0 !important;box-shadow:none !important;}
[data-testid="stSidebar"] button[data-testid^="stBaseButton"]:hover{background:transparent !important;color:var(--req-accent) !important;border-bottom-color:var(--req-accent) !important;}
.req-ex__note{font-size:.72rem;line-height:1.6;color:var(--req-ink-3);margin:2px 0 14px;}
.req-pill{display:inline-block !important;font-family:var(--req-mono);font-size:.68rem;line-height:1.55;padding:3px 0;margin-bottom:3px;border:0 !important;background:transparent !important;}
.req-pill__dot{display:inline-block;width:5px;height:5px;margin-right:7px;vertical-align:middle;background:currentColor;}
.req-side__meta{font-size:.74rem;line-height:1.65;color:var(--req-ink-3);word-break:break-word;margin-bottom:20px;}

/* ── 封面（空状态）：纸的第一页 ───────────────────────────────────────── */
.req-empty{padding:56px 0 40px;}
.req-empty__glyph{display:none;}
.req-empty__title{font-size:1.35rem;font-weight:700;letter-spacing:-.028em;color:var(--req-ink);margin-bottom:14px;}
.req-empty__detail{font-size:.95rem;line-height:1.85;color:var(--req-ink-2);max-width:52ch;}
.req-empty__detail b{font-weight:650;color:var(--req-ink);}
.req-legend{display:flex;flex-wrap:wrap;gap:10px 26px;margin-top:34px;padding-top:18px;border-top:1px solid var(--req-rule);}
.req-legend__item{font-size:.78rem;color:var(--req-ink-2);white-space:nowrap;}
.req-legend__bar{display:inline-block;width:16px;height:2px;margin-right:8px;vertical-align:middle;}
.req-legend__range{color:var(--req-ink-3);}

/* ── 检验结果抬头：分数格 | 竖线 | 内容格 ─────────────────────────────── */
.req-hero{display:grid;grid-template-columns:172px minmax(0,1fr);border-bottom:1px solid var(--req-rule);}
.req-hero__scorewrap{display:flex;flex-direction:column;justify-content:flex-end;border-right:1px solid var(--req-rule);padding:30px 30px 26px 0;}
.req-hero__score{font-family:var(--req-mono);font-size:5.4rem;font-weight:600;line-height:.84;letter-spacing:-.06em;color:var(--req-ink);font-variant-numeric:tabular-nums;}
.req-hero__score.is-bad{color:var(--req-bad);}
.req-hero__score.is-warn{color:var(--req-warn);}
.req-hero__denom{display:block;margin-top:12px;font-family:var(--req-mono);font-size:.72rem;letter-spacing:.06em;color:var(--req-ink-4);}
.req-hero__body{min-width:0;padding:30px 0 26px 40px;}
.req-hero__repo{font-size:1.3rem;font-weight:700;line-height:1.25;letter-spacing:-.025em;color:var(--req-ink);margin-bottom:8px;word-break:break-word;}
.req-hero__repo a{color:inherit;}
.req-hero__state{font-family:var(--req-sans);font-size:.76rem;font-weight:600;margin-bottom:13px;}
.req-hero__verdict{font-size:.97rem;line-height:1.78;color:var(--req-ink-2);max-width:60ch;margin:0 0 20px;}
.req-hero__foot{font-family:var(--req-mono);font-size:.66rem;line-height:1.65;color:var(--req-ink-3);margin-top:15px;}

/* 规格表：竖线分格，无横线堆叠 */
.req-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));}
.req-fact{padding:2px 18px 2px 0;border-left:1px solid var(--req-rule-2);}
.req-fact:nth-child(3n+1){border-left:0;padding-left:0;}
.req-fact:nth-child(n+4){padding-top:12px;}
.req-fact__k{font-size:.7rem;font-weight:500;color:var(--req-ink-3);margin-bottom:4px;}
.req-fact__v{font-family:var(--req-mono);font-size:.86rem;color:var(--req-ink);font-variant-numeric:tabular-nums;}

/* ── 章节：编号 + 标题，右侧批注 ──────────────────────────────────────── */
.req-sec{display:flex;align-items:baseline;gap:14px;margin:48px 0 0;padding-bottom:10px;border-bottom:1.5px solid var(--req-ink);}
.req-sec__num{font-family:var(--req-mono);font-size:.74rem;color:var(--req-accent);font-variant-numeric:tabular-nums;}
.req-sec__label{font-size:1.04rem;font-weight:700;letter-spacing:-.015em;color:var(--req-ink);}
.req-sec__hint{margin-left:auto;font-size:.74rem;line-height:1.5;color:var(--req-ink-3);text-align:right;}

/* ── 三个问题 ─────────────────────────────────────────────────────────── */
.req-answers{border-bottom:1px solid var(--req-rule);}
.req-answer{display:grid;grid-template-columns:36px minmax(0,1fr);gap:0 18px;padding:22px 0 21px;border-top:1px solid var(--req-rule-2);}
.req-answer:first-child{border-top:0;}
.req-answer__n{font-family:var(--req-mono);font-size:.72rem;color:var(--req-ink-4);padding-top:4px;font-variant-numeric:tabular-nums;}
.req-answer__q{font-size:.9rem;font-weight:650;color:var(--req-ink);margin-bottom:6px;}
.req-answer__a{font-size:1.14rem;font-weight:700;line-height:1.4;letter-spacing:-.018em;color:var(--req-ink);margin-bottom:8px;}
.req-answer__a.is-ok{color:var(--req-ok);}
.req-answer__a.is-warn{color:var(--req-warn);}
.req-answer__a.is-bad{color:var(--req-bad);}
.req-answer__d{font-size:.89rem;line-height:1.78;color:var(--req-ink-2);max-width:66ch;}

/* ── 六维 ─────────────────────────────────────────────────────────────── */
.req-dims{border-bottom:1px solid var(--req-rule);}
.req-dim{display:block;padding:18px 0 17px;border-top:1px solid var(--req-rule-2);}
.req-dim:first-child{border-top:0;}
.req-dim__top{display:flex;align-items:baseline;gap:9px;margin-bottom:9px;}
.req-dim__name{font-size:.95rem;font-weight:650;letter-spacing:-.008em;color:var(--req-ink);}
.req-dim__term{font-family:var(--req-mono);font-size:.66rem;font-style:normal;color:var(--req-ink-4);}
.req-dim__score{margin-left:auto;font-family:var(--req-mono);font-size:.9rem;font-weight:600;font-variant-numeric:tabular-nums;}
.req-dim__score i{font-style:normal;font-size:.68rem;color:var(--req-ink-4);font-weight:400;}
.req-dim__track{height:2px;background:var(--req-rule-2);margin-bottom:10px;overflow:hidden;}
.req-dim__fill{height:100%;}
.req-dim__detail{font-size:.87rem;line-height:1.74;color:var(--req-ink-2);max-width:76ch;}

/* ── 次要指标 ─────────────────────────────────────────────────────────── */
.st-key-metricgrid{padding:14px 0 4px;}
[data-testid="stMetric"]{padding:0;border:0 !important;background:transparent !important;}
[data-testid="stMetricLabel"] p{font-size:.7rem !important;color:var(--req-ink-3) !important;}
[data-testid="stMetricValue"]{font-family:var(--req-mono) !important;font-size:1.02rem !important;font-weight:600 !important;color:var(--req-ink) !important;font-variant-numeric:tabular-nums;}
[data-testid="stMetricDelta"]{font-size:.7rem;color:var(--req-ink-3) !important;}

/* ── 折叠附录 ─────────────────────────────────────────────────────────── */
[data-testid="stExpander"]{border:0 !important;border-top:1px solid var(--req-rule) !important;border-bottom:1px solid var(--req-rule) !important;border-radius:0 !important;background:transparent !important;margin:18px 0 0 !important;}
[data-testid="stExpander"] details{border:0 !important;}
[data-testid="stExpander"] summary{font-family:var(--req-mono) !important;font-size:.75rem !important;color:var(--req-ink-2) !important;padding:12px 2px !important;}
[data-testid="stExpander"] summary:hover{color:var(--req-accent) !important;}
[data-testid="stExpanderDetails"]{border-top:1px solid var(--req-rule-2) !important;padding:8px 2px 10px !important;}

/* ── 提示 ─────────────────────────────────────────────────────────────── */
.req-notice{font-size:.87rem;line-height:1.74;color:var(--req-ink-2);padding:12px 0 12px 15px;margin:14px 0;border-left:2px solid var(--req-ink-4);background:transparent;}
.req-notice__tag{display:inline-block;margin-right:9px;font-family:var(--req-mono);font-size:.65rem;letter-spacing:.08em;text-transform:uppercase;color:var(--req-ink);}
.req-notice code{font-family:var(--req-mono);font-size:.86em;background:var(--req-rule-2);padding:1px 4px;}
.req-notice--warn{border-left-color:var(--req-warn);}
.req-notice--bad,.req-notice--error{border-left-color:var(--req-bad);}
.req-notice--ok{border-left-color:var(--req-ok);}
.req-notice--ghost{border-left-color:var(--req-rule);}

/* ── 文档 ─────────────────────────────────────────────────────────────── */
.req-doc-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;padding:0 0 10px;border-bottom:1px solid var(--req-rule);margin-bottom:22px;}
.req-doc-head__kicker{font-size:.72rem;font-weight:500;color:var(--req-ink-3);}
.req-doc-head__title{font-size:.92rem;font-weight:700;letter-spacing:-.01em;color:var(--req-ink);}
.req-doc-head__meta{margin-left:auto;font-size:.72rem;color:var(--req-ink-3);}
.st-key-docbody{max-width:82ch;font-size:.93rem;line-height:1.84;color:var(--req-ink-2);}
.st-key-docbody h1{font-size:1.24rem;font-weight:700;letter-spacing:-.025em;line-height:1.35;color:var(--req-ink);margin:0 0 18px;}
.st-key-docbody h2{font-size:1rem;font-weight:700;letter-spacing:-.015em;color:var(--req-ink);margin:30px 0 11px;padding-bottom:7px;border-bottom:1px solid var(--req-rule-2);}
.st-key-docbody h3{font-size:.94rem;font-weight:650;color:var(--req-ink);margin:20px 0 7px;}
.st-key-docbody p{margin:0 0 12px;line-height:1.84;}
.st-key-docbody ul,.st-key-docbody ol{margin:0 0 14px;padding-left:1.3rem;}
.st-key-docbody li{margin-bottom:6px;line-height:1.8;}
.st-key-docbody strong{color:var(--req-ink);font-weight:650;}
.st-key-docbody blockquote{margin:14px 0;padding:4px 0 4px 15px;border-left:2px solid var(--req-rule);color:var(--req-ink-3);}
.st-key-docbody table{width:100%;border-collapse:collapse;margin:15px 0;font-size:.86rem;}
.st-key-docbody th{font-family:var(--req-mono);font-size:.66rem;letter-spacing:.07em;text-transform:uppercase;color:var(--req-ink-3);font-weight:500;text-align:left;padding:7px 10px 7px 0;border-bottom:1.5px solid var(--req-rule);}
.st-key-docbody td{padding:8px 10px 8px 0;border-bottom:1px solid var(--req-rule-2);color:var(--req-ink-2);vertical-align:top;}
.st-key-doc_actions{margin-top:22px;padding-top:13px;border-top:1px solid var(--req-rule-2);}

/* ── 解剖附录 ─────────────────────────────────────────────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"]{gap:0;border-bottom:1.5px solid var(--req-ink);background:transparent;}
[data-testid="stTabs"] [data-baseweb="tab"]{font-family:var(--req-mono);font-size:.74rem;color:var(--req-ink-3);background:transparent;border:0;border-radius:0;padding:9px 18px 9px 0;}
[data-testid="stTabs"] [aria-selected="true"]{color:var(--req-ink) !important;font-weight:600;border-bottom:2px solid var(--req-accent) !important;}
[data-testid="stTabs"] [data-baseweb="tab-highlight"],[data-testid="stTabs"] [data-baseweb="tab-border"]{display:none;}
[data-testid="stTabs"] [data-baseweb="tab-panel"]{background:transparent !important;border:0 !important;padding:16px 0 0 !important;}
.req-table{width:100%;border-collapse:collapse;font-size:.85rem;}
.req-table th{font-family:var(--req-mono);font-size:.65rem;letter-spacing:.08em;text-transform:uppercase;text-align:left;color:var(--req-ink-3);padding:9px 12px 9px 0;border-bottom:1.5px solid var(--req-ink);}
.req-table td{padding:10px 12px 10px 0;border-bottom:1px solid var(--req-rule-2);color:var(--req-ink-2);vertical-align:top;line-height:1.68;}
.req-table .dim{font-weight:650;color:var(--req-ink);white-space:nowrap;}
.req-table .num{font-family:var(--req-mono);font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right;}
.req-table td:last-child{overflow-wrap:anywhere;}
[data-testid="stDataFrame"],[data-testid="stJson"]{border:0 !important;border-radius:0 !important;background:transparent !important;}
[data-testid="stJson"] pre{white-space:pre-wrap !important;overflow-wrap:anywhere !important;}

/* ── 页脚：纸内最底一行 ───────────────────────────────────────────────── */
.req-foot{margin:44px 0 6px;padding-top:14px;border-top:1.5px solid var(--req-ink);font-size:.72rem;line-height:1.65;color:var(--req-ink-3);display:flex;gap:18px;flex-wrap:wrap;}

/* ── 加载 ─────────────────────────────────────────────────────────────── */
[data-testid="stStatusWidget"]{font-family:var(--req-mono) !important;font-size:.74rem !important;color:var(--req-ink-3) !important;}
[data-testid="stStatusWidget"] details{border:0 !important;border-top:1px solid var(--req-rule) !important;border-bottom:1px solid var(--req-rule) !important;border-radius:0 !important;background:transparent !important;}
[data-testid="stStatusWidget"] summary{border-radius:0 !important;}
.stSpinner>div{border-color:var(--req-ink-4) transparent transparent transparent !important;}
[data-testid="stProgress"] > div > div > div{background:var(--req-ink) !important;}

@media (max-width:820px){
  .block-container{padding:1.6rem 1.15rem 4rem;}
  .st-key-sheet .block-container{padding:0 24px 26px;}
  .req-mast{flex-wrap:wrap;gap:7px 12px;padding-top:34px;}
  .req-mast__note{margin-left:0;width:100%;}
  .req-hero{grid-template-columns:1fr;}
  .req-hero__scorewrap{border-right:0;border-bottom:1px solid var(--req-rule);padding:24px 0 20px;}
  .req-hero__body{padding:22px 0 24px;}
  .req-hero__score{font-size:4rem;}
  .req-facts{grid-template-columns:repeat(2,minmax(0,1fr));}
  .req-fact:nth-child(3n+1){border-left:1px solid var(--req-rule-2);padding-left:14px;}
  .req-fact:nth-child(2n+1){border-left:0;padding-left:0;}
  .req-sec__hint{display:none;}
  .req-doc-head__meta{margin-left:0;width:100%;}
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


def section(num: str, label: str, hint: str = "") -> None:
    """章节标题：编号（氧化红等宽）+ 标题 + 右侧批注。"""
    hint_html = f'<span class="req-sec__hint">{esc(hint)}</span>' if hint else ""
    st.markdown(
        f'<div class="req-sec"><span class="req-sec__num">{esc(num)}</span>'
        f'<span class="req-sec__label">{esc(label)}</span>{hint_html}</div>',
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


def _github_quota_line() -> tuple[str, str, str]:
    """侧栏用的 GitHub 配额一行状态。返回 (文案, 颜色, 说明)。

    为什么值得单独显示：令牌配没配，以前只能靠「多跑几个仓库看会不会限流」
    去猜。匿名 60 次/小时、带令牌 5000 次/小时，差 83 倍 ——
    配错的时候第一个访客还能用，第五个就废了，这种问题必须在界面上自证。
    """
    if github_api is None:
        return ("分析引擎未就绪", "#8A8884", "src/github_api.py 尚未加载")

    now = time.time()
    cached = st.session_state.get("_gh_quota")
    if isinstance(cached, dict) and now - float(cached.get("_ts", 0)) < 120:
        return cached["line"]

    try:
        has = bool(github_api.has_token())
        status = github_api.rate_limit_status() or {}
    except Exception:
        has, status = False, {}

    limit = status.get("limit")
    remaining = status.get("remaining")

    if has and limit:
        line = (
            f"{remaining}/{limit} 每小时 · 已配令牌",
            "#2F5D3A",
            "用个人令牌鉴权，够几百人正常使用。",
        )
    elif has:
        line = (
            "已配令牌 · 配额未取到",
            "#7A5310",
            "令牌已设置，但这次没读到配额，可能是网络抖动。",
        )
    elif limit:
        line = (
            f"{remaining}/{limit} 每小时 · 匿名",
            "#8E3223",
            "匿名配额，一次分析要 6-14 次请求，第 5 个访客就会撞限流。配 GITHUB_TOKEN 可提到 5000 次/小时。",
        )
    else:
        line = (
            "匿名 · 配额未取到",
            "#8E3223",
            "没有配置 GITHUB_TOKEN，配额只有 60 次/小时。",
        )

    st.session_state["_gh_quota"] = {"_ts": now, "line": line}
    return line


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


def _font_face_css() -> str:
    """把自托管 WOFF2 内嵌为 data URL。

    Streamlit 默认不会把项目 assets/ 当静态目录公开，直接写相对 URL 会 404。
    两个可变字体合计约 60 KB，内嵌一次即可让 Windows 与 Linux 渲染一致。
    """
    font_dir = Path(__file__).resolve().parent / "assets" / "fonts"
    faces = []
    for family, filename in (
        ("Geist", "geist-latin-wght-normal.woff2"),
        ("Geist Mono", "geist-mono-latin-wght-normal.woff2"),
    ):
        try:
            payload = base64.b64encode((font_dir / filename).read_bytes()).decode("ascii")
        except OSError:
            continue
        faces.append(
            "@font-face{"
            f"font-family:'{family}';font-style:normal;font-weight:100 900;"
            "font-display:swap;"
            f"src:url(data:font/woff2;base64,{payload}) format('woff2');"
            "}"
        )
    return "".join(faces)


def inject_css() -> None:
    st.markdown(f"<style>{_font_face_css()}</style>" + _CSS, unsafe_allow_html=True)


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
            f'<div class="req-pill" style="color:{esc(badge["tone"])}">'
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

        # ── GitHub 配额状态：让「令牌到底配没配」变成看得见的事实 ──────────
        # 不显示这一项的话，用户只能靠「跑几个仓库看会不会限流」来猜。
        # 匿名 60 次/小时 vs 带令牌 5000 次/小时，差 83 倍，配错代价很大。
        st.markdown('<div class="req-side__tag">GitHub 配额</div>', unsafe_allow_html=True)
        quota_text, quota_tone, quota_detail = _github_quota_line()
        st.markdown(
            f'<div class="req-pill" style="color:{quota_tone}">'
            f'<span class="req-pill__dot"></span>{esc(quota_text)}</div>'
            f'<div class="req-side__meta">{esc(quota_detail)}</div>',
            unsafe_allow_html=True,
        )

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
                    border=False,
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

    # 不要 st.container(border=True)：那会画出一张带边框的卡片，
    # 与「用分隔线组织信息、不套卡片」的整站规矩冲突。
    # 也不要把 markdown 塞进 <div> 里 —— Streamlit 不会在 HTML 块内部解析
    # markdown，那样整篇文档会退化成纯文本。分区交给 .req-doc-head 的下边线。
    st.markdown(
        '<div class="req-doc-head">'
        '<span class="req-doc-head__kicker">交接文档</span>'
        f'<span class="req-doc-head__title">{esc(meta["doc"])}</span>'
        f'<span class="req-doc-head__meta">{esc(model)} · {esc(elapsed)}'
        + (f" · {section_count} 章" if section_count else "")
        + "</span></div>",
        unsafe_allow_html=True,
    )
    # 给文档正文套一个带 key 的无边框容器。
    # Streamlit 1.39+ 会为 key 生成 `.st-key-<key>` 类名，这样
    # `.st-key-docbody h1` 这类后代选择器才作用得到 Streamlit 自己
    # 生成的 markdown 元素。直接塞 <div> 会破坏 markdown 解析（整篇退化成纯文本）。
    with st.container(key="docbody"):
        st.markdown(markdown)

    repo = fmt.repo_label(metrics).replace("/", "-")
    with st.container(key="doc_actions"):
        col_a, col_b, col_c = st.columns([0.22, 0.22, 0.56], vertical_alignment="center", gap="medium")
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
        section("01", "所以，我该关心什么", "三句话回答接手前真正会问的事")
        render_answers(metrics)

    section("02", "凭什么这么判", fmt.band_note(metrics))
    render_dims(metrics)

    with st.expander("原始数字（8 项关键指标）", expanded=False):
        render_metric_cards(metrics)

    section("03", "交接清单", fmt.doc_kicker(metrics))


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

    if (metrics or {}).get("ok"):
        st.markdown(
            '<div class="req-foot">'
            f"<span>{esc(fmt.repo_url(metrics))}</span>"
            "<span>分析层确定性 · 无 LLM 参与打分</span>"
            "</div>",
            unsafe_allow_html=True,
        )


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

    # ── 整个主区都在同一张纸上：抬头、输入、结果或封面 ─────────────────────
    with st.container(key="sheet"):
        render_masthead()

        result = st.session_state.get("result")
        render_top_alerts(result)

        url, submitted = render_input()
        st.markdown(
            '<div class="req-hint req-inputzone" style="padding-top:0">'
            "GitHub / GitLab · 公开仓库 · 回车或点按钮均可触发"
            "　|　分析层不含 AI，六维打分完全确定性</div>",
            unsafe_allow_html=True,
        )

        autorun = bool(st.session_state.pop("autorun", False))
        should_run = (submitted or autorun) and bool(url)

        if should_run:
            st.session_state["current_url"] = url
            forced = bool(st.session_state.get("force_refresh"))

            # 开始新分析前，先把上一次的结果清掉并重跑一次。
            # Streamlit 在脚本运行期间不会移除上一轮的元素：如果这里直接
            # 开始分析，屏幕上会残留上一个仓库的分数与结论（演示视频里
            # v2ex-gae 正在跑，下面却显示 kornia 的「451 位贡献者」），
            # 观众会以为数据错了。清空 + 重跑一次是瞬时的，之后才开始分析。
            if st.session_state.get("result") is not None:
                st.session_state["result"] = None
                st.session_state["autorun"] = True
                st.rerun()

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

            # 演示态与真实结果共用同一张纸
            render_score_hero(DEMO_METRICS, DEMO_NARRATIVE, 41.2)
            section("01", "凭什么这么判", fmt.band_note(DEMO_METRICS))
            render_dims(DEMO_METRICS)
            with st.expander("原始数字（8 项关键指标）", expanded=False):
                render_metric_cards(DEMO_METRICS)
            section("02", "交接清单", fmt.doc_kicker(DEMO_METRICS))
            render_document(DEMO_METRICS, DEMO_NARRATIVE)
            render_forensics(
                DEMO_METRICS,
                {"metrics": DEMO_METRICS, "timings": {"analyze": 35.4, "narrative": 5.8}},
            )
            return

        if result:
            render_result(result)
        else:
            st.markdown(
                '<div class="req-empty">'
                '<div class="req-empty__title">还没有待检的仓库</div>'
                '<div class="req-empty__detail">'
                "在上面贴一个公开仓库地址，或从左侧挑一个样本。<br>"
                "一次检查大约 10–60 秒：接入 → 采集指标 → 生成叙事。"
                "</div>" + _legend_html() + "</div>",
                unsafe_allow_html=True,
            )


if __name__ == "__main__":
    main()
