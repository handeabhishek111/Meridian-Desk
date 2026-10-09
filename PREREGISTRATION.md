# Pre-registration: arm A vs arm B

Written 2026-10-09, before any live session was run or any real data was scored.

The point of writing this down first is that the decision rule cannot be chosen
after seeing the results. If the rule below returns "inconclusive" or "neither",
that is the answer, and the fix is more data or a better arm, not a different rule.

## What is being compared

| | |
|---|---|
| Arm A | TradingAgents-style pipeline: analysts, bull/bear debate, trader, risk team, portfolio manager |
| Arm B | Meridian committee: specialists vote independently, chair pools in log-odds and abstains when split |
| Baseline `single` | One momentum indicator. Tests whether the committee beats one rule. |
| Baseline `drift` | A constant 51% long. Tests whether the committee beats having no signal. |

Both arms and both baselines read **the same frozen fact sheet** per symbol per
session. `verify_not_confounded` fails the trial if they ever did not. This is
what makes the comparison about architecture rather than about data pipelines.

## What is predicted

For each (session, symbol): the probability that the close on that session is
above the previous close. The fact sheet contains only data from strictly before
the session, so there is no look-ahead regardless of when `prepare` is run.

## Primary outcome

**Paired Brier score, arm A minus arm B, over every scored pair.**

Brier rather than hit rate, because it uses the stated probability instead of
throwing it away, and so extracts more information from the same number of days.
Paired, because both arms saw identical inputs: a market-wide move hits both arms
equally and cancels out of the difference. The 95% interval is a 20,000-iteration
percentile bootstrap on the mean difference, seeded so the published interval is
reproducible from the logs.

Decision: the arm with the lower Brier score wins **if the 95% interval excludes
zero**. Otherwise the primary test is inconclusive and the secondary tests below
apply, in this order and no other.

## Gate, applied before the primary test

An arm must beat **every** baseline on the paired Brier test, with the interval
excluding zero. Rationale: between two arms that both lose to a constant, the
higher-scoring one is still not worth running, and a rule that returns a winner
there would be measuring nothing. If no arm clears the gate, the verdict is
`neither`. If exactly one arm clears it, that arm wins and the A-vs-B test can
only confirm it.

## Secondary tests, in fixed order

1. **Head-to-head on disagreements.** Direction calls restricted to pairs where
   one arm said up and the other said down. Pairs where the arms agree carry no
   information about which architecture is better. Exact two-sided sign test;
   decisive at p < 0.05 with at least 20 disagreements.
2. **Calibration error.** Mean |predicted − observed| over probability bins.
   Decisive only on a margin of at least 0.02, so it cannot be won on noise.
3. **Abstention discipline.** Reported, not decisive: whether the arm is more
   accurate on the calls it makes than on the ones it skips.

If none of these separates the arms, the verdict is `inconclusive`. The
pre-registered response to that is to extend the trial or widen the universe,
**not** to pick the arm that happens to be ahead.

## What the sample can actually support

Computed, not assumed; reproduce with `python -m meridian.run power`.

| Scored calls | Power to detect a true 55% directional edge |
|---|---|
| 150 (15 sessions × 10 symbols) | 23% |
| 750 (15 sessions × 50 symbols) | 78% |

To reach 80% power on a true 55% edge takes about **790** scored calls; on a more
realistic 53% edge, about **2,180**.

Those raw counts also overstate the real sample. Indian equities move together,
so same-day observations are not independent. At an intra-day cross-sectional
correlation of 0.3, 15 sessions of 50 symbols behave like roughly **48**
independent observations, and widening from 10 to 50 symbols barely helps,
because the added symbols are driven by the same NIFTY move.

Two consequences, and they shape the whole plan:

- **A 15-session live trial cannot select the better architecture on accuracy.**
  Anyone reporting a winner from 15 days of 10 symbols is reporting noise.
- **The paired difference is the exception.** Common market moves cancel in
  A − B, so the difference is far less correlated than either arm's absolute
  score. The information lives in the disagreements, which are idiosyncratic to
  the architectures rather than driven by the index.

Therefore the decision rests on the **backtest**, which reaches thousands of
observations immediately; the 15-session live trial validates the pipeline and
confirms the backtest rather than carrying the statistics alone. Both arms are
deterministic functions of a fact sheet, and a historical fact sheet contains
only pre-session data, so the backtest is the same experiment with a larger
sample, not a weaker substitute for it.

## Stopping and amendment rules

- The live trial runs 15 sessions. It is not stopped early because a result
  looks good; peeking and stopping is how a coin flip becomes a finding.
- The arms' code is frozen for the duration. Any change to an arm voids the
  sessions already run, and the trial restarts.
- Fixes to the pipeline, the providers or the statistics are allowed and logged,
  because they do not change what an arm would predict from a given sheet.
- Amendments to this document are appended with a date and a reason. Nothing
  above is edited in place once a live session has been scored.
