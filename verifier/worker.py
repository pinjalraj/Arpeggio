"""Candidate worker: imports the candidate module and runs a structured program.

The verifier server launches this as a SEPARATE process:

    python -I verifier/worker.py <result_path>

It holds no expected answers and no verification logic. It performs the calls described
by a small structured program (which function/method, with JSON literal arguments) and
writes the raw outcome to <result_path>. The verifier treats that outcome as untrusted.

Why this is separate from the verifier server:
  * The trusted server never imports candidate code, so candidate code cannot touch the
    server's request handling, limits or result validation.
  * Standard-library modules are imported here BEFORE the candidate's directory is added
    to the END of sys.path, so candidate files cannot shadow them.
  * The candidate's stdout/stderr are discarded by the parent and the outcome goes to a
    dedicated file, so stray prints are never mistaken for a result.

The program carries no expected answer, so candidate code that inspects it learns only the
arguments it is being called with, which it receives anyway.
"""
import json
import os
import sys


def write_result(result_path, payload):
    try:
        with open(result_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str))
    except OSError:
        pass


def run(result_path):
    job = json.loads(sys.stdin.read() or "{}")
    module_name = job["module"]
    steps = job["steps"]

    # Only now is the candidate's directory importable, and only at the END of the path.
    sys.path.append(os.getcwd())
    import importlib
    module = importlib.import_module(module_name)

    regs = {}
    collected = []
    for ins in steps:
        try:
            if ins["op"] == "call":
                value = getattr(module, ins["name"])(*ins.get("args", []))
            elif ins["op"] == "method":
                value = getattr(regs[ins["recv"]], ins["name"])(*ins.get("args", []))
            else:
                raise ValueError("unknown op")
        except BaseException as exc:
            write_result(result_path, {"ok": False, "exc": type(exc).__name__})
            return
        if "into" in ins:
            regs[ins["into"]] = value
        if ins.get("record"):
            collected.append(value)
    result = collected[0] if len(collected) == 1 else collected
    write_result(result_path, {"ok": True, "result": result})


if __name__ == "__main__":
    path = sys.argv[1]
    try:
        run(path)
    except BaseException as exc:
        # Importing the candidate (or a malformed job) can fail; report, never crash loudly.
        write_result(path, {"ok": False, "exc": type(exc).__name__})
