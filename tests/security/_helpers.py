"""Shared helpers for the security regression tests.

Every test runs against a throwaway temporary directory, never the real /workspace,
and uses small harmless fixture tasks defined here instead of the real hidden checks.
Tests assert the SECURE behaviour: a failing test means the weakness is reproduced.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True  # keep __pycache__ out of the repo


def load_sandbox(workspace):
    """Import sandbox/server.py as a fresh module whose WORKSPACE is `workspace`."""
    os.environ["WORKSPACE_DIR"] = str(workspace)
    try:
        spec = importlib.util.spec_from_file_location(
            f"sandbox_server_{id(workspace)}", REPO / "sandbox" / "server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.environ.pop("WORKSPACE_DIR", None)
    return module


def load_verifier():
    """Import verifier/server.py as a fresh module."""
    spec = importlib.util.spec_from_file_location("verifier_server", REPO / "verifier" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encode_files(files):
    """{path: text} -> the snapshot format {path: base64}."""
    import base64
    return {name: base64.b64encode(text.encode()).decode("ascii") for name, text in files.items()}


def wired_env(env_module, task, sandbox, verifier):
    """An Env whose HTTP calls go straight to in-process sandbox and verifier modules."""
    env = env_module.Env("http://sandbox.invalid", "http://verifier.invalid")
    env.task = task
    env._get = lambda path, timeout: {"/snapshot": sandbox.do_snapshot, "/state": sandbox.do_state}[path]()
    env._post = lambda path, payload, timeout: {"/reset": sandbox.do_reset, "/tool": sandbox.do_tool}[path](payload)
    env._verify = lambda payload, timeout: verifier.do_verify(payload)
    return env


def load_env_module():
    """Import harness/env.py. `requests` is not installed on the host, and score()
    only uses it through Env._post, which the tests replace, so a stub is enough."""
    if "requests" not in sys.modules:
        sys.modules["requests"] = types.ModuleType("requests")
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from harness import env
    return env


class TempWorkspace:
    """A temporary directory with a `workspace` folder and an `outside` folder."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="rlsec-"))
        self.workspace = self.root / "workspace"
        self.outside = self.root / "outside"
        self.workspace.mkdir()
        self.outside.mkdir()

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# Harmless fixture: a function whose correct answer is 42, and a buggy starting version.
FIXTURE_EXPECT = 42
FIXTURE_BUGGY = "def answer():\n    return 0\n"
FIXTURE_FIXED = "def answer():\n    return 42\n"

# A structured check program (the new format) that calls fixture_mod.answer().
FIXTURE_PROGRAM = {"module": "fixture_mod",
                   "steps": [{"op": "call", "name": "answer", "args": [], "record": True}]}
# A placeholder "program" for tests that stub the verifier and never run it (B tests).
FIXTURE_CHECK = FIXTURE_PROGRAM


def fixture_program(module="fixture_mod", name="answer", args=()):
    return {"module": module, "steps": [{"op": "call", "name": name, "args": list(args), "record": True}]}


def fixture_task(env_module, cases):
    """A TaskInstance-like object holding only harmless fixture cases."""
    from harness.tasks import Case, TaskInstance
    return TaskInstance(task_id="fixture", title="fixture", difficulty="easy", instructions="fixture",
                        files={"fixture_mod.py": FIXTURE_BUGGY},
                        cases=[Case(g, c, e) for g, c, e in cases],
                        reference={"fixture_mod.py": FIXTURE_FIXED})


def env_with_results(env_module, task, results):
    """An Env whose verifier answer is replaced by `results` (with an empty, valid snapshot)."""
    env = env_module.Env("http://unused", "http://unused")
    env.task = task
    env._get = lambda path, timeout: {"files": {}}
    env._verify = lambda payload, timeout: {"results": results}
    return env
