"""Verifier service: runs the hidden checks against a copy of the agent's files.

This container is reachable only from the harness, on its own internal network. The
agent's sandbox has no route to it, so nothing that happens here can flow back to the
agent. Expected answers and the reward formula stay in the harness: this service only
reports what each check produced.

Trust boundary inside this container:
  * This SERVER process is trusted. It validates every request, enforces the limits, and
    never imports or executes candidate code.
  * Each check runs in a separate, short-lived WORKER process (verifier/worker.py) that
    imports the candidate and performs one structured program. Its output is untrusted
    data, validated here before being returned.

A check is a STRUCTURED program (which module, which calls, JSON literal arguments), not
an arbitrary code snippet, and it never contains the expected answer.
"""
import base64
import binascii
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

PORT = int(os.environ.get("PORT", "8000"))
WORKER = Path(__file__).resolve().parent / "worker.py"
CASE_TIMEOUT = 5                    # seconds for one hidden check
MAX_FILES = 500                     # files in one snapshot
MAX_FILE_BYTES = 1_000_000          # one file
MAX_TOTAL_BYTES = 5_000_000         # whole snapshot
MAX_PATH_CHARS = 255
MAX_CASES = 200
MAX_STEPS = 500                     # instructions in one program
MAX_ARGS_BYTES = 100_000            # serialized arguments of one step
MAX_REQUEST_BYTES = 16_000_000      # base64 + JSON overhead on top of MAX_TOTAL_BYTES
MAX_RESULT_BYTES = 200_000          # outcome file read back from a worker

_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class BadRequest(ValueError):
    """The request is malformed or over a limit. The message never echoes request content."""


# ---------------------------------------------------------------- snapshot validation
def clean_path(name):
    """Return a safe relative PurePosixPath for `name`, or raise BadRequest."""
    if not isinstance(name, str) or not name or len(name) > MAX_PATH_CHARS:
        raise BadRequest("invalid file path")
    if "\x00" in name or "\\" in name or name.startswith("/"):
        raise BadRequest("invalid file path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise BadRequest("invalid file path")
    return path


def decode_files(files):
    """Validate a snapshot {relative path: base64 bytes} and return {PurePosixPath: bytes}."""
    if not isinstance(files, dict):
        raise BadRequest("files must be an object")
    if len(files) > MAX_FILES:
        raise BadRequest(f"too many files (limit {MAX_FILES})")
    decoded, total = {}, 0
    for name, data in files.items():
        path = clean_path(name)
        if not isinstance(data, str):
            raise BadRequest("file content must be base64 text")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise BadRequest("file content is not valid base64")
        if len(raw) > MAX_FILE_BYTES:
            raise BadRequest(f"file over {MAX_FILE_BYTES} bytes")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise BadRequest(f"snapshot over {MAX_TOTAL_BYTES} bytes")
        decoded[path] = raw
    names = set(decoded)
    for path in names:      # a path may not be both a file and a folder
        if any(parent in names for parent in path.parents):
            raise BadRequest("file path conflicts with another file")
    return decoded


def write_project(root, files):
    for path, raw in files.items():
        target = root.joinpath(*path.parts)
        if root not in target.parents:          # defence in depth after clean_path
            raise BadRequest("invalid file path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


# ---------------------------------------------------------------- program validation
def validate_step(ins):
    if not isinstance(ins, dict):
        raise BadRequest("step must be an object")
    if ins.get("op") not in ("call", "method"):
        raise BadRequest("step op must be 'call' or 'method'")
    if not isinstance(ins.get("name"), str) or not _NAME.match(ins["name"]):
        raise BadRequest("step name must be an identifier")
    if ins["op"] == "method" and not isinstance(ins.get("recv"), str):
        raise BadRequest("method step needs a receiver")
    args = ins.get("args", [])
    if not isinstance(args, list):
        raise BadRequest("step args must be a list")
    try:
        if len(json.dumps(args)) > MAX_ARGS_BYTES:
            raise BadRequest("step args too large")
    except (TypeError, ValueError):
        raise BadRequest("step args must be JSON values")
    if "into" in ins and not isinstance(ins["into"], str):
        raise BadRequest("step 'into' must be a string")


def validate_job(case):
    """A case is a structured program, not arbitrary source. Returns a clean job dict."""
    if not isinstance(case, dict):
        raise BadRequest("each case must be a structured program object")
    module = case.get("module")
    if not isinstance(module, str) or len(module) > 100 or not _MODULE.match(module):
        raise BadRequest("program module must be a dotted identifier")
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        raise BadRequest("program steps must be a non-empty list")
    for ins in steps:
        validate_step(ins)
    return {"module": module, "steps": steps}


# ---------------------------------------------------------------- running a check
def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        try:
            process.kill()
        except OSError:
            pass


def sanitize_result(data):
    """The worker's outcome is untrusted. Accept only a well-formed shape."""
    if not isinstance(data, dict):
        return {"ok": False, "exc": "NoResult"}
    if data.get("ok") is True:
        return {"ok": True, "result": data.get("result")}
    name = data.get("exc")
    return {"ok": False, "exc": name if isinstance(name, str) and len(name) <= 80 else "Error"}


def read_result(path):
    try:
        raw = path.read_bytes()
    except OSError:
        return {"ok": False, "exc": "NoResult"}
    if len(raw) > MAX_RESULT_BYTES:
        return {"ok": False, "exc": "ResultTooLarge"}
    try:
        return sanitize_result(json.loads(raw.decode("utf-8", "replace")))
    except ValueError:
        return {"ok": False, "exc": "NoResult"}


def run_program(files, job):
    """Run one structured program in a throwaway worker process. Returns an untrusted,
    then sanitized, outcome dict."""
    scratch = Path(tempfile.mkdtemp(prefix="check-"))
    try:
        project = scratch / "project"
        project.mkdir()
        write_project(project, files)
        result_path = scratch / "result.json"       # outside the project dir the worker imports
        process = subprocess.Popen(
            [sys.executable, "-I", str(WORKER), str(result_path)], cwd=str(project),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
            text=True, start_new_session=True)
        try:
            process.communicate(json.dumps(job), timeout=CASE_TIMEOUT)
        except subprocess.TimeoutExpired:
            kill_group(process)
            process.communicate()
            return {"ok": False, "exc": "Timeout"}
        finally:
            kill_group(process)     # stop anything the check left running
        return read_result(result_path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def do_verify(body):
    if not isinstance(body, dict):
        raise BadRequest("body must be an object")
    files = decode_files(body.get("files"))
    cases = body.get("cases")
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise BadRequest("cases must be a list")
    jobs = [validate_job(case) for case in cases]
    return {"results": [run_program(files, job) for job in jobs]}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/verify":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise BadRequest("request body missing or too large")
            body = json.loads(self.rfile.read(length))
            self._send(200, do_verify(body))
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
        except ValueError:
            self._send(400, {"error": "request is not valid JSON"})
        except Exception as exc:    # report the type only, never request content
            self._send(500, {"error": type(exc).__name__})

    def log_message(self, *args):  # keep the container log quiet
        pass


if __name__ == "__main__":
    print(f"verifier listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
