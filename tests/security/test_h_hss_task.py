"""healthy_service_startup: a two-service readiness-dependency repair task.

Verifies the acceptance checks are trusted harness-side file inspections (not sent to the
verifier/candidate), that the reference repair scores full marks and the no-op scores zero, that
regressions pin the unrelated config, and that reset is deterministic. Scoring needs PyYAML (a
harness dependency); if missing here the scoring tests skip and the harness-image baseline is
authoritative.
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


class HssTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def _task_and_env(self):
        task = ENV.build_task("healthy_service_startup", 0)
        return task, wired_env(ENV, task, self.sandbox, VER)

    def _write(self, path, content):
        self.sandbox.do_tool({"name": "write_file", "args": {"path": path, "content": content}})

    def test_checks_are_file_based_and_carry_no_program(self):
        task, _ = self._task_and_env()
        self.assertTrue(task.cases)
        for case in task.cases:
            self.assertIsNone(case.program)
            self.assertTrue(callable(case.check))

    def test_deterministic_reset_same_files_and_state(self):
        a = ENV.build_task("healthy_service_startup", 0)
        b = ENV.build_task("healthy_service_startup", 23)
        self.assertEqual(a.files, b.files)
        self.sandbox.do_reset({"files": a.files})
        h1 = self.sandbox.do_state()
        self.sandbox.do_reset({"files": b.files})
        h2 = self.sandbox.do_state()
        self.assertEqual(h1, h2)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here; validated in the harness image")
    def test_reference_scores_full_and_noop_scores_zero(self):
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        for path, content in task.reference.items():
            self._write(path, content)
        self.assertGreaterEqual(env.score()["total"], 0.95)
        self.sandbox.do_reset({"files": task.files})      # no-op: leave both faults in place
        self.assertLessEqual(env.score()["total"], 0.05)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_only_one_of_two_fixes_is_not_enough(self):
        # Add the condition but leave the health check wrong: one target still fails -> below pass.
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        half = task.files["docker-compose.yml"].replace(
            "    depends_on:\n      - db                       # only start order, not readiness\n",
            "    depends_on:\n      db:\n        condition: service_healthy\n")
        self._write("docker-compose.yml", half)
        total = env.score()["total"]
        self.assertLess(total, 0.95)
        self.assertGreater(total, 0.0)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_changing_unrelated_config_is_rejected(self):
        # Apply the correct fix but also change the db image: a regression must fail.
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        tampered = task.reference["docker-compose.yml"].replace("postgres:16", "postgres:15")
        self._write("docker-compose.yml", tampered)
        self.assertLess(env.score()["total"], 0.95)


if __name__ == "__main__":
    unittest.main()
