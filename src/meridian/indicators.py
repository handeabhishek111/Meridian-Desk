"""Pure functions over a FactSheet. No I/O, so both arms may share them."""

from __future__ import annotations

import math
import statistics

from .factsheet import FactSheet

POSITIVE = {
    "beat", "beats", "surge", "surges", "record", "upgrade", "upgrades", "profit",
    "growth", "wins", "win", "order", "orders", "expands", "approval", "strong", "rally",
}
NEGATIVE = {
    "miss", "misses", "falls", "fall", "plunge", "downgrade", "downgrades", "loss",
    "probe", "penalty", "weak", "cut", "cuts", "fraud", "default", "slump", "raid",
}


def returns(closes: tuple[float, ...]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]


def sma(closes: tuple[float, ...], n: int) -> float:
    window = closes[-n:] if len(closes) >= n else closes
    return sum(window) / len(window)


def rsi(closes: tuple[float, ...], n: int = 14) -> float:
    r = returns(closes)[-n:]
    gains = sum(x for x in r if x > 0)
    losses = -sum(x for x in r if x < 0)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def volatility(closes: tuple[float, ...], n: int = 20) -> float:
    r = returns(closes)[-n:]
    return statistics.pstdev(r) if len(r) > 1 else 0.0


def momentum(closes: tuple[float, ...], n: int = 10) -> float:
    if len(closes) <= n:
        return 0.0
    return closes[-1] / closes[-1 - n] - 1


def headline_sentiment(sheet: FactSheet) -> float:
    """Lexicon score in [-1, 1]; 0 when there is no news."""
    pos = neg = 0
    for h in sheet.headlines:
        words = {w.strip(".,:;!?'\"()").lower() for w in h.split()}
        pos += len(words & POSITIVE)
        neg += len(words & NEGATIVE)
    total = pos + neg
    return 0.0 if total == 0 else (pos - neg) / total


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))
