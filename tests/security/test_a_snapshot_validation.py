"""A (supporting). Workspace snapshots must stay inside the workspace and within size limits,
and neither the harness nor the verifier may trust paths supplied in a snapshot.

Affected code: sandbox/server.py -> do_snapshot; verifier/server.py -> clean_path, decode_files,
write_project; harness/env.py -> check_snapshot, Env.score.
"""
import base64
import os
import unittest

from _helpers import (FIXTURE_CHECK, FIXTURE_FIXED, FIXTURE_PROGRAM, TempWorkspace, encode_files,
                      fixture_program, fixture_task, load_env_module, load_sandbox, load_verifier)

ENV = load_env_module()
VERIFIER = load_verifier()
BAD_PATHS = ["../escape.py", "/abs.py", "a/../../escape.py", "..", "a\\..\\escape.py", "x\x00.py", "",
             "a" * 300]


class VerifierInputTest(unittest.TestCase):
    def verify(self, files, cases=(FIXTURE_PROGRAM,)):
        return VERIFIER.do_verify({"files": files, "cases": list(cases)})

    def test_rejects_traversal_and_absolute_paths(self):
        for name in BAD_PATHS:
            with self.subTest(path=name):
                with self.assertRaises(VERIFIER.BadRequest):
                    self.verify({name: base64.b64encode(b"x = 1\n").decode()})

    def test_error_messages_do_not_echo_request_content(self):
        with self.assertRaises(VERIFIER.BadRequest) as caught:
            self.verify({"../SECRET-NAME.py": "eA=="})
        self.assertNotIn("SECRET-NAME", str(caught.exception))

    def test_rejects_oversized_and_malformed_snapshots(self):
        big = base64.b64encode(b"x" * (VERIFIER.MAX_FILE_BYTES + 1)).decode()
        chunk = base64.b64encode(b"x" * VERIFIER.MAX_FILE_BYTES).decode()
        cases = {
            "one file too big": {"big.txt": big},
            "total too big": {f"f{i}.txt": chunk for i in range(VERIFIER.MAX_TOTAL_BYTES // VERIFIER.MAX_FILE_BYTES + 1)},
            "too many files": {f"f{i}.txt": "" for i in range(VERIFIER.MAX_FILES + 1)},
            "not base64": {"a.py": "not base64!!"},
            "content not text": {"a.py": 123},
            "file and folder clash": {"a": "eA==", "a/b.py": "eA=="},
            "files not an object": ["a.py"],
        }
        for label, files in cases.items():
            with self.subTest(label):
                with self.assertRaises(VERIFIER.BadRequest):
                    self.verify(files)

    def test_rejects_malformed_or_arbitrary_cases(self):
        files = encode_files({"fixture_mod.py": FIXTURE_FIXED})
        bad = [
            "not a list",
            [1],                                                   # not an object
            ["from fixture_mod import answer"],                    # arbitrary source, not a program
            [{"module": "fixture_mod"}],                           # no steps
            [{"module": "../evil", "steps": [{"op": "call", "name": "answer"}]}],   # bad module
            [{"module": "fixture_mod", "steps": [{"op": "exec", "name": "answer"}]}],  # bad op
            [{"module": "fixture_mod", "steps": [{"op": "call", "name": "a b"}]}],  # bad name
            [{"module": "fixture_mod", "steps": [{"op": "call", "name": "answer", "args": "x"}]}],  # args not list
        ]
        for cases in bad:
            with self.subTest(cases=str(cases)[:40]):
                with self.assertRaises(VERIFIER.BadRequest):
                    VERIFIER.do_verify({"files": files, "cases": cases})

    def test_valid_nested_package_program_still_works(self):
        files = encode_files({"pkg/__init__.py": "", "pkg/fixture_mod.py": FIXTURE_FIXED})
        results = self.verify(files, [fixture_program(module="pkg.fixture_mod")])["results"]
        self.assertEqual(results, [{"ok": True, "result": 42}])


class SandboxSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWorkspace()
        self.sandbox = load_sandbox(self.tmp.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def symlink_or_skip(self, target, link, is_dir=False):
        try:
            os.symlink(target, link, target_is_directory=is_dir)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlinks here ({type(exc).__name__}); run on Linux")

    def test_symlinks_out_of_the_workspace_are_not_followed(self):
        secret_file = self.tmp.outside / "outside.txt"
        secret_file.write_text("OUTSIDE-DATA")
        (self.tmp.outside / "dir").mkdir()
        (self.tmp.outside / "dir" / "inner.txt").write_text("OUTSIDE-DATA")
        self.sandbox.do_reset({"files": {"a.py": "x = 1\n"}})
        self.symlink_or_skip(secret_file, self.tmp.workspace / "link.txt")
        self.symlink_or_skip(self.tmp.outside / "dir", self.tmp.workspace / "linkdir", is_dir=True)
        snapshot = self.sandbox.do_snapshot()
        self.assertEqual(sorted(snapshot["files"]), ["a.py"])
        self.assertNotIn("OUTSIDE-DATA", str(snapshot))

    def test_oversized_workspace_is_refused_not_truncated(self):
        self.sandbox.do_reset({"files": {"a.py": "x = 1\n"}})
        (self.tmp.workspace / "big.bin").write_bytes(b"x" * (self.sandbox.SNAPSHOT_MAX_FILE_BYTES + 1))
        self.assertIn("error", self.sandbox.do_snapshot())

    def test_snapshot_round_trips_workspace_bytes(self):
        self.sandbox.do_reset({"files": {"a.py": "x = 'é'\n", "sub/b.txt": "hi"}})
        files = self.sandbox.do_snapshot()["files"]
        on_disk = {"a.py": (self.tmp.workspace / "a.py").read_bytes(),
                   "sub/b.txt": (self.tmp.workspace / "sub" / "b.txt").read_bytes()}
        self.assertEqual({k: base64.b64decode(v) for k, v in files.items()}, on_disk)


class HarnessDistrustsSnapshotTest(unittest.TestCase):
    def env_with_snapshot(self, snapshot):
        env = ENV.Env("http://unused", "http://unused")
        env.task = fixture_task(ENV, [("target", FIXTURE_CHECK, 42)])
        env._get = lambda path, timeout: snapshot
        env.verifier_calls = 0

        def verify(payload, timeout):
            env.verifier_calls += 1
            return {"results": [{"ok": True, "result": 42}]}
        env._verify = verify
        return env

    def test_invalid_snapshots_score_zero_and_skip_the_verifier(self):
        bad = {
            "traversal path": {"files": {"../x.py": "eA=="}},
            "absolute path": {"files": {"/x.py": "eA=="}},
            "refused by sandbox": {"error": "workspace is over 5000000 bytes"},
            "not base64": {"files": {"a.py": "%%%"}},
            "no file list": {"files": "a.py"},
            "not an object": ["a.py"],
            "too big": {"files": {"a.py": base64.b64encode(b"x" * (ENV.SNAPSHOT_MAX_FILE_BYTES + 1)).decode()}},
        }
        for label, snapshot in bad.items():
            with self.subTest(label):
                env = self.env_with_snapshot(snapshot)
                reward = env.score()
                self.assertEqual(reward["total"], 0.0)
                self.assertIn("error", reward)
                self.assertEqual(env.verifier_calls, 0)


if __name__ == "__main__":
    unittest.main()
