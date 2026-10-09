"""Shared arm contract.

An arm receives frozen FactSheets and returns Predictions. It has no access
to providers or the network; every prediction records the fingerprint of the
exact sheet it read, which is what verify_not_confounded checks.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..factsheet import FactSheet, FactSheetStore


@dataclass
class Message:
    agent: str
    content: str
    score: float | None = None  # agent's lean, -1 (bearish) .. +1 (bullish)


@dataclass
class Prediction:
    arm: str
    session: str
    symbol: str
    category: str
    direction: str  # "up" | "down" | "flat"; for polymarket "up" means YES is underpriced
    prob_up: float  # arm's probability that price rises (equity) or YES resolves (polymarket)
    confidence: float  # 0..1
    fact_sheet_fingerprint: str
    rationale: str
    transcript: list[Message] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Prediction":
        d = dict(d)
        d["transcript"] = [Message(**m) for m in d.get("transcript", [])]
        return cls(**d)


class Arm(ABC):
    name: str

    @abstractmethod
    def predict(self, sheet: FactSheet) -> Prediction: ...

    def run(self, store: FactSheetStore, session: str, symbols: list[str] | None = None) -> list[Prediction]:
        symbols = symbols or store.symbols(session)
        return [self.predict(store.get(session, s)) for s in symbols]


def direction_from(prob_up: float, band: float = 0.04) -> str:
    if prob_up > 0.5 + band:
        return "up"
    if prob_up < 0.5 - band:
        return "down"
    return "flat"


def write_log(path: str | Path, predictions: list[Prediction]) -> None:
    """Append predictions as JSON lines."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for p in predictions:
            f.write(json.dumps(p.to_dict(), sort_keys=True) + "\n")


def read_log(path: str | Path) -> list[Prediction]:
    path = Path(path)
    if not path.exists():
        return []
    return [Prediction.from_dict(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
