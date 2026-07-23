"""SkillLoader — loads reusable SQL pattern skills from markdown files and builds a compact prompt section the agent can reference during SQL generation."""

from pathlib import Path

from askdata.core.paths import project_path


class SkillLoader:
    """Loads reusable SQL pattern skills from markdown files."""

    def __init__(self, skills_dir=None):
        self.skills_dir = project_path(skills_dir) if skills_dir else Path(__file__).resolve().parents[1] / "skills"
        self._skills: dict[str, str] | None = None

    def LoadAll(self) -> dict[str, str]:
        if self._skills is not None:
            return self._skills
        self._skills = {}
        if not self.skills_dir.exists():
            return self._skills
        for path in sorted(self.skills_dir.glob("*.md")):
            self._skills[path.stem] = path.read_text(encoding="utf-8")
        return self._skills

    def BuildPromptSection(self) -> str:
        skills = self.LoadAll()
        if not skills:
            return ""

        lines = ["AVAILABLE ANALYSIS SKILLS (reference these SQL patterns when applicable):"]
        for name, content in skills.items():
            description = self._ExtractSection(content, "## Description")
            when = self._ExtractSection(content, "## When to Use")
            pattern = self._ExtractFirstSqlBlock(content)
            lines.append(f"\nSkill: {name}")
            if description:
                lines.append(f"  {description}")
            if when:
                lines.append(f"  When: {when}")
            if pattern:
                lines.append(f"  Pattern: {pattern[:500]}")
        return "\n".join(lines)

    def _ExtractSection(self, content: str, heading: str) -> str:
        lines = content.splitlines()
        collecting = False
        result = []
        for line in lines:
            stripped = line.strip()
            if stripped == heading:
                collecting = True
                continue
            if collecting and stripped.startswith("##"):
                break
            if collecting and stripped and not stripped.startswith("```"):
                result.append(stripped.strip("- "))
        return " ".join(result[:3])

    def _ExtractFirstSqlBlock(self, content: str) -> str:
        collecting = False
        result = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("```sql"):
                collecting = True
                continue
            if collecting and stripped == "```":
                break
            if collecting:
                result.append(stripped)
        return " ".join(result)
