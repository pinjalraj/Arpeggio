"""A. Can an agent-created module (json.py) run inside hidden-check execution?

Affected code: sandbox/server.py -> CHILD, do_verify (runs `python -c CHILD` with cwd = workspace copy).
Secure behaviour: files written by the agent must not execute before the checker's own imports.
The fixture json.py only writes a harmless canary file in a temporary folder.
"""
import unittest

from _helpers import FIXTURE_CHECK, FIXTURE_FIXED, TempWorkspace, load_sandbox


class ModuleShadowingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.server = load_sandbox(self.tmp.workspace)
        self.canary = self.tmp.outside / "canary.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_agent_json_module_is_not_imported_by_checker(self):
        shadow = f"open({str(self.canary)!r}, 'w').write('imported')\n"
        self.server.do_reset({"files": {"fixture_mod.py": FIXTURE_FIXED, "json.py": shadow}})
        results = self.server.do_verify({"cases": [FIXTURE_CHECK]})["results"]
        self.assertFalse(
            self.canary.exists(),
            f"agent-written json.py ran inside the hidden-check process; verifier returned {results}")


if __name__ == "__main__":
    unittest.main()
