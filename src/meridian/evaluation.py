"""A/B evaluation: the confounding check and the scorecard."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .arms.base import Prediction
from .factsheet import FactSheetStore

Key = tuple[str, str]  # (session, symbol)


class ConfoundedError(AssertionError):
    pass


@dataclass
class ConfoundReport:
    checked: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues and self.checked > 0

    def summary(self) -> str:
        if self.ok:
            return f"OK: {self.checked} (session, symbol) pairs; both arms read identical fact sheets."
        head = f"CONFOUNDED: {len(self.issues)} issue(s) across {self.checked} pairs checked"
        return "\n".join([head] + [f"  - {i}" for i in self.issues[:50]])


def _index(preds: list[Prediction], arm: str, report: ConfoundReport) -> dict[Key, Prediction]:
    out: dict[Key, Prediction] = {}
    for p in preds:
        k = (p.session, p.symbol)
        if k in out and out[k].fact_sheet_fingerprint != p.fact_sheet_fingerprint:
            report.issues.append(f"arm {arm} has two different sheets for {k}")
        out[k] = p
    return out


def verify_not_confounded(
    preds_a: list[Prediction],
    preds_b: list[Prediction],
    store: FactSheetStore | None = None,
    raise_on_fail: bool = False,
) -> ConfoundReport:
    """Prove arm A and arm B saw the identical fact sheet per symbol per session.

    Checks that:
      1. both arms covered exactly the same (session, symbol) pairs;
      2. for each pair, both predictions carry the same fact sheet fingerprint;
      3. if a store is given, that fingerprint matches the frozen sheet on disk.
    """
    report = ConfoundReport()
    a = _index(preds_a, "A", report)
    b = _index(preds_b, "B", report)

    for k in sorted(set(a) - set(b)):
        report.issues.append(f"{k} predicted by arm A only")
    for k in sorted(set(b) - set(a)):
        report.issues.append(f"{k} predicted by arm B only")

    for k in sorted(set(a) & set(b)):
        report.checked += 1
        fa, fb = a[k].fact_sheet_fingerprint, b[k].fact_sheet_fingerprint
        if fa != fb:
            report.issues.append(f"{k}: fingerprints differ (A {fa[:12]}, B {fb[:12]})")
            continue
        if store is not None:
            try:
                frozen = store.get(*k).fingerprint
            except FileNotFoundError:
                report.issues.append(f"{k}: no frozen fact sheet in store")
                continue
            if frozen != fa:
                report.issues.append(f"{k}: arms agree but differ from frozen sheet {frozen[:12]}")

    if raise_on_fail and not report.ok:
        raise ConfoundedError(report.summary())
    return report


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def load_outcomes(path: str | Path) -> dict[Key, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[(row["session"], row["symbol"])] = row
    return out


def scorecard(preds: list[Prediction], outcomes: dict[Key, dict]) -> dict:
    """Hit rate on non-flat calls, Brier score on prob_up, coverage; overall and per category."""
    buckets: dict[str, list[tuple[Prediction, bool]]] = defaultdict(list)
    for p in preds:
        o = outcomes.get((p.session, p.symbol))
        if o is None or o.get("up") is None:
            continue
        buckets["all"].append((p, o["up"]))
        buckets[p.category].append((p, o["up"]))

    result = {}
    for cat, rows in buckets.items():
        calls = [(p, up) for p, up in rows if p.direction != "flat"]
        hits = sum(1 for p, up in calls if (p.direction == "up") == up)
        brier = sum((p.prob_up - (1.0 if up else 0.0)) ** 2 for p, up in rows) / len(rows)
        result[cat] = {
            "n": len(rows),
            "calls": len(calls),
            "hit_rate": round(hits / len(calls), 4) if calls else None,
            "brier": round(brier, 4),
        }
    return result
