# -*- coding: utf-8 -*-
"""
core/config.py — 配置加载与校验

职责：
  - 加载 YAML 配置
  - 提供点号访问（cfg.get("strategy.entry.pos120_max")）
  - 校验必填项与取值范围
  - 支持运行时覆盖
"""
from __future__ import annotations

import os
import copy
from pathlib import Path
from typing import Any, Iterable


class Config:
    """分层配置对象，支持点号路径访问。"""

    def __init__(self, data: dict | None = None, path: str | None = None):
        self._data = data or {}
        self.path = path

    # ---------------------------------------------------------------- 加载
    @classmethod
    def load(cls, path: str) -> "Config":
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError("需要 PyYAML：pip install pyyaml") from e
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        inst = cls(data, str(p))
        inst.validate()
        inst.resolve_paths()
        return inst

    # ---------------------------------------------------------------- 访问
    def get(self, key: str, default: Any = None) -> Any:
        """点号路径访问，如 get('strategy.entry.pos120_max')。"""
        cur = self._data
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set(self, key: str, value: Any) -> None:
        """点号路径写入。"""
        parts = key.split(".")
        cur = self._data
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    def section(self, key: str) -> dict:
        v = self.get(key)
        return v if isinstance(v, dict) else {}

    def as_dict(self) -> dict:
        return copy.deepcopy(self._data)

    def __repr__(self):
        return f"Config({self.path}, keys={list(self._data.keys())})"

    # ---------------------------------------------------------------- 校验
    def validate(self) -> None:
        errs = []
        mode = self.get("app.mode", "simulation")
        if mode not in ("simulation", "live"):
            errs.append(f"app.mode 必须为 simulation/live，当前 {mode}")

        s = self.get("strategy.entry", {})
        if not (0 < s.get("pos120_max", 0.3) <= 1):
            errs.append("strategy.entry.pos120_max 应在 (0,1]")
        if s.get("streak_min", 2) < 1:
            errs.append("strategy.entry.streak_min 应 >= 1")
        if s.get("bb_period", 20) < 5:
            errs.append("strategy.entry.bb_period 应 >= 5")

        e = self.get("strategy.exit", {})
        if e.get("stop_loss_pct", -0.12) >= 0:
            errs.append("strategy.exit.stop_loss_pct 应为负数")
        if e.get("max_hold_days", 60) < 1:
            errs.append("strategy.exit.max_hold_days 应 >= 1")

        p = self.get("strategy.portfolio", {})
        if p.get("max_positions", 5) < 1:
            errs.append("strategy.portfolio.max_positions 应 >= 1")
        if not (0 < p.get("max_position_pct", 0.25) <= 1):
            errs.append("strategy.portfolio.max_position_pct 应在 (0,1]")

        if errs:
            raise ValueError("配置校验失败:\n  - " + "\n  - ".join(errs))

    def resolve_paths(self) -> None:
        """把相对路径转为绝对路径（基于配置文件所在目录）。"""
        base = Path(self.path).parent.parent if self.path else Path.cwd()
        for key in ("datasource.cache_dir", "logging.dir"):
            v = self.get(key)
            if v and not os.path.isabs(v):
                self.set(key, str((base / v).resolve()))

    # ---------------------------------------------------------------- 便捷
    @property
    def mode(self) -> str:
        return self.get("app.mode", "simulation")

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def qmt_enabled(self) -> bool:
        return bool(self.get("qmt.enabled", False))

    def to_strategy_cfg(self) -> dict:
        """导出策略模块需要的配置结构（兼容 strategy/lib/data.py 的接口）。"""
        return {
            "entry": self.section("strategy.entry"),
            "exit": self.section("strategy.exit"),
            "portfolio": self.section("strategy.portfolio"),
            "universe": self.section("universe"),
            "cost": self.section("strategy.cost"),
            "backtest": {
                "start_date": self.get("datasource.backtest_start", "2015-01-01"),
                "benchmark": "000852.SH",
                "risk_free_rate": 0.02,
            },
        }


def load_config(path: str | None = None) -> Config:
    """默认加载 config/config.yaml。"""
    if path is None:
        here = Path(__file__).resolve().parent.parent
        path = str(here / "config" / "config.yaml")
    return Config.load(path)
