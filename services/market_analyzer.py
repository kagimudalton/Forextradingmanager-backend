"""
market_analyzer.py

Rule-based technical analysis engine. This is the "AI Signals" brain for
Version 1 — deterministic indicator math (SMA/EMA crossovers, RSI, ATR-based
volatility, momentum) combined into a confidence score. It's structured so a
real ML model can be dropped in later behind the same `analyze_symbol()`
interface.
"""
from __future__ import annotations

import asyncio
from typing import List, Dict

from services.mt5_connector import connector

WATCHLIST = ["XAUUSD", "EURUSD", "NAS100", "GBPUSD", "BTCUSD"]


def sma(values: List[float], period: int) -> List[float]:
    out = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(values[i + 1 - period:i + 1]) / period)
    return out


def ema(values: List[float], period: int) -> List[float]:
    out = []
    k = 2 / (period + 1)
    prev = None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def atr(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, prev_close = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
    window = trs[-period:] if len(trs) >= period else trs
    return round(sum(window) / len(window), 5)


def detect_trend(closes: List[float]) -> str:
    fast = ema(closes, 8)[-1]
    slow = ema(closes, 21)[-1]
    if fast is None or slow is None:
        return "NEUTRAL"
    diff_pct = (fast - slow) / slow * 100 if slow else 0
    if diff_pct > 0.05:
        return "UP"
    if diff_pct < -0.05:
        return "DOWN"
    return "NEUTRAL"


def detect_volatility(candles: List[Dict]) -> str:
    a = atr(candles)
    last_close = candles[-1]["close"] if candles else 1
    atr_pct = (a / last_close) * 100 if last_close else 0
    if atr_pct < 0.15:
        return "LOW"
    if atr_pct < 0.4:
        return "MEDIUM"
    return "HIGH"


def calculate_confidence(closes: List[float], candles: List[Dict]) -> tuple[int, str, list[str]]:
    """Combine trend, RSI, and volatility into a direction + confidence score."""
    reasons = []
    trend = detect_trend(closes)
    r = rsi(closes)
    vol = detect_volatility(candles)

    score = 50
    direction = "HOLD"

    if trend == "UP":
        score += 20
        direction = "BUY"
        reasons.append("Trend alignment (EMA8 > EMA21)")
    elif trend == "DOWN":
        score += 20
        direction = "SELL"
        reasons.append("Trend alignment (EMA8 < EMA21)")
    else:
        reasons.append("No clear trend")

    if direction == "BUY" and r < 70:
        score += 10
        reasons.append("Momentum strength (RSI not overbought)")
    elif direction == "SELL" and r > 30:
        score += 10
        reasons.append("Momentum strength (RSI not oversold)")
    elif direction != "HOLD":
        score -= 15
        reasons.append("Momentum caution (RSI extreme)")

    if vol == "MEDIUM":
        score += 8
        reasons.append("Liquidity confirmation (healthy volatility)")
    elif vol == "LOW":
        score += 3
        reasons.append("Stable, low-volatility conditions")
    else:
        score -= 5
        reasons.append("High volatility - wider stops advised")

    score = max(0, min(99, score))
    return score, direction, reasons


async def analyze_symbol(symbol: str) -> dict:
    candles = await connector.get_rates(symbol, timeframe="M15", count=100)
    if not candles:
        return {"symbol": symbol, "direction": "HOLD", "confidence": 0, "risk": "UNKNOWN", "reason": ["No data"]}

    closes = [c["close"] for c in candles]
    confidence, direction, reasons = calculate_confidence(closes, candles)
    vol = detect_volatility(candles)
    risk = {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH"}[vol]

    return {
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "risk": risk,
        "reason": reasons,
        "price": closes[-1],
    }


async def analyze_watchlist(symbols: List[str] | None = None) -> List[dict]:
    symbols = symbols or WATCHLIST
    # Run all symbol lookups concurrently rather than one-by-one — each one
    # is a network call to MetaApi now, not a local mock lookup, so doing
    # them sequentially would make /api/signals noticeably slow.
    results = await asyncio.gather(*(analyze_symbol(s) for s in symbols))
    results = list(results)
    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results
