"""Demo tasks. Replace these with your own.

Every task is built from a seed, so the same (task_id, seed) always gives the
same starting files, hidden checks and reference solution. Most tasks here
ignore the seed; `bulk_discount` uses it to generate a different variant each
time, which is a cheap way to get many tasks out of one design.

A hidden check is a STRUCTURED program, not a code snippet. It names a module and a
short list of calls to make on it with literal arguments. The verifier's worker runs
exactly those calls on the candidate's code and reports what came back; the harness
compares that against `expect`. The expected answer is never part of the program, so it
is never sent to the process that runs the candidate's code.
"""
import random
from dataclasses import dataclass


class Raises:
    """Expected outcome: the call raised this exception type (by name)."""
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"Raises({self.name!r})"


@dataclass
class Case:
    group: str      # "target": fails before the fix.  "regression": must keep passing.
    program: dict   # {"module": str, "steps": [...]} run by the verifier worker, or None for a file check
    expect: object  # compared in the harness only; never sent to the worker
    check: object = None  # trusted harness-side check(files)->result for config/file tasks (program is None)


@dataclass
class TaskInstance:
    task_id: str
    title: str
    difficulty: str
    instructions: str
    files: dict      # starting files the agent can see
    cases: list      # hidden checks (list of Case)
    reference: dict  # files that solve the task; used by the oracle baseline


# ---- builders for the structured check programs -------------------------------
def _call(name, args, record=True, into=None):
    step = {"op": "call", "name": name, "args": list(args)}
    if into:
        step["into"] = into
    if record:
        step["record"] = True
    return step


def _method(recv, name, args, record=False, into=None):
    step = {"op": "method", "recv": recv, "name": name, "args": list(args)}
    if into:
        step["into"] = into
    if record:
        step["record"] = True
    return step


def _prog(module, *steps):
    return {"module": module, "steps": list(steps)}


# ---------------------------------------------------------------- slugify
def slugify_task(seed):
    def check(group, text, expect):
        return Case(group, _prog("textutils", _call("slugify", [text])), expect)
    return TaskInstance(
        task_id="slugify", title="Fix slugify", difficulty="easy",
        instructions=("`textutils.slugify` should turn a title into a URL slug: lowercase, "
                      "punctuation removed, any run of spaces or dashes collapsed into a single "
                      "dash, and no leading or trailing dash. Fix it."),
        files={"textutils.py": 'def slugify(text):\n    """Turn a title into a URL slug."""\n'
                               '    return text.lower().replace(" ", "-")\n'},
        cases=[
            check("target", "Hello, World!", "hello-world"),
            check("target", "  Multiple   spaces  ", "multiple-spaces"),
            check("target", "a--b", "a-b"),
            check("target", "C++ rocks", "c-rocks"),
            check("target", "---Already---dashed---", "already-dashed"),
            check("regression", "simple", "simple"),
            check("regression", "two words", "two-words"),
        ],
        reference={"textutils.py": 'import re\n\n\ndef slugify(text):\n'
                                   '    text = re.sub(r"[^a-z0-9]+", "-", text.lower())\n'
                                   '    return text.strip("-")\n'},
    )


# --------------------------------------------------------- parse_duration
def duration_task(seed):
    def check(group, text, expect):
        return Case(group, _prog("durations", _call("parse_duration", [text])), expect)
    return TaskInstance(
        task_id="parse_duration", title="Parse combined durations", difficulty="easy",
        instructions=("`durations.parse_duration` should also accept combined values such as '1h30m' "
                      "or '1h1m1s' and return the total number of seconds as an int. Invalid input "
                      "(an empty string, an unknown unit, or a number with no unit) must raise ValueError."),
        files={"durations.py": 'UNITS = {"s": 1, "m": 60, "h": 3600}\n\n\ndef parse_duration(text):\n'
                               '    """Convert a duration like \'90s\', \'5m\' or \'2h\' to seconds."""\n'
                               '    return int(text[:-1]) * UNITS[text[-1]]\n'},
        cases=[
            check("target", "1h30m", 5400),
            check("target", "1h1m1s", 3661),
            check("target", "5x", Raises("ValueError")),
            check("target", "10d", Raises("ValueError")),
            check("target", "30", Raises("ValueError")),
            check("regression", "45s", 45),
            check("regression", "10m", 600),
            check("regression", "2h", 7200),
            check("regression", "", Raises("ValueError")),
        ],
        reference={"durations.py": 'import re\n\nUNITS = {"s": 1, "m": 60, "h": 3600}\n'
                                   '_PART = re.compile(r"(\\d+)([smh])")\n\n\ndef parse_duration(text):\n'
                                   '    if not text or not re.fullmatch(r"(?:\\d+[smh])+", text):\n'
                                   '        raise ValueError(f"invalid duration: {text!r}")\n'
                                   '    return sum(int(n) * UNITS[u] for n, u in _PART.findall(text))\n'},
    )


# -------------------------------------------------------- merge_intervals
def intervals_task(seed):
    def check(group, data, expect):
        return Case(group, _prog("intervals", _call("merge_intervals", [data])), expect)
    return TaskInstance(
        task_id="merge_intervals", title="Merge intervals", difficulty="medium",
        instructions=("`intervals.merge_intervals` should merge overlapping [start, end] intervals and "
                      "return them as a list of [start, end] lists sorted by start. Intervals that touch, "
                      "such as [1, 2] and [2, 3], count as overlapping. The input may be unsorted."),
        files={"intervals.py": 'def merge_intervals(intervals):\n'
                               '    """Merge overlapping [start, end] intervals and return them sorted."""\n'
                               '    merged = []\n    for start, end in intervals:\n'
                               '        if merged and start < merged[-1][1]:\n'
                               '            merged[-1][1] = max(merged[-1][1], end)\n'
                               '        else:\n            merged.append([start, end])\n    return merged\n'},
        cases=[
            check("target", [[8, 10], [1, 3], [2, 6], [15, 18]], [[1, 6], [8, 10], [15, 18]]),
            check("target", [[1, 2], [2, 3]], [[1, 3]]),
            check("target", [[5, 6], [1, 5], [7, 8]], [[1, 6], [7, 8]]),
            check("target", [[2, 3], [1, 10]], [[1, 10]]),
            check("regression", [[1, 3], [5, 6]], [[1, 3], [5, 6]]),
            check("regression", [[1, 10], [2, 3]], [[1, 10]]),
            check("regression", [], []),
        ],
        reference={"intervals.py": 'def merge_intervals(intervals):\n    merged = []\n'
                                   '    for start, end in sorted(intervals):\n'
                                   '        if merged and start <= merged[-1][1]:\n'
                                   '            merged[-1][1] = max(merged[-1][1], end)\n'
                                   '        else:\n            merged.append([start, end])\n    return merged\n'},
    )


# -------------------------------------------------------------- lru_cache
def lru_task(seed):
    def scenario(group, ops, gets, expect):
        """ops: list of (method, args) setup calls; gets: list of get args to record."""
        steps = [_call("LRUCache", ops[0], record=False, into="c")]
        for method, args in ops[1]:
            steps.append(_method("c", method, args))
        for arg in gets:
            steps.append(_method("c", "get", [arg], record=True))
        return Case(group, _prog("cache", *steps), expect)
    return TaskInstance(
        task_id="lru_cache", title="Fix the LRU cache", difficulty="medium",
        instructions=("`cache.LRUCache` should evict the least recently used key when it is full. Both "
                      "`get` and `put` must mark a key as the most recently used. `get` returns -1 for a "
                      "missing key."),
        files={"cache.py": 'class LRUCache:\n    """A fixed-size cache that evicts the least recently used key."""\n\n'
                           '    def __init__(self, capacity):\n        self.capacity = capacity\n'
                           '        self.data = {}  # insertion order is age order\n\n'
                           '    def get(self, key):\n        return self.data.get(key, -1)\n\n'
                           '    def put(self, key, value):\n'
                           '        if len(self.data) >= self.capacity and key not in self.data:\n'
                           '            del self.data[next(iter(self.data))]\n'
                           '        self.data[key] = value\n'},
        cases=[
            scenario("target", ([2], [("put", [1, 1]), ("put", [2, 2]), ("get", [1]), ("put", [3, 3])]),
                     [1, 2, 3], [1, -1, 3]),
            scenario("target", ([2], [("put", [1, 1]), ("put", [2, 2]), ("put", [1, 10]), ("put", [3, 3])]),
                     [1, 2, 3], [10, -1, 3]),
            scenario("target", ([3], [("put", [1, 1]), ("put", [2, 2]), ("put", [3, 3]),
                                      ("get", [1]), ("get", [2]), ("put", [4, 4])]),
                     [1, 2, 3, 4], [1, 2, -1, 4]),
            scenario("regression", ([1], []), [5], -1),
            scenario("regression", ([1], [("put", [1, 1]), ("put", [2, 2])]), [1, 2], [-1, 2]),
        ],
        reference={"cache.py": 'class LRUCache:\n    def __init__(self, capacity):\n'
                               '        self.capacity = capacity\n        self.data = {}\n\n'
                               '    def get(self, key):\n        if key not in self.data:\n            return -1\n'
                               '        value = self.data.pop(key)\n        self.data[key] = value\n'
                               '        return value\n\n    def put(self, key, value):\n'
                               '        if key in self.data:\n            self.data.pop(key)\n'
                               '        elif len(self.data) >= self.capacity:\n'
                               '            del self.data[next(iter(self.data))]\n        self.data[key] = value\n'},
    )


# ---------------------------------------------- bulk_discount (seeded)
def discount_task(seed):
    rng = random.Random(seed)
    unit = rng.choice([4.99, 7.25, 12.5, 19.99])
    t1 = rng.randint(5, 9)
    t2 = t1 + rng.randint(5, 10)
    d1 = rng.choice([5, 8, 10])
    d2 = d1 + rng.choice([5, 7, 10])
    header = (f"UNIT_PRICE = {unit}\n"
              f"TIERS = [({t2}, {d2}), ({t1}, {d1})]  # (minimum quantity, percent off), highest first\n\n\n")
    body = ('def order_total(quantity):\n    """Total price for `quantity` items after the best tier discount."""\n'
            '    total = quantity * UNIT_PRICE\n    for minimum, percent in TIERS:\n'
            '        if quantity {cmp} minimum:\n            total = total * (100 - percent) / 100\n'
            '            break\n    return {ret}\n')
    buggy = header + body.format(cmp=">", ret="int(total * 100) / 100")
    fixed = header + body.format(cmp=">=", ret="round(total, 2)")

    def total(src, quantity):
        ns = {}
        exec(src, ns)                       # trusted: computes the expected answer in the harness
        return ns["order_total"](quantity)

    def case(group, q):
        return Case(group, _prog("pricing", _call("order_total", [q])), total(fixed, q))

    edge = [q for q in range(0, 40) if q not in (t1, t2) and total(buggy, q) != total(fixed, q)][:2]
    safe = [q for q in range(0, 40) if q not in (t1, t2) and total(buggy, q) == total(fixed, q)][:3]
    cases = [case("target", q) for q in [t1, t2] + edge] + [case("regression", q) for q in safe]
    return TaskInstance(
        task_id="bulk_discount", title="Fix the bulk discount (seeded)", difficulty="medium",
        instructions=("`pricing.order_total` should give an order the discount of the highest tier whose "
                      "minimum quantity it reaches (reaching the minimum counts), and round the total to "
                      "2 decimal places. The tiers are defined at the top of the file."),
        files={"pricing.py": buggy}, cases=cases, reference={"pricing.py": fixed},
    )


# ---------------------------------------------- compose_port_alignment (YAML config repair)
# A DevOps task: the app listens on 8080, but docker-compose.yml maps the host's 8081 to the
# wrong container port and health-checks the wrong port. The fix is to align the container port
# and the health-check with the app's 8080, keeping the host port 8081. Acceptance checks parse
# the resulting files in the trusted harness (never in candidate code); expected values stay here.
_COMPOSE_APP = (
    '"""A tiny web app. It serves /health on PORT."""\n'
    'from http.server import BaseHTTPRequestHandler, HTTPServer\n\n'
    'PORT = 8080\n\n\n'
    'class Handler(BaseHTTPRequestHandler):\n'
    '    def do_GET(self):\n'
    '        self.send_response(200 if self.path == "/health" else 404)\n'
    '        self.end_headers()\n'
    '        self.wfile.write(b"ok")\n\n'
    '    def log_message(self, *args):\n'
    '        pass\n\n\n'
    'if __name__ == "__main__":\n'
    '    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()\n')

_COMPOSE_DOCKERFILE = (
    "FROM python:3.12-slim\n"
    "COPY app.py /app/app.py\n"
    "WORKDIR /app\n"
    "EXPOSE 8080\n"
    'CMD ["python", "app.py"]\n')

_COMPOSE_BROKEN = (
    "services:\n"
    "  web:\n"
    "    build: .\n"
    "    ports:\n"
    '      - "8081:9090"          # host 8081 is correct; the container port is wrong\n'
    "    healthcheck:\n"
    '      test: ["CMD", "python", "-c", "import urllib.request; '
    "urllib.request.urlopen('http://localhost:9090/health')\"]\n"
    "      interval: 5s\n"
    "      timeout: 3s\n"
    "      retries: 5\n")

_COMPOSE_FIXED = (
    "services:\n"
    "  web:\n"
    "    build: .\n"
    "    ports:\n"
    '      - "8081:8080"\n'
    "    healthcheck:\n"
    '      test: ["CMD", "python", "-c", "import urllib.request; '
    "urllib.request.urlopen('http://localhost:8080/health')\"]\n"
    "      interval: 5s\n"
    "      timeout: 3s\n"
    "      retries: 5\n")


# Bounds for untrusted YAML parsed in the harness. A compose file is tiny; these are generous.
_YAML_MAX_BYTES = 65536
_YAML_MAX_NODES = 10000
_YAML_MAX_ALIASES = 50
_YAML_MAX_DEPTH = 100


def _safe_yaml(text):
    """Parse untrusted YAML with the safe loader, after rejecting oversized or excessively complex
    input. The pre-scan uses the event stream (yaml.parse), which does not expand aliases or build
    Python objects, so it is bounded by the document size; over-limit input raises ValueError."""
    import yaml
    if len(text.encode("utf-8")) > _YAML_MAX_BYTES:
        raise ValueError("yaml exceeds size limit")
    depth = max_depth = nodes = aliases = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        name = type(event).__name__
        if name in ("MappingStartEvent", "SequenceStartEvent"):
            depth += 1
            max_depth = max(max_depth, depth)
            nodes += 1
        elif name in ("MappingEndEvent", "SequenceEndEvent"):
            depth -= 1
        elif name == "ScalarEvent":
            nodes += 1
        elif name == "AliasEvent":
            aliases += 1
            nodes += 1
        if nodes > _YAML_MAX_NODES or aliases > _YAML_MAX_ALIASES or max_depth > _YAML_MAX_DEPTH:
            raise ValueError("yaml too complex (node/alias/depth limit)")
    return yaml.safe_load(text)


def _yaml_load(decoded, path):
    return _safe_yaml(decoded[path].decode("utf-8"))


def _port_map(ports):
    """Map {host_port: container_port} from a compose `ports` list (short or long syntax)."""
    mapping = {}
    for entry in ports or []:
        if isinstance(entry, dict):
            mapping[int(entry["published"])] = int(entry["target"])
        else:
            parts = str(entry).split(":")
            if len(parts) >= 2:
                mapping[int(parts[-2])] = int(parts[-1])
    return mapping


def _healthcheck_port(decoded):
    import re
    test = _yaml_load(decoded, "docker-compose.yml")["services"]["web"]["healthcheck"]["test"]
    text = " ".join(test) if isinstance(test, list) else str(test)
    found = [int(p) for p in re.findall(r"localhost:(\d+)", text)]
    return found[0] if found else None


def compose_task(seed):
    def container_port(decoded):     # target: the host-8081 mapping must reach the app's 8080
        ports = _yaml_load(decoded, "docker-compose.yml")["services"]["web"]["ports"]
        return {"ok": True, "result": _port_map(ports).get(8081)}

    def health_port(decoded):        # target: the health check must hit the app's 8080
        return {"ok": True, "result": _healthcheck_port(decoded)}

    def host_ports(decoded):         # regression: the host port stays 8081, nothing else added
        ports = _yaml_load(decoded, "docker-compose.yml")["services"]["web"]["ports"]
        return {"ok": True, "result": sorted(_port_map(ports))}

    def app_port(decoded):           # regression: the app itself still listens on 8080
        import re
        match = re.search(r"PORT\s*=\s*(\d+)", decoded["app.py"].decode("utf-8"))
        return {"ok": True, "result": int(match.group(1)) if match else None}

    return TaskInstance(
        task_id="compose_port_alignment", title="Align the Compose ports", difficulty="medium",
        instructions=(
            "The app in `app.py` serves its health endpoint on the port named by its `PORT` "
            "constant. `docker-compose.yml` is meant to publish that app on host port 8081, but "
            "its container-port mapping and its health-check port are wrong. Fix "
            "`docker-compose.yml` so the host port 8081 maps to the app's real port and the "
            "health check targets that same port. Keep host port 8081 and do not change the app. "
            "Success: `services.web.ports` maps 8081 to the app's port, and "
            "`services.web.healthcheck` targets the app's port."),
        files={"app.py": _COMPOSE_APP, "Dockerfile": _COMPOSE_DOCKERFILE,
               "docker-compose.yml": _COMPOSE_BROKEN},
        cases=[
            Case("target", None, 8080, check=container_port),
            Case("target", None, 8080, check=health_port),
            Case("regression", None, [8081], check=host_ports),
            Case("regression", None, 8080, check=app_port),
        ],
        reference={"docker-compose.yml": _COMPOSE_FIXED},
    )


TASKS = {
    "slugify": slugify_task,
    "parse_duration": duration_task,
    "merge_intervals": intervals_task,
    "lru_cache": lru_task,
    "bulk_discount": discount_task,
    "compose_port_alignment": compose_task,
}
DEMO_TASK = "slugify"


def build_task(task_id, seed):
    if task_id not in TASKS:
        raise KeyError(f"unknown task '{task_id}'. Available: {', '.join(TASKS)}")
    return TASKS[task_id](seed)
