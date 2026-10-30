"""C901 complexity ratchet (audit 2026-10-29 #3): complexity may only go down."""
import unittest

from testing import complexity_budget as budget


class ComplexityRatchetTests(unittest.TestCase):
    def test_no_new_or_grown_complex_functions(self):
        try:
            measured = budget.measure()
        except budget.RuffUnavailable as exc:
            self.skipTest(f"ruff недоступний, храповик складності не перевірено: {exc}")
        limit = budget.threshold()
        problems = budget.violations(measured, budget.load_baseline(), limit)
        self.assertEqual(problems, [], "\n" + "\n".join(problems) + (
            "\nСпростіть функцію або, після рев'ю, оновіть базову лінію: "
            "python -m testing.complexity_budget --update"))

    def test_baseline_threshold_matches_pyproject(self):
        payload_threshold = budget.json.loads(budget.BASELINE.read_text(encoding="utf-8"))["threshold"]
        self.assertEqual(payload_threshold, budget.threshold())
        self.assertTrue(all(value > payload_threshold for value in budget.load_baseline().values()))


class ComplexityRuleTests(unittest.TestCase):
    def test_new_function_over_limit_is_a_violation(self):
        self.assertEqual(budget.violations({"a.py::f": 11}, {}, 10),
                         ["a.py::f: нова функція зі складністю 11 > 10"])

    def test_growth_is_a_violation_but_reduction_is_allowed(self):
        baseline = {"a.py::f": 20, "a.py::g": 15}
        self.assertEqual(budget.violations({"a.py::f": 21, "a.py::g": 12}, baseline, 10),
                         ["a.py::f: складність зросла 20 → 21"])
        self.assertEqual(budget.improvements({"a.py::f": 20}, baseline),
                         ["a.py::g: 15 → в межах порогу"])

    def test_methods_are_keyed_by_qualified_name(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "m.py"
            path.write_text("class A:\n    def run(self):\n        def inner():\n            pass\n",
                            encoding="utf-8")
            self.assertEqual(budget._qualnames(path), {2: "A.run", 3: "A.run.inner"})


if __name__ == "__main__":
    unittest.main()
