import random
import unittest

from meridian.arms.base import Prediction
from meridian.evaluation import decide
from meridian.stats import (binom_two_sided, brier, calibration, head_to_head,
                            min_n_for_power, paired_loss, power_hit_rate)


def pred(arm, sym, p, direction=None, session="2026-10-09"):
    from meridian.arms.base import direction_from
    return Prediction(arm=arm, session=session, symbol=sym, category="equity",
                      direction=direction or direction_from(p), prob_up=p, confidence=0.5,
                      fact_sheet_fingerprint="fp_" + sym, rationale="")


def make(arm, probs, session="2026-10-09"):
    return {(session, s): pred(arm, s, p, session=session) for s, p in probs.items()}


def outs(ups, session="2026-10-09"):
    return {(session, s): {"session": session, "symbol": s, "up": u} for s, u in ups.items()}


class StatsTests(unittest.TestCase):
    def test_paired_loss_detects_a_clear_winner(self):
        syms = [f"S{i}" for i in range(200)]
        rng = random.Random(1)
        ups = {s: rng.random() < 0.5 for s in syms}
        # A is confidently right, B sits on the fence.
        a = make("A", {s: 0.9 if ups[s] else 0.1 for s in syms})
        b = make("B", {s: 0.5 for s in syms})
        r = paired_loss(a, b, outs(ups))
        self.assertEqual(r.better, "A")
        self.assertLess(r.ci_high, 0)

    def test_paired_loss_is_inconclusive_on_noise(self):
        syms = [f"S{i}" for i in range(200)]
        rng = random.Random(2)
        ups = {s: rng.random() < 0.5 for s in syms}
        a = make("A", {s: round(0.4 + rng.random() * 0.2, 3) for s in syms})
        b = make("B", {s: round(0.4 + rng.random() * 0.2, 3) for s in syms})
        self.assertEqual(paired_loss(a, b, outs(ups)).better, "inconclusive")

    def test_head_to_head_ignores_agreement_and_flat(self):
        ups = {"X": True, "Y": True, "Z": True}
        a = make("A", {"X": 0.9, "Y": 0.9, "Z": 0.9})          # up, up, up
        b = make("B", {"X": 0.1, "Y": 0.9, "Z": 0.50})          # down, up, flat
        h = head_to_head(a, b, outs(ups))
        self.assertEqual(h.disagreements, 1)   # only X is a real disagreement
        self.assertEqual((h.a_right, h.b_right), (1, 0))

    def test_sign_test_matches_known_values(self):
        self.assertAlmostEqual(binom_two_sided(5, 10), 1.0, places=6)
        self.assertAlmostEqual(binom_two_sided(10, 10), 2 * 0.5 ** 10, places=9)
        self.assertEqual(binom_two_sided(0, 0), 1.0)

    def test_calibration_rewards_honest_probabilities(self):
        syms = [f"S{i}" for i in range(400)]
        rng = random.Random(3)
        # An honest 70% forecaster: says 0.7 and is right 70% of the time.
        ups = {s: rng.random() < 0.7 for s in syms}
        honest = [pred("A", s, 0.7) for s in syms]
        liar = [pred("B", s, 0.99) for s in syms]
        o = outs(ups)
        self.assertLess(calibration(honest, o).error, calibration(liar, o).error)

    def test_power_helpers_are_monotonic(self):
        self.assertLess(power_hit_rate(100, 0.55), power_hit_rate(1000, 0.55))
        self.assertGreater(min_n_for_power(0.52), min_n_for_power(0.60))
        self.assertEqual(power_hit_rate(0, 0.6), 0.0)

    def test_brier_is_zero_when_certain_and_right(self):
        self.assertEqual(brier(pred("A", "X", 1.0), True), 0.0)
        self.assertEqual(brier(pred("A", "X", 0.0), True), 1.0)


class DecisionRuleTests(unittest.TestCase):
    """The gate exists to stop the rule crowning an arm that beats nothing."""

    def _trial(self, a_probs, b_probs, ups, syms):
        return {
            "A": [pred("A", s, a_probs[s]) for s in syms],
            "B": [pred("B", s, b_probs[s]) for s in syms],
            "single": [pred("single", s, 0.5) for s in syms],
            "drift": [pred("drift", s, 0.51) for s in syms],
        }, outs(ups)

    def test_refuses_a_winner_when_neither_beats_the_baselines(self):
        syms = [f"S{i}" for i in range(300)]
        rng = random.Random(4)
        ups = {s: rng.random() < 0.5 for s in syms}
        # Both arms are overconfident noise; B merely less bad than A.
        a = {s: round(0.2 + rng.random() * 0.6, 3) for s in syms}
        b = {s: round(0.45 + rng.random() * 0.1, 3) for s in syms}
        by_arm, o = self._trial(a, b, ups, syms)
        v = decide(by_arm, o)
        self.assertEqual(v.winner, "neither", v.summary())

    def test_picks_the_arm_that_beats_every_baseline(self):
        syms = [f"S{i}" for i in range(300)]
        rng = random.Random(5)
        ups = {s: rng.random() < 0.5 for s in syms}
        a = {s: 0.75 if ups[s] else 0.25 for s in syms}   # genuinely informative
        b = {s: round(0.49 + rng.random() * 0.02, 3) for s in syms}  # no signal
        by_arm, o = self._trial(a, b, ups, syms)
        v = decide(by_arm, o)
        self.assertEqual(v.winner, "A", v.summary())

    def test_inconclusive_without_outcomes(self):
        syms = ["S1", "S2"]
        by_arm, _ = self._trial({s: 0.6 for s in syms}, {s: 0.4 for s in syms},
                                {}, syms)
        v = decide(by_arm, {})
        self.assertEqual(v.winner, "inconclusive")

    def test_confounded_input_blocks_any_verdict(self):
        syms = ["S1", "S2"]
        by_arm, o = self._trial({s: 0.6 for s in syms}, {s: 0.4 for s in syms},
                                {"S1": True, "S2": False}, syms)
        by_arm["B"][0].fact_sheet_fingerprint = "tampered"
        v = decide(by_arm, o)
        self.assertEqual(v.winner, "inconclusive")
        self.assertIn("identical fact sheets", v.reason)


if __name__ == "__main__":
    unittest.main()
