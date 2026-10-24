"""A. Agent-controlled files must not interfere with hidden-check execution, hidden-check
information must not reach the agent, and the trusted verifier logic must run in a separate
process from the candidate's code.

Affected code: verifier/server.py -> run_program, do_verify (spawns verifier/worker.py);
verifier/worker.py; harness/env.py -> Env.score; harness/tasks.py (structured programs);
sandbox/server.py -> ROUTES_POST, do_snapshot.
All fixtures are harmless: a function returning 42, plus canary files in a temp folder.
"""
import json
import os
import unittest

from _helpers import (FIXTURE_BUGGY, FIXTURE_FIXED, FIXTURE_PROGRAM, TempWorkspace, encode_files,
                      fixture_program, fixture_task, load_env_module, load_sandbox, load_verifier,
                      wired_env)

ENV = load_env_module()
VERIFIER = load_verifier()


class ModuleShadowingTest(unittest.TestCase):
    """Attack attempts: the agent plants modules named after standard-library modules."""

    def setUp(self):
        self.tmp = TempWorkspace()

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, files, program=FIXTURE_PROGRAM):
        return VERIFIER.do_verify({"files": encode_files(files), "cases": [program]})["results"]

    def test_agent_json_module_does_not_run_during_a_check(self):
        canary = self.tmp.outside / "canary_json.txt"
        shadow = f"open({str(canary)!r}, 'w').write('imported')\n"
        results = self.verify({"fixture_mod.py": FIXTURE_FIXED, "json.py": shadow})
        self.assertFalse(canary.exists(),
                         f"agent-written json.py ran inside the worker; verifier returned {results}")
        self.assertEqual(results, [{"ok": True, "result": 42}], "the legitimate check stopped working")

    def test_agent_os_module_does_not_run_during_a_check(self):
        canary = self.tmp.outside / "canary_os.txt"
        shadow = f"open({str(canary)!r}, 'w').write('imported')\n"
        results = self.verify({"fixture_mod.py": FIXTURE_FIXED, "os.py": shadow})
        self.assertFalse(canary.exists(), f"agent-written os.py ran inside the worker; got {results}")
        self.assertEqual(results, [{"ok": True, "result": 42}])

    def test_candidate_runs_in_a_separate_process(self):
        """The candidate records its own PID; it must differ from this (trusted) process."""
        files = {"fixture_mod.py": "import os\n_PID = os.getpid()\ndef answer():\n    return _PID\n"}
        results = self.verify(files)
        self.assertTrue(results[0]["ok"], results)
        self.assertNotEqual(results[0]["result"], os.getpid(),
                            "candidate ran in the same process as the verifier")


class StructureTest(unittest.TestCase):
    """The check is a structured program, not arbitrary source, and carries no answers."""

    ALLOWED_TOP = {"module", "steps"}
    ALLOWED_STEP = {"op", "name", "recv", "args", "record", "into"}

    def test_sandbox_has_no_check_execution_route(self):
        sandbox = load_sandbox(TempWorkspace().workspace)
        self.assertNotIn("/verify", sandbox.ROUTES_POST)
        self.assertFalse(hasattr(sandbox, "CHILD"), "sandbox still carries a hidden-check runner")

    def test_verifier_uses_a_separate_worker_script(self):
        self.assertEqual(VERIFIER.WORKER.name, "worker.py")
        self.assertTrue(VERIFIER.WORKER.exists())
        self.assertFalse(hasattr(VERIFIER, "CHECKER"),
                         "verifier still carries an inline snippet executor")

    def test_programs_are_structured_and_carry_no_expected_answers(self):
        from harness import tasks
        from harness.tasks import Raises
        for task_id in tasks.TASKS:                # every active task, whatever the registry holds
            task = tasks.build_task(task_id, 0)
            for case in task.cases:
                if case.program is None:          # harness-side file check, never sent to the worker
                    continue
                self.assertLessEqual(set(case.program), self.ALLOWED_TOP)
                for step in case.program["steps"]:
                    self.assertLessEqual(set(step), self.ALLOWED_STEP, f"{task_id}: unexpected step key")
                # An expected exception type must never be embedded in what the worker receives.
                if isinstance(case.expect, Raises):
                    self.assertNotIn(case.expect.name, json.dumps(case.program),
                                     f"{task_id}: expected outcome leaked into the worker program")

    def test_rejects_arbitrary_source_as_a_case(self):
        with self.assertRaises(VERIFIER.BadRequest):
            VERIFIER.do_verify({"files": encode_files({"fixture_mod.py": FIXTURE_FIXED}),
                                "cases": ["from fixture_mod import answer\nresult = answer()"]})


class ScoringTest(unittest.TestCase):
    """End-to-end through Env.score with the real sandbox and verifier code (in-process)."""

    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scoring_does_not_modify_the_agent_workspace(self):
        task = fixture_task(ENV, [("target", FIXTURE_PROGRAM, 42), ("regression", FIXTURE_PROGRAM, 42)])
        env = wired_env(ENV, task, self.sandbox, VERIFIER)
        self.sandbox.do_reset({"files": {"fixture_mod.py": FIXTURE_FIXED}})
        before = self.sandbox.do_state()
        self.assertEqual(env.score()["total"], 1.0)
        self.assertEqual(self.sandbox.do_state(), before, "scoring changed the agent's workspace")

    def test_reference_and_noop_scores_unchanged(self):
        task = fixture_task(ENV, [("target", FIXTURE_PROGRAM, 42), ("regression", FIXTURE_PROGRAM, 42)])
        env = wired_env(ENV, task, self.sandbox, VERIFIER)
        self.sandbox.do_reset({"files": {"fixture_mod.py": FIXTURE_FIXED}})
        self.assertEqual(env.score()["total"], 1.0)
        self.sandbox.do_reset({"files": {"fixture_mod.py": FIXTURE_BUGGY}})
        self.assertEqual(env.score()["total"], 0.0)

    def test_exception_outcome_scoring(self):
        from harness.tasks import Raises
        task = fixture_task(ENV, [("target", fixture_program(name="answer"), Raises("ValueError"))])
        env = wired_env(ENV, task, self.sandbox, VERIFIER)
        self.sandbox.do_reset({"files": {"fixture_mod.py": "def answer():\n    raise ValueError('x')\n"}})
        self.assertEqual(env.score()["total"], 0.7)      # single target case that raises as expected
        self.sandbox.do_reset({"files": {"fixture_mod.py": FIXTURE_FIXED}})
        self.assertEqual(env.score()["total"], 0.0)      # returns 42 instead of raising -> fail


if __name__ == "__main__":
    unittest.main()
