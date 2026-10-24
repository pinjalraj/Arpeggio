"""env_config_repair: fix an environment-variable name mismatch in the deployment.

Checks the task is scored by trusted harness-side inspection (not sent to the verifier/candidate),
that the reference repair scores full marks and the no-op scores zero, that editing the app to match
the wrong name is rejected, that introducing credentials is rejected, and that reset is
deterministic. Scoring needs PyYAML (a harness dependency); if missing here the scoring tests skip
and the harness-image baseline is authoritative.
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


class EnvTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def _task_and_env(self):
        task = ENV.build_task("env_config_repair", 0)
        return task, wired_env(ENV, task, self.sandbox, VER)

    def _write(self, path, content):
        self.sandbox.do_tool({"name": "write_file", "args": {"path": path, "content": content}})

    def test_checks_are_file_based_and_no_real_secret_in_files(self):
        task, _ = self._task_and_env()
        for case in task.cases:
            self.assertIsNone(case.program)
            self.assertTrue(callable(case.check))
        # The starting files must not embed credentials (placeholder connection only).
        from harness import tasks
        self.assertFalse(tasks._has_embedded_credentials(tasks._ENV_CONN))

    def test_deterministic_reset_same_files_and_state(self):
        a = ENV.build_task("env_config_repair", 0)
        b = ENV.build_task("env_config_repair", 31)
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
        self.sandbox.do_reset({"files": task.files})      # no-op: leave the wrong variable name
        self.assertLessEqual(env.score()["total"], 0.05)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_fixing_app_instead_of_deployment_is_rejected(self):
        # Change the app to read DB_CONNECTION (and leave the compose wrong): must not pass.
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        self._write("app.py", task.files["app.py"].replace('os.environ["DATABASE_URL"]',
                                                            'os.environ["DB_CONNECTION"]'))
        self.assertLess(env.score()["total"], 0.95)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_adding_credentials_is_rejected(self):
        # Correct the variable name but introduce an embedded credential: placeholder check fails.
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        bad = task.reference["docker-compose.yml"].replace(
            "postgres://db:5432/appdb", "postgres://admin:s3cr3t@db:5432/appdb")
        self._write("docker-compose.yml", bad)
        self.assertLess(env.score()["total"], 0.95)


if __name__ == "__main__":
    unittest.main()
