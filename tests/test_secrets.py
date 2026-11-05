"""密钥读取层级的测试。

背景：项目原先只读 ``os.environ``，这在本地够用，但线上部署不行 ——
Streamlit Community Cloud 的密钥只存在 ``st.secrets`` 里（本地开发放
``.streamlit/secrets.toml``）。只读环境变量会让线上一直以匿名身份访问
GitHub API（60 次/小时），第 5 个访客就撞限流。

这里锁定三件事：
  1. 环境变量优先于 st.secrets（本地开发覆盖线上配置的常规做法）
  2. 环境变量缺失时能回落到 st.secrets
  3. 两者都没有时返回空串，绝不抛异常（命令行脚本、单元测试都靠这个）
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import github_api, llm  # noqa: E402

TOKEN_KEYS = ("GITHUB_TOKEN", "GH_TOKEN")


class _FakeSecrets(dict):
    """最小的 st.secrets 替身：支持 .get()，和真 Streamlit 一致。"""


def _patch_secrets(monkeypatch_map: dict):
    """同时替换 llm 与 github_api 里 import 的 streamlit 模块。

    两个模块都是「用到时才 import streamlit」，所以打在 sys.modules 上最稳。
    """
    import types

    fake = types.ModuleType("streamlit")
    fake.secrets = _FakeSecrets(monkeypatch_map)
    return mock.patch.dict(sys.modules, {"streamlit": fake})


class TestTokenResolution(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in TOKEN_KEYS}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is not None:
                os.environ[k] = v

    def test_env_wins_over_secrets(self):
        os.environ["GITHUB_TOKEN"] = "from_env"
        with _patch_secrets({"GITHUB_TOKEN": "from_secrets"}):
            self.assertEqual(github_api.read_token(), "from_env")

    def test_falls_back_to_secrets(self):
        with _patch_secrets({"GITHUB_TOKEN": "from_secrets"}):
            self.assertEqual(github_api.read_token(), "from_secrets")

    def test_gh_token_alias_in_secrets(self):
        with _patch_secrets({"GH_TOKEN": "alias_secret"}):
            self.assertEqual(github_api.read_token(), "alias_secret")

    def test_nothing_configured_returns_empty(self):
        with _patch_secrets({}):
            self.assertEqual(github_api.read_token(), "")
            self.assertFalse(github_api.has_token())

    def test_headers_carry_token(self):
        with _patch_secrets({"GITHUB_TOKEN": "abc123"}):
            h = github_api._headers()
        self.assertEqual(h.get("Authorization"), "Bearer abc123")

    def test_no_token_means_no_auth_header(self):
        with _patch_secrets({}):
            h = github_api._headers()
        self.assertNotIn("Authorization", h)


class TestLLMEnvResolution(unittest.TestCase):
    KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "HF_TOKEN", "LLM_DISABLE")

    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in self.KEYS}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is not None:
                os.environ[k] = v

    def test_llm_config_comes_from_secrets(self):
        with _patch_secrets({"LLM_BASE_URL": "https://example.com/v1", "LLM_API_KEY": "k", "LLM_MODEL": "m"}):
            info = llm.resolve_provider()
        self.assertTrue(info["configured"])
        self.assertEqual(info["endpoint"], "https://example.com/v1")
        self.assertEqual(info["model"], "m")

    def test_env_overrides_secrets(self):
        os.environ["LLM_MODEL"] = "from_env"
        with _patch_secrets(
            {"LLM_BASE_URL": "https://example.com/v1", "LLM_API_KEY": "k", "LLM_MODEL": "from_secret"}
        ):
            info = llm.resolve_provider()
        self.assertEqual(info["model"], "from_env")

    def test_missing_streamlit_does_not_crash(self):
        """命令行脚本里没有 streamlit 运行时，_env 必须静默返回空串。"""
        os.environ["LLM_BASE_URL"] = ""
        with mock.patch.dict(sys.modules, {"streamlit": None}):
            self.assertEqual(llm._env("LLM_BASE_URL"), "")

    def test_quotes_are_stripped(self):
        """secrets.toml 里手写带引号的值也要能认。"""
        os.environ["LLM_API_KEY"] = '"quoted_key"'
        self.assertEqual(llm._env("LLM_API_KEY"), "quoted_key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
