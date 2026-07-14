# BIRD Backend Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Commit and push the completed BIRD data compatibility and NL2SQL accuracy work with concise, navigable documentation.

**Architecture:** Keep `data-processing/` as the team-owned producer and the backend adapter as the only consumer-side normalization boundary. Separate producer synchronization, backend behavior, and release documentation into reviewable commits.

**Tech Stack:** Python 3.13, uv, Typer, SQLite, sqlglot, pytest, React/Vite.

---

### Task 1: Commit the data-processing synchronization

**Files:**
- Rename: `5-1-data-preprocessing-deliverable/` to `data-processing/`
- Create: `data-processing/README.md`
- Create: `data-processing/scripts/prepare_raw_bird.py`

- [ ] Verify the staged diff contains only the existing producer synchronization.
- [ ] Commit with `chore(data-processing): sync team BIRD preprocessing pipeline`.

### Task 2: Commit backend behavior and tests

**Files:**
- Create: `backend/askdata/data/bird_io.py`
- Create: `backend/askdata/agent/answer_shape.py`
- Modify: `backend/askdata/agent/react_sql_agent.py`
- Modify: `backend/askdata/tools/retriever.py`
- Modify: `backend/askdata/eval/runner.py`
- Modify: `backend/askdata/cli.py`
- Modify: `backend/askdata/db/query_runner.py`
- Test: `tests/test_bird_io.py`
- Test: `tests/test_answer_shape.py`
- Test: `tests/test_data_processing_contract.py`
- Test: `tests/test_query_runner.py`

- [ ] Stage backend implementation, environment files, dependencies, and tests without staging release documentation.
- [ ] Run `uv run pytest -q`; expect all tests to pass.
- [ ] Commit with `feat(backend): add native BIRD contract and improve NL2SQL evaluation`.

### Task 3: Commit concise release documentation

**Files:**
- Modify: `README.md`
- Create: `benchmarks/README.md`
- Create: `benchmarks/bird-minidev-v4pro-seed42-100.json`
- Create: `scripts/setup-dev-env.sh`
- Create: `docs/superpowers/plans/2026-07-14-bird-backend-release.md`

- [ ] Link `backend/INSTRUCTIONS.md` and `benchmarks/README.md` from the root README.
- [ ] Run `git diff --check` and `cd frontend && npm run build`; expect both to succeed.
- [ ] Commit with `docs: document setup and BIRD benchmark results`.

### Task 4: Push the feature branch

**Files:** None.

- [ ] Confirm `git status --short` is empty and review the new commit list.
- [ ] Push `feature/lizeguo-nl2sql` to `origin`.
- [ ] Confirm local HEAD matches `origin/feature/lizeguo-nl2sql`.
