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
    preds_a: list[Prediction] | dict[str, list[Prediction]],
    preds_b: list[Prediction] | None = None,
    store: FactSheetStore | None = None,
    raise_on_fail: bool = False,
) -> ConfoundReport:
    """Prove every arm saw the identical fact sheet per symbol per session.

    Call it either with two arms' predictions, or with a {arm: predictions}
    mapping to check the whole trial at once.

    Checks that:
      1. all arms covered exactly the same (session, symbol) pairs;
      2. for each pair, every arm's prediction carries the same fingerprint;
      3. if a store is given, that fingerprint matches the frozen sheet on disk.
    """
    report = ConfoundReport()

    if isinstance(preds_a, dict):
        by_arm = {name: p for name, p in preds_a.items()}
    else:
        by_arm = {"A": preds_a, "B": preds_b or []}
    if len(by_arm) < 2:
        report.issues.append("need at least two arms to check for confounding")
        if raise_on_fail:
            raise ConfoundedError(report.summary())
        return report

    indexed = {name: _index(preds, name, report) for name, preds in by_arm.items()}
    # The reference arm is the one covering the most pairs, so a missing pair in
    # any arm is reported rather than silently shrinking the comparison set.
    universe: set[Key] = set()
    for idx in indexed.values():
        universe |= set(idx)
    common = set.intersection(*(set(idx) for idx in indexed.values())) if indexed else set()

    for k in sorted(universe - common):
        present = sorted(n for n, idx in indexed.items() if k in idx)
        missing = sorted(n for n, idx in indexed.items() if k not in idx)
        report.issues.append(
            f"{k} predicted by {', '.join(present)} but not by {', '.join(missing)}"
        )

    for k in sorted(common):
        report.checked += 1
        prints = {n: idx[k].fact_sheet_fingerprint for n, idx in indexed.items()}
        distinct = sorted(set(prints.values()))
        if len(distinct) > 1:
            detail = ", ".join(f"{n} {fp[:12]}" for n, fp in sorted(prints.items()))
            report.issues.append(f"{k}: fingerprints differ ({detail})")
            continue
        if store is not None:
            try:
                frozen = store.get(*k).fingerprint
            except FileNotFoundError:
                report.issues.append(f"{k}: no frozen fact sheet in store")
                continue
            if frozen != distinct[0]:
                report.issues.append(
                    f"{k}: arms agree but differ from frozen sheet {frozen[:12]}"
                )

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


# --------------------------------------------------------------------------
# The pre-registered decision rule (see PREREGISTRATION.md)
# --------------------------------------------------------------------------


@dataclass
class Verdict:
    winner: str  # "A", "B", "neither" or "inconclusive"
    reason: str
    gates: list[str] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)

    def summary(self) -> str:
        out = [f"VERDICT: {self.winner} -- {self.reason}", ""]
        out += [f"  gate: {g}" for g in self.gates]
        if self.detail:
            out += [""] + [f"  {d}" for d in self.detail]
        return "\n".join(out)


def decide(
    by_arm: dict[str, list[Prediction]],
    outcomes: dict[Key, dict],
    store: FactSheetStore | None = None,
    min_calibration_margin: float = 0.02,
) -> Verdict:
    """Apply the pre-registered decision rule to the trial logs.

    The order of tests is fixed in advance so the outcome cannot be chosen
    after seeing the data. Nothing here is allowed to look at an arm's
    identity except to report it.
    """
    from . import stats as st

    gates: list[str] = []
    detail: list[str] = []

    # Gate 0: the comparison must not be confounded.
    report = verify_not_confounded(by_arm, store=store)
    if not report.ok:
        return Verdict("inconclusive", "the arms did not read identical fact sheets",
                       gates=["FAIL confound check"], detail=report.summary().splitlines())
    gates.append(f"PASS confound check ({report.checked} pairs on identical sheets)")

    idx = {name: {(p.session, p.symbol): p for p in preds} for name, preds in by_arm.items()}
    scored = sum(1 for k in idx.get("A", {}) if outcomes.get(k, {}).get("up") is not None)
    if scored == 0:
        return Verdict("inconclusive", "no realized outcomes recorded yet", gates=gates)
    gates.append(f"PASS outcomes available ({scored} scored pairs)")

    # Gate 1: a contender must beat *every* baseline, not just the easiest one.
    # "single" tests whether the committee beats one indicator; "drift" tests
    # whether it beats having no signal at all. An arm that loses to either is
    # not worth running, however it compares to the other arm.
    baselines = [b for b in ("single", "drift") if b in idx]
    beat_all: list[str] = []
    for arm in ("A", "B"):
        if arm not in idx:
            continue
        lost_to = []
        for b in baselines:
            r = st.paired_loss(idx[arm], idx[b], outcomes)
            if r is None:
                continue
            detail.append(r.line(f"brier {arm} vs {b}"))
            if r.ci_high >= 0:  # not significantly better than this baseline
                lost_to.append(b)
        if baselines and not lost_to:
            beat_all.append(arm)
        elif lost_to:
            gates.append(f"NOTE arm {arm} does not beat: {', '.join(lost_to)}")
    if baselines and not beat_all:
        gates.append("FAIL no arm beats every baseline")
        return Verdict(
            "neither",
            "no committee beat the trivial baselines on the same data, so the "
            "multi-agent architecture is not yet justified whichever arm scores "
            "higher between the two",
            gates=gates, detail=detail,
        )
    if beat_all:
        gates.append(f"PASS beats every baseline: {', '.join(beat_all)}")
        if len(beat_all) == 1:
            # Only one arm clears the baselines, so the choice is already made
            # and the A-vs-B test can only confirm it.
            only = beat_all[0]
            gates.append(f"PASS only arm {only} clears the baselines")
            return Verdict(
                only,
                f"the only arm that beat every baseline ({', '.join(baselines)})",
                gates=gates, detail=detail,
            )

    if not {"A", "B"} <= set(idx):
        return Verdict("inconclusive", "both arms A and B are needed to decide", gates=gates)

    # Primary test: paired Brier score over every scored pair.
    primary = st.paired_loss(idx["A"], idx["B"], outcomes, loss=st.brier)
    if primary is None:
        return Verdict("inconclusive", "no paired observations to compare", gates=gates)
    detail.append(primary.line("PRIMARY brier A vs B"))
    if primary.better in ("A", "B"):
        gates.append("PASS primary test is decisive")
        return Verdict(primary.better,
                       f"lower Brier score on the primary paired test "
                       f"(diff {primary.mean_diff:+.4f}, 95% CI "
                       f"[{primary.ci_low:+.4f}, {primary.ci_high:+.4f}])",
                       gates=gates, detail=detail)
    gates.append("INCONCLUSIVE primary test: 95% CI straddles zero")

    # Secondary tests, in pre-registered order.
    h = st.head_to_head(idx["A"], idx["B"], outcomes)
    detail.append(h.line())
    if h.disagreements >= 20 and h.p_value < 0.05:
        winner = "A" if h.a_right > h.b_right else "B"
        return Verdict(winner,
                       f"won the direction calls where the arms disagreed "
                       f"({h.a_right}-{h.b_right} of {h.disagreements}, p={h.p_value:.3f})",
                       gates=gates + ["PASS secondary 1: head-to-head"], detail=detail)
    gates.append("INCONCLUSIVE secondary 1: head-to-head")

    cal = {arm: st.calibration(by_arm[arm], outcomes) for arm in ("A", "B")}
    for arm, c in cal.items():
        detail.append(f"calibration {arm}          {c.line()}")
    gap = cal["A"].error - cal["B"].error
    if abs(gap) >= min_calibration_margin:
        winner = "A" if gap < 0 else "B"
        return Verdict(winner,
                       f"better calibrated probabilities (error {cal[winner].error:.4f} vs "
                       f"{cal['B' if winner == 'A' else 'A'].error:.4f})",
                       gates=gates + ["PASS secondary 2: calibration"], detail=detail)
    gates.append("INCONCLUSIVE secondary 2: calibration")

    for arm in ("A", "B"):
        detail.append(f"abstention {arm}           {st.abstention(by_arm[arm], outcomes)}")

    return Verdict(
        "inconclusive",
        "no pre-registered test separated the arms; extend the trial or widen "
        "the universe rather than picking on an unresolved margin",
        gates=gates, detail=detail,
    )
