"""OpenAI 兼容 chat 客户端（严格按 docs/CONTRACT.md 第 5 节）。

provider 解析优先级（每次调用都重新解析，方便切换 / 测试）：

  1. ``LLM_BASE_URL`` + ``LLM_API_KEY``（``LLM_MODEL`` 可选，默认 ``Qwen/Qwen2.5-7B-Instruct``）
     —— 显式覆盖，任意 OpenAI 兼容网关
  2. ``HF_TOKEN`` → ``https://router.huggingface.co/v1``，模型 ``Qwen/Qwen2.5-7B-Instruct``
     —— HF Router 本身就是 OpenAI 兼容接口，POST ``{base}/chat/completions``
        （旧式 ``api-inference.huggingface.co`` 已废弃，不使用）
  3. 本地开发网关 ``http://127.0.0.1:8317/v1``，模型 ``kimi-k3``，
     key 从 ``~/.dsh/.credentials.yaml`` 的 ``CPA_API_KEY`` 字段读取。
     该文件在仓库之外，**key 只在内存里用，绝不写入仓库任何文件**。

对外只有两个函数：``chat()`` 与 ``provider_info()``。
任何网络 / 解析 / 超时异常都转换成 ``ok=False``，**绝不向外抛异常**。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import requests

# ── 常量 ────────────────────────────────────────────────────────────────────
LOCAL_ENDPOINT = "http://127.0.0.1:8317/v1"
# 本地开发网关默认模型：实测同一网关上 wb/deepseek-v4.1-flash 明显快于 kimi-k3
# （非推理模型，无 reasoning_content 开销），本地开发优先用它。
# 需要别的模型时用 LLM_MODEL / CPA_MODEL 覆盖。
LOCAL_MODEL = "wb/deepseek-v4.1-flash"
HF_ENDPOINT = "https://router.huggingface.co/v1"
HF_MODEL = "Qwen/Qwen2.5-7B-Instruct"
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"

CREDENTIALS_PATH = Path.home() / ".dsh" / ".credentials.yaml"

SOURCE_ENV = "env"
SOURCE_HF = "hf_token"
SOURCE_LOCAL = "local_gateway"
SOURCE_NONE = "none"

# 单次请求的连接 / 读取超时上限，避免病态网关把整个页面拖死
_HARD_TIMEOUT_CAP = 120


def _clean(value: str | None) -> str:
    """去掉首尾空白与 YAML 可能带的引号。"""
    if not value:
        return ""
    return value.strip().strip('"').strip("'").strip()


def _env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return ""
    return _clean(value)


def _llm_disabled() -> bool:
    """``LLM_DISABLE=1`` 强制走降级路径（演示断网 / 自测用）。"""
    return _env("LLM_DISABLE").lower() in {"1", "true", "yes", "on"}


def read_cpa_api_key(path: Path | None = None) -> str:
    """从 ``~/.dsh/.credentials.yaml`` 里用正则抠出 ``CPA_API_KEY``。

    不引入 pyyaml：只需要一个标量字段，正则足够，也避免多一个依赖。
    读不到就返回空串（调用方据此判定未配置）。
    """
    target = Path(path) if path else CREDENTIALS_PATH
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    match = re.search(r"^[ \t]*CPA_API_KEY[ \t]*:[ \t]*(.+?)[ \t]*$", text, re.MULTILINE)
    if not match:
        return ""
    return _clean(match.group(1))


def _endpoint_ok(endpoint: str) -> bool:
    """本地网关是否真的可达（1.5 秒探测，失败就当作没配置）。"""
    if not endpoint:
        return False
    try:
        # 只探 TCP，不花额度也不依赖鉴权
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        import socket

        with socket.create_connection((host, port), timeout=1.5):
            return True
    except Exception:
        return False


def resolve_provider() -> dict:
    """解析出本次调用要用的 provider。返回含 key 的完整信息（内部用，勿外泄）。"""
    if _llm_disabled():
        return {
            "configured": False,
            "source": SOURCE_NONE,
            "model": "",
            "endpoint": "",
            "key": "",
            "reason": "LLM_DISABLE 已开启，强制降级",
        }

    # 1) 显式环境变量覆盖
    base = _env("LLM_BASE_URL")
    key = _env("LLM_API_KEY")
    model = _env("LLM_MODEL") or DEFAULT_MODEL
    if base and key:
        return {
            "configured": True,
            "source": SOURCE_ENV,
            "model": model,
            "endpoint": base.rstrip("/"),
            "key": key,
            "reason": "",
        }
    # 半配置（只给了 base 没给 key）仍然放行，很多自建网关不需要鉴权
    if base and not key:
        return {
            "configured": True,
            "source": SOURCE_ENV,
            "model": model,
            "endpoint": base.rstrip("/"),
            "key": "",
            "reason": "",
        }

    # 2) Hugging Face Router
    hf_token = _env("HF_TOKEN") or _env("HUGGINGFACE_API_KEY")
    if hf_token:
        return {
            "configured": True,
            "source": SOURCE_HF,
            "model": _env("HF_MODEL") or HF_MODEL,
            "endpoint": HF_ENDPOINT,
            "key": hf_token,
            "reason": "",
        }

    # 3) 本地开发网关
    cpa = read_cpa_api_key()
    if cpa:
        local_ep = _env("CPA_BASE_URL") or LOCAL_ENDPOINT
        return {
            "configured": True,
            "source": SOURCE_LOCAL,
            "model": _env("CPA_MODEL") or LOCAL_MODEL,
            "endpoint": local_ep.rstrip("/"),
            "key": cpa,
            "reason": "",
        }

    return {
        "configured": False,
        "source": SOURCE_NONE,
        "model": "",
        "endpoint": "",
        "key": "",
        "reason": "未配置 LLM：LLM_BASE_URL/LLM_API_KEY、HF_TOKEN、本地网关均不可用",
    }


def provider_info() -> dict:
    """对外状态（**绝不含 key**）。"""
    try:
        prov = resolve_provider()
        return {
            "configured": bool(prov.get("configured")),
            "source": prov.get("source") or SOURCE_NONE,
            "model": prov.get("model") or "",
            "endpoint": prov.get("endpoint") or "",
        }
    except Exception:
        return {"configured": False, "source": SOURCE_NONE, "model": "", "endpoint": ""}


def _extract_text(data: dict) -> str:
    """只取 ``choices[0].message.content``。

    kimi-k3 / deepseek 一类的推理模型会额外返回 ``reasoning_content``，
    那是思维链，不是正文，必须丢弃。
    """
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):  # 少数网关返回分段数组
            parts = []
            for piece in content:
                if isinstance(piece, dict) and isinstance(piece.get("text"), str):
                    parts.append(piece["text"])
                elif isinstance(piece, str):
                    parts.append(piece)
            return "".join(parts).strip()
        return ""
    text = first.get("text")
    return text.strip() if isinstance(text, str) else ""


def chat(
    messages: list[dict],
    *,
    max_tokens: int = 2048,
    temperature: float = 0.4,
    timeout: int = 60,
    json_mode: bool = False,
) -> dict:
    """调用 OpenAI 兼容 ``/chat/completions``。

    返回 ``{"ok", "text", "model", "error", "source"}``，永不抛异常。
    """
    result = {"ok": False, "text": "", "model": "", "error": None, "source": SOURCE_NONE}
    try:
        prov = resolve_provider()
        result["source"] = prov.get("source") or SOURCE_NONE
        result["model"] = prov.get("model") or ""

        if not prov.get("configured"):
            result["error"] = prov.get("reason") or "未配置 LLM"
            return result

        endpoint = (prov.get("endpoint") or "").rstrip("/")
        if not endpoint:
            result["error"] = "LLM endpoint 为空"
            return result

        if not isinstance(messages, list) or not messages:
            result["error"] = "messages 为空"
            return result

        try:
            budget = max(1, min(int(timeout), _HARD_TIMEOUT_CAP))
        except Exception:
            budget = 60

        payload = {
            "model": prov.get("model") or DEFAULT_MODEL,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if prov.get("key"):
            headers["Authorization"] = "Bearer " + prov["key"]

        url = endpoint + "/chat/completions"
        response = requests.post(url, headers=headers, json=payload, timeout=budget)

        if response.status_code != 200:
            snippet = (response.text or "")[:300].replace("\n", " ")
            result["error"] = f"HTTP {response.status_code}: {snippet}"
            return result

        try:
            data = response.json()
        except Exception as exc:
            result["error"] = f"响应不是合法 JSON：{type(exc).__name__}"
            return result

        if isinstance(data, dict) and isinstance(data.get("model"), str):
            result["model"] = data["model"]

        text = _extract_text(data) if isinstance(data, dict) else ""
        if not text:
            raw_error = data.get("error") if isinstance(data, dict) else None
            if raw_error:
                result["error"] = f"网关返回错误：{str(raw_error)[:300]}"
            else:
                result["error"] = "模型返回空内容"
            return result

        result["ok"] = True
        result["text"] = text
        result["error"] = None
        return result

    except requests.exceptions.Timeout:
        result["error"] = f"请求超时（{timeout}s）"
        return result
    except requests.exceptions.ConnectionError as exc:
        result["error"] = f"连接失败：{type(exc).__name__}"
        return result
    except requests.exceptions.RequestException as exc:
        result["error"] = f"请求异常：{type(exc).__name__}: {exc}"
        return result
    except Exception as exc:  # 兜底：任何情况都不向上抛
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


__all__ = ["chat", "provider_info", "resolve_provider", "read_cpa_api_key"]
