"""Command line for the A/B trial.

  python -m meridian.run prepare  --session 2026-10-09 [--provider live]
  python -m meridian.run arm      --arm A --session 2026-10-09
  python -m meridian.run parallel --session 2026-10-09 [--provider live]
  python -m meridian.run verify   [--session 2026-10-09]
  python -m meridian.run outcomes --session 2026-10-09 [--provider live]
  python -m meridian.run backtest --start 2024-01-01 --end 2026-09-30 [--provider live]
  python -m meridian.run report
  python -m meridian.run power

`prepare` is the only step that touches a data provider. It freezes one fact
sheet per symbol for the session. Every arm reads only those frozen sheets, so
the arms can run as separate processes (or on separate machines sharing the
data folder) and still see byte-identical inputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .arms import ARMS, CONTENDERS, read_log, write_log
from .evaluation import decide, load_outcomes, scorecard, verify_not_confounded
from .factsheet import FactSheetStore
from .stats import min_n_for_power, power_hit_rate

DEFAULT_SYMBOLS = Path(__file__).resolve().parents[2] / "config" / "symbols.json"


def _paths(data: str):
    root = Path(data)
    return FactSheetStore(root / "factsheets"), root / "logs", root / "outcomes.jsonl"


def _symbols(args) -> list[str]:
    if getattr(args, "symbols", None):
        return args.symbols
    cfg = json.loads(Path(args.symbols_file).read_text())
    return cfg["equities"] + [f"polymarket:{s}" for s in cfg.get("polymarket", [])]


def _arms_to_run(args) -> list[str]:
    requested = getattr(args, "arms", None)
    return list(requested) if requested else list(ARMS)


# --------------------------------------------------------------------------


def cmd_prepare(args) -> int:
    from concurrent.futures import ThreadPoolExecutor
    from .providers import get_provider  # only the pipeline imports providers

    store, _, _ = _paths(args.data)
    provider = get_provider(args.provider)
    symbols = _symbols(args)

    # Each symbol costs two sequential HTTPS round trips against a live
    # provider, which dominates everything else in the daily run and has to
    # finish before the open. Symbols are independent and each writes its own
    # file, so they fetch concurrently. The worker count is kept modest to stay
    # polite to the provider rather than to go as fast as possible.
    workers = max(1, min(args.workers, len(symbols)))

    def fetch(sym: str) -> tuple[str, str | None, str | None]:
        try:
            return sym, store.put(provider.fact_sheet(args.session, sym)), None
        except Exception as e:  # a symbol with no data is simply not traded
            return sym, None, str(e)

    started = time.perf_counter()
    if workers == 1:
        results = [fetch(s) for s in symbols]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(fetch, symbols))

    frozen = 0
    for sym, fp, err in results:  # reported in symbol order, not completion order
        if err is not None:
            print(f"FAILED {sym:<32} {err}", file=sys.stderr)
        else:
            frozen += 1
            if not getattr(args, "quiet", False):
                print(f"frozen {sym:<32} {fp[:12]}")
    if not getattr(args, "quiet", False):
        print(f"{frozen}/{len(symbols)} sheets frozen in "
              f"{time.perf_counter() - started:.1f}s with {workers} worker(s)")
    if frozen == 0:
        print(f"No fact sheets frozen for {args.session}.", file=sys.stderr)
        return 1
    return 0


def cmd_arm(args) -> int:
    store, logs, _ = _paths(args.data)
    symbols = store.symbols(args.session)
    if not symbols:
        print(f"No frozen fact sheets for {args.session}; run prepare first.", file=sys.stderr)
        return 1
    preds = ARMS[args.arm]().run(store, args.session, symbols)
    out = logs / f"arm_{args.arm}" / f"{args.session}.jsonl"
    if out.exists():
        out.unlink()  # re-running a session replaces its log
    write_log(out, preds)
    if not getattr(args, "quiet", False):
        for p in preds:
            print(f"[arm {p.arm}] {p.symbol:<32} {p.direction:<5} "
                  f"p={p.prob_up:.3f} conf={p.confidence:.2f}")
    return 0


def _load_arm(logs: Path, arm: str, session: str | None):
    folder = logs / f"arm_{arm}"
    if not folder.exists():
        return []
    files = [folder / f"{session}.jsonl"] if session else sorted(folder.glob("*.jsonl"))
    return [p for f in files for p in read_log(f)]


def _load_all(logs: Path, session: str | None) -> dict[str, list]:
    out = {}
    for arm in ARMS:
        preds = _load_arm(logs, arm, session)
        if preds:
            out[arm] = preds
    return out


def cmd_verify(args) -> int:
    store, logs, _ = _paths(args.data)
    by_arm = _load_all(logs, args.session)
    if len(by_arm) < 2:
        print("Need at least two arms' logs to verify.", file=sys.stderr)
        return 1
    report = verify_not_confounded(by_arm, store=store)
    print(f"arms checked: {', '.join(sorted(by_arm))}")
    print(report.summary())
    return 0 if report.ok else 1


def cmd_parallel(args) -> int:
    if cmd_prepare(args) != 0:
        return 1
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    procs = {
        arm: subprocess.Popen(
            [sys.executable, "-m", "meridian.run", "arm", "--arm", arm,
             "--session", args.session, "--data", args.data]
            + (["--quiet"] if getattr(args, "quiet", False) else []),
            env=env,
        )
        for arm in _arms_to_run(args)
    }
    codes = {arm: p.wait() for arm, p in procs.items()}
    if any(codes.values()):
        print(f"arm exit codes: {codes}", file=sys.stderr)
        return 1
    return cmd_verify(args)


def cmd_outcomes(args) -> int:
    from .providers import get_provider

    store, _, path = _paths(args.data)
    provider = get_provider(args.provider)
    existing = load_outcomes(path)
    added = 0
    for sym in store.symbols(args.session):
        sheet = store.get(args.session, sym)
        try:
            close = provider.realized_close(args.session, sym)
        except Exception as e:
            print(f"FAILED {sym}: {e}", file=sys.stderr)
            continue
        if close is None:
            continue
        prev = sheet.market["yes_price"] if sheet.category == "polymarket" else sheet.closes[-1]
        existing[(args.session, sym)] = {
            "session": args.session, "symbol": sym, "prev": prev, "close": close,
            "up": None if close == prev else close > prev,
        }
        added += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in existing.values()))
    if not getattr(args, "quiet", False):
        print(f"{added} outcomes this session, {len(existing)} total in {path}")
    return 0


def cmd_backtest(args) -> int:
    """Replay the whole pipeline over historical sessions.

    The arms are deterministic functions of a fact sheet, and a fact sheet for
    a past session contains only data from before that session, so a backtest
    is the same experiment as the live trial with a much larger sample. This is
    what gives the comparison enough observations to decide; the live trial
    confirms the pipeline rather than carrying the statistics on its own.
    """
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    sessions = []
    day = start
    while day <= end:
        if day.weekday() < 5:  # NSE trades Monday to Friday
            sessions.append(day.isoformat())
        day += dt.timedelta(days=1)
    print(f"backtesting {len(sessions)} sessions from {start} to {end}")

    done = 0
    for i, session in enumerate(sessions, 1):
        sub = argparse.Namespace(**vars(args))
        sub.session = session
        sub.quiet = True
        if cmd_prepare(sub) != 0:
            continue
        for arm in _arms_to_run(args):
            sub.arm = arm
            cmd_arm(sub)
        cmd_outcomes(sub)
        done += 1
        if i % 20 == 0 or i == len(sessions):
            print(f"  {i}/{len(sessions)} sessions ({done} with data)")

    store, logs, _ = _paths(args.data)
    report = verify_not_confounded(_load_all(logs, None), store=store)
    print(report.summary())
    return 0 if report.ok else 1


def cmd_report(args) -> int:
    store, logs, path = _paths(args.data)
    by_arm = _load_all(logs, None)
    outcomes = load_outcomes(path)
    if not by_arm:
        print("No arm logs found.", file=sys.stderr)
        return 1

    print("=" * 78)
    print("SCORECARD (lower brier is better; hit_rate over non-flat calls only)")
    print("=" * 78)
    for arm in sorted(by_arm):
        print(f"\narm {arm}:")
        print(json.dumps(scorecard(by_arm[arm], outcomes), indent=2))

    sessions = sorted({p.session for preds in by_arm.values() for p in preds})
    scored = sum(1 for k in outcomes if outcomes[k].get("up") is not None)
    print("\n" + "=" * 78)
    print(f"SAMPLE: {len(sessions)} sessions, {scored} scored pairs")
    print("=" * 78)
    for edge in (0.53, 0.55, 0.57, 0.60):
        print(f"  power to detect a true {edge:.0%} directional edge: "
              f"{power_hit_rate(scored, edge):.0%}")

    print("\n" + "=" * 78)
    print("DECISION (pre-registered rule, see PREREGISTRATION.md)")
    print("=" * 78)
    print(decide(by_arm, outcomes, store).summary())
    return 0


def cmd_power(args) -> int:
    print("Sample needed for 80% power to beat a coin flip on direction:")
    for edge in (0.52, 0.53, 0.55, 0.57, 0.60):
        print(f"  true edge {edge:.0%}: {min_n_for_power(edge):>6} scored calls")
    print()
    print("What a 15-session live trial yields (raw pairs, before correlation):")
    for k in (10, 30, 50):
        n = 15 * k
        print(f"  {k:>3} symbols: {n:>5} pairs -> "
              f"power vs a true 55% edge: {power_hit_rate(n, 0.55):.0%}")
    print()
    print("Indian equities move together, so same-day pairs are not independent.")
    print("Effective sample with intra-day correlation rho:")
    for k in (10, 50):
        for rho in (0.3, 0.6):
            n_eff = 15 * k / (1 + (k - 1) * rho)
            print(f"  {k:>3} symbols, rho={rho}: effective n = {n_eff:5.1f}")
    print()
    print("This is why the decision rests on the backtest, and on the paired")
    print("difference restricted to pairs where the arms disagree: a market-wide")
    print("move hits both arms equally and cancels out of the difference.")
    return 0


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meridian.run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def data_arg(p):
        p.add_argument("--data", default=os.environ.get("MERIDIAN_DATA", "data"))
        p.add_argument("--quiet", action="store_true")

    def provider_args(p):
        p.add_argument("--provider", default="sample", choices=["sample", "live"])
        p.add_argument("--symbols", nargs="*")
        p.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS))
        p.add_argument("--workers", type=int, default=8,
                       help="concurrent provider fetches in prepare (1 disables)")

    for name in ("prepare", "outcomes"):
        p = sub.add_parser(name)
        data_arg(p)
        p.add_argument("--session", default=dt.date.today().isoformat())
        provider_args(p)

    p = sub.add_parser("parallel")
    data_arg(p)
    p.add_argument("--session", default=dt.date.today().isoformat())
    p.add_argument("--arms", nargs="*", choices=sorted(ARMS))
    provider_args(p)

    p = sub.add_parser("backtest")
    data_arg(p)
    p.add_argument("--start", required=True)
    p.add_argument("--end", default=dt.date.today().isoformat())
    p.add_argument("--arms", nargs="*", choices=sorted(ARMS))
    provider_args(p)

    p = sub.add_parser("arm")
    data_arg(p)
    p.add_argument("--session", default=dt.date.today().isoformat())
    p.add_argument("--arm", required=True, choices=sorted(ARMS))

    p = sub.add_parser("verify")
    data_arg(p)
    p.add_argument("--session", default=None)

    p = sub.add_parser("report")
    data_arg(p)

    sub.add_parser("power")

    args = ap.parse_args(argv)
    return {
        "prepare": cmd_prepare, "arm": cmd_arm, "parallel": cmd_parallel,
        "verify": cmd_verify, "outcomes": cmd_outcomes, "backtest": cmd_backtest,
        "report": cmd_report, "power": cmd_power,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
