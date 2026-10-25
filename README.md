# Arpeggio — A Verifiable RL Environment for DevOps Repair

A small, working example of the run contract for the hackathon. It has five active DevOps config-repair tasks across two difficulty levels — **easy**: `compose_port_alignment` (the demo/default), `ci_workflow_repair`, `env_config_repair`; **medium**: `healthy_service_startup`, `k8s_service_probe` — a verifier, a reference-solution check, a no-op check, and a model loop that talks to any model through an OpenAI-compatible endpoint. Checks run either in an isolated verifier worker (for code) or as trusted file-inspection in the harness (for config files).

**How judges run it.** Judges can clone the `main` branch, configure `MODEL`, `API_KEY`, `BASE_URL`, and `TASK_ID` in `.env`, and run the documented Docker Compose command. `MODE=all` runs all five active tasks; `MODE=baselines` runs the baseline checks without calling a model.

> **Scope and limitations:** The active tasks validate configuration; they do not launch live Kubernetes clusters, run hosted CI workflows, or prove runtime service health. The environment includes security and execution controls, but these do not guarantee protection against every attack. See the security report and tests for details.



## Quick start

You need Linux, macOS or WSL with Docker and Docker Compose v2. Nothing else.

```bash
cp .env.example .env          # 1. create your config
# edit .env: set MODEL, API_KEY and BASE_URL (skip this to try the offline demo)
docker compose up --build --abort-on-container-exit --exit-code-from harness   # 2. run everything
```

With the default `MODEL=mock/solver` it runs offline with no API key, so you can see the whole flow first.

## Calling the model

Your model name, API key and base URL arrive in the API key email. Every model is called the same way, through an OpenAI-compatible client:

```python
import openai

client = openai.OpenAI(
    api_key="api_key",
    base_url="base_url"
)

# Example with text only
response = client.chat.completions.create(
    model="model_name",
    messages=[
        {
            "role": "user",
            "content": "Hi, How are you?"
        }
    ]
)

print(response)
```

In this template the three values come from environment variables, so nothing is hard-coded: `API_KEY`, `BASE_URL` and `MODEL` in your `.env`. The harness (`harness/agents.py`) makes the same call, with your tools added. To check your credentials before anything else:

```bash
API_KEY=... BASE_URL=... MODEL=... python check_model.py
```

When judges run your repo they change only these values (and the task), so your environment must work whichever model they plug in.

## What one command does

```
docker compose up --build ...
        |
        |-- builds the images, starts the sandbox and the verifier
        v
 1. ORACLE check   reference solution runs through the environment      must score >= 0.95
 2. NO-OP check    an agent that gives up immediately                   must score <= 0.05
        |   if either fails: stop, the model is never called
        v
 3. MODEL run      your chosen model works on the task                  clock starts at its first request
        |
        v
 logs/<run-id>/    gate.jsonl, gate.json, episode.jsonl, summary.json
```

Nothing is run separately. Build, both checks and the model run all happen from the single command.

## The contract


|                                |                                                                                                                                                                              |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Command**                    | `docker compose up --build --abort-on-container-exit --exit-code-from harness`                                                                                               |
| **Config** (`.env`)            | `MODEL`, `API_KEY`, `BASE_URL`, `TASK_ID`, `SEED`, `MODE`, optional `MAX_TOKENS`                                                                                             |
| **Extra instructions**         | a text file in `inputs/` named by `APPEND_FILE` in `.env` (default `inputs/append.txt`), added to the system prompt exactly as written. See [Extra instructions](#extra-instructions)                                                                                             |
| **Modes** (`MODE`)             | `full` (default): checks, then the model on `TASK_ID`. `all`: checks for every task, then the model on every task in turn. `baselines`: checks for every task, no model call |
| **Outputs** (`logs/<run-id>/`) | `gate.jsonl` and `gate.json` (baseline checks), `episode.jsonl` (one line per step; `episode-<task>.jsonl` per task with `MODE=all`), `summary.json`                         |
| **Exit code**                  | `0` finished, `1` crash, `2` a baseline check failed, `3` bad configuration                                                                                                  |




### Limits used in the live run


| Limit                | Value                                                                                                                        |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Build                | 3 minutes from a fresh clone with an empty Docker cache                                                                      |
| Baseline checks      | 2 minutes for the oracle and no-op checks on the chosen task (printed and saved as `gate_seconds` in `gate.json`)            |
| Time per model run   | 10 minutes, starting at the first model request (build and baseline checks are not counted)                                  |
| Steps                | 40. **One step is one model reply**, however many tool calls it holds                                                        |
| Tool calls per reply | 10. Extra calls are not run and get an error back                                                                            |
| Unreadable replies   | An unreadable or tool-less reply still uses a step. The agent gets a clear error and the run continues                       |
| Tool output          | cut at 4,000 characters                                                                                                      |
| Context              | trimmed by the harness so the conversation stays under about 48K tokens; a provider context-overflow error counts as a crash |




## Layout

```
docker-compose.yml        three services: sandbox (no internet), verifier (harness-only) and harness
Dockerfile.sandbox        image for the sandbox
Dockerfile.verifier       image for the verifier
Dockerfile.harness        image for the harness
check_model.py            one-message connectivity check for your credentials
sandbox/server.py         runs the agent's tools in an isolated workspace
verifier/server.py        runs the hidden checks on a copy of the workspace
harness/run.py            entry point: oracle, then no-op, then the model
harness/runner.py         step loop, limits, context trimming, telemetry
harness/env.py            reset / step / score (the verifier lives here)
harness/tasks.py          the tasks, with hidden checks and a reference solution
harness/tools.py          tool schemas shown to the model
harness/agents.py         OpenAI-compatible client, offline mock models, baselines
```

The verifier's expected answers and reward code live in the harness container. The agent only ever touches the sandbox container, which has no internet access. To score, the harness copies the workspace out of the sandbox and sends it with the hidden checks to the verifier container, which sits on its own internal network that the sandbox has no route to. Hidden checks never enter the sandbox.

## Try it

```bash
# offline demo: the reference-solution mock solves the default task (compose_port_alignment)
docker compose up --build --abort-on-container-exit --exit-code-from harness

# an agent that does nothing (should end as a failure with reward 0)
MODEL=mock/idle docker compose up --build --abort-on-container-exit --exit-code-from harness

# run every task's baseline checks without a model: use this for your baseline results
MODE=baselines docker compose up --build --abort-on-container-exit --exit-code-from harness

# the model on every task, one after another, ignores TASK_ID (each gets its own 40 steps and 10 minutes)
MODE=all docker compose up --build --abort-on-container-exit --exit-code-from harness

# a different task
TASK_ID=k8s_service_probe docker compose up --build --abort-on-container-exit --exit-code-from harness
```

Variables can be set in `.env` or on the command line as above.

## Extra instructions

In Run B the judges add an instructions file to the system prompt, the same file for every team. Its contents aren't disclosed. To check that your harness picks up such a file:

```bash
# 1. put the instructions in a file under inputs/
echo "Explain your plan in one line before each tool call." > inputs/extra.txt
# 2. name it in .env (or on the command line), then run as usual. No rebuild is needed.
APPEND_FILE=inputs/extra.txt docker compose up --build --abort-on-container-exit --exit-code-from harness
```

The terminal shows which file was loaded with its size and hash, and the run saves a copy as `logs/<run-id>/append.txt`. `summary.json` records it under `append`, and every step's `model_input` contains the full system prompt. A named file that is missing, empty or not UTF-8 stops the run with exit code 3. Your harness must add the file unchanged and must not special-case it.

## Keeping the build fast

The build and both checks must fit in 5 minutes on the judges' machine (3 for the build, 2 for the checks), and the judges' network may be slower than yours. This template builds in under a minute from an empty cache. To check yours the way judges will see it:

```bash
docker builder prune -af && time docker compose build --no-cache
```

- Pin every dependency to an exact version in `requirements.txt`, including transitive ones. `>=` can pull a new major version on the day.
- Install only what the environment needs. Avoid large toolchains (CUDA, full `build-essential`, browsers you don't use), model weights and datasets downloaded at build time. Ship small fixed data in the repo instead.
- Prefer `-slim` base images and packages with prebuilt wheels. Compiling from source is the usual cause of a slow build.
- Nothing may be downloaded at run time: the sandbox has no internet.



## Make it your own

1. Replace the tasks in `harness/tasks.py`. A task builds from a seed and provides the starting files, hidden checks and a **reference solution**. Tasks may be checked by candidate code in the isolated verifier worker, or (for config files) by trusted file-inspection in the harness; the `discount_task` function kept in that file shows how a seed can generate a different variant each time.
2. Keep both baselines honest. The reference solution must score at least 0.95 and the no-op agent at most 0.05 on **every** task. The run stops before the model is called if either check fails, which is how a leaky verifier gets caught early. (While building this template, the no-op check flagged one of its own tasks that paid out for doing nothing.)
3. Change the tools in `harness/tools.py` and `sandbox/server.py`, and the scoring in `harness/env.py::Env.score`.
4. Run `MODE=baselines` and keep `logs/<run-id>/gate.json`; it goes in your submission zip as your baseline results.
5. Run the model a few times, and keep the logs from at least 3 runs for your zip. `MODE=all` is a quick way to see that every task holds up, since judges may pick any of them for Run B.



## Submitting

Judges run everything from a clone of your repo's `main` branch, so what matters is what is in the repo. The zip you upload is reference material only (baseline results, test-run logs, sample trajectories, design doc and deck). See the problem statement for the exact list and deadline.

## Notes

- Your GPT-Luna key is for testing your environment only. It can't be used for anything else.
- Never commit API keys. `.env` is ignored by git.
- If your environment needs an LLM of its own, you must bring your own setup for it.

