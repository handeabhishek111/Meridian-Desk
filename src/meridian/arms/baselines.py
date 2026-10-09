"""Baseline arms. Without these, the trial can only say which of A and B is
better, not whether either is worth running.

Both baselines read the same frozen fact sheets as A and B, so they enter the
same paired comparison on identical inputs.
"""

from __future__ import annotations

from ..factsheet import FactSheet
from .. import indicators as ind
from .base import Arm, Message, Prediction, direction_from


class ArmDrift(Arm):
    """Always long at the unconditional up-rate. Beats any model with no signal.

    If neither committee beats this, the committees are adding nothing but cost.
    """

    name = "drift"

    def __init__(self, up_rate: float = 0.51):
        self.up_rate = up_rate

    def predict(self, sheet: FactSheet) -> Prediction:
        p = self.up_rate
        return Prediction(
            arm=self.name,
            session=sheet.session,
            symbol=sheet.symbol,
            category=sheet.category,
            direction=direction_from(p),
            prob_up=p,
            confidence=0.0,
            fact_sheet_fingerprint=sheet.fingerprint,
            rationale=f"unconditional up-rate {p:.2f}, ignores the fact sheet",
            transcript=[Message("drift", f"p_up={p:.3f}", 0.0)],
        )


class ArmSingle(Arm):
    """One momentum rule, no committee, no debate.

    This is the arm to beat: if the multi-agent machinery in A and B cannot
    beat a single indicator on the same data, the architecture is not earning
    its complexity.
    """

    name = "single"

    def predict(self, sheet: FactSheet) -> Prediction:
        if sheet.category == "polymarket":
            p = sheet.market["yes_price"]
            note = "market price unchanged"
        else:
            mom = ind.momentum(sheet.closes, 10)
            p = ind.sigmoid(mom * 12)
            note = f"10d momentum {mom:+.2%}"
        return Prediction(
            arm=self.name,
            session=sheet.session,
            symbol=sheet.symbol,
            category=sheet.category,
            direction=direction_from(p),
            prob_up=round(p, 4),
            confidence=round(min(1.0, abs(p - 0.5) * 2), 4),
            fact_sheet_fingerprint=sheet.fingerprint,
            rationale=note,
            transcript=[Message("single_rule", note, p * 2 - 1)],
        )
