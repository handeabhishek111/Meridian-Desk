"""The fact sheet: the single source of inputs both arms read.

A fact sheet is built once per (session, symbol) by the shared data pipeline,
frozen to disk, and fingerprinted. Arms never fetch data themselves; they load
the frozen sheet from the store. That is what keeps the A/B trial a comparison
of committee architectures rather than of data pipelines.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FactSheet:
    session: str  # trading session date, YYYY-MM-DD
    symbol: str  # e.g. "RELIANCE.NS" or "polymarket:<slug>"
    category: str  # "equity" or "polymarket"
    as_of: str  # ISO timestamp of the last datapoint included
    source: str  # provider name, recorded for audit
    closes: tuple[float, ...] = ()  # oldest -> newest daily closes
    volumes: tuple[float, ...] = ()
    fundamentals: dict[str, Any] = field(default_factory=dict)
    headlines: tuple[str, ...] = ()
    market: dict[str, Any] = field(default_factory=dict)  # polymarket fields
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["closes"] = list(self.closes)
        d["volumes"] = list(self.volumes)
        d["headlines"] = list(self.headlines)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FactSheet":
        return cls(
            session=d["session"],
            symbol=d["symbol"],
            category=d["category"],
            as_of=d["as_of"],
            source=d["source"],
            closes=tuple(d.get("closes", ())),
            volumes=tuple(d.get("volumes", ())),
            fundamentals=dict(d.get("fundamentals", {})),
            headlines=tuple(d.get("headlines", ())),
            market=dict(d.get("market", {})),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


class FactSheetStore:
    """Write-once store at <root>/<session>/<safe symbol>.json.

    The first write for a (session, symbol) wins. Rewriting with different
    content raises, so neither arm can ever see a sheet the other did not.
    """

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def path_for(self, session: str, symbol: str) -> Path:
        safe = symbol.replace(":", "__").replace("/", "_")
        return self.root / session / f"{safe}.json"

    def put(self, sheet: FactSheet) -> str:
        path = self.path_for(sheet.session, sheet.symbol)
        if path.exists():
            existing = self.get(sheet.session, sheet.symbol)
            if existing.fingerprint != sheet.fingerprint:
                raise FileExistsError(
                    f"Fact sheet for {sheet.symbol} on {sheet.session} is already "
                    f"frozen with a different fingerprint; refusing to overwrite."
                )
            return existing.fingerprint
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(sheet.canonical_json())
        os.replace(tmp, path)
        return sheet.fingerprint

    def get(self, session: str, symbol: str) -> FactSheet:
        path = self.path_for(session, symbol)
        return FactSheet.from_dict(json.loads(path.read_text()))

    def symbols(self, session: str) -> list[str]:
        folder = self.root / session
        if not folder.exists():
            return []
        return sorted(
            FactSheet.from_dict(json.loads(p.read_text())).symbol
            for p in folder.glob("*.json")
        )
