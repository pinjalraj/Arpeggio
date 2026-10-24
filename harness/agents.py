"""Agents: the model client, two offline mock models, and the baselines."""
import json
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str          # raw text exactly as the model sent it
    extra: dict = field(default_factory=dict)   # provider-specific tool_call fields to replay
                                                 # as-is, e.g. Gemini's extra_content.google.thought_signature

    def parse(self):
        """Return (args, error). `args` is a dict when the arguments are valid JSON."""
        try:
            args = json.loads(self.arguments) if self.arguments else {}
        except json.JSONDecodeError as exc:
            return None, f"arguments are not valid JSON ({exc.msg})"
        if not isinstance(args, dict):
            return None, "arguments must be a JSON object"
        return args, None

    def history_arguments(self):
        """Arguments as stored in the conversation. Invalid JSON is replaced with {} so
        the next request is still accepted by every provider."""
        args, error = self.parse()
        return json.dumps(args) if error is None else "{}"


@dataclass
class Reply:
    text: str | None
    tool_calls: list = field(default_factory=list)
    raw: str = ""
    tokens: int = 0


def tool_calls_from_message(message):
    """Parse the SDK message's tool calls, keeping any non-standard per-call fields (such as
    Gemini's `extra_content`) so they can be replayed on the next request exactly as returned."""
    calls = []
    for index, call in enumerate(getattr(message, "tool_calls", None) or []):
        function = getattr(call, "function", None)
        extra = dict(getattr(call, "model_extra", None) or {})   # fields outside the OpenAI schema
        calls.append(ToolCall(id=call.id or f"call_{index}",
                              name=(function.name if function else "") or "",
                              arguments=(function.arguments if function else "") or "",
                              extra=extra))
    return calls


# ------------------------------------------------------------ real models
class OpenAIClient:
    """Calls any model through an OpenAI-compatible endpoint.

    Judges and teams are given a base_url, an api_key and a model name; the harness reads
    them from the environment variables BASE_URL, API_KEY and MODEL. The same call is used
    for every model, so switching models only means changing those values.
    """

    def __init__(self, model, api_key, base_url=None, max_tokens=None):
        import openai
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url or None, max_retries=2)
        self.model, self.max_tokens = model, max_tokens

    def start(self, task):
        pass

    def complete(self, messages, tools, timeout):
        kwargs = dict(model=self.model, messages=messages, tools=tools, timeout=timeout)
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens      # optional: set MAX_TOKENS to cap each reply
        response = self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        calls = tool_calls_from_message(message)
        usage = getattr(response, "usage", None)
        return Reply(text=message.content, tool_calls=calls, raw=response.model_dump_json(),
                     tokens=getattr(usage, "total_tokens", 0) or 0)


# ------------------------------------------------------------ mock models
class MockClient:
    """Offline stand-ins so the template runs with no API key.

    mock/solver  writes the reference solution, then finishes.
    mock/idle    finishes immediately without doing anything.
    """

    def __init__(self, kind):
        self.kind = kind
        self.turn = 0
        self.task = None

    def start(self, task):
        self.task, self.turn = task, 0

    def complete(self, messages, tools, timeout):
        self.turn += 1
        calls = []
        if self.kind == "solver" and self.turn == 1:
            for path, content in self.task.reference.items():
                calls.append(("write_file", {"path": path, "content": content}))
            calls.append(("run_python", {"code": "print('checked')"}))
        else:
            calls.append(("finish", {}))
        tool_calls = [ToolCall(f"mock-{self.turn}-{i}", name, json.dumps(args))
                      for i, (name, args) in enumerate(calls)]
        return Reply(text=None, tool_calls=tool_calls, raw=json.dumps({"mock": self.kind, "turn": self.turn}))


def make_client(model, api_key, base_url, max_tokens=None):
    if model.startswith("mock/"):
        kind = model.split("/", 1)[1]
        if kind not in ("solver", "idle"):
            raise ValueError("mock models are 'mock/solver' and 'mock/idle'")
        return MockClient(kind)
    if not api_key:
        raise ValueError("API_KEY is not set")
    return OpenAIClient(model, api_key, base_url, max_tokens)


# -------------------------------------------------------------- baselines
def oracle_actions(task):
    """The reference solution as a list of (tool, args) actions. Must score full marks."""
    return [("write_file", {"path": path, "content": content}) for path, content in task.reference.items()]


def noop_actions(task):
    """An agent that gives up immediately. Must score about zero."""
    return []
