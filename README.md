# Meridian Desk A/B trial

Arm A (TradingAgents-style pipeline) vs arm B (Meridian committee) for Indian
equities and Polymarket, plus two baselines they have to beat.

Every arm reads the **same frozen fact sheet** per symbol per session, so the
trial compares committee architectures and not data pipelines. `prepare` is the
only step that touches a data provider; the arms cannot fetch data, and a test
enforces that by inspecting their imports.

- [PLAN.md](PLAN.md) — the route from here to a decision, and why the original
  15-day plan could not have produced one on its own.
- [PREREGISTRATION.md](PREREGISTRATION.md) — the decision rule, fixed in advance.

## Running it

    export PYTHONPATH=src                       # on Windows: set PYTHONPATH=src

    python -m meridian.run power                 # what a given sample can detect
    python -m meridian.run parallel --session 2026-10-09                    # offline, sample data
    python -m meridian.run parallel --session 2026-10-09 --provider live     # real data
    python -m meridian.run outcomes --session 2026-10-09 --provider live     # after the close
    python -m meridian.run verify                # the arms read identical sheets
    python -m meridian.run report                # scorecard, power, verdict
    python -m unittest discover -s tests

The backtest is where the decision is made, because it reaches thousands of
observations instead of a few hundred:

    python -m meridian.run backtest --start 2024-01-01 --end 2026-09-30 --provider live
    python -m meridian.run report

## Layout

| Path | What it does |
|---|---|
| `factsheet.py` | The frozen, fingerprinted, write-once input every arm reads |
| `providers.py` | Yahoo and Polymarket fetchers, plus deterministic sample data. Only `prepare` imports it |
| `indicators.py` | Pure functions over a fact sheet, shared by the arms |
| `arms/arm_a.py` | Analysts, bull/bear debate, trader, risk team, portfolio manager |
| `arms/arm_b.py` | Independent specialist votes, pooled in log-odds, abstains when split |
| `arms/baselines.py` | `single` (one indicator) and `drift` (no signal) |
| `stats.py` | Paired Brier, bootstrap CI, sign test, calibration, power |
| `evaluation.py` | `verify_not_confounded` and the pre-registered `decide` |
| `run.py` | The command line |

## Two things to know before trusting a result

**The live providers have never returned a row.** They are written against the
Yahoo chart API and the Polymarket Gamma API, but this project was built in an
environment that blocks both hosts, so every number produced so far came from
synthetic sample data. Validate them before reading anything into a report.

**On sample data the verdict is `neither`,** and that is correct. The sample
provider is a seeded random walk with no predictable structure, so the no-signal
baseline wins and the rule refuses to crown a committee. It is a useful check
that the rule does not rubber-stamp whichever arm happens to be ahead.

## Changing the arms

Both arms are rule-based stand-ins for LLM committees, deliberately separated
from the data pipeline so real agents can be dropped into `arms/` without
touching it. Note that LLM agents make an arm non-deterministic, which needs a
fixed seed and a cached transcript per fact sheet to keep the backtest
reproducible.

`config/symbols.json` holds the 10-symbol default; `config/symbols_nifty50.json`
is a 50-symbol alternative (`--symbols-file`). Polymarket entries are market slugs.
