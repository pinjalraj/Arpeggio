"""D. Episode reset must remove candidate files/processes, and the candidate must be confined.

Affected code: sandbox/server.py -> do_reset, _kill_candidate_processes, tool_run_python
(the privilege drop to the 'coder' user), _confine_shared_dirs.

The confinement and kill-by-uid guarantees only exist when run_python is dropped to a separate
unprivileged user, which needs the server to run as root with a 'coder' account present. That
holds in the container; off-container (host, or a container run as non-root) the drop is
inactive and those checks skip with a clear reason. `test_c_control_and_cleanup.py` covers the
within-architecture cleanup that works everywhere.
"""
import os
import unittest

from _helpers import TempWorkspace, load_sandbox


class ResetStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.server = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def _require_drop(self):
        if self.server.CAND_IDS is None:
            self.skipTest("candidate privilege drop inactive (needs root + 'coder' user); "
                          "confinement/termination are enforced and validated in the container")

    def test_workspace_files_are_cleared(self):
        """Control: files inside the workspace do not survive reset."""
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        self.server.do_tool({"name": "write_file", "args": {"path": "leftover.txt", "content": "hi"}})
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        self.assertFalse((self.tmp.workspace / "leftover.txt").exists())

    def test_candidate_runs_as_an_unprivileged_user(self):
        self._require_drop()
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        out = self.server.do_tool({"name": "run_python", "args": {"code": "import os; print(os.getuid())"}})
        self.assertFalse(out["is_error"], out)
        uid = int(out["output"].splitlines()[0])
        self.assertEqual(uid, self.server.CAND_IDS[0])
        self.assertNotEqual(uid, os.getuid(), "candidate ran as the same user as the server")

    def test_candidate_cannot_write_outside_its_area(self):
        self._require_drop()
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        allowed = self.server.do_tool({"name": "run_python", "args": {
            "code": "import os; open(os.path.join(os.environ['TMPDIR'], 'ok'), 'w').write('x'); print('ok')"}})
        self.assertFalse(allowed["is_error"], allowed)
        denied = self.server.do_tool({"name": "run_python", "args": {
            "code": "open('/opt/should_not_write.txt', 'w').write('x')"}})
        self.assertTrue(denied["is_error"], "candidate wrote to a root-only path outside its area")

    def test_detached_candidate_process_is_killed_on_reset(self):
        self._require_drop()
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        # start_new_session makes the child leave run_python's tracked group (a plain escape).
        code = ("import subprocess\n"
                "p = subprocess.Popen(['sleep', '60'], start_new_session=True,\n"
                "                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
                "                     stderr=subprocess.DEVNULL)\n"
                "print(p.pid)\n")
        out = self.server.do_tool({"name": "run_python", "args": {"code": code}})
        self.assertFalse(out["is_error"], out)
        pid = int(out["output"].splitlines()[0])
        self.assertTrue(os.path.exists(f"/proc/{pid}"), "detached process did not start")
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        self.assertFalse(os.path.exists(f"/proc/{pid}"), "detached candidate process survived reset")


if __name__ == "__main__":
    unittest.main()
