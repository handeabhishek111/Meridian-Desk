"""Arm A: TradingAgents-style pipeline.

Analyst team -> bull/bear researchers debate -> research manager ->
trader -> risk team (aggressive / neutral / conservative) -> portfolio manager.
Each stage reads only the previous stage's output and the fact sheet.
"""

from __future__ import annotations

from ..factsheet import FactSheet
from .. import indicators as ind
from .base import Arm, Message, Prediction, direction_from


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class ArmA(Arm):
    name = "A"

    def __init__(self, debate_rounds: int = 2):
        self.debate_rounds = debate_rounds

    # ---- analyst team --------------------------------------------------
    def _analysts(self, s: FactSheet) -> list[Message]:
        if s.category == "polymarket":
            m = s.market
            p = m["yes_price"]
            return [
                Message("market_analyst", f"YES at {p:.3f}, 1d change {m['one_day_change']:+.3f}",
                        _clip(m["one_day_change"] * 10)),
                Message("liquidity_analyst", f"liquidity {m['liquidity']:.0f}, 24h vol {m['volume_24h']:.0f}",
                        0.0),
                Message("contrarian_analyst", "extreme prices tend to overshoot",
                        _clip((0.5 - p) * 0.6)),
            ]
        c = s.closes
        mom = ind.momentum(c, 10)
        trend = c[-1] / ind.sma(c, 20) - 1
        r = ind.rsi(c)
        tech = _clip(mom * 8 + trend * 6 - max(0, r - 70) / 30 + max(0, 30 - r) / 30)
        f = s.fundamentals
        fund = 0.0
        if f:
            fund = _clip((25 - f.get("pe", 25)) / 25 * 0.5 + (f.get("roe", 0.15) - 0.15) * 3
                         - (f.get("debt_to_equity", 0.5) - 0.5) * 0.4)
        news = ind.headline_sentiment(s)
        return [
            Message("market_analyst", f"10d momentum {mom:+.2%}, vs SMA20 {trend:+.2%}, RSI {r:.0f}", tech),
            Message("fundamentals_analyst", f"fundamentals {f or 'n/a'}", fund),
            Message("news_analyst", f"{len(s.headlines)} headlines, sentiment {news:+.2f}", news),
        ]

    # ---- research debate ----------------------------------------------
    def _debate(self, reports: list[Message]) -> tuple[float, list[Message]]:
        bull_pts = sum(m.score for m in reports if m.score and m.score > 0)
        bear_pts = -sum(m.score for m in reports if m.score and m.score < 0)
        msgs = []
        for rnd in range(1, self.debate_rounds + 1):
            # Each side concedes a fraction of its case to the other's strongest point.
            bull_pts, bear_pts = bull_pts - 0.15 * bear_pts, bear_pts - 0.15 * bull_pts
            bull_pts, bear_pts = max(bull_pts, 0.0), max(bear_pts, 0.0)
            msgs.append(Message("bull_researcher", f"round {rnd}: case strength {bull_pts:.2f}", bull_pts))
            msgs.append(Message("bear_researcher", f"round {rnd}: case strength {bear_pts:.2f}", -bear_pts))
        verdict = _clip(bull_pts - bear_pts)
        msgs.append(Message("research_manager", f"net view {verdict:+.2f}", verdict))
        return verdict, msgs

    # ---- trader, risk, PM ---------------------------------------------
    def predict(self, sheet: FactSheet) -> Prediction:
        transcript = self._analysts(sheet)
        verdict, debate = self._debate(transcript)
        transcript += debate

        trader_lean = verdict
        transcript.append(Message("trader", f"proposes lean {trader_lean:+.2f}", trader_lean))

        vol = ind.volatility(sheet.closes) if sheet.category == "equity" else 0.02
        risk_scale = {"aggressive": 1.0, "neutral": 0.7, "conservative": max(0.2, 0.6 - vol * 15)}
        for who, k in risk_scale.items():
            transcript.append(Message(f"risk_{who}", f"sizing x{k:.2f}", trader_lean * k))
        final_lean = trader_lean * sum(risk_scale.values()) / len(risk_scale)

        if sheet.category == "polymarket":
            prob_up = _clip(sheet.market["yes_price"] + final_lean * 0.15, 0.01, 0.99)
        else:
            prob_up = ind.sigmoid(final_lean * 1.5)
        confidence = min(1.0, abs(final_lean))
        direction = direction_from(prob_up)
        transcript.append(Message("portfolio_manager", f"final {direction} p={prob_up:.3f}", final_lean))
        return Prediction(
            arm=self.name,
            session=sheet.session,
            symbol=sheet.symbol,
            category=sheet.category,
            direction=direction,
            prob_up=round(prob_up, 4),
            confidence=round(confidence, 4),
            fact_sheet_fingerprint=sheet.fingerprint,
            rationale=f"research manager net {verdict:+.2f}, risk-adjusted {final_lean:+.2f}",
            transcript=transcript,
        )
