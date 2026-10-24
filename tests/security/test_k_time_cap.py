"""Time-cap enforcement in the episode loop (bounded-overrun fix).

The 10-minute episode deadline is checked between steps AND before each tool call within a reply:
once no episode time remains, no further tool calls run, the episode stops before another model
step, and the termination is recorded as time_cap. Env.step also caps each request to the remaining
budget (never above the per-tool timeout, never below 1s). These are not a hard real-time stop: an
in-flight request can still outlive its client timeout; see the report.
"""
import json
import tempfile
import unittest
from pathlib import Path

from _helpers import load_env_module

ENV = load_env_module()
import importlib
runner = importlib.import_module("harness.runner")
from harness.agents import Reply, ToolCall


class ScriptClient:
    """Returns a scripted reply (a list of tool names) on each complete() call."""
    def __init__(self, replies):
        self.replies, self.i = replies, 0

    def start(self, task):
        pass

    def complete(self, messages, tools, timeout):
        names = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return Reply(text=None, tool_calls=[ToolCall(f"c{j}", n, "{}") for j, n in enumerate(names)])


class CountEnv:
    task = object()

    def __init__(self):
        self.steps = 0

    def reset(self, task_id, seed):
        return "go"

    def state_hash(self):
        return "hash"

    def score(self):
        return {"tests_passed": 0.0, "no_regressions": 0.0, "total": 0.0}

    def step(self, name, args, timeout=None):
        self.steps += 1
        return "ran", False


def _run(env, client, limits):
    path = Path(tempfile.mkdtemp()) / "ep.jsonl"
    log = runner.JsonlLog(path)
    try:
        result = runner.run_episode(env, "t", 0, client, "", limits, log)
    finally:
        log.close()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    return result, records


class TimeCapTest(unittest.TestCase):
    def test_expired_deadline_runs_no_tools_and_stops(self):
        env = CountEnv()
        result, records = _run(env, ScriptClient([["list_files"]]), runner.Limits(max_steps=5, max_seconds=0))
        self.assertEqual(result["termination"], "time_cap")
        self.assertEqual(env.steps, 0, "a tool ran after the deadline")
        step_rows = [r for r in records if r.get("step")]
        self.assertEqual(len(step_rows), 1, "started another step after the deadline")
        self.assertEqual(step_rows[0]["tool_result"], ["not run: episode time cap reached"])

    def test_deadline_during_multi_tool_reply(self):
        env = CountEnv()
        result, records = _run(env, ScriptClient([["list_files", "read_file", "write_file"]]),
                               runner.Limits(max_steps=5, max_seconds=0))
        self.assertEqual(result["termination"], "time_cap")
        self.assertEqual(env.steps, 0)
        step_rows = [r for r in records if r.get("step")]
        self.assertEqual(step_rows[0]["tool_result"],
                         ["not run: episode time cap reached"] * 3)
        kinds = [e["kind"] for e in step_rows[0]["errors"]]
        self.assertEqual(kinds.count("time_cap"), 3)

    def test_time_cap_termination_is_recorded(self):
        _, records = _run(CountEnv(), ScriptClient([["list_files"]]), runner.Limits(max_steps=5, max_seconds=0))
        end = [r for r in records if r.get("event") == "episode_end"]
        self.assertEqual(len(end), 1)
        self.assertEqual(end[0]["termination"], "time_cap")

    def test_normal_run_still_executes_tools_within_budget(self):
        env = CountEnv()
        result, _ = _run(env, ScriptClient([["list_files"], ["finish"]]),   # list_files needs no args
                         runner.Limits(max_steps=5, max_seconds=600))
        self.assertNotEqual(result["termination"], "time_cap")
        self.assertEqual(env.steps, 1, "the tool before the deadline did not run")


class EnvStepTimeoutCapTest(unittest.TestCase):
    def _env(self):
        env = ENV.Env("http://unused", "http://unused")   # tool_timeout defaults to 60
        self.captured = {}
        env._post = lambda path, payload, timeout: (self.captured.update(t=timeout)
                                                    or {"output": "ok", "is_error": False})
        return env

    def test_timeout_is_floored_capped_and_defaulted(self):
        env = self._env()
        env.step("list_files", {}, timeout=0.1)
        self.assertEqual(self.captured["t"], 1)          # never below 1s
        env.step("list_files", {}, timeout=-5)
        self.assertEqual(self.captured["t"], 1)          # never zero/negative
        env.step("list_files", {}, timeout=5)
        self.assertEqual(self.captured["t"], 5)          # uses the remaining budget
        env.step("list_files", {}, timeout=1000)
        self.assertEqual(self.captured["t"], env.tool_timeout)   # never above the per-tool timeout
        env.step("list_files", {}, timeout=None)
        self.assertEqual(self.captured["t"], env.tool_timeout)   # default when not supplied


if __name__ == "__main__":
    unittest.main()
