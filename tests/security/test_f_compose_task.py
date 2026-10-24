"""compose_port_alignment: the first DevOps config-repair task.

Verifies the task's acceptance checks are trusted, harness-side file inspections (not sent to
the verifier/candidate), that the reference repair scores full marks and the no-op scores zero,
that a wrong repair (editing the app instead of the compose) is rejected, and that reset is
deterministic. The scoring checks need PyYAML (a harness dependency); if it is missing on this
runner they skip, and the harness-image baseline gate is the authoritative evidence.
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


class ComposeTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def _task_and_env(self):
        task = ENV.build_task("compose_port_alignment", 0)
        return task, wired_env(ENV, task, self.sandbox, VER)

    def _write(self, path, content):
        self.sandbox.do_tool({"name": "write_file", "args": {"path": path, "content": content}})

    def test_checks_are_file_based_and_carry_no_program(self):
        task, _ = self._task_and_env()
        self.assertTrue(task.cases, "task has no checks")
        for case in task.cases:
            self.assertIsNone(case.program, "a file-check case must not send a program to the verifier")
            self.assertTrue(callable(case.check), "a file-check case needs a trusted check callable")

    def test_deterministic_reset_same_files_and_state(self):
        a = ENV.build_task("compose_port_alignment", 0)
        b = ENV.build_task("compose_port_alignment", 7)
        self.assertEqual(a.files, b.files, "files depend on the seed")
        self.sandbox.do_reset({"files": a.files})
        h1 = self.sandbox.do_state()
        self.sandbox.do_reset({"files": b.files})
        h2 = self.sandbox.do_state()
        self.assertEqual(h1, h2, "same task gives a different state hash")

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here; validated in the harness image")
    def test_reference_scores_full_and_noop_scores_zero(self):
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        for path, content in task.reference.items():
            self._write(path, content)
        self.assertGreaterEqual(env.score()["total"], 0.95, "reference did not score >= 0.95")
        self.sandbox.do_reset({"files": task.files})      # no-op: leave the broken compose in place
        self.assertLessEqual(env.score()["total"], 0.05, "no-op scored above 0.05")

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_wrong_repair_editing_the_app_is_rejected(self):
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        self._write("app.py", task.files["app.py"].replace("PORT = 8080", "PORT = 9090"))
        self.assertLess(env.score()["total"], 0.95, "changing the app instead of the compose passed")

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed here")
    def test_partial_repair_scores_below_pass(self):
        # Fix only the port mapping, leave the health check wrong: one target still fails.
        task, env = self._task_and_env()
        self.sandbox.do_reset({"files": task.files})
        self._write("docker-compose.yml", task.files["docker-compose.yml"].replace("8081:9090", "8081:8080"))
        total = env.score()["total"]
        self.assertLess(total, 0.95)
        self.assertGreater(total, 0.0)


@unittest.skipUnless(HAVE_YAML, "PyYAML not installed here; validated in the harness image")
class SafeYamlLimitsTest(unittest.TestCase):
    """The trusted harness-side YAML loader must reject oversized/complex input, not blow up."""

    def _tasks(self):
        from harness import tasks
        return tasks

    def test_normal_compose_files_load(self):
        tasks = self._tasks()
        for text in (tasks._COMPOSE_BROKEN, tasks._COMPOSE_FIXED):
            self.assertIn("services", tasks._safe_yaml(text))

    def test_oversized_yaml_is_rejected(self):
        tasks = self._tasks()
        big = "key: '" + ("x" * 70000) + "'\n"
        with self.assertRaises(ValueError):
            tasks._safe_yaml(big)

    def test_deeply_nested_yaml_is_rejected(self):
        tasks = self._tasks()
        deep = "".join("  " * i + "k:\n" for i in range(150))   # ~23 KB, depth 150 > 100
        with self.assertRaises(ValueError):
            tasks._safe_yaml(deep)

    def test_alias_heavy_yaml_is_rejected(self):
        tasks = self._tasks()
        doc = "base: &a [1, 2, 3]\nitems:\n" + "".join("  - *a\n" for _ in range(200))  # 200 aliases > 50
        with self.assertRaises(ValueError):
            tasks._safe_yaml(doc)

    def test_many_nodes_rejected(self):
        tasks = self._tasks()
        flat = "".join("- 1\n" for _ in range(10200))           # ~40 KB, >10000 nodes, under size cap
        with self.assertRaises(ValueError):
            tasks._safe_yaml(flat)


if __name__ == "__main__":
    unittest.main()
