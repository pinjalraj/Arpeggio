"""Sandbox service: runs the agent's tools inside an isolated workspace.

This container sits on an internal Docker network with no internet access.
It holds only the agent's workspace. Hidden checks never come here: the harness
takes a snapshot of the workspace and sends it to the separate verifier service,
which the sandbox has no network route to.
"""
import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
PORT = int(os.environ.get("PORT", "8000"))
MAX_OUTPUT = 4000          # characters returned to the agent per tool call
RUN_TIMEOUT = 20           # seconds for one run_python call
SNAPSHOT_MAX_FILES = 500                # must match the verifier's limits
SNAPSHOT_MAX_FILE_BYTES = 1_000_000
SNAPSHOT_MAX_TOTAL_BYTES = 5_000_000


def safe_path(relative: str) -> Path:
    path = (WORKSPACE / relative).resolve()
    if path != WORKSPACE and WORKSPACE not in path.parents:
        raise ValueError("path must stay inside the project directory")
    return path


def clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n[output cut at {MAX_OUTPUT} characters]"


def all_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


# ----------------------------------------------------------------------------
# tools the agent can call
# ----------------------------------------------------------------------------
def tool_list_files(args):
    files = [str(p.relative_to(WORKSPACE)) for p in all_files(WORKSPACE)]
    return "\n".join(files) if files else "(no files)"


def tool_read_file(args):
    path = safe_path(args["path"])
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {args['path']}")
    return clip(path.read_text(errors="replace"))


def tool_write_file(args):
    path = safe_path(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"])
    return f"wrote {len(args['content'])} characters to {args['path']}"


def tool_run_python(args):
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run([sys.executable, "-c", args["code"]], cwd=WORKSPACE,
                              capture_output=True, text=True, timeout=RUN_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return f"error: the code ran longer than {RUN_TIMEOUT} seconds and was stopped"
    output = (done.stdout + done.stderr).strip() or "(no output)"
    return clip(f"{output}\n[exit code {done.returncode}]")


TOOLS = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "run_python": tool_run_python,
}


# ----------------------------------------------------------------------------
# endpoints used by the harness
# ----------------------------------------------------------------------------
def do_reset(body):
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    for child in WORKSPACE.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for name, content in body["files"].items():
        path = safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return {"ok": True}


def do_tool(body):
    name, args = body["name"], body.get("args", {})
    if name not in TOOLS:
        return {"output": f"error: unknown tool '{name}'", "is_error": True}
    try:
        return {"output": TOOLS[name](args), "is_error": False}
    except Exception as exc:  # tool-level problems go back to the agent, not the harness
        return {"output": f"error: {exc}", "is_error": True}


def do_snapshot(_body=None):
    """Regular files in the workspace as {relative path: base64}, for the verifier.

    Symlinks (to files or folders) and anything that is not a regular file are left out,
    so a snapshot can never include data from outside the workspace. Over a size limit,
    the snapshot is refused rather than cut short, and the harness scores it as a failure.
    """
    files, total = {}, 0
    for folder, dirs, names in os.walk(WORKSPACE, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not os.path.islink(os.path.join(folder, d)))
        for name in sorted(names):
            path = os.path.join(folder, name)
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode):
                continue
            if len(files) >= SNAPSHOT_MAX_FILES:
                return {"error": f"workspace has more than {SNAPSHOT_MAX_FILES} files"}
            if info.st_size > SNAPSHOT_MAX_FILE_BYTES:
                return {"error": f"a workspace file is over {SNAPSHOT_MAX_FILE_BYTES} bytes"}
            total += info.st_size
            if total > SNAPSHOT_MAX_TOTAL_BYTES:
                return {"error": f"workspace is over {SNAPSHOT_MAX_TOTAL_BYTES} bytes"}
            with open(path, "rb") as handle:
                raw = handle.read(SNAPSHOT_MAX_FILE_BYTES + 1)
            rel = Path(path).relative_to(WORKSPACE).as_posix()
            files[rel] = base64.b64encode(raw).decode("ascii")
    return {"files": files}


def do_state(_body=None):
    files = {str(p.relative_to(WORKSPACE)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in all_files(WORKSPACE)}
    return {"files": files}


ROUTES_POST = {"/reset": do_reset, "/tool": do_tool}


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
        elif self.path == "/state":
            self._send(200, do_state())
        elif self.path == "/snapshot":
            self._send(200, do_snapshot())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        handler = ROUTES_POST.get(self.path)
        if handler is None:
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, handler(body))
        except Exception as exc:
            self._send(400, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, *args):  # keep the container log quiet
        pass


if __name__ == "__main__":
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print(f"sandbox listening on :{PORT}, workspace {WORKSPACE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
