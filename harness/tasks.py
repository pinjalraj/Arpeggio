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


# ---------------------------------------------- ci_workflow_repair (GitHub Actions repair)
# A DevOps task: a tiny app has its tests under `tests/`, but the CI workflow runs the test
# discovery against the wrong directory. The fix is to point the workflow's test command at the
# real tests directory, without touching the app or the tests. Acceptance checks parse the
# workflow in the trusted harness (bounded YAML loader) and inspect the command semantics.
_CI_APP = (
    '"""A tiny calculator exercised by the test suite."""\n\n\n'
    'def add(a, b):\n    return a + b\n\n\n'
    'def subtract(a, b):\n    return a - b\n')

_CI_TEST = (
    "import unittest\n\n"
    "from calc import add, subtract\n\n\n"
    "class CalcTest(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
    "    def test_subtract(self):\n        self.assertEqual(subtract(5, 2), 3)\n\n\n"
    'if __name__ == "__main__":\n    unittest.main()\n')

_CI_WORKFLOW = (
    "name: CI\n"
    "on:\n"
    "  push:\n"
    "    branches: [ main ]\n"
    "jobs:\n"
    "  test:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - uses: actions/setup-python@v5\n"
    "        with:\n"
    '          python-version: "3.12"\n'
    "      - name: Run unit tests\n"
    "        run: python -m unittest discover -s {dir} -t .\n")

_CI_BROKEN = _CI_WORKFLOW.format(dir="test")      # wrong: the tests live under ./tests
_CI_FIXED = _CI_WORKFLOW.format(dir="tests")
_CI_PATH = ".github/workflows/ci.yml"


def _ci_workflow(decoded):
    return _safe_yaml(decoded[_CI_PATH].decode("utf-8"))


def _find_test_run(wf):
    """The `run` string of the step that invokes the unit-test runner, or None."""
    for job in (wf.get("jobs") or {}).values():
        for step in (job.get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if isinstance(run, str) and "unittest" in run:
                return run
    return None


def _discover_dir(run):
    """The directory passed to `unittest discover -s <dir>`, or None."""
    import shlex
    tokens = shlex.split(run)
    if "-s" in tokens:
        i = tokens.index("-s")
        if i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def _workflow_events(wf):
    """Event names for the workflow. PyYAML (YAML 1.1) parses the bare key `on` as boolean True,
    so look under both the string key and True rather than weakening the check."""
    on = wf.get("on")
    if on is None:
        on = wf.get(True)
    if isinstance(on, dict):
        return set(on)
    if isinstance(on, list):
        return set(on)
    if isinstance(on, str):
        return {on}
    return set()


def ci_task(seed):
    def discover_dir(decoded):       # target: the test command points at the real tests dir
        return {"ok": True, "result": _discover_dir(_find_test_run(_ci_workflow(decoded)))}

    def dir_exists(decoded):         # target: that directory actually holds the tests
        want = _discover_dir(_find_test_run(_ci_workflow(decoded)))
        present = bool(want) and any(k == want or k.startswith(want + "/") for k in decoded)
        return {"ok": True, "result": present}

    def runs_unittest(decoded):      # regression: the workflow still runs the tests
        return {"ok": True, "result": _find_test_run(_ci_workflow(decoded)) is not None}

    def push_trigger(decoded):       # regression: the push trigger survives (handles on->True)
        return {"ok": True, "result": "push" in _workflow_events(_ci_workflow(decoded))}

    def app_unchanged(decoded):      # regression: the application is untouched
        return {"ok": True, "result": decoded["calc.py"].decode("utf-8") == _CI_APP}

    def tests_unchanged(decoded):    # regression: the tests are untouched
        return {"ok": True, "result": decoded["tests/test_calc.py"].decode("utf-8") == _CI_TEST}

    return TaskInstance(
        task_id="ci_workflow_repair", title="Repair the CI workflow", difficulty="medium",
        instructions=(
            "`calc.py` is a small app and its tests live in `tests/`. The GitHub Actions workflow "
            "at `.github/workflows/ci.yml` is meant to run those tests, but its test command points "
            "at the wrong directory, so CI never runs them. Fix the `run:` command in "
            "`.github/workflows/ci.yml` so `unittest discover` targets the real tests directory. "
            "Do not change `calc.py` or the tests. Success: the workflow's test step discovers tests "
            "from the directory that actually contains them, and the push trigger and test run remain."),
        files={"calc.py": _CI_APP, "tests/test_calc.py": _CI_TEST, _CI_PATH: _CI_BROKEN},
        cases=[
            Case("target", None, "tests", check=discover_dir),
            Case("target", None, True, check=dir_exists),
            Case("regression", None, True, check=runs_unittest),
            Case("regression", None, True, check=push_trigger),
            Case("regression", None, True, check=app_unchanged),
            Case("regression", None, True, check=tests_unchanged),
        ],
        reference={_CI_PATH: _CI_FIXED},
    )


# ------------------------------------------ healthy_service_startup (readiness dependency)
# A DevOps task with two services. The app must start only after the db is *ready*, but the
# compose file both (a) gives the db a health check that does not test readiness and (b) makes the
# app depend on the db only for start order. The fix: a pg_isready health check on db, and
# app.depends_on.db.condition = service_healthy. Everything else must stay unchanged.
_HSS_APP = (
    '"""A tiny service that would connect to the database named by DATABASE_URL."""\n'
    'import os\n\n'
    'DATABASE_URL = os.environ.get("DATABASE_URL", "")\n'
    'PORT = 8080\n\n\n'
    'def main():\n'
    '    print(f"would serve on {PORT}, db={DATABASE_URL}")\n\n\n'
    'if __name__ == "__main__":\n    main()\n')

_HSS_DOCKERFILE = (
    "FROM python:3.12-slim\n"
    "COPY app.py /app/app.py\n"
    "WORKDIR /app\n"
    "EXPOSE 8080\n"
    'CMD ["python", "app.py"]\n')

_HSS_BROKEN = (
    "services:\n"
    "  app:\n"
    "    build: .\n"
    "    ports:\n"
    '      - "8080:8080"\n'
    "    environment:\n"
    "      DATABASE_URL: postgres://db:5432/app\n"
    "    depends_on:\n"
    "      - db                       # only start order, not readiness\n"
    "  db:\n"
    "    image: postgres:16\n"
    "    environment:\n"
    "      POSTGRES_PASSWORD: secret\n"
    "    healthcheck:\n"
    '      test: ["CMD-SHELL", "echo ok"]   # does not test database readiness\n'
    "      interval: 5s\n"
    "      timeout: 3s\n"
    "      retries: 5\n")

_HSS_FIXED = (
    "services:\n"
    "  app:\n"
    "    build: .\n"
    "    ports:\n"
    '      - "8080:8080"\n'
    "    environment:\n"
    "      DATABASE_URL: postgres://db:5432/app\n"
    "    depends_on:\n"
    "      db:\n"
    "        condition: service_healthy\n"
    "  db:\n"
    "    image: postgres:16\n"
    "    environment:\n"
    "      POSTGRES_PASSWORD: secret\n"
    "    healthcheck:\n"
    '      test: ["CMD-SHELL", "pg_isready -U postgres"]\n'
    "      interval: 5s\n"
    "      timeout: 3s\n"
    "      retries: 5\n")


def _hss_compose(decoded):
    return _safe_yaml(decoded["docker-compose.yml"].decode("utf-8"))


def _service(wf, name):
    return (wf.get("services") or {}).get(name) or {}


def _healthcheck_text(service):
    test = (service.get("healthcheck") or {}).get("test")
    if isinstance(test, list):
        return " ".join(str(t) for t in test)
    return str(test) if test is not None else ""


def _depends_condition(service, dep):
    deps = service.get("depends_on")
    if isinstance(deps, dict) and isinstance(deps.get(dep), dict):
        return deps[dep].get("condition")
    return None       # list form or missing => start-order only, no readiness condition


def _service_env(service, key):
    env = service.get("environment")
    if isinstance(env, dict):
        return env.get(key)
    if isinstance(env, list):
        for item in env:
            if isinstance(item, str) and item.split("=", 1)[0] == key:
                return item.split("=", 1)[1] if "=" in item else None
    return None


def hss_task(seed):
    def db_healthcheck_ready(decoded):   # target: db health check actually probes readiness
        text = _healthcheck_text(_service(_hss_compose(decoded), "db"))
        return {"ok": True, "result": "pg_isready" in text}

    def app_waits_for_healthy(decoded):  # target: app waits for db to be healthy, not just started
        cond = _depends_condition(_service(_hss_compose(decoded), "app"), "db")
        return {"ok": True, "result": cond}

    def service_names(decoded):          # regression: the two service names are unchanged
        return {"ok": True, "result": sorted((_hss_compose(decoded).get("services") or {}))}

    def app_port(decoded):               # regression: the app port mapping is unchanged
        ports = _service(_hss_compose(decoded), "app").get("ports")
        return {"ok": True, "result": sorted([h, c] for h, c in _port_map(ports).items())}

    def db_image(decoded):               # regression: the db image is unchanged
        return {"ok": True, "result": _service(_hss_compose(decoded), "db").get("image")}

    def app_database_url(decoded):       # regression: the app's DATABASE_URL is unchanged
        return {"ok": True, "result": _service_env(_service(_hss_compose(decoded), "app"), "DATABASE_URL")}

    return TaskInstance(
        task_id="healthy_service_startup", title="Make the app wait for a healthy database",
        difficulty="medium",
        instructions=(
            "`docker-compose.yml` runs an `app` service that must start only after the `db` service "
            "is actually ready. Two things are wrong: the `db` health check does not test database "
            "readiness, and `app`'s `depends_on` controls only start order, not readiness. Fix "
            "`docker-compose.yml` so (1) the `db` service's health check uses `pg_isready`, and "
            "(2) `app` depends on `db` with `condition: service_healthy`. Keep everything else "
            "unchanged: the service names `app` and `db`, the app's `8080:8080` port, the `db` image "
            "`postgres:16`, and the app's `DATABASE_URL`. Do not change `app.py` or the Dockerfile."),
        files={"app.py": _HSS_APP, "Dockerfile": _HSS_DOCKERFILE, "docker-compose.yml": _HSS_BROKEN},
        cases=[
            Case("target", None, True, check=db_healthcheck_ready),
            Case("target", None, "service_healthy", check=app_waits_for_healthy),
            Case("regression", None, ["app", "db"], check=service_names),
            Case("regression", None, [[8080, 8080]], check=app_port),
            Case("regression", None, "postgres:16", check=db_image),
            Case("regression", None, "postgres://db:5432/app", check=app_database_url),
        ],
        reference={"docker-compose.yml": _HSS_FIXED},
    )


# ------------------------------------------------ env_config_repair (env-var name contract)
# A DevOps task: the app requires the environment variable DATABASE_URL, but the compose file
# supplies the connection under the wrong name DB_CONNECTION. The fix is to provide the value under
# DATABASE_URL in the deployment, leaving the application source unchanged. All values are synthetic
# placeholders (no real credentials). "Fixing" the app to read DB_CONNECTION is rejected.
_ENV_APP = (
    '"""Reads its database connection from the required DATABASE_URL environment variable."""\n'
    'import os\n\n'
    'DATABASE_URL = os.environ["DATABASE_URL"]   # required; the deployment must set this\n'
    'PORT = 8080\n\n\n'
    'def main():\n'
    '    print(f"would connect to {DATABASE_URL} and serve on {PORT}")\n\n\n'
    'if __name__ == "__main__":\n    main()\n')

_ENV_DOCKERFILE = (
    "FROM python:3.12-slim\n"
    "COPY app.py /app/app.py\n"
    "WORKDIR /app\n"
    "EXPOSE 8080\n"
    'CMD ["python", "app.py"]\n')

# Synthetic placeholder connection string: no username, password, token or real host.
_ENV_CONN = "postgres://db:5432/appdb"

_ENV_COMPOSE = (
    "services:\n"
    "  app:\n"
    "    build: .\n"
    "    environment:\n"
    "      {key}: {conn}\n"
    "      LOG_LEVEL: info\n"
    "    ports:\n"
    '      - "8080:8080"\n')

_ENV_BROKEN = _ENV_COMPOSE.format(key="DB_CONNECTION", conn=_ENV_CONN)   # wrong variable name
_ENV_FIXED = _ENV_COMPOSE.format(key="DATABASE_URL", conn=_ENV_CONN)


def _env_compose(decoded):
    return _safe_yaml(decoded["docker-compose.yml"].decode("utf-8"))


def _has_embedded_credentials(value):
    """True if a connection string embeds a user:password@ credential (i.e. not a placeholder)."""
    import re
    return isinstance(value, str) and re.search(r"://[^/@\s]*:[^/@\s]*@", value) is not None


def env_task(seed):
    def supplies_database_url(decoded):   # target: the deployment sets the required variable name
        app = _service(_env_compose(decoded), "app")
        return {"ok": True, "result": _service_env(app, "DATABASE_URL") is not None}

    def database_url_value(decoded):      # target: and it carries the intended connection string
        app = _service(_env_compose(decoded), "app")
        return {"ok": True, "result": _service_env(app, "DATABASE_URL")}

    def app_source_unchanged(decoded):    # regression: the application source is untouched
        return {"ok": True, "result": decoded["app.py"].decode("utf-8") == _ENV_APP}

    def log_level_unchanged(decoded):     # regression: unrelated setting is preserved
        app = _service(_env_compose(decoded), "app")
        return {"ok": True, "result": _service_env(app, "LOG_LEVEL")}

    def app_port_unchanged(decoded):      # regression: the app port mapping is preserved
        ports = _service(_env_compose(decoded), "app").get("ports")
        return {"ok": True, "result": sorted([h, c] for h, c in _port_map(ports).items())}

    def no_embedded_credentials(decoded):  # regression: configuration stays a placeholder
        app = _service(_env_compose(decoded), "app")
        return {"ok": True, "result": not _has_embedded_credentials(_service_env(app, "DATABASE_URL"))}

    return TaskInstance(
        task_id="env_config_repair", title="Repair the env-var name mismatch", difficulty="medium",
        instructions=(
            "`app.py` requires the environment variable `DATABASE_URL`, but `docker-compose.yml` "
            "supplies the connection string under the wrong name `DB_CONNECTION`, so the app would "
            "fail to start. Fix `docker-compose.yml` so the app's `environment` provides the same "
            "connection string under the name `DATABASE_URL`. Do not change `app.py`, and do not "
            "change the connection value, the `LOG_LEVEL` setting or the `8080:8080` port. The "
            "value is a placeholder — do not add real credentials. Success: the app service's "
            "`environment` sets `DATABASE_URL` to the placeholder connection string and the "
            "application source is unchanged."),
        files={"app.py": _ENV_APP, "Dockerfile": _ENV_DOCKERFILE, "docker-compose.yml": _ENV_BROKEN},
        cases=[
            Case("target", None, True, check=supplies_database_url),
            Case("target", None, _ENV_CONN, check=database_url_value),
            Case("regression", None, True, check=app_source_unchanged),
            Case("regression", None, "info", check=log_level_unchanged),
            Case("regression", None, [[8080, 8080]], check=app_port_unchanged),
            Case("regression", None, True, check=no_embedded_credentials),
        ],
        reference={"docker-compose.yml": _ENV_FIXED},
    )


# Active task registry. Retired starter Python-utility tasks keep their functions above as shared
# reference code; the benchmark is moving to DevOps config-repair tasks. At least five stay active.
TASKS = {
    "slugify": slugify_task,
    "bulk_discount": discount_task,
    "compose_port_alignment": compose_task,
    "ci_workflow_repair": ci_task,
    "healthy_service_startup": hss_task,
    "env_config_repair": env_task,
}
DEMO_TASK = "slugify"


def build_task(task_id, seed):
    if task_id not in TASKS:
        raise KeyError(f"unknown task '{task_id}'. Available: {', '.join(TASKS)}")
    return TASKS[task_id](seed)
