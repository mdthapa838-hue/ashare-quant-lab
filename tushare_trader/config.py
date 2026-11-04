# -*- coding: utf-8 -*-
"""
tushare_trader/config.py — 配置加载与校验

配置全部来自 YAML（`config/system.yaml`），代码不硬编码参数。
token 不写入配置文件，见 `credentials.py`。
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "system.yaml"


class Config:
    """分层配置对象，支持点号路径访问。"""

    def __init__(self, data: dict | None = None, path: str | None = None):
        self._data = data or {}
        self.path = path

    # ---------------------------------------------------------------- 加载
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        try:
            import yaml
        except ImportError as e:  # pragma: no cover - 依赖缺失时的提示
            raise RuntimeError("需要 PyYAML：pip install pyyaml") from e
        p = Path(path) if path else DEFAULT_CONFIG
        if not p.exists():
            raise FileNotFoundError(f"配置文件不存在: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        inst = cls(data, str(p))
        inst.validate()
        inst.resolve_paths()
        return inst

    # ---------------------------------------------------------------- 访问
    def get(self, key: str, default: Any = None) -> Any:
        cur = self._data
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        cur = self._data
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[parts[-1]] = value

    def section(self, key: str) -> dict:
        v = self.get(key)
        return v if isinstance(v, dict) else {}

    def as_dict(self) -> dict:
        return copy.deepcopy(self._data)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"Config({self.path}, keys={sorted(self._data)})"

    # ---------------------------------------------------------------- 校验
    def validate(self) -> None:
        errs: list[str] = []

        mode = self.get("app.mode", "simulation")
        if mode not in ("backtest", "simulation", "live"):
            errs.append(f"app.mode 必须为 backtest/simulation/live，当前 {mode}")

        e = self.get("strategy.entry", {})
        if not 0 < float(e.get("pos120_max", 0.30)) <= 1:
            errs.append("strategy.entry.pos120_max 应在 (0,1]")
        if int(e.get("streak_min", 2)) < 1:
            errs.append("strategy.entry.streak_min 应 >= 1")
        if int(e.get("bb_period", 20)) < 5:
            errs.append("strategy.entry.bb_period 应 >= 5")

        x = self.get("strategy.exit", {})
        if float(x.get("stop_loss_pct", -0.12)) >= 0:
            errs.append("strategy.exit.stop_loss_pct 必须为负数")
        if int(x.get("max_hold_days", 60)) < 1:
            errs.append("strategy.exit.max_hold_days 应 >= 1")
        if x.get("take_profit", "upper") not in ("upper", "mid"):
            errs.append("strategy.exit.take_profit 必须为 upper 或 mid")
        if x.get("stop_mode", "fixed") not in ("fixed", "atr", "entry_low"):
            errs.append("strategy.exit.stop_mode 必须为 fixed/atr/entry_low")

        p = self.get("strategy.portfolio", {})
        if int(p.get("max_positions", 5)) < 1:
            errs.append("strategy.portfolio.max_positions 应 >= 1")
        if not 0 < float(p.get("max_position_pct", 0.25)) <= 1:
            errs.append("strategy.portfolio.max_position_pct 应在 (0,1]")
        if float(p.get("initial_capital", 1_000_000)) <= 0:
            errs.append("strategy.portfolio.initial_capital 应 > 0")

        u = self.get("universe", {})
        if float(u.get("market_cap_min", 30)) >= float(u.get("market_cap_max", 80)):
            errs.append("universe.market_cap_min 应小于 market_cap_max")

        if errs:
            raise ValueError("配置校验失败:\n  - " + "\n  - ".join(errs))

    def resolve_paths(self) -> None:
        """把相对路径解析为绝对路径（基于仓库根目录，即配置文件的上两级）。"""
        base = Path(self.path).resolve().parent.parent if self.path else Path.cwd()
        for key in ("datasource.cache_dir", "logging.dir", "output.dir"):
            v = self.get(key)
            if isinstance(v, str) and v and not os.path.isabs(v):
                self.set(key, str((base / v).resolve()))

    # ---------------------------------------------------------------- 便捷
    @property
    def mode(self) -> str:
        return self.get("app.mode", "simulation")

    @property
    def data_cfg(self) -> dict:
        return self.section("datasource.tushare")

    @property
    def strategy_cfg(self) -> dict:
        """策略模块需要的配置结构（entry/exit/portfolio/universe/cost/risk）。"""
        return {
            "entry": self.section("strategy.entry"),
            "exit": self.section("strategy.exit"),
            "portfolio": self.section("strategy.portfolio"),
            "cost": self.section("strategy.cost"),
            "risk": self.section("strategy.risk"),
            "universe": self.section("universe"),
        }


def load_config(path: str | os.PathLike | None = None) -> Config:
    return Config.load(path)
