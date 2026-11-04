# -*- coding: utf-8 -*-
"""
tushare_trader/credentials.py — Tushare token 解析

**token 永不写入仓库。** 解析顺序（先命中先用）：

  1. 显式传入（CLI `--token`）
  2. 环境变量 `TUSHARE_TOKEN`（CI / 服务器推荐）
  3. 文件 `~/.tushare/token`（本地开发，权限建议 600）
  4. 项目内 `.tushare_token`（已被 .gitignore 排除）

任何来源都失败时抛 `TokenNotFound`，错误信息只列出「尝试过哪些来源」，
绝不回显 token 内容。
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "TUSHARE_TOKEN"
HOME_TOKEN = Path.home() / ".tushare" / "token"


class TokenNotFound(RuntimeError):
    """所有来源都没有可用 token。"""


def _from_env() -> str | None:
    v = os.environ.get(ENV_VAR, "").strip()
    return v or None


def _from_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # 允许 "TUSHARE_TOKEN=xxx" 或只有一行 token
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            line = line.split("=", 1)[1].strip().strip("'\"")
        if line:
            return line
    return None


def resolve_token(explicit: str | None = None,
                  project_root: Path | str | None = None) -> str:
    """
    按优先级解析 token。

    Raises
    ------
    TokenNotFound
        全部来源均无 token。
    """
    if explicit and explicit.strip():
        return explicit.strip()

    env = _from_env()
    if env:
        return env

    home = _from_file(HOME_TOKEN)
    if home:
        return home

    root = Path(project_root) if project_root else Path.cwd()
    proj = _from_file(root / ".tushare_token")
    if proj:
        return proj

    raise TokenNotFound(
        "未找到 Tushare token。请任选一种方式提供：\n"
        f"  1) 环境变量：export {ENV_VAR}=你的token\n"
        f"  2) 文件：{HOME_TOKEN}（内容为一行 token）\n"
        f"  3) 项目内 .tushare_token 文件（已被 .gitignore 排除）\n"
        "  4) 命令行：--token 你的token（仅临时使用，会出现在 shell 历史中）"
    )


def mask(token: str | None) -> str:
    """脱敏显示：仅保留首尾 4 位。"""
    if not token:
        return "<none>"
    t = str(token)
    if len(t) <= 10:
        return "*" * len(t)
    return f"{t[:4]}{'*' * (len(t) - 8)}{t[-4:]}"


def token_source(explicit: str | None = None,
                 project_root: Path | str | None = None) -> str:
    """返回当前 token 的来源名称（用于 doctor 输出，不含 token 内容）。"""
    if explicit and explicit.strip():
        return "cli"
    if _from_env():
        return f"env:{ENV_VAR}"
    if _from_file(HOME_TOKEN):
        return f"file:{HOME_TOKEN}"
    root = Path(project_root) if project_root else Path.cwd()
    if _from_file(root / ".tushare_token"):
        return "file:./.tushare_token"
    return "missing"
