"""Regression: provider-specific tool_call metadata (e.g. Gemini's
`extra_content.google.thought_signature`) must survive a tool-call round trip.

The SDK returns the signature inside an assistant tool_call; the harness must capture it when
parsing the reply and replay it unchanged when rebuilding the next request, without inventing or
hardcoding anything and without overriding the core tool_call fields.
"""
import types
import unittest

from _helpers import load_env_module

ENV = load_env_module()          # ensures repo on path + requests stub
import importlib
agents = importlib.import_module("harness.agents")
runner = importlib.import_module("harness.runner")

SIG = {"extra_content": {"google": {"thought_signature": "SIG-abc123"}}}


def fake_message(model_extra):
    call = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name="write_file", arguments='{"path": "x.py", "content": "y"}'),
        model_extra=model_extra)
    return types.SimpleNamespace(content=None, tool_calls=[call])


class ThoughtSignatureTest(unittest.TestCase):
    def test_parse_captures_extra_content(self):
        calls = agents.tool_calls_from_message(fake_message(dict(SIG)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "write_file")
        self.assertEqual(calls[0].extra, SIG)

    def test_round_trip_replays_signature_unchanged(self):
        calls = agents.tool_calls_from_message(fake_message(dict(SIG)))
        message = runner.assistant_message(agents.Reply(text=None, tool_calls=calls))
        tc = message["tool_calls"][0]
        self.assertEqual(tc["id"], "call_1")
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["function"]["name"], "write_file")
        self.assertEqual(tc["function"]["arguments"], '{"path": "x.py", "content": "y"}')
        # the signature is replayed exactly, at the same place the SDK returned it
        self.assertEqual(tc["extra_content"]["google"]["thought_signature"], "SIG-abc123")

    def test_no_extra_is_backward_compatible(self):
        # A model that returns no extra fields yields a plain tool_call (no stray keys).
        calls = agents.tool_calls_from_message(fake_message(None))
        self.assertEqual(calls[0].extra, {})
        tc = runner.assistant_message(agents.Reply(text=None, tool_calls=calls))["tool_calls"][0]
        self.assertEqual(set(tc), {"id", "type", "function"})

    def test_extra_never_overrides_core_fields(self):
        calls = agents.tool_calls_from_message(fake_message({"id": "spoof", "type": "spoof", **SIG}))
        tc = runner.assistant_message(agents.Reply(text=None, tool_calls=calls))["tool_calls"][0]
        self.assertEqual(tc["id"], "call_1")          # real id kept
        self.assertEqual(tc["type"], "function")      # real type kept
        self.assertEqual(tc["extra_content"], SIG["extra_content"])

    def test_mock_tool_calls_have_no_extra(self):
        # The offline mock client builds ToolCall positionally; extra defaults to empty.
        tc = agents.ToolCall("mock-1", "finish", "{}")
        self.assertEqual(tc.extra, {})


if __name__ == "__main__":
    unittest.main()
