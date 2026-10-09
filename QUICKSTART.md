# Quickstart on your machine

Python 3.10+ and no dependencies to install.

    cd meridian-desk
    set PYTHONPATH=src            # Windows cmd
    $env:PYTHONPATH="src"         # Windows PowerShell
    export PYTHONPATH=src         # macOS / Linux

## 1. Confirm the code is sound (offline, 10 seconds)

    python -m unittest discover -s tests

Expect `Ran 16 tests ... OK`.

## 2. The one thing only you can do: prove the live data works

This is the gate on everything else. The providers have never returned a
single row, because the container this was built in blocks both Yahoo and
Polymarket. Until this passes, every number the system produces is synthetic.

    python -m meridian.run prepare --session 2026-10-09 --provider live

Then check, by eye:

- Do the closes look like real NSE prices? RELIANCE around 1,400-1,500,
  TCS around 3,000-3,200, as of late 2026. A number that is out by 10x or
  100x means a wrong adjustment.
- Open `data/factsheets/2026-10-09/RELIANCE.NS.json` and compare the last
  close against the NSE site for the previous trading day. **Check two
  symbols by hand.** A stale or misaligned bar is the one failure that
  corrupts everything downstream while looking perfectly healthy.
- `as_of` must be the trading day *before* the session, never the session
  itself.

Then confirm outcomes line up with the right day:

    python -m meridian.run outcomes --session 2026-10-09 --provider live

In `data/outcomes.jsonl`, `prev` must equal the fact sheet's last close and
`close` must be that session's close.

**Paste me the output of both commands, plus the two symbols you checked by
hand.** If anything looks wrong, that is the most useful thing you can send.

## 3. One full day, all four arms

    python -m meridian.run parallel --session 2026-10-09 --provider live
    python -m meridian.run report

`parallel` must end with `OK: N (session, symbol) pairs; both arms read
identical fact sheets.` If it does not, stop and send me the output: the
whole comparison rests on that line.

## 4. The backtest (only after step 2 passes)

Hours, mostly network. Run it overnight.

    python -m meridian.run backtest --start 2024-01-01 --end 2025-06-30 --provider live
    python -m meridian.run report

Add `--symbols-file config/symbols_nifty50.json` for the 50-symbol universe.
Add `--workers 1` if the provider starts rate-limiting or refusing.

## Pushing to GitHub

The git history is already in this folder, so once Claude has access to the
repo:

    git remote add origin https://github.com/handeabhishek111/Meridian-Desk.git
    git push -u origin main
