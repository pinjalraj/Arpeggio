"""ci_workflow_repair: the second DevOps task (GitHub Actions workflow repair).

Verifies the acceptance checks are trusted harness-side file inspections (not sent to the
verifier/candidate), that the reference repair scores full marks and the no-op scores zero, that
a wrong repair (editing the app/tests) is rejected, that the YAML 1.1 `on`->True quirk is handled,
and that reset is deterministic. Scoring checks need PyYAML (a harness dependency); if it is
missing on this runner they skip, and the harness-image baseline gate is the authoritative evidence.
"""
import unittest

from _helpers import TempWorkspace, load_env_module, load_sandbox, load_verifier, wired_env

ENV = load_env_module()
VER = load_verifier()
try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except Exception:
    HAVE_YAML = False


class CiTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def _task_and_env(self):
        task = ENV.build_task("ci_workflow_repair", 0)
        return task, wired_env(ENV, task, self.sandbox, VER)

    def _write(self, path, content):
        self.sandbox.do_tool({"name": "write_file", "args": {"path": path, "content": content}})

    def test_registered_and_parse_duration_retired(self):
        from harness import tasks
        self.assertIn("ci_workflow_repair", tasks.TASKS)
        self.assertNotIn("parse_duration", tasks.TASKS)
        self.assertGreaterEqual(len(tasks.TASKS), 5)

    def test_checks_are_file_based_and_carry_no_program(self):
        task, _ = self._task_and_env()
        self.assertTrue(task.cases)
        for case in task.cases:
            self.assertIsNone(case.program)
            self.assertTrue(callable(case.check))

    def test_deterministic_reset_same_files_and_state(self):
        a = ENV.build_task("ci_workflow_repair", 0)
        b = ENV.build_task("ci_workflow_repair", 11)
        self.assertEqual(a.files, b.files)
        self.sandbox.do_reset({"files": a.files})
        h1 = self.sandbox.do_state()
        self.sandbox.do_reset({"files": b.files})
        h2 = self.sandbox.do_state()
        self.assertEqual(h1, h2)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here; validated in the harness image")
    def test_on_key_boolean_quirk_is_handled(self):
        from harness import tasks
        wf = tasks._safe_yaml(tasks._CI_BROKEN)
        # YAML 1.1 turns the bare key `on` into True; the helper must still find the push trigger.
        self.assertIn("push", tasks._workflow_events(wf))

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_reference_scores_full_and_noop_scores_zero(self):
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        for path, content in task.reference.items():
            self._write(path, content)
        self.assertGreaterEqual(env.score()["total"], 0.95)
        self.sandbox.do_reset({"files": task.files})      # no-op: leave the broken workflow
        self.assertLessEqual(env.score()["total"], 0.05)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_editing_app_or_tests_is_rejected(self):
        task, env = self._task_and_env()
        # Repair the workflow correctly but also tamper with the app: a regression check must fail.
        self.sandbox.do_reset({"files": task.files})
        for path, content in task.reference.items():
            self._write(path, content)
        self._write("calc.py", task.files["calc.py"].replace("a + b", "a + b + 1"))
        self.assertLess(env.score()["total"], 0.95)


if __name__ == "__main__":
    unittest.main()
