# Meridian Desk A/B trial

Arm A (TradingAgents-style pipeline) vs arm B (Meridian committee) for Indian equities and Polymarket.
Both arms read the same frozen fact sheet per symbol per session, so the trial compares committee
architectures, not data pipelines.

    export PYTHONPATH=src
    python -m meridian.run parallel --session 2026-10-09 --provider sample   # offline test
    python -m meridian.run parallel --session 2026-10-09 --provider live     # real data (needs internet)
    python -m meridian.run outcomes --session 2026-10-09 --provider live     # after market close
    python -m meridian.run verify                                            # confound check
    python -m meridian.run score
    python -m unittest discover -s tests

Edit `config/symbols.json` to change symbols; Polymarket entries are market slugs.
Arms are rule-based stand-ins for the agent committees; swap in LLM agents inside `arms/` without
touching the data pipeline.
