from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.prompts import BuildSqlPrompt
from askdata.retrieval.retriever import BirdSchemaIndex
from askdata.agent.skill_loader import SkillLoader


def test_schema_prompt_includes_database_instructions(tmp_path):
    instructions_dir = tmp_path / "instructions"
    instructions_dir.mkdir()
    (instructions_dir / "demo.md").write_text(
        """
## Business Term Mappings
- active item -> items.status = 'active'

## JOIN Patterns
- items.category_id = categories.id
""",
        encoding="utf-8",
    )
    index = BirdSchemaIndex(instructions_dir=instructions_dir).Build([
        {
            "databaseId": "demo",
            "databasePath": "/tmp/demo.sqlite",
            "tables": [
                {
                    "tableName": "items",
                    "columns": [
                        {"columnName": "id", "columnType": "integer", "isPrimary": True},
                        {"columnName": "status", "columnType": "text"},
                    ],
                }
            ],
            "foreignKeys": [],
        }
    ])

    context = index.Retrieve("demo", "How many active items?")

    assert "Business Context" in context["schema_prompt"]
    assert "active item -> items.status = 'active'" in context["schema_prompt"]
    assert "items.category_id = categories.id" in context["schema_prompt"]


def test_sql_prompt_includes_available_skills(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "ratio-analysis.md").write_text(
        """
# ratio-analysis

## Description
Compute the ratio between two groups.

## When to Use
- What is the ratio of X to Y?

## SQL Pattern
```sql
SELECT CAST(SUM(CASE WHEN <a> THEN 1 ELSE 0 END) AS REAL) / NULLIF(SUM(CASE WHEN <b> THEN 1 ELSE 0 END), 0) AS ratio
FROM <table>
```
""",
        encoding="utf-8",
    )

    skills_section = SkillLoader(skills_dir).BuildPromptSection()
    prompt = BuildSqlPrompt(
        "What is the ratio of active to inactive items?",
        "Database: demo\nTable items(id integer, status text)",
        skills_section=skills_section,
    )

    assert "AVAILABLE ANALYSIS SKILLS" in prompt
    assert "Skill: ratio-analysis" in prompt
    assert "NULLIF" in prompt
