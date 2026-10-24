"""B. Can missing, extra or malformed verifier results produce an incorrect score?

Affected code: harness/env.py -> Env.score (zip over cases/results), _equal.
These tests replace the sandbox's /verify reply with simulated results, so they check
only how the harness scores what it receives. Fixture cases are harmless.
Secure behaviour: anything other than exactly one well-formed result per case scores as a failure.
"""
import unittest

from _helpers import FIXTURE_CHECK, env_with_results, fixture_task, load_env_module

ENV = load_env_module()
CASES = [("target", FIXTURE_CHECK, 42), ("target", FIXTURE_CHECK, 42), ("target", FIXTURE_CHECK, 42),
         ("regression", FIXTURE_CHECK, 42)]
GOOD = {"ok": True, "result": 42}
BAD = {"ok": False, "exc": "AssertionError"}


def score(results, cases=CASES):
    return env_with_results(ENV, fixture_task(ENV, cases), results).score()


class ResultHandlingTest(unittest.TestCase):
    def test_baseline_full_and_empty(self):
        """Control: the scoring formula gives 1.0 for all-pass and 0.0 for all-fail."""
        self.assertEqual(score([GOOD] * 4)["total"], 1.0)
        self.assertEqual(score([BAD] * 4)["total"], 0.0)

    def test_missing_results_count_as_failures(self):
        """Only 1 of 4 results returned (and it passes): true share of passing cases is 1/4."""
        total = score([GOOD])["total"]
        self.assertLessEqual(total, 0.7 * 1 / 3, f"missing results were ignored; score was {total}")

    def test_extra_results_are_ignored_or_rejected(self):
        """4 failing results followed by passing extras must not raise the score."""
        total = score([BAD] * 4 + [GOOD] * 4)["total"]
        self.assertEqual(total, 0.0, f"extra results changed the score to {total}")

    def test_non_dict_result_does_not_crash_scoring(self):
        try:
            total = score(["not a dict", GOOD, GOOD, GOOD])["total"]
        except Exception as exc:
            self.fail(f"malformed result crashed score(): {type(exc).__name__}: {exc}")
        self.assertLess(total, 1.0)

    def test_ok_must_be_true_not_just_truthy(self):
        total = score([{"ok": "yes", "result": 42}] * 4)["total"]
        self.assertEqual(total, 0.0, f"'ok': 'yes' was accepted as a pass; score was {total}")

    def test_bool_result_does_not_match_integer_expectation(self):
        cases = [("target", FIXTURE_CHECK, 1)]
        total = score([{"ok": True, "result": True}], cases)["total"]
        self.assertEqual(total, 0.0, f"result True matched expected 1; score was {total}")


if __name__ == "__main__":
    unittest.main()
