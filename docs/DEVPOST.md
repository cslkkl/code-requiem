# Devpost 提交材料 — 七项，可直接复制粘贴

> 提交页：<https://build-your-first-ai-app.devpost.com/> → `Join hackathon` → `My projects`
> 每一项都用 `---` 分隔，方块内的内容即为要粘贴进 Devpost 表单的原文。
> 带 `[方括号]` 的是占位符，提交前必须替换。
>
> 截止：**北京时间 2026-11-28 06:00**（UTC 2026-11-27 22:00）

---

## ① 项目标题与描述

### 1-A. 英文版（Devpost 表单主填这一版）

**Project title:**

```
Code Requiem — a health score for the codebase you just inherited
```

**Short tagline (≤ 200 chars):**

```
Paste any public Git repo. Get a deterministic 0–100 health score and an AI-written handover document — so you know what you inherited before you touch it.
```

**Inspiration:**

```
Nobody documents a handover until it's too late. The engineer who built the service left, the README was last updated two years ago, and the new owner has to guess which file is load-bearing. "Bus factor" is a number every tech lead talks about and nobody measures.

I wanted to turn that vague dread into a number you can put in a spreadsheet — and then into a document someone will actually read.
```

**What it does:**

```
Code Requiem takes any public Git repository URL and produces two things:

1. A deterministic 0–100 health score, computed from six weighted dimensions:
   recency of last commit (30), bus factor (25), commit trend over the last
   12 months (15), dependency staleness against PyPI/npm (15), issue response
   time (10), and documentation completeness (5).

2. A handover document written by Qwen2.5-7B-Instruct. Above 70 the project is
   alive and you get a "Code Will" — what exists, who maintains it, which files
   are landmines you must not touch, and what order to touch things in.
   Between 30 and 69 you get both the will and a preview eulogy of how this
   project dies if nothing changes. Below 30 you get the Eulogy: cause of death,
   root cause, accelerating factors, lessons.

The score is pure Python: no LLM, no randomness, no network-dependent scoring.
Run it twice on the same repo at the same moment and you get the identical
number, which is the point — it's evidence, not vibes.

Concretely, a bus factor of 1 scores 0 out of 25 and the document states it in
plain language: "if this person leaves, the project ends."
```

**How I built it:**

```
Analysis layer (deterministic): shallow git clone at depth 200, GitPython for
history and authorship, dedup authors by email, and a pure-function scoring
module with no imports beyond the standard library. Dependency staleness comes
from the public PyPI and npm registry JSON APIs with a 5-second timeout; GitHub's
Issues API provides the median time-to-close.

Narrative layer (AI): metrics are serialized to JSON and handed to Qwen2.5-7B-Instruct
via the Hugging Face Inference API through an OpenAI-compatible endpoint. The model
receives no repo access and no authority over the score — its only job is turning
a metrics table into prose.

UI: Streamlit, deployed to Streamlit Community Cloud with the token stored in app Secrets.

The dominant engineering cost was not the AI. It was making the thing never crash:
empty repos, one-commit repos, private repos, dead registry APIs, rate limits,
Go projects with no dependency API. Every one of those has a defined fallback, and
the LLM being down degrades to a metrics-only document instead of an error page.
```

**Challenges:**

```
The hardest decision was refusing to let the LLM touch the score. It would have
been trivial to ask the model "rate this repo's health" and it would have produced
confident numbers that change every run. That is useless for a risk report.

The second was threshold design. A 60% contributor share for bus factor rather
than the usual 80%: at 80% the warning arrives after the person has already left.
60% still gives you a quarter to do something.
```

**What's next:**

```
Weight bus factor by git blame lines instead of commit count, support private
repos with user-supplied tokens, and sample the score weekly to plot the actual
death curve of a project rather than a single snapshot.
```

### 1-B. 中文版（用于中文社区、README 顶部、答辩口播）

**标题：**

```
代码安魂曲 — 给刚接手的项目做一次健康体检
```

**一句话：**

```
输入任意公开 Git 仓库地址，用纯确定性算法打出 0–100 健康分，再由 AI 写成一份能直接贴进交接单的文档。
```

**它解决什么问题：**

```
原作者离职，README 两年没更新，新接手的人不知道该先看哪个文件、不知道该不敢动哪块代码。
"Bus factor" 是每个技术负责人都会提、但没人真的去量的东西。

代码安魂曲把这种模糊的恐惧变成一个能写进表格的数字，再变成一份有人愿意读完的文档。
```

**它怎么解决：**

```
第一层（确定性）：浅克隆仓库，读 git log 与作者分布，查 PyPI / npm 的依赖发布时间，
查 GitHub issue 的响应中位数。六个维度加权：最后提交 30 分、bus factor 25 分、
活跃趋势 15 分、依赖健康 15 分、issue 响应 10 分、文档完整度 5 分。
这一层不含任何 AI，同一仓库同一时刻跑两次分数必然相同。

第二层（AI）：Qwen2.5-7B-Instruct 只负责把算好的指标写成人话。
≥70 分输出「代码遗嘱」；30–69 分输出「临终关怀」（遗嘱 + 预演悼词）；
<30 分输出「项目悼词」。bus factor = 1 时文档会明确写出"这个人离开，项目即终止"。

价值主张落在工程交接与单点风险盘点，悼词只是让人愿意点开的那层糖衣。
```

**技术栈：**

```
Python 3.12 · Streamlit · GitPython · Hugging Face Inference API（Qwen2.5-7B-Instruct）· Streamlit Community Cloud
```

---

## ② 可运行原型链接

**Built with / Try it out 字段填：**

```
https://code-requiem-iczezzcmbgqssvxkaciarxe.streamlit.app
```

> 已部署到 Streamlit Community Cloud（HF Spaces 免费版已不支持 Streamlit，
> 实测 `sdk=streamlit` 返回 400、`gradio`/`docker` 需 PRO；详见 docs/DEPLOY.md）。
>
> 校验标准（SPEC 第 8 章要求）：**陌生人无账号、无登录、直接打开即可用**。
> 提交前用无痕窗口自己点一遍，冷启动等到页面完全加载再截图。

**Video demo link 字段填：**

```
[VIDEO_URL]
```

---

## ③ AI 实现说明

> 这是评审重点，也是本项目最需要说清楚的一点：**AI 在哪、不在哪。**

```
## Where AI is used

One model, one job.

- Provider: Hugging Face Inference API (OpenAI-compatible router endpoint,
  https://router.huggingface.co/v1/chat/completions)
- Model: Qwen/Qwen2.5-7B-Instruct, by the Qwen team, Apache-2.0 licensed
- Auth: a Hugging Face Read token, stored only in the app's Secrets as HF_TOKEN

The model receives a single JSON object containing already-computed metrics —
score total, six dimension scores, bus factor, top contributors with commit
shares, stalest dependencies, median issue close time, and a list of files
carrying FIXME/HACK markers. It receives no repository access, no shell, no
ability to fetch anything.

Its output is Markdown prose: five fixed sections for the will, five for the
eulogy. That's the entire AI surface area.

## Where AI is deliberately absent

The analysis layer contains zero AI, zero randomness, and zero LLM calls.

src/scoring.py is a set of pure functions: integers in, integers out. The six
dimension scores are computed from lookup tables whose thresholds are published
in docs/SPEC.md chapter 3 and unit-tested against that table. src/git_metrics.py
reads only the local clone. Nothing in the scoring path can be influenced by
model output, temperature, sampling, or the model's opinion of the code.

Verification is therefore trivial and mechanical: run the same repo twice, the
deterministic_key and the total must be byte-identical. This is an acceptance
test, not an aspiration. A score you cannot reproduce is not evidence.

## Why this split

A handover document is used to make decisions — do we refactor, do we rewrite,
do we staff a second maintainer. If the number moved between runs, or if the
model could be persuaded that a dead repo was healthy because the code looked
well organized, the document would be worthless as risk evidence.

So: deterministic for the verdict, generative for the wording. The AI is a
typesetter, not a judge.

## Degradation strategy

The LLM is treated as an optional dependency of the last step.

- No token configured → the app runs fully, scoring is normal, narrative
  sections are rendered from a deterministic template straight off the metrics,
  and a banner reads "AI narrative unavailable, structured metrics below."
- Inference API returns 5xx / times out (60s) / rate-limits → the same fallback
  path, with the failure reason surfaced in the banner.
- Any exception in the narrative layer is caught; markdown is never empty.

There is no configuration and no network condition under which the app fails to
produce a score and a document.
```

---

## ④ 源代码链接

**Repository 字段填：**

```
https://github.com/cslkkl/code-requiem
```

**补充说明（填在 Additional info / Built with 里）：**

```
Public repository, MIT licensed. Contains no API keys, tokens, or credentials —
all secrets are injected as environment variables and, in production, through
Streamlit Cloud Secrets (GITHUB_TOKEN, HF_TOKEN).

Structure:
  app.py                 Streamlit entry point
  src/analyzer.py        orchestration: clone, collect, score, assemble
  src/scoring.py         six-dimension scoring, pure functions
  src/git_metrics.py     git log, authorship, LOC, risk scanning
  src/deps.py            PyPI / npm dependency staleness
  src/issues.py          GitHub issue response median
  src/llm.py             provider priority + fallback
  src/narrative.py       metrics -> Markdown (incl. degraded template)
  docs/SPEC.md           algorithm specification and thresholds
  docs/CONTRACT.md       module contract (function signatures, schema)
  docs/DEVPOST.md        this submission text
  docs/DEMO.md           demo shot list
  requirements.txt       pinned runtime dependencies
```

---

## ⑤ 演示视频说明

**视频标题：**

```
Code Requiem — from repo URL to handover document in 65 seconds
```

**Video description（Devpost 字段）：**

```
64-second continuous demo. Both analyses are real runs against live
repositories — the progress bars and stage labels are the actual pipeline,
not an animation. Waiting time is compressed; nothing is cut out.

0:00–0:06   The blank report sheet. One input field, three samples.
0:06–0:12   Paste kornia/kornia. The analyzer runs: clone, collect metrics,
            generate narrative.
0:12–0:35   Score 100 / 100, state "alive". Three questions a new owner
            actually asks, answered first: can I still use this, who do I
            call when it breaks, what do I do first. Then the evidence —
            six dimensions, each with the number and the reason. Then the
            Code Will: what exists, who maintains it, which files are
            landmines, what order to touch things in.
0:35–0:64   Switch to livid/v2ex-gae, last commit 9 years ago. Score 14,
            state "dead". The Eulogy: cause of death, root cause, what the
            successor should and should not do. Note the bus factor of 1 —
            one contributor holds 95% of the commits, so the document says
            in one line: "there is nobody left to call."

The point of the two acts: the same deterministic algorithm produces both
the optimistic and the terminal verdict. Nothing about the score depends on
the model.

Recorded at 1440×900 with an automated Playwright script driving the real
app; only the idle waiting intervals are sped up in post.
```

**视频参数（供你上传时核对，不填表单）：**

| 项 | 值 |
|---|---|
| 文件 | `docs/demo-draft.mp4` |
| 时长 | 64.6 秒 |
| 分辨率 | 1440×900 |
| 体积 | 1.1 MB |
| 字幕 | 无（画面内已有中文说明，未烧字幕） |

**评审提示（不填表单，留给你自己核对）：**

- 时长 60–90 秒区间内
- 两段分析都是真实运行，进度条来自真实管线
- 分镜脚本见 [`docs/DEMO.md`](DEMO.md)

---

## ⑥ README 说明

**填在 Devpost 的 "README / Documentation" 字段：**

```
The repository README is the primary documentation and covers, in order:

- 30-second overview with an ASCII pipeline diagram (input -> deterministic score
  -> AI narrative -> Markdown document)
- Why the project exists: engineering handover and bus factor risk, with the three
  questions a new owner actually asks
- Quick start on Windows PowerShell and macOS/Linux, using uv with a mirror index,
  including the exact commands and virtualenv paths
- Three ways to configure the LLM: Hugging Face token (HF_TOKEN, with a link to
  https://huggingface.co/settings/tokens and instructions for Space Secrets), a
  custom OpenAI-compatible gateway via LLM_BASE_URL / LLM_API_KEY / LLM_MODEL, and
  running with no configuration at all in degraded mode
- An explicit architecture section declaring that the analysis layer contains no AI,
  with a table of all six scoring dimensions, their weights, and where each
  threshold is specified
- The three output modes and their trigger conditions
- The seven edge cases and their defined fallbacks
- Tech stack table, full project tree, and a third-party dependency table with
  licenses — including the Qwen2.5-7B-Instruct model attribution (Qwen team,
  Apache-2.0, accessed through Hugging Face Inference)
- Known limitations (unauthenticated GitHub rate limits, email-based author dedup,
  registry-only dependency analysis, sampling on very large repos) and a roadmap
- MIT license text

Link: https://github.com/cslkkl/code-requiem#readme
```

---

## ⑦ 你的贡献说明

> **规则强制项。** 必须如实披露 AI 辅助工具的使用。下面这版按第一人称写，可直接粘贴。

```
I'm a student, and I led this project solo — SPEC, algorithm, acceptance testing,
and every delivery decision. I also used AI coding assistants, and here is exactly
how.

What I decided myself (no AI involvement in the judgement):

- The concept and its framing. I explicitly chose to sell this as engineering
  handover and bus factor risk assessment, not as a eulogy generator. The eulogy
  is the hook; the handover document is the product. That positioning decision
  drives everything else in the repo.
- The scoring model. Six dimensions, the weights (30/25/15/15/10/5), and every
  threshold table. I wrote docs/SPEC.md chapter 3 before writing a line of code,
  including the justification for a 60% bus factor cutoff instead of the
  conventional 80% — 60% still leaves time to act.
- The hardest architectural constraint: the analysis layer must contain no AI at
  all. I rejected the obvious "ask the model to rate the repo" approach because a
  score that changes between runs is not evidence. The LLM is confined to wording.
- The degradation policy. I specified that a missing or failing LLM must produce a
  template-rendered metrics document and never an error page, and that no network
  failure may prevent a score from being produced.
- The seven edge cases and their required fallbacks, and the acceptance checklist
  in docs/SPEC.md chapter 7 — including the determinism test (same repo, same
  moment, identical score).
- All review and acceptance. I read the code that came back, ran it against the
  reference repos, checked the score ranges against my own expected distribution,
  and sent back what was wrong. Where the implementation and the spec disagreed,
  the spec won or the spec was deliberately amended — I made that call each time.

What AI assistants did:

I used Claude and DeepSeek as coding assistants throughout. They wrote
implementation code from my specifications — the git history collector, the
registry API clients, the Streamlit layout — and drafted portions of the prompt
text for the will and eulogy sections. They also drafted documentation prose that
I edited, including early drafts of this submission text and the README.

How I kept that honest:

Every module was written against docs/CONTRACT.md, which I authored: function
signatures, the result schema, error shapes, and file ownership. That contract is
why three modules could be developed in parallel without drifting, and it is also
why I can say what is mine — the constraints were mine, the code was typed by
whatever was fastest.

The AI-assisted parts are implementation and prose. The product thesis, the
algorithm, the refusal to let the model score, the failure policy, and the
acceptance criteria are my work, and I can defend each of them line by line.

Model attribution: the app calls Qwen/Qwen2.5-7B-Instruct (Qwen team, Apache-2.0)
through the Hugging Face Inference API, in production only. It converts computed
metrics into narrative text and has no influence on the score.
```

---

## 提交前自检清单

| # | 项 | 检查 |
|---|---|---|
| 1 | 标题与描述 | 中英双版齐全，价值主张落在交接 / bus factor |
| 2 | 可运行原型 | `https://code-requiem-iczezzcmbgqssvxkaciarxe.streamlit.app` 已替换，无痕窗口能直接打开 |
| 3 | AI 实现说明 | 写明 HF Inference + Qwen2.5-7B-Instruct，写明分析层零 AI，写明降级 |
| 4 | 源代码 | 链接可访问，仓库无任何 key |
| 5 | 演示视频 | 60–90 秒，两段连续分析，字幕烧进画面 |
| 6 | README | 安装 / 功能 / 技术栈 / 试用 / 截图 五项齐全 |
| 7 | 贡献说明 | 已如实披露 Claude / DeepSeek 辅助范围 |
| — | 红线 | 全仓库 grep 一遍 `hf_`、`sk-`、`api_key`、`password`，必须为空 |
| — | 截止 | 北京时间 2026-11-28 06:00 之前点下 Submit |
