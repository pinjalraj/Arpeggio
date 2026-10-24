# Security Regression Test Report — RL-ENV-TEMPLATE

- **Branch:** `devops-environment` (working tree was clean before this phase)
- **Date:** 2026-10-24
- **Scope:**
  - Phase 1 (sections 1–10): tests only.
  - Phase 2 (section 11): fix for issue A, which adds a separate verifier container. Task definitions and the reward formula are unchanged in both phases.
- **Where tests ran:**
  - A, B and D1: Windows host, Python 3.14.5, inside throwaway temporary folders. Re-run on **Linux, Python 3.12.15** in a disposable container with identical results (section 6b).
  - C and D2: one disposable container (`rlenv-sectest`) built from the unmodified `Dockerfile.sandbox` (Python 3.12, Linux), approved by the user.

## 1. Executive summary

| ID | Weakness | Result |
|----|----------|--------|
| A  | Agent-created `json.py` runs inside hidden-check execution; check source reachable from the agent | **FIXED in phase 2** (was CONFIRMED VULNERABILITY; see section 11) |
| B1 | Missing verifier results are silently ignored (score inflated) | **FIXED (phase B)** (was CONFIRMED; see section 13) |
| B2 | Truthy non-boolean `ok` value counts as a pass | **FIXED (phase B)** (was CONFIRMED) |
| B3 | Malformed (non-object) result crashes scoring | **FIXED (phase B)** (was CONFIRMED) |
| B4 | Boolean `True` matches an expected integer `1` | **FIXED (phase B)** (was CONFIRMED) |
| B-extra | Extra verifier results | FIXED: an exact count is now required (was NOT REPRODUCED) |
| B5 | Extra trailing results change the score | NOT REPRODUCED |
| B6 | Agent-printed fake result markers inside the check process | NOT TESTED |
| C-pre | Agent code ran as the same user as the server | **FIXED in container (phase E)**: run_python runs as `coder`, the server as root — section 15 |
| C-auth | Control endpoints (`/reset`, `/tool`, `/snapshot`, `/state`) reachable unauthenticated | **FIXED in container (phase E)**: token required (verified 401 vs 200) and the candidate cannot read it (different uid) — section 15 |
| C  | Actual interference with / impersonation of the sandbox server | NOT TESTED (candidate can't signal/ptrace the root server; no interference attack simulated) |
| D1 | Files outside the workspace survive reset | **FIXED in container (phase E)**: candidate confined to workspace + managed temp (verified `/opt` and `/tmp` writes denied); both wiped on reset — section 15 |
| D2 | Processes survive reset | **FIXED in container (phase E)**: reset kills all `coder` processes by uid, including a detached one (verified) — section 15 |

**Current unit suite (after phase 2):** 23 tests on Linux/Python 3.12. 18 pass and 5 fail; the 5 failures are the still-open B1–B4 and D1. See section 11.

Phase 1 unit test suite result: **9 tests, 3 passed, 6 failed**, identical on Windows/Python 3.14.5 and Linux/Python 3.12.15. All 6 failures are expected security failures (assertion failures), with 0 test-runner errors and 0 compatibility failures. The container probe is a separate script (section 3, C and D2). The tests check for the *secure* behaviour, so a failing test means the weakness was reproduced. Once the fixes are in, all of them should pass.

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
These tests replace the `/verify` reply with simulated results. Since phase 2 that reply comes from the verifier service, not the sandbox. They check only how the harness scores what it receives.

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

Created (all committed in `00cebbd` on `devops-environment`):
- `tests/security/_helpers.py`
- `tests/security/test_a_module_shadowing.py`
- `tests/security/test_b_result_handling.py`
- `tests/security/test_d_reset_state.py`
- `tests/security/docker_probe_c_d.py` (container-only probe for C and D2)
- `audit-notes/security_test_report.md` (this file)

Changed: none. No production code, tasks, rewards, Dockerfiles or Compose files were modified.

Docker resources: the image `rlenv-sectest-sandbox:local` was created and kept. The containers `rlenv-sectest` and `rlenv-sectest-unit` were created and removed.

## 10. Git checkpoint

The security evidence is committed as **`00cebbd`** ("test: add security regression tests and report") on `devops-environment`.
- That commit adds the 6 files above, 509 lines, and only adds lines.
- It changes no production files: `harness/`, `sandbox/`, the Dockerfiles, `docker-compose.yml` and `requirements.txt` are identical to the previous commit `0041843`.
- The branch has no upstream tracking set, so whether `00cebbd` has been pushed was not checked.
- Later edits to this report, such as this section, are separate and still uncommitted until reviewed.

Note: these tests currently **fail by design**, because they show the weaknesses are real. Don't wire them into a check that blocks the Docker run until the fixes land.

## 11. Phase 2 — Fix for issue A (separate verifier container)

### 11.1 What changed and why

**Before:**
- The harness posted every hidden check's **source** to the sandbox's `/verify` endpoint.
- The sandbox ran each check with `python -c CHILD <source>`, with the working directory set to a copy of the agent's files.
- That meant:
  - agent files came first on the import path, so test A could replace `json`;
  - the check source sat in the process's command-line arguments;
  - everything ran inside the agent's own container, as the agent's own user, after every step.

**After:**
```
harness --GET /snapshot---------------> sandbox    (agent tools only; no check execution)
harness --POST /verify (files+checks)-> verifier   (own internal network; sandbox has no route to it)
```

| Design decision | Where | Why |
|---|---|---|
| Hidden checks run in a new `verifier` container | `verifier/server.py`, `Dockerfile.verifier`, `docker-compose.yml` | The check source never enters the agent's container, so nothing the agent leaves behind there (files, processes) can observe it |
| The verifier sits on its own `verify` network (internal), shared only with the harness | `docker-compose.yml` | No path verifier ↔ sandbox and no internet. Verifier output only reaches the harness, and the agent never sees verifier results |
| Check source goes through **stdin**, never command-line arguments or files | `verifier/server.py` → `CHECKER`, `run_check` | Not visible in `/proc/*/cmdline`, the process list or the disk |
| The checker runs with `python -I` and the project folder is added at the **end** of `sys.path`, after the checker imports `json`, `os` and `sys` | `CHECKER` | Agent files cannot replace standard-library modules, but `from textutils import ...` still works |
| A fresh folder per check, deleted afterwards; the process group is killed after each check | `run_check`, `kill_group` | One check cannot leave state or processes for the next |
| The verifier runs as a non-root user (uid 1001) with a read-only root filesystem, a 64 MB `/tmp`, no capabilities, `no-new-privileges`, and 128 pids / 512 MB limits; its code is owned by root | `Dockerfile.verifier`, `docker-compose.yml` | Nothing running inside can change the verifier |
| The sandbox's `CHILD` and `/verify` are removed. A new `GET /snapshot` returns regular files only, never follows symlinks, skips `__pycache__`, and is **refused** (not truncated) over 500 files, 1 MB per file or 5 MB in total | `sandbox/server.py` → `do_snapshot` | The snapshot cannot contain data from outside the workspace |
| The harness validates the snapshot before using it: relative paths only, no `..`, `\` or NUL, valid base64, the same size limits. An invalid snapshot scores 0.0 with an `error` field, and the verifier is not called | `harness/env.py` → `check_snapshot`, `Env.score` | The harness does not trust the sandbox |
| The verifier re-validates everything: paths, sizes, base64, file/folder clashes, case types and counts, request size (16 MB). Errors name the problem but never echo request content | `verifier/server.py` → `clean_path`, `decode_files`, `do_verify`, `Handler` | It does not trust the harness's input either, and its errors cannot leak check source |

**Unchanged:**
- task definitions, expected answers (still harness-only) and the reward formula;
- the model interface, tools and the oracle and no-op baselines;
- JSONL log fields (checked: the same keys as before);
- the single `docker compose up` command.

### 11.2 Test results (phase 2)

Unit suite: `python -B -m unittest discover -s tests/security -p "test_*.py" -v`

| Platform | Result |
|---|---|
| Linux, Python 3.12.15, disposable container (`--network none`, repo mounted read-only) | 23 tests: 18 PASS, 5 FAIL |
| Windows host, Python 3.14.5 | 23 tests: 17 PASS, 5 FAIL, 1 SKIP (no permission to create symlinks on Windows) |

The 5 failures are the still-open B1–B4 and D1. They are expected and not caused by this change.

| Test | Kind | Linux / 3.12 |
|---|---|---|
| `test_agent_json_module_is_not_imported_by_checker` | **attack attempt** (planted `json.py` canary) | PASS: canary not created, legitimate check still returns 42 |
| `test_agent_os_module_is_not_imported_by_checker` | **attack attempt** (planted `os.py` canary) | PASS |
| `test_check_source_not_in_process_arguments` | **attack attempt** (agent module records `sys.argv` and `/proc/self/cmdline`) | PASS: marker absent; the test also confirms the spy module ran |
| `test_scoring_leaves_no_check_source_in_the_agent_workspace` | **attack attempt** (end-to-end `Env.score`, then search the workspace, `list_files`, snapshot and state for a marker) | PASS |
| `test_sandbox_has_no_check_execution_route` | precondition (code structure) | PASS |
| `test_reference_and_noop_scores_unchanged` | regression (fixture oracle 1.0, no-op 0.0) | PASS |
| `test_rejects_traversal_and_absolute_paths` (8 paths) | **attack attempt** on the verifier input | PASS |
| `test_rejects_oversized_and_malformed_snapshots` (7 cases) | **attack attempt** on the verifier input | PASS |
| `test_rejects_malformed_cases` | input validation | PASS |
| `test_error_messages_do_not_echo_request_content` | leak check | PASS |
| `test_valid_nested_snapshot_still_works` | regression | PASS |
| `test_symlinks_out_of_the_workspace_are_not_followed` | **attack attempt** (file and folder symlinks to outside data) | PASS on Linux; SKIP on Windows |
| `test_oversized_workspace_is_refused_not_truncated` | limit | PASS |
| `test_snapshot_round_trips_workspace_bytes` | regression (exact bytes) | PASS |
| `test_invalid_snapshots_score_zero_and_skip_the_verifier` (7 cases) | **attack attempt** (harness given a forged snapshot) | PASS |

On the first Windows run, `test_snapshot_round_trips_workspace_bytes` failed. The reason was that `do_reset` writes text with Windows encoding and line endings on a Windows host, not a snapshot problem. The test was corrected to compare against the bytes on disk, which is the property under test. Linux was unaffected.

**Compose and runtime checks (full stack, real containers):**

| Check | Kind | Result |
|---|---|---|
| `docker compose config` | config validation | PASS. Networks: sandbox `[isolated]`; verifier `[verify]`; harness `[internet, isolated, verify]`. Both `isolated` and `verify` are `internal: true`. The verifier has `read_only: true` and `cap_drop: [ALL]` |
| `docker inspect` of the running containers | precondition | PASS. sandbox: `rl-env-template_isolated`, user `sandbox`. verifier: `rl-env-template_verify`, user `verifier`, read-only root filesystem |
| sandbox → `verifier:8000` by name | **connection attempt** | PASS: DNS lookup failed (`gaierror`) |
| sandbox → verifier IP `172.20.0.2:8000` directly | **connection attempt** | PASS: connection failed (`OSError`) |
| verifier → sandbox IP `172.18.0.2:8000` and by name | **connection attempt** | PASS: failed |
| sandbox and verifier → `example.com:80` | **connection attempt** | PASS: DNS failed (no internet) |
| `MODE=baselines docker compose up --build ...` | baseline gate | PASS. All 5 tasks: oracle 1.00 (≥ 0.95), no-op 0.00 (≤ 0.05). Gate took 11.99 s; exit 0; `logs/20261024-104917/gate.json` |
| `docker compose up --build ...` (offline `mock/solver`, `slugify`) | demo | PASS. Gate 2.46 s; `termination: success`, reward 1.0, 2 steps, state hash `457d640b` (same as before); exit 0; `logs/20261024-104948/` |
| Hidden-check text in the new run logs (count only) | leak check | PASS: 0 matches in `gate.jsonl` and `episode.jsonl` |

The full-stack runs used the project's own Compose containers. They were left **stopped** (`docker compose stop`), not removed.

### 11.3 Remaining limitations

1. **The agent's code still runs inside each check process.** A check has to import the agent's module to test it, so that code shares the check's process. It could inspect the check while it runs, or print a fake result marker (B6).
   - The fix **contains** this: there is no network path back to the sandbox, the scratch folders are deleted, and verifier results never reach the model.
   - It is **not** prevented.
   - Faking results (B6) and the other scoring problems (B1–B4) are scheduled for the next phase. B6 remains **NOT TESTED**.
2. **Leftover state inside the verifier container.** Agent code running inside a check could write into the verifier's `/tmp` (64 MB limit). Leftover processes are killed after each check, but files written outside the per-check folder are not cleared until the container restarts. This could affect later checks in the same run, but cannot reach the agent. The reset-cleanup phase (D) should cover the verifier too. **NOT TESTED.**
3. **Isolation between the verifier and the harness was not attack-tested.** They share a network. The harness listens on no port. **NOT TESTED.**
4. **Memory use.** `run_check` keeps the last 100,000 characters of a check's output, but the full output is buffered first. The 512 MB container memory limit is the backstop. **NOT TESTED.**
5. **The sandbox control endpoints (C) are unchanged.** The agent can still call `/snapshot`, but that only returns its own files. Same-user interference with the sandbox server stays **NOT TESTED**.
6. **D1 and D2 are still open** (files and processes survive reset in the sandbox).

### 11.4 Files changed in phase 2

| File | Change |
|---|---|
| `verifier/server.py` | **new**: verifier service (`/health`, `/verify`), isolated checker, input validation and limits |
| `Dockerfile.verifier` | **new**: non-root image with root-owned code |
| `docker-compose.yml` | `verifier` service, `verify` internal network; the harness joins `verify`, gets `VERIFIER_URL`, and waits for the verifier to be healthy |
| `sandbox/server.py` | removed `CHILD` and `/verify`; added `GET /snapshot` |
| `harness/env.py` | `Env(sandbox_url, verifier_url)`; `score()` takes a snapshot, validates it, and verifies via the verifier; adds `check_snapshot`. Scoring math unchanged |
| `harness/run.py` | reads `VERIFIER_URL`, waits for both services |
| `README.md` | layout and description mention the verifier |
| `tests/security/_helpers.py` | `load_verifier`, `encode_files`, `wired_env`. `env_with_results` adapted to the new `Env` interface; B test assertions unchanged |
| `tests/security/test_a_module_shadowing.py` | test A now targets the verifier, plus `os.py`, argv-leak, workspace-leak, route and baseline tests |
| `tests/security/test_a_snapshot_validation.py` | **new**: path traversal, symlink, size, malformed-input and harness-distrust tests |
| `audit-notes/security_test_report.md` | this section and the summary updates |

`tests/security/docker_probe_c_d.py` is unchanged; it doesn't use `/verify`.

## 12. Phase 3 — Process isolation and structured checks (strengthens the issue-A fix)

### 12.1 Why the architecture changed again

Phase 2 moved checks into a separate verifier container, but the check source (an arbitrary
Python snippet) still ran in the **same interpreter** as the candidate's imported code, and the
snippet was sent into that process. Per the decision that *untrusted candidate code must never
share a Python interpreter with trusted check logic*, and that a *structured check specification*
is preferred over arbitrary source, phase 3 makes two changes:

1. **Two processes, not one.** The trusted verifier **server** process now never imports candidate
   code. Each check runs in a throwaway **worker** subprocess (`verifier/worker.py`). The server
   validates the request, enforces limits, spawns the worker, and validates its output.
2. **Structured checks, not snippets.** A check is now a small structured program — a module name
   plus a list of `call`/`method` steps with JSON literal arguments (`harness/tasks.py`). There is
   no arbitrary check source anywhere, and the program never contains the expected answer.

### 12.2 How it works now

```
harness  --GET /snapshot-->  sandbox        (agent tools only)
harness  --POST /verify {files, programs}-->  verifier SERVER  (trusted; no candidate import)
                                                   |  spawns one per check
                                                   v
                                            verifier WORKER  (python -I worker.py)
                                              imports candidate, runs the structured steps,
                                              writes an untrusted outcome to a dedicated file
```

- The harness keeps the expected answers and the reward formula and does all comparisons
  (`harness/env.py` → `Env.score`). For an expected exception it compares the outcome's exception
  type name (`Raises`); otherwise it compares the returned value with `_equal`.
- The worker imports the standard library **before** adding the candidate's directory to the END of
  `sys.path`, so candidate files can't shadow stdlib modules.
- The worker's stdout/stderr are discarded and its outcome is written to a dedicated file; the server
  does **not** scan candidate-controlled stdout for a marker. The outcome is then structurally
  validated (`ok` must be exactly `True`; the value size and exception-name length are bounded).
- Per-check limits: a fresh temp dir deleted afterwards, a 5 s wall-clock timeout, the worker's
  process group killed after each check, and (in the container) a read-only root FS, a 64 MB tmpfs,
  `cap_drop: ALL`, `no-new-privileges`, 128 pids and 512 MB.

### 12.3 Test results (phase 3)

| Check | Kind | Result |
|---|---|---|
| Unit suite, Linux/Python 3.12.15, disposable container (`--network none`, repo read-only) | regression + structural | 27 tests: 22 PASS, 5 FAIL (the known B1–B4 and D1) |
| Unit suite, Windows host/Python 3.14.5 | regression + structural | 27 tests: 21 PASS, 5 FAIL, 1 SKIP (symlink test needs Linux) |
| `json.py` / `os.py` planted by the agent | **attack attempt** | PASS: not imported during a check; the real check still returns 42 |
| Candidate runs in a separate process (PID differs from the verifier) | **attack-relevant** | PASS |
| Programs carry no expected answers; `ValueError` not present in `parse_duration` programs | leak check | PASS |
| An arbitrary source string is rejected as a case | input validation | PASS |
| Scoring does not modify the agent workspace | regression | PASS |
| Reference / no-op fixture scores (1.0 / 0.0), and an expected-exception case | regression | PASS |
| Snapshot path-traversal / absolute / symlink / oversize / malformed | **attack attempt** | PASS (symlink on Linux; SKIP on Windows) |
| `docker compose config` | config validation | PASS |
| `MODE=baselines` full stack, all 5 tasks | baseline gate | PASS: oracle 1.00 (≥ 0.95), no-op 0.00 (≤ 0.05) for every task; 15.81 s; exit 0; `logs/20261024-124219/gate.json` |
| Offline `mock/solver` demo (`slugify`) | demo | PASS: success, reward 1.0, 2 steps, state hash `457d640b` (unchanged); exit 0; `logs/20261024-124300/` |
| Expected answers / exception names in run logs | leak check | PASS: 0 occurrences of `hello-world`, `already-dashed` or `ValueError` |

During this work the LRU task migration twice produced a wrong expected shape (the constructor was
recorded, then a single-get case expected a list). Both were caught by the baseline oracle check
(oracle 0.85 / 0.00) and fixed; no reward threshold was weakened. The five tasks' intended behaviour
is unchanged: same inputs, same expected outputs as the original snippets.

### 12.4 Remaining limitations and untested claims (honest)

1. **A candidate can still forge its OWN check outcome.** Candidate code runs in the worker process,
   so it can write the outcome file itself. For a value check this only "passes" if it produces the
   correct value (i.e. solves the task). For an **expected-exception** check a non-raising candidate
   could in principle write `{"ok": false, "exc": "ValueError"}` and be scored as passing. This is a
   real residual. It is contained — expected answers are never sent to the worker, the comparison is
   in the harness, and the verifier is network-isolated from the sandbox — but **not prevented**, and
   **NOT TESTED** (no attack was simulated, per instruction).
2. **Leftover files inside the verifier container.** Candidate code could write under the 64 MB tmpfs;
   only the per-check temp dir is deleted. This cannot reach the agent. **NOT TESTED.**
3. **Per-check memory/CPU are not individually capped** (only the 512 MB / pids container limits and
   the 5 s timeout). A single check could use up to the container limit. **NOT fully enforced.**
4. **B1–B4 (scoring leniency) and D1/D2 (reset cleanup) are still open** — later phases.
5. The forging residual (1) means this fix satisfies "candidate cannot read hidden verification
   information" but does **not** fully satisfy "candidate cannot influence its own reported result".
   That influence is bounded to its own check and cannot raise the score without the correct answer,
   except the exception-type case noted above.

### 12.5 Files changed in phase 3

| File | Change |
|---|---|
| `verifier/worker.py` | **new**: throwaway candidate runner (separate process); imports candidate, runs the structured steps, writes an untrusted outcome; holds no answers or verification logic |
| `verifier/server.py` | rewritten: trusted server spawns a worker per check, validates the structured program and the worker's outcome; no longer execs check snippets (`CHECKER`/`run_check` gone) |
| `Dockerfile.verifier` | also copies `worker.py` |
| `harness/tasks.py` | `Case.program` is now a structured program (module + steps); added `Raises` for expected exceptions; all five tasks migrated with unchanged behaviour |
| `harness/env.py` | `score()` sends programs and compares outcomes (value via `_equal`, exception via `Raises`); imports `Raises` |
| `tests/security/_helpers.py` | structured `FIXTURE_PROGRAM` / `fixture_program`; `FIXTURE_CHECK` now aliases the program |
| `tests/security/test_a_module_shadowing.py` | rewritten: shadowing, separate-process, no-answer-leak, structured-only, scoring tests |
| `tests/security/test_a_snapshot_validation.py` | malformed/arbitrary-case rejection and nested-package program updated to the structured format |
| `audit-notes/security_test_report.md` | this section |

Unchanged in phase 3: `harness/runner.py`, `harness/run.py` (phase 2 already added `VERIFIER_URL`),
`docker-compose.yml` (phase 2), the sandbox, the tasks' reference solutions and reward formula.

## 13. Issue B — Scoring correctness

### 13.1 What was wrong and what changed

`Env.score` trusted the verifier reply and compared loosely, which let four things slip through
(all reproduced by `tests/security/test_b_result_handling.py`). The fix is in `harness/env.py`
only; the reward formula, the five tasks and their thresholds are unchanged.

| Requirement | Fix in `harness/env.py` |
|---|---|
| Exactly one result per case; missing/extra must not inflate the score | `score()` reads `results` from the reply, and if it is not a list or its length ≠ the number of cases, returns a controlled zero (`_zero`) instead of zipping to the shorter list |
| A pass needs the exact Boolean `True` | `_passed()` requires `result.get("ok") is True` for a value case and `is False` for an expected exception — a truthy `"yes"` or `1` no longer passes |
| Malformed results must not crash the episode | `_passed()` first checks `isinstance(result, dict)`; a non-dict (or wrong-typed) result is a failed case, not an exception |
| Booleans must not match integer expectations | `_equal()` now returns `a is b` when either side is a bool, so a result of `True` never equals an expected `1`; genuine `int`/`float` comparisons keep the `1e-9` tolerance |
| Expected exceptions and return values keep working | the `Raises` path and the value path are both handled in `_passed()`; the five tasks behave exactly as before |
| Treat worker replies as untrusted | the reply shape, the result count, each result's type and the `ok` flag are all validated in the harness, on top of the verifier's own `sanitize_result` |

`_zero()` returns `{"tests_passed": 0.0, "no_regressions": 0.0, "total": 0.0, "error": ...}` and is also
used for the existing bad-snapshot case. In normal operation the verifier always returns exactly one
well-formed result per case, so the count guard never trips for honest runs.

### 13.2 Test results

| Check | Result |
|---|---|
| `test_b_result_handling.py` (6 tests: baseline, missing, extra, malformed, truthy-ok, bool≠int) | **all PASS** (were failing by design before this change) |
| Full unit suite, Windows/Python 3.14.5 | 27 tests: 25 PASS, 1 FAIL (D1, still open), 1 SKIP (symlink needs Linux) |
| Full unit suite, Linux/Python 3.12.15, disposable container | 27 tests: 26 PASS, 1 FAIL (D1) |
| `MODE=baselines` full stack, all 5 tasks | PASS: oracle 1.00, no-op 0.00 for every task; 16.34 s; exit 0; `logs/20261024-125852/gate.json` |
| Offline `mock/solver` demo | PASS: success, reward 1.0, 2 steps; exit 0 |

The only remaining unit failure is `test_d_reset_state.py::test_files_written_outside_workspace_are_cleared`
(Issue D, reset cleanup), which is out of scope for this phase.

### 13.3 Remaining limitations

- The exception-outcome forging residual from section 12.4 is unchanged: scoring is now strict about
  shape and Booleans, but a candidate that controls its own worker outcome could still report
  `{"ok": false, "exc": "ValueError"}` for an expected-exception case. Bounded (no expected answers
  reach the worker; network-isolated) and still **NOT TESTED**.
- Issue D (files/processes surviving reset) is still open.

### 13.4 Files changed in this phase

| File | Change |
|---|---|
| `harness/env.py` | `score()` validates the reply and enforces an exact result count; new `_passed()` (shape + `ok is True`/`is False`); `_equal()` makes bools match only bools; new `_zero()` helper |
| `audit-notes/security_test_report.md` | this section and the summary-table updates |

No other files changed. The verifier, sandbox, tasks, Docker config and reward formula are untouched.

## 14. Issue C/D — Control access, reset isolation, resource limits

This phase hardens the parts that the current single-identity architecture can enforce, and is
explicit about the parts that need a separate candidate identity (proposed at the end). No task,
reward, scoring or verifier behaviour changed.

### 14.1 What was implemented

| Area | Change | Where |
|---|---|---|
| Control-endpoint auth | `/reset`, `/tool`, `/snapshot`, `/state` now require a shared `X-Sandbox-Token`; `/health` stays open. The token is read once and `pop`-ed from `os.environ` so it is not in the server's own `/proc/<pid>/environ`. Constant-time compare | `sandbox/server.py` → `authorized`, `OPEN_PATHS`, `Handler._guard`; `harness/env.py` sends the header; `docker-compose.yml` passes `SANDBOX_TOKEN` to sandbox + harness |
| Reset file cleanup | A managed candidate temp/home dir (`CAND_TMP`, outside the workspace so it is never snapshotted) is wiped and recreated on every reset; `run_python` runs with `TMPDIR`/`HOME` pointing there | `sandbox/server.py` → `CAND_TMP`, `do_reset`, `tool_run_python`; compose sets `CAND_TMP=/home/sandbox/cand-tmp` |
| Reset process cleanup | Each `run_python` runs in its own process group (`start_new_session`); the group ids are tracked and SIGKILL-ed on reset and on timeout | `sandbox/server.py` → `_groups`, `_kill_tracked_groups`, `tool_run_python`, `do_reset` |
| Resource limits | Added `cpus: "1.0"` to the sandbox and verifier (alongside the existing `mem_limit: 512m` and `pids_limit`); `run_python` keeps the 20 s wall-clock and 4 KB output cap | `docker-compose.yml` |
| Controlled failures | A sandbox call that raises during a step becomes a controlled tool error; a scoring/`state_hash` failure ends the episode as `crash` with a logged reason instead of an unhandled traceback | `harness/runner.py` → `run_episode` |
| Server import hygiene | The sandbox server runs under `python -I` so files in `/workspace` cannot shadow its own imports | `Dockerfile.sandbox` |

The agent's own `run_python` is intentionally **not** run with `-I`: the agent must be able to import and
test its workspace files. Isolation of the agent is by container/network, not by restricting its imports.

### 14.2 Test results

| Check | Kind | Result |
|---|---|---|
| `test_c_control_and_cleanup.py` (11 tests) | control auth, reset cleanup, controlled errors, fail-safe runner | **all PASS** |
| Live token enforcement in the running sandbox (`/health` 200; `/state` no/wrong token → 401; right token → 200) | **access-control check** | PASS |
| Full unit suite, Windows/Python 3.14.5 | regression | 37 tests: 35 PASS, 1 FAIL (D1 arbitrary-outside residual), 1 SKIP (symlink needs Linux) |
| Full unit suite, Linux/Python 3.12.15, disposable container | regression | 37 tests: 36 PASS, 1 FAIL (D1 residual) |
| `docker compose config` | config validation | PASS |
| `MODE=baselines`, all 5 tasks (token auth active) | baseline gate | PASS: oracle 1.00, no-op 0.00 for every task; 17.59 s; exit 0; `logs/20261024-131643/gate.json` |
| Offline `mock/solver` demo | demo | PASS: success, reward 1.0; exit 0 |

The one failing unit test, `test_d_reset_state.py::test_files_written_outside_workspace_are_cleared`,
writes to an arbitrary absolute path outside both the workspace and the managed temp dir. It is left
failing on purpose — it marks the residual that only the identity change below closes.

### 14.3 Honest limitations (what is NOT enforced yet)

The current sandbox runs the server and the candidate's `run_python` under the **same user**. That
makes three properties only partial:

1. **Control-endpoint auth (C-auth).** Unauthenticated access is blocked (verified), but a same-uid
   candidate can read the token from a sibling process's environment (e.g. PID 1 / `tini`'s
   `/proc/1/environ`). So the token stops external/unauthenticated callers but not a determined
   candidate. **NOT fully enforced; the candidate-side read is NOT TESTED.**
2. **Process termination on reset (D2).** Tracked process groups are killed, but a candidate process
   that calls `setsid()` again (or double-forks) leaves the tracked group and would survive. Guaranteed
   termination needs kill-by-uid (root + `CAP_KILL`) or a per-episode PID namespace. **Partial.**
3. **File confinement on reset (D1).** The managed temp/home is wiped, but candidate code can still
   write to any path its user can write (e.g. `/tmp`, other world-writable dirs), and those survive
   reset. Full confinement needs a candidate-writable area restricted to wiped dirs. **Partial.**

Resource limits are enforced at the container level (cpus/mem/pids) and per call (wall-clock, output).
Per-process `setrlimit` was deliberately not added inside the threaded server to avoid fork-time
deadlocks; the container limits cover the same ground. The exception-outcome forging residual from
section 12.4 and Issue D remain as previously described.

### 14.4 Proposed next change (needs approval) — separate candidate identity

The smallest architecture that actually enforces 1–3 above:

- Run the sandbox **server as root** inside the container, with `cap_drop: ALL` but `cap_add:
  [SETUID, SETGID, KILL]` and `no-new-privileges` kept.
- Run every `run_python` child **dropped to an unprivileged user** (e.g. `coder`, uid 1001) that cannot
  read root's `/proc/*/environ`, cannot signal or ptrace the server, and whose writable area is limited
  to the workspace and the managed temp.
- On reset, the root server kills **all** processes owned by `coder` (not just tracked groups) and wipes
  `coder`'s writable dirs.

This changes the container's privilege model (root + three caps instead of fully unprivileged), so I am
not implementing it without sign-off. With it, C-auth, D1 and D2 become fully enforced.

### 14.5 Files changed in this phase

| File | Change |
|---|---|
| `sandbox/server.py` | token-guarded control endpoints; `run_python` via `Popen` with its own process group, managed `TMPDIR`/`HOME`, timeout group-kill; reset kills tracked groups and wipes the managed temp |
| `harness/env.py` | sends `X-Sandbox-Token` on control calls; `state_hash` routed through the guarded getter; reads the token from the environment |
| `harness/runner.py` | sandbox/verifier failures during a step become controlled errors / a clean `crash` instead of a traceback |
| `Dockerfile.sandbox` | server runs under `python -I` |
| `docker-compose.yml` | `cpus` limits for sandbox + verifier; `SANDBOX_TOKEN` for sandbox + harness; `CAND_TMP` for the sandbox |
| `tests/security/test_c_control_and_cleanup.py` | **new**: control-auth, reset cleanup, controlled-error and fail-safe-runner tests |
| `audit-notes/security_test_report.md` | this section and the summary-table updates |

Unchanged: `verifier/*`, `harness/tasks.py`, the reference solutions, scoring and reward formula.

## 15. Phase E — Separate candidate identity (completes Issue C/D)

This replaces the single-identity residuals from section 14.3 with an enforced separation: the
sandbox server runs as root (with only three capabilities) and runs the agent's `run_python` as a
different unprivileged user, `coder`.

### 15.1 Design (smallest portable form)

- **Supervisor = root, three caps only.** `docker-compose.yml` sets `cap_drop: [ALL]` and
  `cap_add: [SETUID, SETGID, KILL]`, keeps `no-new-privileges: true`, and uses no privileged mode and
  no Docker socket. No `DAC_OVERRIDE`, so the root server still obeys file permissions.
- **Candidate = `coder` (uid 1001).** `run_python` is launched with Python's `user=`/`group=`/
  `extra_groups=[]`, which drops to `coder` in the child (needs SETUID/SETGID). The candidate gets a
  stripped environment (no token, no root env), `HOME`/`TMPDIR` in the managed temp.
- **Shared-group ownership.** Because root obeys permissions, `/workspace` and the managed temp are
  `root:coder`, mode `2770` (set-group-id): both the server and `coder` can read/write them, and task
  files are made group-writable so the agent can edit them. `coder` cannot write anywhere else.
- **Confinement.** At startup the server removes world-write from `/tmp`, `/var/tmp` and `/dev/shm`, so
  `coder` can only write the workspace and the managed temp — both wiped on reset.
- **Guaranteed termination.** On reset the root server kills **every process owned by `coder`** (by
  scanning `/proc/*/status`), so a detached process that left its group is still killed; `KILL` makes
  this work across sessions.
- **Off-container fallback.** When the server is not root or `coder` is absent (host, unit tests), the
  drop and kill-by-uid are inactive and the sandbox behaves as before; the drop-dependent tests skip.

### 15.2 Validation (in the real Compose sandbox, root + `coder`)

Run via `docker compose exec` into the running sandbox (authenticated local client):

| # | Property | Result |
|---|---|---|
| 1 | Server and candidate run under different UIDs | PASS: server uid 0, `run_python` uid **1001** |
| 2 | Candidate cannot read the control token | PASS: `SANDBOX_TOKEN` absent from its env; reading `/proc/1/environ` → **PermissionError** |
| 3 | Control endpoints reject unauthenticated requests | PASS: `/state` no token → **401**, wrong token → **401**, correct → **200** |
| 4 | Candidate confined to its area | PASS: write `/opt` → **PermissionError**, write `/tmp` → **PermissionError**, write workspace → **WROTE** |
| 5 | Detached process does not survive reset | PASS: a `sleep` in its own session was alive before reset and **gone after** |

Other checks:

| Check | Result |
|---|---|
| `docker compose config` | VALID (sandbox `cap_drop: ALL`, `cap_add: [SETUID, SETGID, KILL]`, not privileged) |
| Clean build `docker compose build --no-cache` | all three images built; no extra packages; within the build budget |
| `MODE=baselines`, all 5 tasks | PASS: oracle 1.00, no-op 0.00 for every task; 19.49 s; exit 0; `logs/20261024-134208/gate.json` |
| Offline `mock/solver` demo | PASS: success, reward 1.0, 2 steps; exit 0 |
| Unit suite — host (Python 3.14.5) | 39 tests OK, 4 skipped (drop-dependent + Windows symlink) |
| Unit suite — Linux container, non-root (Python 3.12.15) | 39 tests OK, 3 skipped (drop-dependent) |

The drop-dependent unit tests (`test_d_reset_state.py`) skip unless run as root with a `coder` user;
properties 1–5 above are their in-container equivalent and are the authoritative evidence.

### 15.3 Verified vs assumed, and remaining risks

**Verified on this machine (Docker Desktop, Linux containers):** different UIDs; token unreadable by the
candidate; control endpoints need the token; candidate cannot write outside its area; detached candidate
process killed on reset; all baselines and the demo still pass; clean build works.

**Assumptions / residuals:**
- Evidence is from Docker Desktop on Windows (Linux VM). A judge's native Linux Docker behaves the same
  for capabilities, uid drop and `/proc`, but this was not run on their machine. `SETUID`/`SETGID`/`KILL`
  are in Docker's default set, so `cap_drop: ALL` + `cap_add` of these needs no special daemon config.
- The server runs as **root inside the container** (locked down: three caps, no privileged, no socket,
  internal network). That is a deliberate trade for the privilege drop; it is not host root.
- The **exception-outcome forging** residual in the verifier (section 12.4) is unrelated to this phase and
  still open; B-scoring (section 13) and the task library are unaffected.
- `coder` could still consume CPU/memory up to the container limits during a single call (bounded by the
  20 s wall-clock, `cpus`, `mem_limit`, `pids_limit`). Per-call rlimits were intentionally not added
  inside the threaded server.

### 15.4 Files changed in phase E

| File | Change |
|---|---|
| `sandbox/server.py` | resolve `coder` ids; `run_python` drops to `coder` via `user=`/`group=`; reset kills all `coder` processes by uid; `_confine_shared_dirs` removes world-write from `/tmp`, `/var/tmp`, `/dev/shm`; task files made group-writable |
| `Dockerfile.sandbox` | add `coder` (uid 1001); `/workspace` and the managed temp owned `root:coder` mode `2770`; server runs as root under `python -I` |
| `docker-compose.yml` | sandbox `cap_add: [SETUID, SETGID, KILL]`; `CAND_TMP=/home/coder/cand-tmp` |
| `tests/security/test_d_reset_state.py` | rewritten: unprivileged-uid, confinement and detached-process-kill tests (skip when the drop is inactive) |
| `audit-notes/security_test_report.md` | this section and the summary-table updates |

Unchanged: `harness/*` (the phase C/D `env.py`/`runner.py` changes stand), `verifier/*`, `harness/tasks.py`,
the reference solutions, scoring and reward formula.

## 16. Task library — compose_port_alignment (first DevOps task)

A new sixth task was added; the five starter tasks are unchanged.

### 16.1 What it is
A config-repair task. The workspace has a tiny web app (`app.py`, listens on port 8080), a
`Dockerfile`, and a `docker-compose.yml` whose container-port mapping and health-check port are
wrong while the host port 8081 is correct. The agent must read the app and the compose file, see
that the app uses 8080, and fix `docker-compose.yml` so host 8081 maps to 8080 and the health
check targets 8080 — without changing the app. Task id: `compose_port_alignment`, difficulty
medium. It uses the existing tools (`read_file`, `write_file`, …); no new tool was needed.

### 16.2 Verifier compatibility — trusted file-inspection checks
The existing verifier runs candidate *Python* modules. A YAML task has no module to call, so the
smallest safe extension was a second kind of check that needs **no candidate code at all**:

- A `Case` may now carry a trusted `check(files)` callable instead of a `program`. When `program`
  is `None`, `Env.score` decodes the workspace snapshot and runs the check **in the harness**
  (`harness/env.py` → `_decode_snapshot`, `_run_file_check`). The check parses the file with
  `yaml.safe_load` and returns a value that is compared to the expected value with the same strict
  `_passed`/`_equal` logic.
- Expected values stay in the harness and are **never** sent to the verifier or the candidate; the
  verifier and its worker are unchanged, and `program`-based tasks still run exactly as before.
- The four acceptance checks are programmatic, not string matches: they parse the compose YAML and
  the app source and assert the container port for host 8081 is the app's port, the health-check
  port is the app's port (both target checks), and — as regressions — the host port stays 8081 and
  the app still listens on 8080 (so "fixing" by editing the app instead is rejected).
- One pinned dependency was added for this: `PyYAML==6.0.2` in `requirements.txt` (harness image
  only). The verifier and sandbox images gain nothing.

Safety note: YAML is parsed with a bounded loader, `harness/tasks.py::_safe_yaml` (see section 16.6).
`yaml.safe_load` executes no code, and before it runs the loader rejects oversized or excessively
complex input. Any rejection raises `ValueError`, which `_run_file_check` turns into a failed check
(a controlled task failure), never a harness crash.

### 16.3 Results (commands and outcomes)
- Unit tests, harness image (has PyYAML): `python -B -m unittest discover -s tests/security -p
  "test_f_compose_task.py"` → 5 tests OK: file-check-not-sent-to-verifier, deterministic reset,
  reference ≥ 0.95, no-op ≤ 0.05, wrong-repair (edit the app) rejected, partial-repair < 0.95.
- Full unit suite on host (no PyYAML): 44 tests OK, 7 skipped (the 3 YAML scoring tests, the 3
  privilege-drop tests, and the Windows symlink test). No failures; the five starter tasks and
  their structure tests still pass.
- `docker compose config` → VALID.
- Baseline gate, all six tasks (`MODE=baselines docker compose up --build …`) → all pass;
  `compose_port_alignment` oracle 1.00 (≥ 0.95), no-op 0.00 (≤ 0.05); 15.36 s; exit 0;
  `logs/20261024-143048/gate.json`. Thresholds were not changed.
- Offline demo (`TASK_ID=compose_port_alignment docker compose up …`) → success, reward 1.0, 2 steps,
  exit 0.
- Determinism: `build_task("compose_port_alignment", 0)` and `… 7)` give identical files and the
  same `/state` hash (the task ignores the seed, like the other fixed tasks).
- Logs: the expected answers / per-case results do not appear in `episode.jsonl` or `gate.jsonl`
  (the repaired compose the agent writes naturally contains `8080`, but that is its own work
  product, not a leaked expected value).

### 16.4 Unchanged limitation
The exception-outcome forging residual from section 12.4 is **not** fixed and is unrelated to this
task: `compose_port_alignment` uses only harness-side file checks (no candidate worker), so it is
not exposed to that residual, but the residual still stands for the module-based tasks.

### 16.5 Files changed in this phase
| File | Change |
|---|---|
| `harness/tasks.py` | `Case` gains a `check` field; new `compose_port_alignment` task (files, instructions, four file-check cases, reference); registered in `TASKS` |
| `harness/env.py` | `score()` runs `program=None` cases as trusted harness-side file checks; `_decode_snapshot`, `_run_file_check` |
| `requirements.txt` | add `PyYAML==6.0.2` (harness only) |
| `tests/security/test_f_compose_task.py` | **new**: task and file-check unit tests |
| `tests/security/test_a_module_shadowing.py` | structure test skips file-check cases (no program) |
| `audit-notes/security_test_report.md` | this section |

Unchanged: the verifier and worker, the sandbox, the scoring/reward formula and thresholds, and the
five starter tasks.

## 16.6 Hardening — bounded YAML loader

The untrusted YAML parsed in the harness is now vetted before `safe_load`.

- `harness/tasks.py::_safe_yaml(text)`:
  1. rejects input over **64 KB** (`_YAML_MAX_BYTES`);
  2. pre-scans the **event stream** (`yaml.parse` with `SafeLoader`) — events do not build Python
     objects and do not expand aliases, so the scan is bounded by the document size — counting nodes,
     alias events and nesting depth, and raising `ValueError` as soon as any exceeds its limit
     (**10000 nodes, 50 aliases, depth 100**);
  3. only then calls `yaml.safe_load` (no custom objects, no code execution).
- An over-limit document therefore never reaches `safe_load`, so an alias-heavy / deeply-nested /
  huge document cannot drive expansion in the harness. A rejection is a `ValueError`, caught by
  `_run_file_check` as a failed check — a controlled task failure, not a crash. The four acceptance
  checks and the reference/no-op thresholds are unchanged.

Results:
- Focused YAML tests in `tests/security/test_f_compose_task.py::SafeYamlLimitsTest` (run in the
  harness image, which has PyYAML): **5/5 OK** — normal compose files load; oversized, deeply nested,
  alias-heavy, and many-node inputs are each rejected with `ValueError`.
- Full `test_f_compose_task` in the harness image: **10/10 OK**. On host (no PyYAML) the YAML tests
  skip.
- Six-task baseline gate (`MODE=baselines docker compose up --build …`): all pass; oracle 1.00,
  no-op 0.00 for every task including `compose_port_alignment`; 19.17 s; exit 0;
  `logs/20261024-143745/gate.json`.
- Offline demo (`TASK_ID=compose_port_alignment`): success, reward 1.0; exit 0.

Remaining limitation: the limits are generous fixed constants suited to small config files; a task
needing larger YAML would need them raised. The exception-outcome forging residual (section 12.4) is
unrelated and still open. Files changed: `harness/tasks.py` (loader + limits),
`tests/security/test_f_compose_task.py` (YAML-limit tests), this report.
