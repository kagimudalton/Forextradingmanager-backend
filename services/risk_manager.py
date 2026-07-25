"""
risk_manager.py

Position sizing, exposure, and drawdown guardrails. Pure calculation module —
no side effects — so it's easy to unit test independent of MT5 or the DB.
"""
from __future__ import annotations

from typing import Optional


def position_size(balance: float, risk_percent: float, stop_loss_pips: float,
                   pip_value: float = 10.0) -> float:
    """
    Standard fixed-fractional position sizing.
    lots = (balance * risk_percent / 100) / (stop_loss_pips * pip_value)
    """
    if stop_loss_pips <= 0 or pip_value <= 0:
        return 0.0
    risk_amount = balance * (risk_percent / 100)
    lots = risk_amount / (stop_loss_pips * pip_value)
    return round(max(lots, 0.01), 2)


def calculate_stop_loss(entry_price: float, direction: str, atr_value: float,
                         multiplier: float = 1.5) -> float:
    offset = atr_value * multiplier
    if direction == "BUY":
        return round(entry_price - offset, 5)
    return round(entry_price + offset, 5)


def calculate_take_profit(entry_price: float, direction: str, atr_value: float,
                           reward_ratio: float = 2.0, multiplier: float = 1.5) -> float:
    offset = atr_value * multiplier * reward_ratio
    if direction == "BUY":
        return round(entry_price + offset, 5)
    return round(entry_price - offset, 5)


def exposure_level(open_trades: int, max_trades: int) -> str:
    if max_trades <= 0:
        return "UNKNOWN"
    ratio = open_trades / max_trades
    if ratio < 0.34:
        return "LOW"
    if ratio < 0.75:
        return "MEDIUM"
    return "HIGH"


def drawdown_percent(balance: float, equity: float) -> float:
    if balance <= 0:
        return 0.0
    dd = (balance - equity) / balance * 100
    return round(max(dd, 0.0), 2)


def daily_risk_used(today_loss: float, balance: float) -> float:
    if balance <= 0:
        return 0.0
    return round(abs(min(today_loss, 0)) / balance * 100, 2)


def check_trade_allowed(open_trades: int, max_trades: int, daily_loss_percent: float,
                         max_daily_loss_percent: float) -> tuple[bool, Optional[str]]:
    if open_trades >= max_trades:
        return False, f"Max open trades reached ({max_trades})"
    if daily_loss_percent >= max_daily_loss_percent:
        return False, f"Daily loss limit reached ({max_daily_loss_percent}%)"
    return True, None


def risk_status(balance: float, equity: float, open_trades: int, max_trades: int,
                 risk_percent: float, today_loss: float = 0.0,
                 max_daily_loss_percent: float = 5.0) -> dict:
    return {
        "daily_risk_percent": risk_percent,
        "daily_risk_used_percent": daily_risk_used(today_loss, balance),
        "exposure": exposure_level(open_trades, max_trades),
        "open_trades": open_trades,
        "max_trades": max_trades,
        "drawdown_percent": drawdown_percent(balance, equity),
        "max_daily_loss_percent": max_daily_loss_percent,
    }
