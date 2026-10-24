"""C/D. Control endpoints require a token, reset clears managed temp state, and malformed
requests or backend failures produce controlled errors instead of crashing the harness.

Affected code: sandbox/server.py -> authorized, OPEN_PATHS, do_reset, do_tool, tool_run_python;
harness/runner.py -> run_episode (scoring/step error handling).

NOTE: these cover the within-architecture guarantees. Robust control-access against same-uid
candidate code, and guaranteed killing of processes that escape their group, need the separate
candidate identity proposed in section 14 and are NOT proven here.
"""
import os
import tempfile
import unittest
from pathlib import Path

from _helpers import TempWorkspace, load_env_module, load_sandbox

ENV = load_env_module()


class ControlAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_health_is_open_control_routes_are_not(self):
        self.assertIn("/health", self.sandbox.OPEN_PATHS)
        for path in ("/reset", "/tool", "/snapshot", "/state"):
            self.assertNotIn(path, self.sandbox.OPEN_PATHS)

    def test_token_required_when_configured(self):
        self.sandbox.TOKEN = "s3cret"
        try:
            self.assertFalse(self.sandbox.authorized({}))
            self.assertFalse(self.sandbox.authorized({"X-Sandbox-Token": "wrong"}))
            self.assertTrue(self.sandbox.authorized({"X-Sandbox-Token": "s3cret"}))
        finally:
            self.sandbox.TOKEN = ""

    def test_open_when_no_token_configured(self):
        self.sandbox.TOKEN = ""
        self.assertTrue(self.sandbox.authorized({}))

    def test_token_is_not_left_in_the_process_environment(self):
        # server.py pops SANDBOX_TOKEN from os.environ at import so it is not in /proc/<pid>/environ.
        self.assertNotIn("SANDBOX_TOKEN", os.environ)


class ResetCleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reset_wipes_the_managed_candidate_temp_dir(self):
        self.sandbox.do_reset({"files": {"a.py": "x = 1\n"}})
        cand = Path(self.sandbox.CAND_TMP)
        (cand / "leftover.txt").write_text("from episode 1")
        self.sandbox.do_reset({"files": {"a.py": "x = 1\n"}})
        self.assertFalse((cand / "leftover.txt").exists(),
                         "managed candidate temp survived reset")

    def test_run_python_temp_lands_in_the_managed_dir_and_is_wiped(self):
        self.sandbox.do_reset({"files": {"a.py": "x = 1\n"}})
        code = ("import os\n"
                "open(os.path.join(os.environ['TMPDIR'], 'scratch.bin'), 'w').write('tmp')\n"
                "print('wrote')\n")
        out = self.sandbox.do_tool({"name": "run_python", "args": {"code": code}})
        self.assertFalse(out["is_error"], out)
        cand = Path(self.sandbox.CAND_TMP)
        self.assertTrue((cand / "scratch.bin").exists(), "run_python temp did not land in the managed dir")
        self.sandbox.do_reset({"files": {"a.py": "x = 1\n"}})
        self.assertFalse((cand / "scratch.bin").exists(), "run_python temp survived reset")


class ControlledErrorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)
        self.sandbox.do_reset({"files": {"a.py": "x = 1\n"}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_tool_is_a_controlled_error(self):
        out = self.sandbox.do_tool({"name": "nope", "args": {}})
        self.assertTrue(out["is_error"])
        self.assertIn("unknown tool", out["output"])

    def test_missing_argument_is_a_controlled_error(self):
        out = self.sandbox.do_tool({"name": "run_python", "args": {}})
        self.assertTrue(out["is_error"])
        self.assertTrue(out["output"].startswith("error:"))

    def test_run_python_timeout_is_reported_not_raised(self):
        self.sandbox.RUN_TIMEOUT = 1
        try:
            out = self.sandbox.do_tool({"name": "run_python",
                                        "args": {"code": "import time\nwhile True:\n    time.sleep(1)\n"}})
        finally:
            self.sandbox.RUN_TIMEOUT = 20
        self.assertFalse(out["is_error"])          # the tool handled it; the episode continues
        self.assertIn("longer than", out["output"])


class RunnerFailsSafeTest(unittest.TestCase):
    """A backend failure during scoring ends the episode as a controlled crash, not a traceback."""

    def test_scoring_exception_becomes_crash(self):
        import importlib
        runner = importlib.import_module("harness.runner")
        from harness.agents import Reply, ToolCall

        class FakeEnv:
            task = object()

            def reset(self, task_id, seed):
                return "do the task"

            def state_hash(self):
                return "deadbeef"

            def score(self):
                raise RuntimeError("verifier unreachable")

        class FakeClient:
            def start(self, task):
                pass

            def complete(self, messages, tools, timeout):
                return Reply(text=None, tool_calls=[ToolCall("1", "finish", "{}")])

        log_path = Path(tempfile.mkdtemp()) / "ep.jsonl"
        log = runner.JsonlLog(log_path)
        try:
            result = runner.run_episode(FakeEnv(), "t", 0, FakeClient(), "",
                                        runner.Limits(max_steps=3), log)
        finally:
            log.close()
        self.assertEqual(result["termination"], "crash")
        self.assertIsNone(result["reward"]["total"])


if __name__ == "__main__":
    unittest.main()
