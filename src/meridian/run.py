"""Command line for the A/B trial.

  python -m meridian.run prepare  --session 2026-10-09 [--provider live]
  python -m meridian.run arm      --arm A --session 2026-10-09
  python -m meridian.run parallel --session 2026-10-09 [--provider live]
  python -m meridian.run verify   [--session 2026-10-09]
  python -m meridian.run outcomes --session 2026-10-09 [--provider live]
  python -m meridian.run score

`prepare` is the only step that touches a data provider. It freezes one fact
sheet per symbol for the session. `arm` reads only those frozen sheets, so arm
A and arm B can run as separate processes (or on separate machines sharing the
data folder) and still see byte-identical inputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from .arms import ARMS, read_log, write_log
from .evaluation import load_outcomes, scorecard, verify_not_confounded
from .factsheet import FactSheetStore

DEFAULT_SYMBOLS = Path(__file__).resolve().parents[2] / "config" / "symbols.json"


def _paths(data: str):
    root = Path(data)
    return FactSheetStore(root / "factsheets"), root / "logs", root / "outcomes.jsonl"


def _symbols(args) -> list[str]:
    if args.symbols:
        return args.symbols
    cfg = json.loads(Path(args.symbols_file).read_text())
    return cfg["equities"] + [f"polymarket:{s}" for s in cfg.get("polymarket", [])]


def cmd_prepare(args) -> int:
    from .providers import get_provider  # only the pipeline imports providers

    store, _, _ = _paths(args.data)
    provider = get_provider(args.provider)
    failures = 0
    for sym in _symbols(args):
        try:
            fp = store.put(provider.fact_sheet(args.session, sym))
            print(f"frozen {sym:<32} {fp[:12]}")
        except Exception as e:  # keep going; a missing sheet is simply not traded
            failures += 1
            print(f"FAILED {sym:<32} {e}", file=sys.stderr)
    return 1 if failures and failures == len(_symbols(args)) else 0


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
    for p in preds:
        print(f"[arm {p.arm}] {p.symbol:<32} {p.direction:<5} p={p.prob_up:.3f} conf={p.confidence:.2f}")
    return 0


def _load_arm(logs: Path, arm: str, session: str | None):
    folder = logs / f"arm_{arm}"
    files = [folder / f"{session}.jsonl"] if session else sorted(folder.glob("*.jsonl"))
    return [p for f in files for p in read_log(f)]


def cmd_verify(args) -> int:
    store, logs, _ = _paths(args.data)
    report = verify_not_confounded(
        _load_arm(logs, "A", args.session), _load_arm(logs, "B", args.session), store
    )
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
             "--session", args.session, "--data", args.data],
            env=env,
        )
        for arm in ("A", "B")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in existing.values()))
    print(f"{len(existing)} outcomes in {path}")
    return 0


def cmd_score(args) -> int:
    _, logs, path = _paths(args.data)
    outcomes = load_outcomes(path)
    print(json.dumps({arm: scorecard(_load_arm(logs, arm, None), outcomes) for arm in ("A", "B")}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meridian.run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, session_required=True):
        p.add_argument("--data", default=os.environ.get("MERIDIAN_DATA", "data"))
        if session_required:
            p.add_argument("--session", default=dt.date.today().isoformat())

    for name in ("prepare", "parallel", "outcomes"):
        p = sub.add_parser(name)
        common(p)
        p.add_argument("--provider", default="sample", choices=["sample", "live"])
        p.add_argument("--symbols", nargs="*")
        p.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS))

    p = sub.add_parser("arm")
    common(p)
    p.add_argument("--arm", required=True, choices=sorted(ARMS))

    p = sub.add_parser("verify")
    common(p, session_required=False)
    p.add_argument("--session", default=None)

    p = sub.add_parser("score")
    common(p, session_required=False)

    args = ap.parse_args(argv)
    if args.cmd == "parallel" and not hasattr(args, "session"):
        args.session = dt.date.today().isoformat()
    return {
        "prepare": cmd_prepare, "arm": cmd_arm, "parallel": cmd_parallel,
        "verify": cmd_verify, "outcomes": cmd_outcomes, "score": cmd_score,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
