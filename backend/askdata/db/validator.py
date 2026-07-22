"""
validator.py
基于 sqlglot 的 SQL 安全校验模块 (AST 级别危险操作拦截)

职责:
    1. 只允许 SELECT / WITH...SELECT / UNION 类型的只读查询通过
    2. 拦截 DROP / DELETE / UPDATE / INSERT / ALTER / CREATE / TRUNCATE 等写操作
    3. 拦截多语句注入 (例如 "SELECT 1; DROP TABLE users;")
    4. 拦截 INTO OUTFILE / INTO DUMPFILE 等文件写出操作 (MySQL 特有攻击面)
    5. 支持表名黑名单 (禁止访问系统表 / 敏感表)

注意:
    不同版本的 sqlglot 对部分语句(如 TRUNCATE、GRANT)的解析类型可能不同,
    有些方言会退化解析为 exp.Command。因此额外将 exp.Command 也视为高风险,
    一并拦截,以降低"新语法未被识别"导致绕过的风险。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import sqlglot
from sqlglot import exp


class SQLRiskLevel(str, Enum):
    SAFE = "safe"
    BLOCKED = "blocked"


@dataclass
class ValidationResult:
    is_valid: bool
    risk_level: SQLRiskLevel
    reason: str = ""
    statement_count: int = 1
    normalized_sql: str = ""


# 明确禁止的写操作 / DDL 类型节点
_DANGEROUS_TYPES = tuple(
    t
    for t in (
        getattr(exp, "Drop", None),
        getattr(exp, "Delete", None),
        getattr(exp, "Update", None),
        getattr(exp, "Insert", None),
        getattr(exp, "Alter", None),
        getattr(exp, "Create", None),
        getattr(exp, "TruncateTable", None),
        getattr(exp, "Grant", None),
        getattr(exp, "Command", None),  # 兜底: 未被具体建模的管理类语句
    )
    if t is not None
)

# 允许通过的根节点类型 (只读查询)
_ALLOWED_ROOT_TYPES = tuple(
    t
    for t in (
        getattr(exp, "Select", None),
        getattr(exp, "Union", None),
        getattr(exp, "With", None),
    )
    if t is not None
)

_SYSTEM_SCHEMAS = {"information_schema", "mysql", "performance_schema", "sys", "pg_catalog"}


class SQLValidator:
    def __init__(
        self,
        dialect: str = "mysql",
        max_statements: int = 1,
        forbidden_tables: Optional[set] = None,
        max_joins: int = 8,
        max_subquery_depth: int = 4,
    ):
        """
        :param dialect: sqlglot 解析方言, 需与实际数据库一致 (mysql/postgres/sqlite 等)
        :param max_statements: 允许的最大语句数, 默认仅允许单条语句, 防止分号注入多条语句
        :param forbidden_tables: 禁止访问的表名黑名单 (如系统表 / 敏感表), 大小写不敏感
        """
        self.dialect = dialect
        self.max_statements = max_statements
        self.forbidden_tables = {t.lower() for t in (forbidden_tables or set())}
        self.max_joins = max_joins
        self.max_subquery_depth = max_subquery_depth

    def validate(self, sql: str) -> ValidationResult:
        sql = (sql or "").strip()
        if not sql:
            return ValidationResult(False, SQLRiskLevel.BLOCKED, "SQL 语句为空")

        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except Exception as e:
            return ValidationResult(False, SQLRiskLevel.BLOCKED, f"SQL 解析失败: {e}")

        if not statements:
            return ValidationResult(False, SQLRiskLevel.BLOCKED, "未解析出有效语句")

        if len(statements) > self.max_statements:
            return ValidationResult(
                False,
                SQLRiskLevel.BLOCKED,
                f"检测到 {len(statements)} 条语句, 疑似多语句注入, 仅允许 {self.max_statements} 条",
            )

        root = statements[0]

        # 1. 根节点类型检查: 必须是只读查询
        if not isinstance(root, _ALLOWED_ROOT_TYPES):
            return ValidationResult(
                False,
                SQLRiskLevel.BLOCKED,
                f"不允许的语句类型: {type(root).__name__}, 仅允许 SELECT/WITH/UNION 查询",
            )

        # 2. 遍历整棵 AST, 拦截任何嵌套的危险节点
        #    (例如子查询 / CTE 内部藏入写操作)
        for node in root.walk():
            if isinstance(node, _DANGEROUS_TYPES):
                return ValidationResult(
                    False,
                    SQLRiskLevel.BLOCKED,
                    f"检测到危险操作节点: {type(node).__name__}",
                )
            if isinstance(node, getattr(exp, "Into", ())):
                return ValidationResult(
                    False,
                    SQLRiskLevel.BLOCKED,
                    "检测到 INTO 子句 (可能用于文件写出), 已拦截",
                )

        # 3. 系统对象和表名黑名单检查
        tables = list(root.find_all(exp.Table))
        system_objects = {
            table.sql(dialect=self.dialect)
            for table in tables
            if table.name.lower().startswith("sqlite_")
            or str(table.db or "").lower() in _SYSTEM_SCHEMAS
        }
        if system_objects:
            return ValidationResult(
                False,
                SQLRiskLevel.BLOCKED,
                f"禁止访问系统对象: {', '.join(sorted(system_objects))}",
            )

        tables_used = {table.name.lower() for table in tables}
        hit = tables_used & self.forbidden_tables
        if hit:
            return ValidationResult(
                False,
                SQLRiskLevel.BLOCKED,
                f"禁止访问的表: {', '.join(sorted(hit))}",
            )

        # 4. 复杂度限制，防止模型生成高风险笛卡尔积或深层嵌套查询
        join_count = sum(1 for _ in root.find_all(exp.Join))
        if join_count > self.max_joins:
            return ValidationResult(
                False,
                SQLRiskLevel.BLOCKED,
                f"SQL 包含 {join_count} 个 JOIN，超过允许上限 {self.max_joins}",
            )

        max_depth = 0
        for select in root.find_all(exp.Select):
            depth = 0
            parent = select.parent
            while parent is not None:
                if isinstance(parent, exp.Select):
                    depth += 1
                parent = parent.parent
            max_depth = max(max_depth, depth)
        if max_depth > self.max_subquery_depth:
            return ValidationResult(
                False,
                SQLRiskLevel.BLOCKED,
                f"SQL 子查询深度 {max_depth} 超过允许上限 {self.max_subquery_depth}",
            )

        normalized = root.sql(dialect=self.dialect)
        return ValidationResult(True, SQLRiskLevel.SAFE, "", len(statements), normalized)
