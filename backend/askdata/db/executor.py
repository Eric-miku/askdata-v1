"""
executor.py
封装 SQLAlchemy 的安全 SQL 执行模块

输出格式对齐前端 QueryResultResponse 契约中由本模块负责的部分:
    result.columns    -> ColumnMeta[]   (含 key/title/type/format/sortable)
    result.rows       -> Record<str,any>[]
    result.pagination -> { page, page_size, total }
    error             -> { code, message, detail }

注意: request_id / question / chart_builder / analysis 不属于本模块职责,
由 Agent 编排层或 API 层在拿到 ExecutionResult.to_api_format() 的返回值后,
和其他节点的产出一起拼装成完整的 QueryResultResponse。
"""

from __future__ import annotations

import datetime
import decimal
import re
import time
from dataclasses import dataclass, field
from typing import Optional

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")

import sqlglot
from sqlglot import exp
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .validator import SQLValidator, ValidationResult


# ---------------------------------------------------------------------------
# 错误码: 与前端约定的 error.code 保持一致, Agent/API 层可以据此做不同的重试/提示策略
# ---------------------------------------------------------------------------
class ErrorCode:
    SQL_BLOCKED = "SQL_BLOCKED"       # 未通过安全校验 (危险操作 / 多语句注入 / 黑名单表)
    DB_ERROR = "DB_ERROR"             # 数据库执行报错 (语法错误、字段不存在、连接失败等)
    TIMEOUT = "TIMEOUT"               # 执行超时
    UNKNOWN_ERROR = "UNKNOWN_ERROR"   # 兜底


@dataclass
class ErrorInfo:
    code: str
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class ColumnMeta:
    key: str
    title: str
    type: str = "string"  # 'string' | 'number' | 'date' | 'datetime' | 'percent' | 'currency'
    unit: Optional[str] = None
    format: str = "plain"  # 'plain' | 'currency' | 'percent' | 'date'
    sortable: bool = True

    def to_dict(self) -> dict:
        d = {"key": self.key, "title": self.title, "type": self.type, "format": self.format, "sortable": self.sortable}
        if self.unit:
            d["unit"] = self.unit
        return d


@dataclass
class PaginationMeta:
    page: int
    page_size: int
    total: int

    def to_dict(self) -> dict:
        return {"page": self.page, "page_size": self.page_size, "total": self.total}


@dataclass
class ExecutionResult:
    success: bool
    columns: list = field(default_factory=list)   # List[ColumnMeta]
    rows: list = field(default_factory=list)       # List[dict]
    pagination: Optional[PaginationMeta] = None
    elapsed_ms: float = 0.0
    sql: str = ""
    error: Optional[ErrorInfo] = None

    def to_api_format(self) -> dict:
        """
        产出 QueryResultResponse 中本模块负责的字段。
        Agent/API 层用法示例:
            exec_out = executor.execute(sql, page=1, page_size=50)
            response = {
                "request_id": ...,      # API 层生成
                "question": ...,        # API 层透传用户输入
                **exec_out,             # 展开 status / sql / result / error
                "chart_builder": ...,   # NL2SQL / Agent 层生成
                "analysis": ...,        # result_analyzer 生成
            }
        """
        if not self.success:
            return {
                "status": "error",
                "sql": self.sql,
                "error": self.error.to_dict() if self.error else None,
            }
        return {
            "status": "success",
            "sql": self.sql,
            "result": {
                "columns": [c.to_dict() for c in self.columns],
                "rows": self.rows,
                "pagination": self.pagination.to_dict() if self.pagination else None,
            },
        }


class SQLExecutor:
    def __init__(
        self,
        db_url: str,
        dialect: str = "mysql",
        default_page_size: int = 50,
        max_page_size: int = 1000,
        forbidden_tables: Optional[set] = None,
        pool_pre_ping: bool = True,
        engine: Optional[Engine] = None,
    ):
        """
        :param db_url: SQLAlchemy 连接串
        :param dialect: 传给 sqlglot 的方言标识, 需与 db_url 对应的数据库类型一致
        :param default_page_size: 未指定 page_size 时的默认每页条数
        :param max_page_size: page_size 的硬上限, 防止前端传入超大值拖垮数据库
        :param engine: 允许外部注入已创建的 Engine (便于测试复用内存库连接)
        """
        self.engine: Engine = engine or create_engine(db_url, pool_pre_ping=pool_pre_ping)
        self.dialect = dialect
        self.validator = SQLValidator(dialect=dialect, forbidden_tables=forbidden_tables)
        self.default_page_size = default_page_size
        self.max_page_size = max_page_size

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def execute(self, sql: str, page: int = 1, page_size: Optional[int] = None) -> ExecutionResult:
        """
        校验 -> 改写分页 -> 执行 -> 统一格式化。
        校验失败 / 执行失败都不抛异常, 而是封装进 ExecutionResult.error,
        方便 Agent 编排层统一捕获并决定是否触发 SQL Repair 重试。
        """
        page = max(1, page)
        page_size = max(1, min(page_size or self.default_page_size, self.max_page_size))

        validation: ValidationResult = self.validator.validate(sql)
        if not validation.is_valid:
            return ExecutionResult(
                success=False,
                sql=sql,
                error=ErrorInfo(ErrorCode.SQL_BLOCKED, "生成的 SQL 未通过安全校验", validation.reason),
            )

        try:
            paged_sql, count_sql = self._build_paginated_queries(validation.normalized_sql, page, page_size)
            fetch_cap = page_size  # 正常路径: SQL 里已经有精确的 LIMIT, fetchmany 按 page_size 取即可
        except Exception:
            # AST 改写失败 (极少数复杂语句结构), 降级为不注入 LIMIT 直接执行。
            # 这种情况下 SQL 本身可能没有行数限制, 必须在 Python 侧用 fetchmany 硬性兜底,
            # 否则遇到大表会整表拉进内存, 拖垮服务和数据库。
            paged_sql, count_sql = validation.normalized_sql, None
            fetch_cap = self.max_page_size

        start = time.perf_counter()
        try:
            with self.engine.connect() as conn:
                try:
                    conn = conn.execution_options(postgresql_readonly=True)
                except Exception:
                    pass

                result = conn.execute(text(paged_sql))
                col_names = list(result.keys())
                deduped_names = self._dedupe_column_names(col_names)
                fetched = result.fetchmany(fetch_cap)  # 硬性行数上限, 不用 fetchall, 防止无 LIMIT 时拖垮内存
                rows = [dict(zip(deduped_names, r)) for r in fetched]

                total = len(rows)
                if count_sql:
                    try:
                        total = conn.execute(text(count_sql)).scalar_one()
                    except Exception:
                        total = (page - 1) * page_size + len(rows)  # 退化估计值
                elif len(fetched) >= fetch_cap:
                    # fallback 路径且行数刚好等于硬上限, total 大概率不准确(可能还有更多行未取)
                    # 没有更好的手段拿到精确总数, 只能先如实反映"已知下限"
                    total = len(rows)

            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            columns = self._infer_columns(deduped_names, rows)

            return ExecutionResult(
                success=True,
                columns=columns,
                rows=rows,
                pagination=PaginationMeta(page=page, page_size=page_size, total=total),
                elapsed_ms=elapsed_ms,
                sql=paged_sql,
            )

        except SQLAlchemyError as e:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            return ExecutionResult(
                success=False,
                sql=paged_sql,
                elapsed_ms=elapsed_ms,
                error=ErrorInfo(ErrorCode.DB_ERROR, "SQL 执行失败", self._simplify_db_error(e)),
            )
        except Exception as e:  # noqa: BLE001
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            return ExecutionResult(
                success=False,
                sql=paged_sql,
                elapsed_ms=elapsed_ms,
                error=ErrorInfo(ErrorCode.UNKNOWN_ERROR, "未知错误", str(e)),
            )

    # ------------------------------------------------------------------
    # 分页 / 计数 SQL 改写 (基于 sqlglot AST, 比字符串拼接更可靠)
    # ------------------------------------------------------------------
    def _build_paginated_queries(self, sql: str, page: int, page_size: int):
        root = sqlglot.parse_one(sql, read=self.dialect)

        # 计数查询: 去掉原有 LIMIT/OFFSET 后包一层 SELECT COUNT(*)
        unlimited = root.copy()
        unlimited.set("limit", None)
        unlimited.set("offset", None)
        count_query = exp.select(exp.Count(this=exp.Star())).from_(unlimited.subquery(alias="_count_sub"))
        count_sql = count_query.sql(dialect=self.dialect)

        # 分页查询: 用我们自己的 page/page_size 覆盖模型可能生成的 LIMIT
        paged = root.copy()
        paged.set("limit", exp.Limit(expression=exp.Literal.number(page_size)))
        paged.set("offset", exp.Offset(expression=exp.Literal.number((page - 1) * page_size)))
        paged_sql = paged.sql(dialect=self.dialect)

        return paged_sql, count_sql

    # ------------------------------------------------------------------
    # 重名列处理: 多表 JOIN 未加别名时 (如 SELECT a.id, b.id FROM a JOIN b)
    # SQLAlchemy 返回的 col_names 会有重复, dict(zip(...)) 会静默丢数据。
    # 这里给重复列加后缀区分, 保证 columns 数量和 rows 的 key 数量一致。
    # 注意: 这只是兜底, 更根本的做法是提醒 NL2SQL 组生成 SQL 时对重名列显式加别名。
    # ------------------------------------------------------------------
    @staticmethod
    def _dedupe_column_names(col_names: list) -> list:
        seen: dict = {}
        deduped = []
        for name in col_names:
            if name not in seen:
                seen[name] = 0
                deduped.append(name)
            else:
                seen[name] += 1
                deduped.append(f"{name}_{seen[name]}")
        return deduped

    # ------------------------------------------------------------------
    # 列类型推断: 根据实际返回值的 Python 类型推断前端需要的 ColumnMeta.type
    # ------------------------------------------------------------------
    @staticmethod
    def _infer_columns(col_names: list, rows: list) -> list:
        columns = []
        for name in col_names:
            col_type = "string"
            for row in rows:
                v = row.get(name)
                if v is None:
                    continue
                if isinstance(v, bool):
                    col_type = "string"  # 布尔值前端契约里没有对应类型, 按文本展示
                elif isinstance(v, (int, float, decimal.Decimal)):
                    col_type = "number"
                elif isinstance(v, datetime.datetime):
                    col_type = "datetime"
                elif isinstance(v, datetime.date):
                    col_type = "date"
                elif isinstance(v, str) and _DATETIME_RE.match(v):
                    col_type = "datetime"
                elif isinstance(v, str) and _DATE_RE.match(v):
                    col_type = "date"
                else:
                    col_type = "string"
                break  # 用第一个非空值判断即可, 同一列类型通常一致

            fmt = "plain"
            if col_type == "number":
                # 简单启发式: 字段名包含金额/价格等关键字时, 提示前端按货币格式化
                # 命中率有限, 更准确的做法是从 NL2SQL 组的 Schema 元信息里读字段语义, 后续可对接
                lowered = name.lower()
                if any(k in lowered for k in ("amount", "price", "salary", "revenue", "cost", "金额", "价格")):
                    fmt = "currency"
                elif any(k in lowered for k in ("rate", "ratio", "percent", "占比", "率")):
                    fmt = "percent"
            elif col_type in ("date", "datetime"):
                fmt = "date"

            columns.append(
                ColumnMeta(
                    key=name,
                    title=name,  # 中文展示名建议由 NL2SQL/Schema 层的字段注释覆盖, 这里先用原始字段名兜底
                    type=col_type,
                    format=fmt,
                    sortable=True,
                )
            )
        return columns

    @staticmethod
    def _simplify_db_error(e: SQLAlchemyError) -> str:
        msg = str(getattr(e, "orig", None) or e)
        return msg.split("\n")[0][:300]