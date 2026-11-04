"""Issue 响应分析（保留兼容入口）。

实际实现在 github_api.py —— 这里只是转出，避免老代码 import 断掉。
"""

from __future__ import annotations

try:
    from .github_api import analyze_issues, commit_count, repo_meta  # type: ignore
except ImportError:  # 脚本模式
    from github_api import analyze_issues, commit_count, repo_meta  # type: ignore

__all__ = ["analyze_issues", "repo_meta", "commit_count"]
