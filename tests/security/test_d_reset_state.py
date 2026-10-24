"""D. Do files survive a reset and remain visible to a later episode?

Affected code: sandbox/server.py -> do_reset (clears WORKSPACE only), tool_run_python.
The "outside" folder is a temporary stand-in for places like /tmp or the sandbox user's home.
Process survival is not tested here: it needs a disposable container (see the report).
"""
import unittest

from _helpers import TempWorkspace, load_sandbox


class ResetStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.server = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_workspace_files_are_cleared(self):
        """Control: files inside the workspace do not survive reset."""
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        self.server.do_tool({"name": "write_file", "args": {"path": "leftover.txt", "content": "hi"}})
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        self.assertFalse((self.tmp.workspace / "leftover.txt").exists())

    def test_files_written_outside_workspace_are_cleared(self):
        note = self.tmp.outside / "note.txt"
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        out = self.server.do_tool({"name": "run_python",
                                   "args": {"code": f"open({str(note)!r}, 'w').write('episode 1')"}})
        self.assertFalse(out["is_error"], out)
        self.server.do_reset({"files": {"a.py": "x = 1\n"}})
        seen = self.server.do_tool({"name": "run_python",
                                    "args": {"code": f"import os; print(os.path.exists({str(note)!r}))"}})
        self.assertFalse(note.exists(),
                         f"file from episode 1 survived reset; episode 2 run_python saw: {seen['output']!r}")


if __name__ == "__main__":
    unittest.main()
