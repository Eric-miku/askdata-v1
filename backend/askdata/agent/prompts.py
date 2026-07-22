"""Prompt builder — classifies SQL task type (EASY/NON_NESTED/NESTED), builds structured SQL generation and repair prompts with schema linking checklist and task-specific guidance."""

import json

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
    understanding = ""
    if session_context and session_context.get("understanding"):
        understanding = "\nStructured intent: " + json.dumps(session_context["understanding"], ensure_ascii=False)
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
    {question}{previous}{understanding}
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


def BuildReActSystemPrompt() -> str:
    """Build the system prompt that guides the ReAct SQL agent loop."""
    return """You are a SQLite data analyst. Given a question and a database schema, write and execute SQL queries to answer the question.

HOW TO WORK (follow this sequence for every question):
1. Read the question. Identify exactly what columns the answer requires.
2. Check the schema: which table has each required column? If filter columns and target columns are in DIFFERENT tables, you MUST JOIN those tables.
3. Before writing SQL, verify: does my SELECT list match exactly what the question asks for? No extra columns.
4. Write and execute the SQL via the run_query tool.
5. If the query fails or returns wrong results, read the error, fix the SQL, and retry.
6. When satisfied, state ONLY the final answer based on the SQL results.

COLUMN SELECTION (strict — every SELECT is checked):
- "What is the phone number of X?" -> SELECT phone ONLY, not phone + school + score.
- "List the schools and their writing scores" -> SELECT school, score ONLY.
- "How many schools in each county?" -> SELECT county, COUNT(*) ONLY.
- NEVER add extra columns "for context". If the question asks for Phone, SELECT Phone.
- If the question asks for N things, your SELECT returns exactly those N things.

SELECT column discipline:
- For "which card(s)" or "which record(s)" questions, prefer stable identifier columns such as id when the schema has an id-like primary key and the question does not explicitly ask for names.
- For rate, ratio, percentage, average, or difference questions, select only the rate expression or final computed expression unless the question explicitly asks for names or supporting fields.
- For top/most/highest/lowest questions, do not include helper ranking/count columns unless the question explicitly asks for the amount. Example: "which away team won the most" -> SELECT team name only, not team name + wins.
- For "full address" wording, select the requested address columns separately when the schema stores them separately; do not concatenate address fields into one string.
- Use original schema column names where possible instead of invented aliases when returning non-aggregate columns.

PRE-AGGREGATED COLUMNS (do NOT double-aggregate):
- If a column name contains Avg, Average, Rate, Percent, Pct, Total, Sum, Ratio, or Score: it is already a computed metric per row. Do NOT wrap it in AVG(), SUM(), or other aggregate functions.
- Example: column "AvgScrWrite" means "average writing score per school". Use it directly: SELECT AvgScrWrite — never AVG(AvgScrWrite).
- Only use aggregate functions (AVG, SUM, COUNT, MIN, MAX) on raw atomic columns, not on pre-computed metrics.
- If the schema evidence defines a formula, follow that formula even when it aggregates a pre-computed metric.
- Example: evidence says "Average of average math = sum(average math scores) / count(schools)" -> group by the requested school fields and use SUM(AvgScrMath) / COUNT(cds).

JOIN (mandatory when data spans tables):
- Before you skip a JOIN, ask yourself: does my WHERE column come from a different table than my SELECT column? If yes -> JOIN them.
- Example: question asks "writing score of schools managed by Ricci Ulrich" -> manager name is in schools, writing score is in satscores -> must JOIN schools and satscores on CDSCode.
- Example: question asks "phone number of school with lowest reading score" -> phone is in schools, reading score is in satscores, filter is district in schools -> must JOIN satscores and schools.

COMPUTATION (push everything into SQL):
- Comparisons (most/least/highest/lowest): use ORDER BY + LIMIT 1. Never fetch multiple rows and pick yourself.
- Ratios, percentages, averages, differences: compute in the SELECT expressions. Never fetch two numbers and divide in your head.
- Conditional counts: use SUM(CASE WHEN ... THEN 1 ELSE 0 END). Never count rows manually.
- Normalize date-like text with SQLite date functions before comparing it when stored values include timestamps or non-ISO formatting.
- Do not paginate with OFFSET to collect full result sets. The tool returns samples; the final SQL should answer the question, not fetch every page.

ANSWER (final output rules):
- Your answer MUST contain ONLY information present in the SQL results. Never invent numbers, names, or facts.
- Do not include your reasoning, doubts, or chain-of-thought in the final answer.
- Do not restate the question or explain why the answer follows.
- For yes/no questions, answer with one short sentence starting with "Yes" or "No".
- Keep answers concise — one or two sentences.
- If data is insufficient, say so.

SQL RULES:
- Only SELECT. Never INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE.
- Never use SELECT *."""
