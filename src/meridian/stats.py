"""Paired statistics for the A/B comparison.

The arms read identical fact sheets, so every (session, symbol) pair yields a
*paired* observation. That matters: a market-wide move hits both arms equally
and cancels in the difference, so the paired difference is far less noisy than
either arm's absolute score. All the evidence about architecture therefore
lives in the pairs where the arms disagree.

Standard library only, so the trial has no dependency that could drift.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Sequence

from .arms.base import Prediction

Key = tuple[str, str]


# --------------------------------------------------------------------------
# Loss functions (lower is better)
# --------------------------------------------------------------------------


def brier(p: Prediction, up: bool) -> float:
    return (p.prob_up - (1.0 if up else 0.0)) ** 2


def log_loss(p: Prediction, up: bool, eps: float = 1e-6) -> float:
    q = min(max(p.prob_up, eps), 1 - eps)
    return -math.log(q if up else 1 - q)


# --------------------------------------------------------------------------
# Paired inference
# --------------------------------------------------------------------------


@dataclass
class PairedResult:
    n: int
    mean_a: float
    mean_b: float
    mean_diff: float  # A - B; negative favours A (lower loss is better)
    ci_low: float
    ci_high: float
    p_value: float
    better: str  # "A", "B" or "inconclusive"

    def line(self, label: str) -> str:
        return (
            f"{label:<22} n={self.n:<5} A={self.mean_a:.4f} B={self.mean_b:.4f} "
            f"diff={self.mean_diff:+.4f} [{self.ci_low:+.4f}, {self.ci_high:+.4f}] "
            f"p={self.p_value:.3f} -> {self.better}"
        )


def _bootstrap_ci(diffs: Sequence[float], iters: int = 20000, seed: int = 20261009,
                  alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap on the mean difference.

    Seeded so the published interval is reproducible from the logs.
    """
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(iters):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(alpha / 2 * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return lo, hi


def _t_p_value(diffs: Sequence[float]) -> float:
    """Two-sided p-value for mean(diffs) == 0, normal approximation."""
    n = len(diffs)
    if n < 2:
        return 1.0
    m = sum(diffs) / n
    var = sum((x - m) ** 2 for x in diffs) / (n - 1)
    if var <= 0:
        return 0.0 if m != 0 else 1.0
    t = m / math.sqrt(var / n)
    return 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))


def paired_loss(
    a: dict[Key, Prediction],
    b: dict[Key, Prediction],
    outcomes: dict[Key, dict],
    loss: Callable[[Prediction, bool], float] = brier,
    keys: Sequence[Key] | None = None,
) -> PairedResult | None:
    """Compare two arms on a paired loss over the keys both arms scored."""
    use = [k for k in (keys if keys is not None else sorted(set(a) & set(b)))
           if outcomes.get(k, {}).get("up") is not None]
    if not use:
        return None
    la = [loss(a[k], outcomes[k]["up"]) for k in use]
    lb = [loss(b[k], outcomes[k]["up"]) for k in use]
    diffs = [x - y for x, y in zip(la, lb)]
    mean_diff = sum(diffs) / len(diffs)
    lo, hi = _bootstrap_ci(diffs)
    p = _t_p_value(diffs)
    better = "inconclusive"
    if lo > 0:
        better = "B"
    elif hi < 0:
        better = "A"
    return PairedResult(
        n=len(use),
        mean_a=sum(la) / len(la),
        mean_b=sum(lb) / len(lb),
        mean_diff=mean_diff,
        ci_low=lo,
        ci_high=hi,
        p_value=p,
        better=better,
    )


def binom_two_sided(wins: int, n: int) -> float:
    """Exact two-sided sign-test p-value against p=0.5."""
    if n == 0:
        return 1.0
    def pmf(k: int) -> float:
        return math.comb(n, k) * 0.5 ** n
    target = pmf(wins)
    return min(1.0, sum(pmf(k) for k in range(n + 1) if pmf(k) <= target + 1e-15))


@dataclass
class HeadToHead:
    """Direction calls on the pairs where the arms disagree."""
    disagreements: int
    a_right: int
    b_right: int
    p_value: float

    def line(self) -> str:
        if self.disagreements == 0:
            return "head-to-head          no disagreements; the arms are indistinguishable on direction"
        return (
            f"head-to-head           disagree={self.disagreements:<5} "
            f"A right={self.a_right:<4} B right={self.b_right:<4} "
            f"p={self.p_value:.3f}"
        )


def head_to_head(a: dict[Key, Prediction], b: dict[Key, Prediction],
                 outcomes: dict[Key, dict]) -> HeadToHead:
    """Score only the pairs where the arms made different directional calls.

    Pairs where both arms agree carry no information about which architecture
    is better, so they are excluded. A "flat" call is treated as declining to
    bet: a disagreement needs one arm to call up and the other down.
    """
    a_right = b_right = 0
    for k in sorted(set(a) & set(b)):
        up = outcomes.get(k, {}).get("up")
        if up is None:
            continue
        da, db = a[k].direction, b[k].direction
        if da == db or "flat" in (da, db):
            continue
        if (da == "up") == up:
            a_right += 1
        else:
            b_right += 1
    decided = a_right + b_right
    return HeadToHead(decided, a_right, b_right, binom_two_sided(a_right, decided))


# --------------------------------------------------------------------------
# Single-arm diagnostics that 15 days CAN measure reliably
# --------------------------------------------------------------------------


@dataclass
class Calibration:
    bins: list[tuple[float, float, float, int]]  # (lo, hi, observed rate, n)
    error: float  # mean |predicted - observed|, weighted by bin count

    def line(self) -> str:
        return f"calibration error {self.error:.4f} over {sum(b[3] for b in self.bins)} scored calls"


def calibration(preds: Sequence[Prediction], outcomes: dict[Key, dict],
                edges: Sequence[float] = (0.0, 0.4, 0.45, 0.5, 0.55, 0.6, 1.01)) -> Calibration:
    """Are stated probabilities honest? A well-calibrated 60% should be right 60% of the time."""
    buckets: list[tuple[float, float, float, int]] = []
    total_w = 0.0
    total_err = 0.0
    for lo, hi in zip(edges, edges[1:]):
        rows = [
            (p, outcomes[(p.session, p.symbol)]["up"])
            for p in preds
            if lo <= p.prob_up < hi
            and outcomes.get((p.session, p.symbol), {}).get("up") is not None
        ]
        if not rows:
            continue
        observed = sum(1 for _, up in rows if up) / len(rows)
        predicted = sum(p.prob_up for p, _ in rows) / len(rows)
        buckets.append((lo, hi, round(observed, 4), len(rows)))
        total_err += abs(predicted - observed) * len(rows)
        total_w += len(rows)
    return Calibration(buckets, round(total_err / total_w, 4) if total_w else 0.0)


def abstention(preds: Sequence[Prediction], outcomes: dict[Key, dict]) -> dict:
    """Does abstaining actually avoid the hard cases?

    A committee that abstains well should be more accurate on the calls it does
    make than on the ones it skipped. If the skipped pairs would have been just
    as easy, the abstention is noise, not discipline.
    """
    called = skipped = called_right = 0
    for p in preds:
        up = outcomes.get((p.session, p.symbol), {}).get("up")
        if up is None:
            continue
        if p.direction == "flat":
            skipped += 1
        else:
            called += 1
            called_right += int((p.direction == "up") == up)
    return {
        "called": called,
        "skipped": skipped,
        "abstention_rate": round(skipped / (called + skipped), 4) if called + skipped else None,
        "hit_rate_on_calls": round(called_right / called, 4) if called else None,
    }


# --------------------------------------------------------------------------
# Power, so the decision rule is set with its limits known
# --------------------------------------------------------------------------


def power_hit_rate(n: int, true_edge: float, alpha: float = 0.05) -> float:
    """Chance of detecting a real directional edge over a coin flip at n calls."""
    if n <= 0:
        return 0.0
    se0 = math.sqrt(0.25 / n)
    se1 = math.sqrt(true_edge * (1 - true_edge) / n)
    crit = 0.5 + 1.959964 * se0
    return 1 - 0.5 * (1 + math.erf((crit - true_edge) / (se1 * math.sqrt(2))))


def min_n_for_power(true_edge: float, power: float = 0.80) -> int:
    n = 10
    while n < 100000 and power_hit_rate(n, true_edge) < power:
        n += 10
    return n
