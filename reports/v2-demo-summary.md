# AskData V2.1 Integrated Verification

- Verification window: 2026-07-16 02:05–02:24 CST (UTC+08:00)
- Verified commit: `6c67916024f74301d92e46a420989a411191cacf`
- Environment: local macOS worktree, Python 3.13 virtual environment, deterministic local fixtures unless stated otherwise
- Secret handling: configuration values were checked only for presence; no keys, remote error bodies, or database rows are recorded here

## Release gate summary

| Gate | Status | Evidence |
| --- | --- | --- |
| Backend suite | Verified | `311 passed`, one Starlette/httpx deprecation warning, exit 0 |
| Frontend suite | Verified | 15 test files, 72 tests passed, exit 0 |
| Frontend production build | Verified | TypeScript and Vite build exit 0; bundle-size warning remains |
| Restart persistence | Verified locally | API-created session listed and reopened after a new application lifespan; one turn persisted |
| Vector outage fallback | Verified locally | Outage, disabled-vector, and restart-focused tests: `3 passed`; remote exception details suppressed by the tested contract |
| Live vector retrieval | Blocked | `MILVUS_URI` is absent and the optional `pymilvus` dependency is not installed |
| Golden demo predictions | Blocked | No supported capture command or separate captured prediction artifact exists in the repository |
| Fixed 100-question BIRD baseline | Not run | Data and manifest exist, but processed database paths target another checkout; after an interrupted escalation wait, long/live runs were intentionally not restarted |

The verified local gates do not constitute a full production release claim. The demo and BIRD result files remain absent because producing them without real captured outputs would be misleading.

## Commands and observed results

### Complete backend suite

```bash
uv run pytest -q
```

Observed on the final rerun: exit 0; `311 passed, 1 warning in 3.17s`. The warning reports Starlette's deprecated TestClient/httpx compatibility import.

### Complete frontend suite

```bash
cd frontend
npm test -- --run
```

Observed: exit 0; 15 files and 72 tests passed in 3.70s.

### Frontend production build

```bash
cd frontend
npm run build
```

Observed on the final rerun: exit 0; 2,389 modules transformed and the build completed in 4.15s. Vite warned that the generated JavaScript chunk is larger than 500 kB after minification (`2,117.88 kB`, gzip `687.73 kB`). This is a performance follow-up, not a build failure.

### Vector outage and disabled-vector behavior

```bash
PYTHONPATH=backend .venv/bin/pytest -p no:cacheprovider \
  tests/test_session_store.py::test_sessions_and_turns_survive_store_restart \
  tests/test_retriever.py::test_vector_startup_failure_is_cached_and_returns_safe_fallback \
  tests/test_retriever.py::test_disabled_vector_configuration_makes_no_validation_call \
  -q
```

Observed: exit 0; `3 passed in 0.04s`.

This verifies two degraded paths:

1. A fully configured vector startup/search failure falls back once to lexical retrieval, returns a fixed safe warning, and does not expose the remote failure text.
2. `VECTOR_RETRIEVAL_ENABLED=false` does not construct the embedding client, silently uses lexical retrieval, and still returns lexical schema context. Incomplete vector configuration follows the same silent lexical path.

Live semantic retrieval was not exercised. `EMBEDDING_API_URL` is present, but the runtime setting `MILVUS_URI` is not; a differently named `MILVUS_HOST` entry is not consumed by AskData. The optional `pymilvus` package is also absent, so both `MILVUS_URI` and `uv sync --extra vector` are required before a live check.

### Restart persistence through the API

A temporary SQLite application database and deterministic local graph fixture were used with the real FastAPI routes, `QueryService`, and `SessionStore`:

1. Start application lifespan A.
2. `POST /api/query` and retain the returned session ID.
3. Close lifespan A, including the SQLite connection.
4. Start a new application lifespan B against the same file.
5. `GET /api/sessions` and `GET /api/sessions/{session_id}`.

Observed:

- Session ID: `aa326a0d-ea29-47ef-b069-89b4bf84c7b7`
- Listed after restart: yes
- Reopened turn count: 1

The temporary database and helper were removed after the check. This verifies persistence and API reopening without making an external LLM call.

### Golden demo comparison

The supported command requires a separately captured predictions file:

```bash
PYTHONPATH=backend .venv/bin/python -m askdata.cli eval-demo \
  --cases tests/fixtures/v2_demo_cases.json \
  --out /tmp/askdata-v2-demo.json
```

Observed: exit 2 with `Missing option '--predictions'`; no report was created.

This is the correct safety behavior. The fixture's embedded predictions are marked test-only and were not copied into a release artifact. The repository currently provides the deterministic comparison command but no command that captures all required fields from live API responses, streaming parity, retrieval attribution, outage state, and restart state into `reports/v2-demo-predictions.json`. Therefore `reports/v2-demo.json` was not fabricated.

To close this gate, add or provide a supported capture procedure, produce a separate prediction artifact from the system under evaluation, then run:

```bash
uv run askdata eval-demo \
  --cases tests/fixtures/v2_demo_cases.json \
  --predictions reports/v2-demo-predictions.json \
  --out reports/v2-demo.json
```

### Fixed BIRD baseline prerequisites

Read-only inventory found:

- 11 SQLite database files under the main workspace's `data/bird/databases`
- 29 processed files
- A nonempty fixed manifest with 100 unique question IDs
- All 100 manifest IDs present in `processed/questions.jsonl`
- Nonempty LLM endpoint, key, and model settings (values not printed)

The baseline was not run. The processed metadata declares legacy paths such as `5-1-data-preprocessing-deliverable/...`; when loaded from this isolated worktree, the resolver targets a nonexistent path inside the worktree instead of the available database directory in the main checkout. A later read-only `uv run` inventory/focused-test invocation waited for sandbox escalation for approximately 875 seconds and was interrupted before producing output. Per operator direction, no long external run was restarted.

To close this gate, regenerate the processed bundle in this worktree or replace its declared database paths with valid absolute/repository-relative paths, then run:

```bash
uv run askdata eval-bird \
  --question-manifest benchmarks/bird-minidev-v4pro-seed42-100.json \
  --out reports/bird-v2.1-100.json
```

The resulting evidence must report relaxed execution accuracy, strict execution accuracy, valid SQL rate, exact match, latency, model identity, manifest hash, and data fingerprint together.

## Final checklist status

| Requirement | Evidence status |
| --- | --- |
| Typed discriminated responses with session/turn/trace | Covered by the 311-test backend suite |
| Material, schema-supported clarification | Covered by backend and frontend suites; not live-demo captured |
| No proxy SQL for missing entities | Covered by deterministic tests; no release demo artifact yet |
| Answer, SQL, rows, and chart share the selected candidate | Covered by deterministic pipeline tests |
| No more than six SQL candidates | Covered by deterministic pipeline tests |
| Vector failure degrades to lexical retrieval | Focused local verification passed |
| ChartSpec contains no executable options | Backend and frontend suites passed |
| Existing frontend shell/tokens/accessibility remain intact | Frontend suite and production build passed |
| Sessions survive restart and reopen | Verified through real local API routes with one persisted turn |
| Streaming and non-streaming final-payload parity | Covered by deterministic tests; not live-demo captured. A completed stream ends in one `final`; clarification/error may also have a preceding lifecycle frame |
| Fixed-manifest strict and relaxed BIRD accuracy | Blocked; no V2.1 report generated |

## Known limitations and follow-ups

1. Add a first-class demo prediction capture command or documented harness; `eval-demo` intentionally compares only.
2. Provide `MILVUS_URI` and install `pymilvus` with `uv sync --extra vector` before claiming live semantic retrieval.
3. Regenerate or relocate the BIRD processed metadata so database paths are portable across worktrees.
4. Run the fixed 100-question baseline once the data path and live LLM preconditions are satisfied.
5. Split the frontend bundle to address Vite's greater-than-500-kB chunk warning.
6. Update the TestClient dependency path before Starlette removes the deprecated httpx compatibility layer.
