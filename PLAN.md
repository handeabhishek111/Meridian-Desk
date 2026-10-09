# Plan: from here to choosing an arm

The decision rule is in [PREREGISTRATION.md](PREREGISTRATION.md) and is fixed.
This is the sequence of work that feeds it.

## The problem with the original plan, in one paragraph

The plan was to run both arms for 15 days and pick the winner. The arithmetic
does not support that. Fifteen sessions of 10 symbols is 150 scored calls, which
gives a 23% chance of detecting a real 55% directional edge; detecting one needs
about 790 calls. Worse, Indian equities move together, so same-day calls are not
independent: at a cross-sectional correlation of 0.3, those 150 calls behave like
about 40 independent ones, and widening to 50 symbols takes it to only about 48,
because the extra symbols follow the same index. A 15-day accuracy comparison
would return whichever arm got luckier, and would do it with a confident-looking
number attached. Run `python -m meridian.run power` to see the figures.

The fix is not to abandon the live trial but to stop asking it to carry the
statistics. Both arms are deterministic functions of a fact sheet, and a fact
sheet for a past session contains only data from before that session. So the
backtest *is* the same experiment, with two years of sessions instead of fifteen.
The live trial's job is to prove the pipeline is sound and to confirm the
backtest, which is a job fifteen sessions can actually do.

## Stage 1 — Harden the pipeline (done)

- Shared write-once fact sheet store, fingerprinted; arms cannot fetch data, and
  a test enforces that by inspecting their imports.
- `verify_not_confounded` across all four arms, and it refuses to return a
  verdict if the check fails.
- Paired statistics: Brier, bootstrap CI, exact sign test, calibration.
- Two baselines, `single` and `drift`, that a committee must beat to be worth
  running at all.
- 16 tests, including one that confirms the rule returns `neither` when neither
  arm beats a constant.

## Stage 2 — Validate against live data (next, needs your machine)

This environment blocks the Yahoo and Polymarket hosts, so the live providers are
written but have never returned a row. Until they do, nothing downstream is real.

1. `python -m meridian.run prepare --session <today> --provider live` and confirm
   the fact sheets hold plausible NSE closes.
2. Check two symbols by hand against the NSE site. A wrong adjustment or a
   stale bar is the one failure mode that would corrupt every later stage while
   looking perfectly healthy.
3. `python -m meridian.run outcomes --session <a past session> --provider live`
   and confirm the realized close matches the session, not the day after.

Expect to spend real time here. Symbol-level data problems are the usual reason
a trial like this produces a confident wrong answer.

## Stage 3 — Backtest, which is where the decision is made

    python -m meridian.run backtest --start 2024-01-01 --end 2026-09-30 --provider live
    python -m meridian.run report

About 650 sessions. With a NIFTY 50 universe that is roughly 32,000 scored
pairs, which is ample for the primary test even after correlation is accounted
for, and enough to split the sample.

Do this in two halves, and in this order:

1. **2024-01-01 to 2025-06-30 — development.** Look at everything. Fix arms,
   tune thresholds, change the universe. This half is for making mistakes in.
2. **2025-07-01 to 2026-09-30 — held out.** Run the pre-registered rule once.
   If an arm wins only on the development half, it was fitted to it, and that is
   the finding.

Also check regime stability: run the report on each calendar quarter separately.
An arm that wins overall but loses in three quarters out of six has not earned
the win; it caught one trend.

## Stage 4 — The 15-session live trial

Now the live trial has a job it can do. Each trading day:

    python -m meridian.run parallel --session <today> --provider live   # before open
    python -m meridian.run outcomes --session <today> --provider live   # after close

What it is for:

- **Look-ahead detection.** If the live hit rate is markedly worse than the
  backtest's on the same arm, the backtest was leaking future data. This is the
  single most valuable thing the live trial buys, and 15 sessions is enough,
  because a leak shows up as a large gap rather than a subtle one.
- **Operational truth.** Does `prepare` finish before the open? Do symbols drop
  out? What happens on a holiday or a halt?
- **Confirmation, not selection.** If the live sign is consistent with the
  backtest, the backtest's verdict stands. If it contradicts it, do not pick the
  live winner either; find out why they disagree.

Automate it rather than relying on memory: a single routine that runs `parallel`
before the open and `outcomes` after the close, 15 trading days in a row. I can
set that up when you want it.

## Stage 5 — Decide

    python -m meridian.run report

The rule produces one of four verdicts, and all four are acceptable outcomes:

| Verdict | What to do |
|---|---|
| `A` or `B` | Ship that arm. Record the interval, not just the winner. |
| `neither` | Neither committee beat a constant. The honest move is to fix the arms, not to ship the less-bad one. |
| `inconclusive` | Extend the window or widen the universe. Do not pick the arm that is ahead. |

My expectation, stated now so it can be wrong on the record: the primary test
will separate the arms on the backtest, and `inconclusive` on the 15 live
sessions alone. `neither` is a live possibility and worth taking seriously,
since on the synthetic data the no-signal baseline currently beats both arms.

## Timing: what each stage costs

Measured on sample data in this container; the live figures depend on your
network and machine.

| Step | Cost |
|---|---|
| Arm compute | 0.2 ms per symbol per arm. All four arms over 50 symbols: 32 ms |
| Daily run, 4 arms in parallel | under 1 s excluding network |
| `prepare`, live, 50 symbols | the only real cost: 2 HTTPS round trips per symbol, fetched 8-way concurrent |
| `report` on 880 pairs | 4 s |
| Backtest, 650 sessions x 50 symbols | hours, dominated by network; a one-off, run it overnight |

The arms are not the bottleneck and never will be; the fetches are. `prepare`
fetches concurrently (`--workers`, default 8), which on a 50-symbol universe at
0.4 s per fetch is 2.8 s instead of 20 s. Use `--workers 1` if a provider starts
rate-limiting.

Two things worth knowing:

- **Widening from 10 to 50 symbols adds no meaningful delay to the daily run**,
  because the extra fetches happen concurrently and the arm compute is noise.
- **The backtest is slow but off the critical path.** It runs once, offline, and
  caches every fact sheet it freezes, so re-running the arms over an existing
  backtest needs no network at all. Re-scoring after an arm change is minutes,
  not hours.

## Open questions for you

1. **Universe.** `config/symbols.json` has 10 symbols. NIFTY 50 costs nothing
   extra on a backtest and roughly five times the pairs. Any reason not to?
2. **LLM agents.** Both arms are rule-based stand-ins today. They are separated
   from the data pipeline, so swapping in real LLM committees does not touch it,
   but it makes the arms non-deterministic, which needs a seed and a cached
   transcript per sheet to keep the backtest reproducible. Worth doing before
   the trial or after?
3. **Polymarket.** The section exists and is wired through the same fact sheet,
   but `config/symbols.json` lists no markets, and resolution is slower than a
   daily close, so it needs its own horizon. Include it in this trial or run it
   separately?
4. **Horizon.** Everything above predicts the next daily close. If the real use
   is a multi-day hold, the trial should measure that instead, and the numbers
   change.
