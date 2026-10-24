"""Sandbox service: runs the agent's tools inside an isolated workspace.

This container sits on an internal Docker network with no internet access.
It holds only the agent's workspace. Hidden checks never come here: the harness
takes a snapshot of the workspace and sends it to the separate verifier service,
which the sandbox has no network route to.

Trust model inside the container (see audit-notes/security_test_report.md section 15):
  * The server (supervisor) runs as root, but with only SETUID, SETGID and KILL
    capabilities (every other capability dropped, no privileged mode).
  * The agent's `run_python` code runs as a different, unprivileged user (`coder`). It
    cannot read the server's root-only files or its control token, cannot signal the
    server, and can write only in the workspace and a managed temp dir.
  * On reset the server kills every process owned by `coder` (so a detached process that
    left its process group is still terminated) and wipes the candidate-writable dirs.
Control endpoints (/reset, /tool, /snapshot, /state) require a shared token; /health is open.
When the server is not running as root (local/offline/unit tests) it still works, but the
privilege drop and kill-by-uid are inactive — those are enforced only in the container.
"""
import base64
import hashlib
import hmac
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
# Temp/home for candidate code, kept OUTSIDE the workspace so it is not snapshotted and
# can be wiped on reset. Overridable so the container can point it at a coder-owned path.
CAND_TMP = Path(os.environ.get("CAND_TMP") or (WORKSPACE.parent / "cand-tmp")).resolve()
PORT = int(os.environ.get("PORT", "8000"))
# Read once, then drop from the environment so candidate code can't read it via /proc.
TOKEN = os.environ.pop("SANDBOX_TOKEN", "") or ""
CANDIDATE_USER = os.environ.get("CANDIDATE_USER", "coder")
MAX_OUTPUT = 4000          # characters returned to the agent per tool call
RUN_TIMEOUT = 20           # wall-clock seconds for one run_python call
SNAPSHOT_MAX_FILES = 500                # must match the verifier's limits
SNAPSHOT_MAX_FILE_BYTES = 1_000_000
SNAPSHOT_MAX_TOTAL_BYTES = 5_000_000

OPEN_PATHS = {"/health"}                 # reachable without the token
_groups_lock = threading.Lock()
_groups = set()                          # process-group ids started by run_python


def _candidate_ids():
    """(uid, gid) to drop candidate code to, or None when we can't (not root / not POSIX)."""
    if os.name != "posix":
        return None
    try:
        import pwd
        if os.geteuid() != 0:
            return None
        rec = pwd.getpwnam(CANDIDATE_USER)
        return (rec.pw_uid, rec.pw_gid)
    except (KeyError, OSError, AttributeError):
        return None


CAND_IDS = _candidate_ids()


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


def _writable(path: Path):
    """Make a file editable by the candidate user as well as the server. Best-effort."""
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass


def _clear_dir(path: Path):
    """Remove the contents of `path` without following symlinks, keeping `path` itself
    (so its candidate-owned permissions are preserved)."""
    try:
        children = list(path.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# process cleanup
# ----------------------------------------------------------------------------
def _kill_group(pgid):
    if pgid is None:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        pass


def _kill_tracked_groups():
    with _groups_lock:
        groups = list(_groups)
        _groups.clear()
    for pgid in groups:
        _kill_group(pgid)


def _kill_candidate_processes():
    """Terminate everything the previous episode left running. Killing by uid (as root)
    catches detached processes that left their process group; the tracked-group kill is a
    fallback for the non-root case."""
    _kill_tracked_groups()
    if not CAND_IDS:
        return
    uid = CAND_IDS[0]
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/status") as handle:
                for line in handle:
                    if line.startswith("Uid:"):
                        if int(line.split()[1]) == uid:
                            try:
                                os.kill(int(entry), signal.SIGKILL)
                            except OSError:
                                pass
                        break
        except (OSError, ValueError):
            continue


def _pgid(pid):
    try:
        return os.getpgid(pid)
    except (OSError, AttributeError):
        return None


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
    _writable(path)                      # the candidate user may also edit it via run_python
    return f"wrote {len(args['content'])} characters to {args['path']}"


def tool_run_python(args):
    CAND_TMP.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
           "TMPDIR": str(CAND_TMP), "HOME": str(CAND_TMP)}
    # No -I: the agent's own sandbox may import and test its workspace files.
    popen_kwargs = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True     # own process group so leftovers can be killed
    if CAND_IDS:
        popen_kwargs.update(user=CAND_IDS[0], group=CAND_IDS[1], extra_groups=[])
    process = subprocess.Popen([sys.executable, "-c", args["code"]], cwd=WORKSPACE,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, env=env, **popen_kwargs)
    pgid = _pgid(process.pid)
    if pgid is not None:
        with _groups_lock:
            _groups.add(pgid)
    try:
        output, _ = process.communicate(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(pgid)
        try:
            process.kill()
            process.communicate()
        except OSError:
            pass
        return f"error: the code ran longer than {RUN_TIMEOUT} seconds and was stopped"
    finally:
        if pgid is not None:
            with _groups_lock:
                _groups.discard(pgid)
    output = (output or "").strip() or "(no output)"
    return clip(f"{output}\n[exit code {process.returncode}]")


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
    # End the previous episode's processes and candidate-writable state first.
    _kill_candidate_processes()
    CAND_TMP.mkdir(parents=True, exist_ok=True)
    _clear_dir(CAND_TMP)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    _clear_dir(WORKSPACE)
    for name, content in body["files"].items():
        path = safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        _writable(path)
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
ROUTES_GET = {"/state": do_state, "/snapshot": do_snapshot}


def authorized(headers):
    """Control endpoints need the shared token. If none is configured (local/offline), they
    stay open. The token is never sent to candidate code or returned to the agent."""
    if not TOKEN:
        return True
    return hmac.compare_digest(headers.get("X-Sandbox-Token", "") or "", TOKEN)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _guard(self):
        if self.path in OPEN_PATHS or authorized(self.headers):
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if not self._guard():
            return
        handler = ROUTES_GET.get(self.path)
        if handler is None:
            return self._send(404, {"error": "not found"})
        try:
            self._send(200, handler())
        except Exception as exc:
            self._send(400, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):
        if not self._guard():
            return
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


def _confine_shared_dirs():
    """Remove world-write from shared dirs so the candidate can only write the workspace and
    its managed temp. Only meaningful when a separate candidate user is in effect."""
    if not CAND_IDS:
        return
    for shared in ("/tmp", "/var/tmp", "/dev/shm"):
        try:
            os.chmod(shared, 0o755)
        except OSError:
            pass


if __name__ == "__main__":
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    CAND_TMP.mkdir(parents=True, exist_ok=True)
    _confine_shared_dirs()
    print(f"sandbox listening on :{PORT}, workspace {WORKSPACE}, "
          f"candidate_uid={CAND_IDS[0] if CAND_IDS else 'self'}"
          f"{', token required' if TOKEN else ''}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
