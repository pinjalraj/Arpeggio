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
