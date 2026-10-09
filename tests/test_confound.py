import ast
import dataclasses
import pathlib
import tempfile
import unittest

from meridian.arms import ArmA, ArmB
from meridian.evaluation import ConfoundedError, verify_not_confounded
from meridian.factsheet import FactSheetStore
from meridian.providers import SampleProvider

SESSION = "2026-10-09"
SYMS = ["RELIANCE.NS", "TCS.NS", "polymarket:some-market"]


def frozen_store(tmp):
    store, prov = FactSheetStore(tmp), SampleProvider()
    for s in SYMS:
        store.put(prov.fact_sheet(SESSION, s))
    return store


class ConfoundTests(unittest.TestCase):
    def test_same_sheet_passes(self):
        with tempfile.TemporaryDirectory() as t:
            store = frozen_store(t)
            a, b = ArmA().run(store, SESSION), ArmB().run(store, SESSION)
            r = verify_not_confounded(a, b, store)
            self.assertTrue(r.ok, r.summary())
            self.assertEqual(r.checked, len(SYMS))

    def test_different_sheet_is_caught(self):
        with tempfile.TemporaryDirectory() as t:
            store = frozen_store(t)
            a, b = ArmA().run(store, SESSION), ArmB().run(store, SESSION)
            sheet = store.get(SESSION, "TCS.NS")
            tampered = dataclasses.replace(sheet, closes=sheet.closes[:-1] + (sheet.closes[-1] * 1.01,))
            b_bad = ArmB().predict(tampered)
            b = [b_bad if p.symbol == "TCS.NS" else p for p in b]
            r = verify_not_confounded(a, b, store)
            self.assertFalse(r.ok)
            with self.assertRaises(ConfoundedError):
                verify_not_confounded(a, b, store, raise_on_fail=True)

    def test_missing_symbol_is_caught(self):
        with tempfile.TemporaryDirectory() as t:
            store = frozen_store(t)
            a, b = ArmA().run(store, SESSION), ArmB().run(store, SESSION)
            self.assertFalse(verify_not_confounded(a, b[:-1], store).ok)

    def test_store_is_write_once(self):
        with tempfile.TemporaryDirectory() as t:
            store = frozen_store(t)
            sheet = store.get(SESSION, "TCS.NS")
            with self.assertRaises(FileExistsError):
                store.put(dataclasses.replace(sheet, source="other"))

    def test_arms_never_import_providers(self):
        root = pathlib.Path(__file__).resolve().parents[1] / "src" / "meridian" / "arms"
        for f in root.glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                names = []
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                self.assertFalse(any("providers" in n or "urllib" in n for n in names), f"{f.name}: {names}")


if __name__ == "__main__":
    unittest.main()
