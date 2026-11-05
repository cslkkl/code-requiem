"""录演示视频：用 Playwright 驱动真实分析过程，输出 1080p mp4。

为什么要自动化录：
  1. 手动录屏容易出现「鼠标乱晃、等太久、忘记点哪里」的废镜头
  2. 演示必须展示真实分析耗时（10-60 秒），人工掐点做不到稳定
  3. 评委看的是「这东西真的能跑」，录线上地址比录本地更有说服力

用法：
    .venv\\Scripts\\python.exe -u scripts\\record_demo.py --target live
    .venv\\Scripts\\python.exe -u scripts\\record_demo.py --target local

输出：docs/demo-draft.mp4（无声，供后期配音或直接加字幕）
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "_video"
FINAL = ROOT / "docs" / "demo-draft.mp4"

LIVE_URL = "https://code-requiem-iczezzcmbgqssvxkaciarxe.streamlit.app"
LOCAL_URL = "http://127.0.0.1:8501"

VIEWPORT = {"width": 1440, "height": 900}

# 演示用的仓库：一个活的、一个死的，正好展示两种模式
DEMO_ALIVE = "https://github.com/kornia/kornia"
DEMO_DEAD = "https://github.com/livid/v2ex-gae"


def _pause(page, ms: int, note: str = "") -> None:
    if note:
        print(f"    · {note}")
    page.wait_for_timeout(ms)


def _type_url(page, url: str) -> None:
    """逐字输入，让录屏里能看到输入过程。

    用 `press_sequentially` 而不是循环 `press(ch)`：后者对 `/` `:` 这类字符
    会按「键名」解析，实际打进去的内容会被改写，导致提交后分析一个坏地址。
    """
    box = page.locator('[data-testid="stTextInput"] input').first
    box.click()
    box.fill("")
    box.press_sequentially(url, delay=22)
    page.wait_for_timeout(200)
    actual = box.input_value()
    if actual != url:
        # 兜底：万一逐字输入还是丢字，直接填对，保证录的是真实交互
        box.fill(url)
        print(f"      ⚠ 逐字输入得到 {actual!r}，已改为直接填充")


def _submit(page) -> None:
    page.locator('button[data-testid="stBaseButton-primaryFormSubmit"]').first.click()


def _wait_result(page, timeout_s: int = 120) -> bool:
    """等结果出现：出现「所以，我该关心什么」或「凭什么这么判」即为完成。

    同时识别常见失败态，避免对着错误页白等 120 秒。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = page.inner_text("body")
        if "所以，我该关心什么" in body or "凭什么这么判" in body:
            return True
        for bad, label in (
            ("配额已用完", "限流"),
            ("无法访问该仓库", "仓库不可达"),
            ("网络异常", "网络异常"),
            ("超过 90", "超时"),
        ):
            if bad in body:
                print(f"      ⚠ 页面报错：{label}")
                return False
        page.wait_for_timeout(700)
    return False


def record(url: str, out: Path, *, dead_demo: bool) -> tuple[bool, dict]:
    """录一段原始视频，并返回关键时间点（相对视频开头的秒数）。

    结构是两幕，因为产品本身有两条腿：
      第一幕 kornia（存活）→ 遗嘱，演示「输入 → 分数 → 三个问题 → 六维 → 文档」
      第二幕 v2ex-gae（已故）→ 悼词，证明同一套算法能翻到另一面
    只演一幕会让评委以为这工具只会夸奖活跃项目。

    为什么要记时间点：真实分析要 13-30 秒，直接放进 90 秒的演示里会把
    节奏彻底拖垮。但伪造进度条又是不诚实的。正确做法是**录真实过程，
    后期只把「等待」那一段加速**，其余一帧不动 —— 观众看到的进度条与
    阶段名都来自真实运行。
    """
    from playwright.sync_api import sync_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    marks: dict = {"waits": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-device-scale-factor=1"])
        t0 = time.monotonic()
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(OUT_DIR),
            record_video_size=VIEWPORT,
            locale="zh-CN",
        )
        page = context.new_page()

        print(f"  → 打开 {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        _pause(page, 3200, "等首屏渲染")

        # ── 第一幕：存活仓库 ────────────────────────────────────────────
        print("  [第一幕] 存活仓库 → 遗嘱")
        _pause(page, 3600, "空状态：产品定位")

        print(f"  → 输入 {DEMO_ALIVE}")
        _type_url(page, DEMO_ALIVE)
        _pause(page, 900)

        marks["waits"].append((time.monotonic() - t0, "w1-start"))
        _submit(page)
        _pause(page, 1200)
        ok1 = _wait_result(page)
        marks["waits"][0] = (marks["waits"][0][0], time.monotonic() - t0)
        print(f"      分析 {marks['waits'][0][1] - marks['waits'][0][0]:.1f} 秒")

        if ok1:
            _pause(page, 5200, "分数 + 三个问题（重点停留）")
            beats = [
                (520, 4600, "六维：前三维"),
                (430, 4600, "六维：后三维"),
                (760, 5200, "文档：遗嘱正文"),
                (700, 5000, "文档：交接步骤"),
                (700, 4600, "解剖面板"),
            ]
            for px, dwell, note in beats:
                page.mouse.wheel(0, px)
                _pause(page, dwell, note)
            page.mouse.wheel(0, -3000)
            _pause(page, 1600, "回到顶部")

        # ── 第二幕：已故仓库 ────────────────────────────────────────────
        print("  [第二幕] 已故仓库 → 悼词")
        dead_btn = page.locator('button:has-text("v2ex-gae")').first
        if dead_btn.count() > 0:
            marks["waits"].append((time.monotonic() - t0, "w2-start"))
            dead_btn.click()
            _pause(page, 1200)
            ok2 = _wait_result(page)
            marks["waits"][-1] = (marks["waits"][-1][0], time.monotonic() - t0)
            print(f"      分析 {marks['waits'][-1][1] - marks['waits'][-1][0]:.1f} 秒")
            if ok2:
                _pause(page, 5400, "已故：低分 + 三个问题")
                page.mouse.wheel(0, 560)
                _pause(page, 4800, "为什么判死")
                page.mouse.wheel(0, 700)
                _pause(page, 5400, "悼词正文")
                page.mouse.wheel(0, 700)
                _pause(page, 4200, "悼词续")
                page.mouse.wheel(0, -2200)
                _pause(page, 2800, "收尾")
        else:
            print("      ⚠ 找不到已故样本按钮，跳过第二幕")

        marks["total"] = time.monotonic() - t0
        video_path = page.video.path()
        page.close()
        context.close()
        browser.close()

    if video_path and Path(video_path).is_file():
        shutil.copy2(video_path, out)
        print(f"  ✓ 原始录屏 → {out}（{marks['total']:.1f} 秒）")
        return True, marks
    print("  ✗ 没生成视频文件")
    return False, marks


def _build_speedramp_filter(waits: list[tuple[float, float]], total: float, speed: float) -> str:
    """把若干「等待段」按 speed 加速，其余原速，拼成一条完整时间线。

    做法：把整条时间线按等待段切成 N+1 个不重叠片段，
    每段先 trim + setpts 归零，等待段额外除以 speed，最后 concat。
    """
    cuts: list[tuple[float, float, float]] = []
    cursor = 0.0
    for a, b in sorted(waits):
        a = max(0.0, min(a, total))
        b = max(a + 0.1, min(b, total))
        if a > cursor + 0.05:
            cuts.append((cursor, a, 1.0))
        cuts.append((a, b, speed))
        cursor = b
    if total > cursor + 0.05:
        cuts.append((cursor, total, 1.0))
    if not cuts:
        return "scale=1440:900:flags=lanczos,fps=30,format=yuv420p[out]"

    parts: list[str] = []
    labels: list[str] = []
    for idx, (a, b, rate) in enumerate(cuts):
        label = f"s{idx}"
        if abs(rate - 1.0) < 1e-6:
            parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[{label}]")
        else:
            parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{rate:.3f}[{label}]")
        labels.append(f"[{label}]")
    return (
        ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=0,"
        "scale=1440:900:flags=lanczos,fps=30,format=yuv420p[out]"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["live", "local"], default="live")
    ap.add_argument("--dead", action="store_true", help="只演示已故仓库（悼词模式）")
    ap.add_argument(
        "--speed",
        type=float,
        default=8.0,
        help="分析等待段的加速倍数（默认 8 倍，只作用于等待段）",
    )
    args = ap.parse_args()

    url = LIVE_URL if args.target == "live" else LOCAL_URL
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not shutil.which("ffmpeg"):
        print("缺少 ffmpeg，无法压制最终视频")
        return 2

    print(f"=== 录制演示（{args.target}）===")
    name = "dead" if args.dead else "alive"
    raw = OUT_DIR / f"raw-{args.target}-{name}.mp4"
    ok, marks = record(url, raw, dead_demo=args.dead)

    waits = [(a, b) for a, b in (marks.get("waits") or []) if b > a]
    total = marks.get("total", 0.0)
    saved = sum((b - a) - (b - a) / args.speed for a, b in waits)
    print(f"  → 等待段 {len(waits)} 处，合计缩短 {saved:.1f} 秒")

    import subprocess

    if waits and saved > 0.5:
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(raw),
            "-filter_complex",
            _build_speedramp_filter(waits, total, args.speed),
            "-map",
            "[out]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "21",
            "-movflags",
            "+faststart",
            str(FINAL),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(raw),
            "-vf",
            "scale=1440:900:flags=lanczos,fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "21",
            "-movflags",
            "+faststart",
            str(FINAL),
        ]

    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("  ✗ ffmpeg 失败:")
        print("   ", (r.stderr or "")[:700].replace("\n", "\n    "))
        return 4

    dur = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(FINAL),
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        secs = float(dur)
    except ValueError:
        secs = 0.0
    print()
    print(f"  ✓ {FINAL}")
    print(f"    成片 {secs:.1f} 秒（{args.speed:.0f} 倍速压缩等待段）")
    if 55 <= secs <= 98:
        print("    时长在目标区间内（目标 60-90 秒，允许少量余量）")
    else:
        print("    时长偏离目标区间，可调 --speed（越大越短）")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
