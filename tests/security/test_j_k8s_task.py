"""k8s_service_probe: fix a Kubernetes readinessProbe port and Service targetPort.

Checks the task is scored by trusted harness-side inspection (not sent to the verifier/candidate),
that the reference repair scores full marks and the no-op scores zero, that fixing only one manifest
is not enough, that editing the app or weakening unrelated settings is rejected, and that reset is
deterministic. Scoring needs PyYAML; if missing here those tests skip and the harness-image baseline
is authoritative.
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


class K8sTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def _task_and_env(self):
        task = ENV.build_task("k8s_service_probe", 0)
        return task, wired_env(ENV, task, self.sandbox, VER)

    def _write(self, path, content):
        self.sandbox.do_tool({"name": "write_file", "args": {"path": path, "content": content}})

    def test_checks_are_file_based(self):
        task, _ = self._task_and_env()
        self.assertTrue(task.cases)
        for case in task.cases:
            self.assertIsNone(case.program)
            self.assertTrue(callable(case.check))

    def test_deterministic_reset_same_files_and_state(self):
        a = ENV.build_task("k8s_service_probe", 0)
        b = ENV.build_task("k8s_service_probe", 42)
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
        self.sandbox.do_reset({"files": task.files})      # no-op: leave both wrong ports
        self.assertLessEqual(env.score()["total"], 0.05)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_fixing_only_one_manifest_is_not_enough(self):
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        self._write("deployment.yaml", task.reference["deployment.yaml"])   # fix probe only
        total = env.score()["total"]
        self.assertLess(total, 0.95)
        self.assertGreater(total, 0.0)

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_editing_app_or_weakening_settings_is_rejected(self):
        # Correct both ports but also drop replicas: a regression must fail.
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        self._write("service.yaml", task.reference["service.yaml"])
        self._write("deployment.yaml", task.reference["deployment.yaml"].replace("replicas: 3", "replicas: 1"))
        self.assertLess(env.score()["total"], 0.95)


if __name__ == "__main__":
    unittest.main()
