# Security Regression Test Report — RL-ENV-TEMPLATE

- **Branch:** `devops-environment` (working tree was clean before this phase)
- **Date:** 2026-10-24
- **Scope:** tests only. No production code, task definitions, rewards, Dockerfiles or Compose files were changed.
- **Where tests ran:**
  - A, B and D1: Windows host, Python 3.14.5, inside throwaway temporary folders. Re-run on **Linux, Python 3.12.15** in a disposable container with identical results (section 6b).
  - C and D2: one disposable container (`rlenv-sectest`) built from the unmodified `Dockerfile.sandbox` (Python 3.12, Linux), approved by the user.

## 1. Executive summary

| ID | Weakness | Result |
|----|----------|--------|
| A  | Agent-created `json.py` runs inside hidden-check execution | **CONFIRMED VULNERABILITY** |
| B1 | Missing verifier results are silently ignored (score inflated) | **CONFIRMED VULNERABILITY** |
| B2 | Truthy non-boolean `ok` value counts as a pass | **CONFIRMED VULNERABILITY** (only reachable if sandbox output is untrusted) |
| B3 | Malformed (non-object) result crashes scoring | **CONFIRMED VULNERABILITY** (robustness / crash) |
| B4 | Boolean `True` matches an expected integer `1` | **CONFIRMED VULNERABILITY** (low impact) |
| B5 | Extra trailing results change the score | NOT REPRODUCED |
| B6 | Agent-printed fake result markers inside the check process | NOT TESTED |
| C-pre | Preconditions for interference: agent code runs as the same user as the server and can reach its port | **CONFIRMED PRECONDITION** (container) |
| C  | Actual interference with / impersonation of the sandbox server | NOT TESTED (deliberately out of scope) |
| D1 | Files outside the workspace survive reset | **CONFIRMED VULNERABILITY** (host simulation; `/tmp` case also confirmed in container) |
| D2 | Processes survive reset | **CONFIRMED VULNERABILITY** (container) |

Unit test suite result: **9 tests, 3 passed, 6 failed**, identical on Windows/Python 3.14.5 and Linux/Python 3.12.15. All 6 failures are expected security failures (assertion failures), with 0 test-runner errors and 0 compatibility failures. The container probe is a separate script (section 3, C and D2). The tests check for the *secure* behaviour, so a failing test means the weakness was reproduced. Once the fixes are in, all of them should pass.

## 2. How to run

From the repository root (no packages needed; `requests` is stubbed only inside the tests):

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests/security -p "test_*.py" -v
```

Outcome mapping: **FAIL = CONFIRMED VULNERABILITY**, **PASS = NOT REPRODUCED**, **SKIP / absent = NOT TESTED**.

The tests use harmless fixture code in [`tests/security/_helpers.py`](../tests/security/_helpers.py): a function whose correct answer is `42`. The real hidden checks in `harness/tasks.py` are never loaded, printed or copied.

## 3. Individual results

### A — Module shadowing during hidden-check execution — CONFIRMED VULNERABILITY
- **Test:** `test_a_module_shadowing.py::test_agent_json_module_is_not_imported_by_checker`
- **Affected code:** `sandbox/server.py` → `CHILD`, `do_verify()`. Each check runs `python -c CHILD` with the working directory set to a copy of the agent's workspace. For `-c`, Python puts the working directory first on `sys.path`.
- **Fixture:** the workspace contains a `json.py` that only writes a canary file to a temporary folder.
- **Expected (secure):** the checker imports the real standard-library `json`, so the canary is not created.
- **Actual:** the canary was created. Verifier output was `[{'ok': False, 'exc': 'NoResult'}]`, because the fake module replaced the real `json` the checker needs.
- **Evidence:** `AssertionError: True is not false : agent-written json.py ran inside the hidden-check process; verifier returned [{'ok': False, 'exc': 'NoResult'}]`
- **Impact:** agent-controlled code runs before and around the hidden checks. This was also confirmed in the earlier audit: such code can see the check source passed on the command line. Because `score()` runs after **every** step (`harness/runner.py`, `run_episode`), this can happen during the episode, not just at the end.

### B — Verifier result handling (`harness/env.py` → `Env.score`, `_equal`)
These tests replace the sandbox's `/verify` reply with simulated results. They check only how the harness scores what it receives.

| Test | Expected (secure) | Actual | Classification |
|------|-------------------|--------|----------------|
| `test_baseline_full_and_empty` (control) | 1.0 for all-pass, 0.0 for all-fail | as expected | PASS (control) |
| `test_missing_results_count_as_failures` — 4 cases, only 1 passing result returned | ≤ 0.233 | **0.7** | CONFIRMED VULNERABILITY |
| `test_extra_results_are_ignored_or_rejected` — 4 failing results, then extra passing ones | 0.0 | 0.0 | NOT REPRODUCED |
| `test_non_dict_result_does_not_crash_scoring` | no crash, score < 1.0 | **`AttributeError: 'str' object has no attribute 'get'`** | CONFIRMED VULNERABILITY |
| `test_ok_must_be_true_not_just_truthy` — `"ok": "yes"` | 0.0 | **1.0** | CONFIRMED VULNERABILITY |
| `test_bool_result_does_not_match_integer_expectation` | 0.0 | **0.7** | CONFIRMED VULNERABILITY (low) |

- **Root cause:** `zip(cases, results)` stops at the shorter list, so missing results simply disappear from the tally. `result.get("ok")` accepts any truthy value and assumes `result` is a dict. `_equal` only rejects booleans when the *result* is a bool and the expectation is numeric, and `True == 1` in Python.
- **Impact:** if the sandbox's reply can be shortened or altered (see C), the harness over-scores instead of failing closed. A malformed reply crashes the episode instead of scoring 0.
- **B6, fake result markers printed by agent code inside the check process: NOT TESTED.** From reading the code, the server takes the *last* `@@RESULT@@` marker on stdout, so the checker's own marker normally wins. Whether agent code could stop the checker from printing its marker has not been tested. Treat this as a hypothesis.

### C — Sandbox-server interference

The probe was **observational only**. It did not stop, signal, replace or impersonate the server, so it confirms the *preconditions* for interference, not an attack.

- **Probe:** [`tests/security/docker_probe_c_d.py`](../tests/security/docker_probe_c_d.py), the `C_CODE` part, sent through the normal `run_python` tool.
- **Affected code:** `Dockerfile.sandbox` (`USER sandbox` for the server); `sandbox/server.py` → `tool_run_python` (runs as the same user); `Handler` (no authentication on `/reset`, `/verify`, `/state`).
- **Expected (secure):** agent code runs as a different, less-privileged user than the server and cannot reach the server's control port.
- **Actual output:**
  ```
  C: agent_uid=1000 server_procs=[('1', '1000'), ('22', '1000')] health_from_agent=200
  ```
  - PID 1 is the sandbox server, running as uid 1000.
  - PID 22 is the probe's own `run_python` process. It matched only because the probe's code contains the text `server.py`.
  - Agent code also runs as uid 1000.
  - Agent code got HTTP 200 from `localhost:8000/health`.
- **Classification:**
  - Preconditions: **CONFIRMED PRECONDITION**. The agent and the server share a user, and the server's port is reachable from agent code with no authentication.
  - Interference or impersonation itself: **NOT TESTED**. This was deliberately left out; no attack was attempted.
- **Potential impact (hypothesis):** a same-user process can normally send signals to, and compete for the port of, another process of that user. Together with B1–B4 (the harness trusts the sandbox's reply), a compromised sandbox reply could change scores. This chain has not been demonstrated.

### D — State surviving reset (`sandbox/server.py` → `do_reset`)
| Test | Expected (secure) | Actual | Classification |
|------|-------------------|--------|----------------|
| `test_workspace_files_are_cleared` (control) | file inside the workspace removed | removed | PASS (control) |
| `test_files_written_outside_workspace_are_cleared` | a file written by `run_python` outside the workspace is gone after reset | **still present; episode 2 saw it (`True`)** | CONFIRMED VULNERABILITY (host simulation) |

- **Root cause:** `do_reset` only empties `WORKSPACE`. Locations such as `/tmp` or the sandbox user's home are untouched, and the sandbox container is reused for the oracle, no-op and model runs, and for every task in `MODE=all`.
- **Container confirmation:** in the disposable container, a file written to `/tmp` by `run_python` was still there after `/reset` (`tmp_marker_exists=True`, see D2 output).

#### D2 — Processes surviving reset — CONFIRMED VULNERABILITY (container)
- **Probe:** `docker_probe_c_d.py`, the `D_START` and `D_CHECK` parts.
  1. `run_python` starts a harmless `sleep 30` in its own session and writes `/tmp/rlsec_marker.txt`.
  2. The driver calls `/reset`.
  3. `run_python` checks `/proc/<pid>`.
- **Affected code:** `sandbox/server.py` → `do_reset` (only clears `WORKSPACE`, does not stop processes), `tool_run_python` (detached children outlive the call).
- **Expected (secure):** after reset, no process started by the previous episode is running and `/tmp` is clean.
- **Actual output:**
  ```
  D2: sleep_pid=27 after_reset_state=S tmp_marker_exists=True
  ```
  The `sleep` process was still alive (state `S`, sleeping) after reset, and the `/tmp` file was still present.
- **Impact:** a process or file left by one episode is still running or present in the next. In the real flow the same sandbox container serves the oracle, no-op and model runs, and every task in `MODE=all`.
- **Cleanup:** the `sleep` process stopped on its own after 30 s at most. The whole container, including `/tmp`, was deleted by `docker stop` (`--rm`).

## 4. Relevant files and functions

| File | Function | Related tests |
|------|----------|---------------|
| `sandbox/server.py` | `CHILD`, `do_verify`, `do_reset`, `tool_run_python` | A, B6, C, D |
| `harness/env.py` | `Env.score`, `_equal` | B1–B5 |
| `harness/runner.py` | `run_episode` (calls `score()` every step) | A (impact) |
| `Dockerfile.sandbox`, `docker-compose.yml` | single `sandbox` user, container reused across episodes | C, D (not yet tested) |

## 5. Limitations

- Unit tests A, B and D1 now pass the portability check on Linux and Python 3.12 (section 6b).
  - They still run outside the real two-container Compose setup.
  - D1 on Linux used a temporary folder as the stand-in. The real `/tmp` case was confirmed separately by the D2 probe.
  - The test image has no `requests` package. `harness/env.py` was loaded with the same test-only stub as on Windows, which is fine because `score()` only reaches the network through `_post`, and the tests replace it.
- B tests simulate sandbox replies. They show how the harness reacts to bad input, not that an agent can produce that input.
- C was observational only. Actual interference or impersonation of the server remains **NOT TESTED**.
- B6 (fake result markers from agent code) remains **NOT TESTED**.
- The test container was started with `docker run` and **without `--init`**, so the server was PID 1. Compose uses `init: true`, which puts `tini` at PID 1. This doesn't change the user IDs or whether processes and files survive `/reset`, but it is a small difference from production.
- The container ran with `--network none`, which is stricter than Compose's `isolated` internal network. Network reachability between the sandbox and the harness was not part of this test.

## 6. Docker commands that were run (approved)

Pre-check: `docker image ls rlenv-sectest-sandbox` and `docker ps -a --filter name=^/rlenv-sectest$` both returned nothing, so neither name was in use.

```bash
docker build -f Dockerfile.sandbox -t rlenv-sectest-sandbox:local .
docker run -d --rm --name rlenv-sectest --network none --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 --memory 512m -v "C:/Users/asus/Desktop/Hackathon/RL-ENV-TEMPLATE/tests/security:/tests:ro" rlenv-sectest-sandbox:local
docker exec rlenv-sectest python -B /tests/docker_probe_c_d.py
docker stop rlenv-sectest
```

- `docker inspect` confirmed `net=none priv=false capdrop=[ALL]`, with the only mount `tests/security -> /tests` and `rw=false`. No Docker socket, `.env` or secrets were passed in.
- After `docker stop`, no `rlenv-sectest` container remained.
- The image `rlenv-sectest-sandbox:local` (177 MB) was **kept**, as instructed. It can be removed later with `docker image rm rlenv-sectest-sandbox:local`.

## 6b. Portability check: unit suite on Linux / Python 3.12 (run)

- **Pre-checks:**
  - Branch is `devops-environment`, with only the 6 untracked files from section 9 and no tracked changes.
  - No container named `rlenv-sectest-unit` existed.
  - `python:3.12-slim` was not cached by tag. To avoid a download, the run used the already-built `rlenv-sectest-sandbox:local` image, which is that same base and is the image the sandbox runs in production.

Command (Git Bash on Windows; `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting the container paths):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --name rlenv-sectest-unit --network none --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 --memory 512m -v "C:/Users/asus/Desktop/Hackathon/RL-ENV-TEMPLATE:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 rlenv-sectest-sandbox:local python -B -m unittest discover -s tests/security -p "test_*.py" -v
```

The actual run also printed environment checks first (`python --version`, `id`, the network interface list, and a write attempt on `/repo`).

**Isolation evidence (from inside the container):**
- `Python 3.12.15`
- `uid=1000(sandbox)`, so the tests did not run as root.
- The only network interface was `lo` (`--network none`).
- `touch /repo/.rw_probe` printed `Read-only file system`, so the repository mount was read-only.
- After the run, no `rlenv-sectest-unit` container remained (`--rm`), and `git status` was unchanged.

**Results:** `Ran 9 tests ... FAILED (failures=6)`, exit code 1. That exit code is expected, because failures mark reproduced weaknesses.

| Test | Linux / 3.12 | Windows / 3.14 | Meaning |
|------|--------------|----------------|---------|
| A `test_agent_json_module_is_not_imported_by_checker` | FAIL | FAIL | expected security failure (confirmed) |
| B `test_baseline_full_and_empty` | ok | ok | control |
| B `test_missing_results_count_as_failures` | FAIL (0.7) | FAIL (0.7) | expected security failure (confirmed) |
| B `test_extra_results_are_ignored_or_rejected` | ok | ok | not reproduced |
| B `test_non_dict_result_does_not_crash_scoring` | FAIL (`AttributeError`) | FAIL | expected security failure (confirmed) |
| B `test_ok_must_be_true_not_just_truthy` | FAIL (1.0) | FAIL (1.0) | expected security failure (confirmed) |
| B `test_bool_result_does_not_match_integer_expectation` | FAIL (0.7) | FAIL (0.7) | expected security failure (confirmed) |
| D `test_workspace_files_are_cleared` | ok | ok | control |
| D `test_files_written_outside_workspace_are_cleared` | FAIL | FAIL | expected security failure (confirmed) |

- **Runner errors** (unittest `ERROR`, import failures, missing modules): **0**.
- **Compatibility differences** between platforms: **0**.
- The `AttributeError` in `test_non_dict_result_does_not_crash_scoring` comes from `harness/env.py:56`. The test catches it and reports it as a FAIL on purpose, because the crash is the weakness being tested.
- **Still not run anywhere:** C's actual interference or impersonation, and B6 (fake result markers from agent code).

## 7. Unresolved risks

1. Hidden-check source is visible to agent code during the episode (A, plus verification every step).
2. The harness trusts the sandbox's verify reply without checking its shape or count (B1–B4).
3. The sandbox server and agent code share one user, and the server's port is reachable from agent code (C precondition confirmed). Whether this can actually be used to interfere is untested.
4. Files in `/tmp` and running processes survive `/reset` (D1 and D2 confirmed).

## 8. Prioritized recommendations (not implemented)

1. **Isolate check execution** (A). In `sandbox/server.py` `CHILD`/`do_verify`, run checks with `python -I` (isolated mode: the working directory is not on `sys.path` and user site is off). Import `json` before any workspace path is added, and pass results through a channel the checked code can't write to. Re-test with test A.
2. **Fail closed in `Env.score`** (B). Require `len(results) == len(cases)`, require each result to be a dict with `ok is True`, reject booleans on both sides in `_equal`, and score 0 on any malformed reply instead of crashing. Re-test with the B tests.
3. **Verify only at episode end**, or at least never expose check source during the episode (`harness/runner.py`). Re-test with A and a new test that counts verify calls per episode.
4. **Full reset between episodes** (D1, D2, now confirmed). Stop every process left by the agent, clear `/tmp` and home, or recreate the sandbox container for each episode. Re-test with D1 and `docker_probe_c_d.py` (D2 should report `after_reset_state=absent tmp_marker_exists=False`).
5. **Separate users** for the server and agent code, and authenticate or restrict the control endpoints (C). Re-test with `docker_probe_c_d.py` (C should report different UIDs, and no access to the control port from agent code).

## 9. Files created or changed in this phase

Created (all untracked):
- `tests/security/_helpers.py`
- `tests/security/test_a_module_shadowing.py`
- `tests/security/test_b_result_handling.py`
- `tests/security/test_d_reset_state.py`
- `tests/security/docker_probe_c_d.py` (container-only probe for C and D2)
- `audit-notes/security_test_report.md` (this file)

Changed: none. No production code, tasks, rewards, Dockerfiles or Compose files were modified.

Docker resources: the image `rlenv-sectest-sandbox:local` was created and kept. The container `rlenv-sectest` was created and removed.

## 10. Git checkpoint recommendation (not done)

After review, commit only the files above on `devops-environment`:

```bash
git add tests/security audit-notes/security_test_report.md
git commit -m "test: add security regression tests and report"
```

Note: these tests currently **fail by design**, because they show the weaknesses are real. Don't wire them into a check that blocks the Docker run until the fixes land.
