"""The environment: reset / step / score, backed by the sandbox and verifier services.

The agent's tools run in the sandbox. Hidden checks run in the verifier, which only the
harness can reach: score() copies the workspace out of the sandbox and sends it, with the
check code, to the verifier. Expected answers and the reward formula never leave here.
"""
import base64
import binascii
import hashlib
import json
from pathlib import PurePosixPath

import requests

from .tasks import Raises, build_task

PASS_SCORE = 0.95   # a run counts as a success at or above this score
SNAPSHOT_MAX_FILES = 500                # the same limits are enforced by the verifier
SNAPSHOT_MAX_FILE_BYTES = 1_000_000
SNAPSHOT_MAX_TOTAL_BYTES = 5_000_000
MAX_PATH_CHARS = 255


class Env:
    def __init__(self, sandbox_url, verifier_url, tool_timeout=60, verify_timeout=120):
        self.url = sandbox_url.rstrip("/")
        self.verifier_url = verifier_url.rstrip("/")
        self.tool_timeout = tool_timeout
        self.verify_timeout = verify_timeout
        self.task = None

    def _post(self, path, payload, timeout):
        response = requests.post(self.url + path, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _get(self, path, timeout):
        response = requests.get(self.url + path, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _verify(self, payload, timeout):
        response = requests.post(self.verifier_url + "/verify", json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()

    # ---------------------------------------------------------------- reset
    def reset(self, task_id, seed):
        """Start a clean episode. The same (task_id, seed) always gives the same start."""
        self.task = build_task(task_id, seed)
        self._post("/reset", {"files": self.task.files}, self.tool_timeout)
        return (f"Task: {self.task.instructions}\n\n"
                "The project is in the current directory. Use the tools to look at the files, "
                "make your changes and test them, then call `finish`.")

    # ----------------------------------------------------------------- step
    def step(self, name, args):
        """Run one tool call. Returns (text, is_error)."""
        result = self._post("/tool", {"name": name, "args": args}, self.tool_timeout)
        return result["output"], result["is_error"]

    def state_hash(self):
        files = requests.get(self.url + "/state", timeout=self.tool_timeout).json()["files"]
        return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:8]

    # ---------------------------------------------------------------- score
    def score(self):
        """Programmatic verifier. Returns the reward components and their total.

        reward = tests_passed + no_regressions
          tests_passed   = 0.7 x share of target checks that pass
          no_regressions = 0.3 x share of regression checks that pass, but only
                           in proportion to target progress, so doing nothing earns 0
        """
        cases = self.task.cases
        files, problem = check_snapshot(self._get("/snapshot", self.tool_timeout))
        if problem:     # an unusable workspace fails every check instead of being cut short
            return {"tests_passed": 0.0, "no_regressions": 0.0, "total": 0.0, "error": problem}
        results = self._verify({"files": files, "cases": [c.program for c in cases]},
                               self.verify_timeout)["results"]
        tally = {"target": [0, 0], "regression": [0, 0]}
        for case, result in zip(cases, results):
            if isinstance(case.expect, Raises):
                passed = (not result.get("ok")) and result.get("exc") == case.expect.name
            else:
                passed = result.get("ok") and _equal(result.get("result"), case.expect)
            tally[case.group][1] += 1
            tally[case.group][0] += 1 if passed else 0
        target = tally["target"][0] / max(1, tally["target"][1])
        regression = tally["regression"][0] / max(1, tally["regression"][1])
        components = {"tests_passed": round(0.7 * target, 4),
                      "no_regressions": round(0.3 * target * regression, 4)}
        components["total"] = round(sum(components.values()), 4)
        return components


def check_snapshot(snapshot):
    """Validate the sandbox's snapshot without trusting it. Returns (files, problem)."""
    if not isinstance(snapshot, dict):
        return None, "snapshot is not an object"
    if "error" in snapshot:
        return None, f"snapshot refused: {str(snapshot['error'])[:200]}"
    files = snapshot.get("files")
    if not isinstance(files, dict):
        return None, "snapshot has no file list"
    if len(files) > SNAPSHOT_MAX_FILES:
        return None, "snapshot has too many files"
    total = 0
    for name, data in files.items():
        if not _safe_relative(name):
            return None, "snapshot has an invalid file path"
        if not isinstance(data, str):
            return None, "snapshot file content is not text"
        try:
            size = len(base64.b64decode(data, validate=True))
        except (binascii.Error, ValueError):
            return None, "snapshot file content is not valid base64"
        total += size
        if size > SNAPSHOT_MAX_FILE_BYTES or total > SNAPSHOT_MAX_TOTAL_BYTES:
            return None, "snapshot is over the size limit"
    return files, None


def _safe_relative(name):
    if not isinstance(name, str) or not name or len(name) > MAX_PATH_CHARS:
        return False
    if "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    return not any(part in ("", ".", "..") for part in PurePosixPath(name).parts)


def _equal(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-9
    return a == b
