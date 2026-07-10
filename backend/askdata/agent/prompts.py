"""Prompt builder — classifies SQL task type (EASY/NON_NESTED/NESTED), builds structured SQL generation and repair prompts with schema linking checklist and task-specific guidance."""

def ClassifySqlTask(question: str, schema_prompt: str) -> str:
    lowered = f" {(question or '').lower()} "
    nested_hints = [
        " but not ",
        " not in ",
        " except ",
        " intersect ",
        " union ",
        " without ",
        " never ",
        " greater than average",
        " less than average",
        " more than average",
        " fewer than average",
        " higher than average",
        " lower than average",
    ]
    if any(hint in lowered for hint in nested_hints):
        return "NESTED"
    if "Join " in (schema_prompt or ""):
        return "NON_NESTED"
    return "EASY"


def TaskGuidance(task_type: str) -> str:
    if task_type == "NESTED":
        return """NESTED guidance:
- Decompose the question into the main query and subquery or set-operation requirement.
- Use IN, NOT IN, EXISTS, NOT EXISTS, EXCEPT, INTERSECT, or UNION only when the question meaning requires it.
- Keep SELECT columns aligned with the main question, not only the subquestion."""
    if task_type == "NON_NESTED":
        return """NON_NESTED guidance:
- JOIN key columns must come from the schema join lines or primary/foreign key columns.
- Do not invent join conditions.
- Include every table needed for selected columns and filtered columns."""
    return """EASY guidance:
- Prefer a direct SELECT from the single relevant table.
- Use WHERE, ORDER BY, GROUP BY, and aggregation only when the question asks for them."""


def BuildSqlPrompt(
    question: str,
    schema_prompt: str,
    session_context: dict | None = None,
    skills_section: str = "",
) -> str:
    previous = ""
    if session_context and session_context.get("last_sql"):
        previous = f"\nPrevious SQL: {session_context['last_sql']}"
    task_type = ClassifySqlTask(question, schema_prompt)
    return f"""You are an AI assistant that converts BIRD natural language questions into one SQLite SELECT SQL query.
Return SQL only. Do not use markdown. Do not wrap the answer in tags. Do not use SELECT *.
Target execution engine is SQLite. Use only the tables and columns in the schema.

SQL generation rules:
- SELECT list must include every attribute explicitly requested by the question.
- Include requested attributes even if that attribute is also used in WHERE.
- Do not add extra SELECT columns that the question does not ask for.
- DISTINCT: use only when the question explicitly says distinct/unique, OR when the output is a list of entity/category names and duplicate rows would mislead. Never use DISTINCT inside COUNT unless the question asks for unique entities.
- Aggregation: "how many" / "number of" / "count" -> use COUNT(*), not raw rows. "percentage" / "ratio" / "rate" / "difference" / "average" -> return the final computed value, not intermediate components.
- Literal values must come from the question text or evidence; never guess thresholds.
- Never split a single identifier into multiple values, for example "TR004_8_9" is one bond_id, not two atom_ids.
- Add LIMIT only when the result is row-level or exploratory. Prefer compact aliases for aggregate columns.

<schema_linking_checklist>
Before writing SQL, internally identify:
- COLUMN SELECTION: attributes explicitly requested by the question
- filter columns: attributes used to restrict rows
- join keys: foreign-key or primary-key relationships needed across tables
- literal values: quoted strings and numeric thresholds from the question or evidence
- SQL shape: whether the task needs COUNT, SUM, AVG, percentage, ratio, GROUP BY, nesting, or set operations
</schema_linking_checklist>

<task_type>{task_type}</task_type>
{TaskGuidance(task_type)}

{skills_section}

<schema>
{schema_prompt}
</schema>

<question>
{question}{previous}
</question>
"""


def BuildRepairPrompt(question: str, sql: str, error_message: str, schema_prompt: str) -> str:
    return f"""Repair this SQLite SELECT SQL query.
Return SQL only. Do not use markdown.

Question: {question}
Error: {error_message}
SQL: {sql}

Schema:
{schema_prompt}
"""
