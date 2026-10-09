"""Arm B: Meridian committee.

Specialists vote independently (no debate, no sight of each other's views),
each giving a probability and a confidence. The chair pools votes in log-odds
space weighted by confidence, records dissent, and abstains to "flat" when
the committee is split.
"""

from __future__ import annotations

import math
import statistics

from ..factsheet import FactSheet
from .. import indicators as ind
from .base import Arm, Message, Prediction, direction_from


class ArmB(Arm):
    name = "B"

    def __init__(self, split_threshold: float = 0.9):
        self.split_threshold = split_threshold  # stdev of member logits that forces abstain

    def _votes(self, s: FactSheet) -> list[tuple[str, float, float, str]]:
        """(member, prob_up, confidence, note)"""
        if s.category == "polymarket":
            m = s.market
            p = m["yes_price"]
            liq_conf = min(1.0, math.log10(1 + m["liquidity"]) / 6)
            return [
                ("price_discovery", p, 0.8, "market price is the prior"),
                ("flow_reader", min(0.99, max(0.01, p + m["one_day_change"] * 0.5)), 0.4 * liq_conf + 0.1,
                 f"1d change {m['one_day_change']:+.3f}"),
                ("base_rate", 0.5 + (p - 0.5) * 0.8, 0.3, "shrink toward 50%"),
            ]
        c = s.closes
        r = ind.rsi(c)
        mom = ind.momentum(c, 10)
        trend = c[-1] / ind.sma(c, 50) - 1
        vol = ind.volatility(c)
        votes = [
            ("trend_follower", ind.sigmoid(trend * 10), min(1.0, abs(trend) * 10 + 0.2), f"vs SMA50 {trend:+.2%}"),
            ("momentum", ind.sigmoid(mom * 12), min(1.0, abs(mom) * 8 + 0.2), f"10d {mom:+.2%}"),
            ("mean_reversion", ind.sigmoid((50 - r) / 20), min(1.0, abs(r - 50) / 40 + 0.1), f"RSI {r:.0f}"),
            ("risk_officer", 0.5, min(1.0, vol * 40), f"vol {vol:.2%}, pulls toward neutral"),
        ]
        if s.fundamentals:
            f = s.fundamentals
            val = (25 - f.get("pe", 25)) / 25 + (f.get("roe", 0.15) - 0.15) * 4
            votes.append(("value", ind.sigmoid(val), 0.3, f"PE {f.get('pe')}, ROE {f.get('roe')}"))
        if s.headlines:
            sent = ind.headline_sentiment(s)
            votes.append(("news_desk", ind.sigmoid(sent * 1.2), 0.15 + 0.1 * min(len(s.headlines), 5) / 5,
                          f"sentiment {sent:+.2f}"))
        return votes

    def predict(self, sheet: FactSheet) -> Prediction:
        votes = self._votes(sheet)
        transcript = [Message(name, f"p_up={p:.3f} conf={w:.2f} ({note})", p * 2 - 1) for name, p, w, note in votes]

        total_w = sum(w for _, _, w, _ in votes) or 1.0
        pooled = sum(ind.logit(p) * w for _, p, w, _ in votes) / total_w
        spread = statistics.pstdev([ind.logit(p) for _, p, _, _ in votes]) if len(votes) > 1 else 0.0
        prob_up = ind.sigmoid(pooled)
        dissenters = [n for n, p, _, _ in votes if (p - 0.5) * (prob_up - 0.5) < 0]

        if spread > self.split_threshold:
            direction = "flat"
            note = f"committee split (spread {spread:.2f}); chair abstains"
        else:
            direction = direction_from(prob_up)
            note = f"pooled p={prob_up:.3f}, spread {spread:.2f}"
        confidence = max(0.0, min(1.0, abs(prob_up - 0.5) * 2 * (1 - spread / 2)))
        transcript.append(Message("chair", f"{note}; dissent: {', '.join(dissenters) or 'none'}", prob_up * 2 - 1))

        return Prediction(
            arm=self.name,
            session=sheet.session,
            symbol=sheet.symbol,
            category=sheet.category,
            direction=direction,
            prob_up=round(prob_up, 4),
            confidence=round(confidence, 4),
            fact_sheet_fingerprint=sheet.fingerprint,
            rationale=note,
            transcript=transcript,
        )
