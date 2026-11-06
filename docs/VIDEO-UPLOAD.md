# 演示视频上传文案

> 文件：`docs/demo-draft.mp4` · 64.6 秒 · 1440×900 · 1.1 MB
> 上传后设为**公开**（Devpost 评审要能直接打开），把链接填回
> [`docs/DEVPOST.md`](DEVPOST.md) ② 的 `[VIDEO_URL]`。

---

## 标题（英文，YouTube 用）

```
Code Requiem — from repo URL to handover document in 65 seconds
```

## 标题（中文，B站 用）

```
代码安魂曲：给一个陌生仓库做健康体检，65 秒出一份交接文档
```

---

## 简介 / Description

```
Paste any public Git repo. Get a deterministic 0–100 health score and an
AI-written handover document — so you know what you inherited before you
touch it.

Live demo: https://code-requiem-iczezzcmbgqssvxkaciarxe.streamlit.app
Source: https://github.com/cslkkl/code-requiem

Two real runs, no mock data:

  kornia/kornia   → 100/100, "alive"  → Code Will
  livid/v2ex-gae  →  14/100, "dead"   → Eulogy

Why the score is trustworthy: the analysis layer contains zero AI, zero
randomness, zero LLM calls. Run the same repo twice and you get the
byte-identical number. The model (Qwen2.5-7B-Instruct) only turns the
computed metrics into prose — it never sees the repo and never touches
the score.

Built with Python 3.12, Streamlit, GitPython, and the Hugging Face
Inference API. Deployed on Streamlit Community Cloud.
```

---

## 标签 / Tags

```
streamlit, python, ai, developer tools, code health, bus factor,
technical debt, git, qwen, hugging face
```

---

## 章节（YouTube 时间轴，可选）

```
0:00  The blank report
0:06  kornia/kornia — score 100, Code Will
0:35  livid/v2ex-gae — score 14, Eulogy
```

---

## 上传后要做的三件事

1. 确认视频状态是 **Public / 公开**（不是 Unlisted，除非你确认 Devpost 允许）
2. 复制形如 `https://www.youtube.com/watch?v=XXXXXXXXXXX` 的链接
3. 把链接告诉我，我填进 DEVPOST.md 并做最后一遍自检

---

## 不要做的事

- 不要加背景音乐盖掉解说（本片无解说，无声是正常的）
- 不要用平台自动生成的缩略图（建议手动选 0:20 附近的分数画面）
- 不要重新压缩（1.1 MB 已经很小，再压文字会糊）
