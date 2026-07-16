# AskData V2.2 Design

## Goal

AskData V2.2 improves Text2SQL reliability by strengthening question understanding, value grounding, and regression coverage without replacing the current staged ReAct pipeline.

The scope is the stable version of V2.2:

- Add a deterministic `QuestionAnalyzer`.
- Add a bounded `ValueLinker`.
- Add a manual regression harness for known hard cases.
- Keep the existing `StagedSqlPipeline`, ReAct SQL agent, SQL quality gates, and result quality gates.

CHASE-SQL style multi-candidate generation and pairwise selection are intentionally out of scope for this iteration.

## Background

Recent regressions showed that one-off prompt changes are not enough:

- Monterey high schools failed when the model used inspection queries and the pipeline stopped too early.
- Debit Card decrease-rate failed when the model returned intermediate values instead of the final ratio.
- Toxicology TR060 returned only `molecule.label` because intent inference missed plural `elements` matching schema column `element`.
- Total enrollment questions were blocked too early by an over-strict ambiguity gate.

These bugs came from three repeatable weak spots:

- Question understanding was too shallow.
- Literal values were not explicitly linked to likely database columns.
- Known hard cases were not captured in an always-runnable regression suite.

## Current Pipeline

The current request path is:

```text
QueryService
  -> AgentGraph.Run()
    -> SemanticRetriever.Retrieve()
    -> StagedSqlPipeline.Run()
      -> AmbiguityGate.Check()
      -> _InferIntent()
      -> ReActSqlAgent.GenerateCandidates()
      -> EvaluateStaticSql()
      -> query_runner.Execute()
      -> EvaluateResult()
      -> repair / retry / finish
    -> ResultAnalyzer.Analyze()
```

V2.2 keeps this path but inserts structured analysis and value grounding before SQL generation:

```text
SemanticRetriever
  -> QuestionAnalyzer
  -> ValueLinker
  -> StagedSqlPipeline
```

## Component 1: QuestionAnalyzer

Create `backend/askdata/agent/question_analyzer.py`.

`QuestionAnalyzer` converts a natural-language question plus schema/evidence into a deterministic analysis object. It does not call the LLM in V2.2.

Responsibilities:

- Infer answer shape: `listing`, `scalar`, `ratio`, or `ranking`.
- Identify requested output columns using normalized schema matching.
- Extract literal filters from the question.
- Extract formula hints from BIRD evidence.
- Preserve enough structure for prompts and quality gates.

Initial model:

```python
class QuestionFilter(BaseModel):
    raw: str
    kind: Literal["identifier", "number", "date", "text"]
    normalized: str | None = None


class QuestionAnalysis(BaseModel):
    intent: IntentContract
    requested_outputs: list[str] = []
    filters: list[QuestionFilter] = []
    formula_hints: list[str] = []
    notes: list[str] = []
```

Required deterministic behavior:

- `elements` matches schema column `element`.
- `labels` matches schema column `label`.
- `2012/8/25` normalizes to `2012-08-25`.
- `634.8` is extracted as a number filter.
- `TR060` is extracted as an identifier filter.
- Evidence such as `Consumption decrease rate = ...` becomes a formula hint.

`StagedSqlPipeline._InferIntent()` remains as a fallback during the transition, but pipeline construction should prefer `QuestionAnalyzer`.

## Component 2: ValueLinker

Create `backend/askdata/tools/value_linker.py`.

`ValueLinker` maps extracted question literals to likely table/column locations. It should be deterministic and bounded.

Responsibilities:

- Link identifiers such as `TR060` to ID-like columns.
- Link numeric values such as `634.8` to numeric columns.
- Link dates such as `2012/8/25` to date-like columns.
- Link text values such as `Monterey` to categorical text columns.
- Return ranked candidates with reasons and confidence.

Initial model:

```python
class ValueLink(BaseModel):
    value: str
    normalized_value: str
    table: str
    column: str
    confidence: float
    reason: str
```

Expected examples:

```text
TR060 -> molecule.molecule_id
634.8 -> transactions_1k.Price
2012/8/25 -> transactions_1k.Date = '2012-08-25'
Monterey -> schools.County or frpm.County Name
High schools -> frpm.School Type
```

Bounded lookup rules:

- Search selected retriever tables first.
- Prioritize columns whose names match literal type: `id`, `date`, `price`, `amount`, `county`, `city`, `type`, `name`.
- Cap inspected columns per query.
- Use `SELECT 1 ... LIMIT 1` probes, not full scans returning rows.
- Never expose raw probe SQL in user-facing trace.

## Component 3: Pipeline Integration

Modify `backend/askdata/agent/graph.py` and `backend/askdata/agent/pipeline.py`.

`AgentGraph.Run()` should build:

```python
retrieval = retriever.index.Retrieve(database_id, question)
analysis = QuestionAnalyzer().Analyze(question, retrieval["schema"], retrieval.get("evidence", ""))
value_links = ValueLinker().Link(question, retrieval, analysis)
```

The retrieval context passed into `StagedSqlPipeline` should include:

```python
{
    "analysis": analysis,
    "intent": analysis.intent,
    "value_links": value_links,
}
```

`ReActSqlAgent` prompt context should include a compact analysis section:

```text
Question Analysis:
- answer shape: listing
- requested outputs: element, label
- filters:
  - TR060 -> molecule.molecule_id = 'TR060'
- formula:
  - Consumption decrease rate = (consumption_2012 - consumption_2013) / consumption_2012
```

Prompt rules:

- This section is guidance, not a replacement for schema.
- If value links contain multiple candidates, the SQL should prefer the highest-confidence link but remain schema-valid.
- For ratio/rate questions, the final SQL must select the computed expression, not intermediate components.

Quality gate integration:

- `EvaluateStaticSql` continues to use `IntentContract`.
- The first V2.2 implementation does not need a new quality-gate type.
- Existing checks should become more effective because `IntentContract` is richer.

## Component 4: Manual Regression Harness

Create:

```text
backend/askdata/eval/manual_regressions.py
tests/fixtures/manual_regressions.json
tests/test_manual_regressions.py
```

The harness captures high-value known cases and runs them without relying on the live LLM by default.

Fixture shape:

```json
{
  "id": "toxicology_tr060_elements_label",
  "database_id": "toxicology",
  "question": "What are the elements of the toxicology and label of molecule TR060?",
  "expected_columns": ["element", "label"],
  "min_rows": 1,
  "expected_error": null,
  "must_not_sql": ["SELECT * FROM molecule"]
}
```

Initial cases:

- `toxicology_tr060_elements_label`
- `debit_card_consumption_decrease_rate`
- `california_monterey_high_school_frpm_address`
- `california_total_enrollment_over_500`

Two execution modes:

- Deterministic mode: uses explicit candidate SQL fixtures or fake candidates so normal tests are stable.
- Optional live mode: calls `AgentGraph` and the real ReAct path. Live mode is not required for CI and may fail because of network or model variance.

Assertions should focus on behavior:

- no unexpected error
- required columns are present
- row count is at least the fixture minimum
- selected SQL does not contain forbidden patterns
- optional SQL fragments can be checked when they are robust

The harness should not require exact SQL string equality for live ReAct runs.

## Error Handling

If `QuestionAnalyzer` cannot confidently identify outputs or filters, it should return an analysis with partial data and notes. The pipeline should continue using the existing fallback intent inference.

If `ValueLinker` cannot link a literal, it should return no link for that literal. This must not block SQL generation.

If a value probe fails because of a database error, the linker should skip that column and record an internal reason. It should not surface probe errors to the user.

## Testing Strategy

Unit tests:

- QuestionAnalyzer plural/singular output matching.
- Date, number, identifier, and formula extraction.
- ValueLinker exact identifier, numeric, date, and text matches.
- ValueLinker bounded behavior with no full-result scans.

Pipeline tests:

- Pipeline uses `analysis.intent` over fallback `_InferIntent()`.
- Prompt context includes compact question analysis and value links.
- Bad partial answers are rejected when required outputs are known.

Regression tests:

- The four initial manual regression cases run in deterministic mode.
- Live mode is available through an explicit command but is not part of default pytest.

## Out of Scope

V2.2 stable scope does not include:

- CHASE-SQL multi-agent candidate generation.
- Pairwise LLM SQL selection.
- Full DEA-style LLM question decomposition.
- Vector index rebuilding.
- Frontend changes.
- Broad BIRD benchmark reruns.

## Success Criteria

V2.2 is successful when:

- Existing backend tests pass.
- Manual regression deterministic mode passes for the four initial hard cases.
- `QuestionAnalyzer` correctly identifies required outputs for Toxicology TR060.
- `ValueLinker` correctly links the core literals in the initial hard cases.
- Pipeline behavior is unchanged for simple existing queries except where richer analysis prevents known bad answers.
