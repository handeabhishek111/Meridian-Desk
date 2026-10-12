"""Data providers. Only the shared pipeline (run.py prepare) calls these.

Arms must never import this module: they read frozen fact sheets instead.
A test enforces that rule.

Providers return data strictly *before* the session date, so a prediction for
session D can be scored against D's close without look-ahead.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import urllib.parse
import urllib.request
from typing import Protocol

from .factsheet import FactSheet

USER_AGENT = "Mozilla/5.0 (meridian-desk)"


class Provider(Protocol):
    name: str

    def fact_sheet(self, session: str, symbol: str) -> FactSheet: ...

    def realized_close(self, session: str, symbol: str) -> float | None: ...


def _get_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# --------------------------------------------------------------------------
# Sample provider: deterministic synthetic data, for tests and offline runs.
# --------------------------------------------------------------------------


class SampleProvider:
    name = "sample"

    def __init__(self, lookback: int = 60):
        self.lookback = lookback

    def _rng(self, symbol: str) -> random.Random:
        seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:12], 16)
        return random.Random(seed)

    def _series(self, symbol: str, until: dt.date) -> list[tuple[dt.date, float, float]]:
        rng = self._rng(symbol)
        start = dt.date(2026, 1, 1)
        price = 500 + rng.random() * 2500
        drift = (rng.random() - 0.5) * 0.002
        out = []
        day = start
        while day <= until:
            if day.weekday() < 5:
                price *= 1 + drift + rng.gauss(0, 0.015)
                out.append((day, round(price, 2), float(int(1e6 * (0.5 + rng.random())))))
            day += dt.timedelta(days=1)
        return out

    def fact_sheet(self, session: str, symbol: str) -> FactSheet:
        d = dt.date.fromisoformat(session)
        if symbol.startswith("polymarket:"):
            rng = random.Random(f"{symbol}|{session}")
            p = round(0.1 + rng.random() * 0.8, 3)
            return FactSheet(
                session=session,
                symbol=symbol,
                category="polymarket",
                as_of=f"{(d - dt.timedelta(days=1)).isoformat()}T23:59:00Z",
                source=self.name,
                market={
                    "question": f"Sample market {symbol.split(':', 1)[1]}",
                    "yes_price": p,
                    "one_day_change": round((rng.random() - 0.5) * 0.1, 3),
                    "volume_24h": round(rng.random() * 50000, 2),
                    "liquidity": round(rng.random() * 100000, 2),
                    "end_date": (d + dt.timedelta(days=30)).isoformat(),
                },
            )
        series = [row for row in self._series(symbol, d) if row[0] < d][-self.lookback:]
        rng = self._rng(symbol + "|fund")
        return FactSheet(
            session=session,
            symbol=symbol,
            category="equity",
            as_of=f"{series[-1][0].isoformat()}T15:30:00+05:30",
            source=self.name,
            closes=tuple(r[1] for r in series),
            volumes=tuple(r[2] for r in series),
            fundamentals={
                "pe": round(10 + rng.random() * 40, 1),
                "pb": round(1 + rng.random() * 8, 2),
                "roe": round(0.05 + rng.random() * 0.25, 3),
                "debt_to_equity": round(rng.random() * 1.5, 2),
            },
            headlines=(f"{symbol} sample headline for {session}",),
        )

    def realized_close(self, session: str, symbol: str) -> float | None:
        if symbol.startswith("polymarket:"):
            return None
        d = dt.date.fromisoformat(session)
        rows = [r for r in self._series(symbol, d) if r[0] == d]
        return rows[0][1] if rows else None


# --------------------------------------------------------------------------
# Live providers: Yahoo Finance chart API (NSE symbols use the .NS suffix)
# and the public Polymarket Gamma API. Standard library only.
# --------------------------------------------------------------------------


class YahooProvider:
    name = "yahoo"

    def __init__(self, lookback: int = 60):
        self.lookback = lookback

    def _daily(self, symbol: str, until: dt.date):
        # Anchor the window to `until`, not to now: a backtest session a year
        # ago must see the six months before it, not the last six months.
        ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
        end = dt.datetime.combine(until, dt.time(23, 59, 59), ist)
        q = urllib.parse.urlencode({
            "period1": int((end - dt.timedelta(days=183)).timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
        })
        data = _get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{q}")
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        quote = res["indicators"]["quote"][0]
        rows = []
        for t, c, v in zip(ts, quote["close"], quote["volume"]):
            if c is None:
                continue
            rows.append((dt.datetime.fromtimestamp(t, ist).date(), float(c), float(v or 0)))
        return rows

    def _headlines(self, symbol: str) -> tuple[str, ...]:
        try:
            # The news search indexes plain tickers; "RELIANCE.NS" returns
            # nothing while "RELIANCE" returns news.
            q = urllib.parse.urlencode({"q": symbol.split(".")[0], "newsCount": 8, "quotesCount": 0})
            data = _get_json(f"https://query1.finance.yahoo.com/v1/finance/search?{q}")
            return tuple(n["title"] for n in data.get("news", []) if n.get("title"))
        except Exception:
            return ()

    def fact_sheet(self, session: str, symbol: str) -> FactSheet:
        d = dt.date.fromisoformat(session)
        rows = [r for r in self._daily(symbol, d) if r[0] < d][-self.lookback:]
        if not rows:
            raise ValueError(f"No price history for {symbol} before {session}")
        # The news search API has no historical archive, so a session older
        # than a couple of days gets no headlines rather than today's news
        # (which would be look-ahead). The arms' news desks abstain when the
        # list is empty.
        recent = dt.date.today() - d <= dt.timedelta(days=2)
        return FactSheet(
            session=session,
            symbol=symbol,
            category="equity",
            as_of=f"{rows[-1][0].isoformat()}T15:30:00+05:30",
            source=self.name,
            closes=tuple(round(r[1], 4) for r in rows),
            volumes=tuple(r[2] for r in rows),
            fundamentals={},
            headlines=self._headlines(symbol) if recent else (),
        )

    def realized_close(self, session: str, symbol: str) -> float | None:
        d = dt.date.fromisoformat(session)
        rows = [r for r in self._daily(symbol, d) if r[0] == d]
        return rows[0][1] if rows else None


class PolymarketProvider:
    name = "polymarket"

    def _market(self, slug: str) -> dict:
        data = _get_json(
            "https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode({"slug": slug})
        )
        if not data:
            raise ValueError(f"Polymarket market not found: {slug}")
        return data[0]

    def fact_sheet(self, session: str, symbol: str) -> FactSheet:
        slug = symbol.split(":", 1)[1]
        m = self._market(slug)
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices)
        yes = float(prices[0]) if prices else float(m.get("lastTradePrice") or 0.5)
        return FactSheet(
            session=session,
            symbol=symbol,
            category="polymarket",
            as_of=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            source=self.name,
            market={
                "question": m.get("question", slug),
                "yes_price": round(yes, 4),
                "one_day_change": float(m.get("oneDayPriceChange") or 0.0),
                "volume_24h": float(m.get("volume24hr") or 0.0),
                "liquidity": float(m.get("liquidity") or 0.0),
                "end_date": m.get("endDate", ""),
            },
        )

    def realized_close(self, session: str, symbol: str) -> float | None:
        m = self._market(symbol.split(":", 1)[1])
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices)
        return float(prices[0]) if prices else None


class RoutingProvider:
    """Equities to Yahoo, polymarket:<slug> symbols to Polymarket."""

    name = "live"

    def __init__(self):
        self.equity = YahooProvider()
        self.poly = PolymarketProvider()

    def _pick(self, symbol: str):
        return self.poly if symbol.startswith("polymarket:") else self.equity

    def fact_sheet(self, session: str, symbol: str) -> FactSheet:
        return self._pick(symbol).fact_sheet(session, symbol)

    def realized_close(self, session: str, symbol: str) -> float | None:
        return self._pick(symbol).realized_close(session, symbol)


def get_provider(name: str) -> Provider:
    if name == "sample":
        return SampleProvider()
    if name == "live":
        return RoutingProvider()
    raise ValueError(f"Unknown provider {name!r}; use 'sample' or 'live'")
