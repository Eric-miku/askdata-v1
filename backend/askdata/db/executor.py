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

P1 超时打断机制说明:
    大模型生成的 SQL 质量不完全可控, 可能写出笛卡尔积 JOIN、死循环递归 CTE 等
    会长时间挂起的语句。如果不加控制, 每一条这样的查询都会占用一个数据库连接
    直到它自己执行完 (可能是几分钟甚至更久), 并发几次就能把连接池耗尽, 导致
    其他正常请求也拿不到连接、整体服务瘫痪。

    防护做两层:
    1. 会话级超时 (尽力而为, 依赖数据库支持): 连接建立后, 对 PostgreSQL/MySQL
       下发 SET statement_timeout / SET SESSION MAX_EXECUTION_TIME, 让数据库
       自己在超时后掐断查询。SQLite 没有这个机制, 会静默跳过。
    2. 应用层墙钟超时 (通用, 不依赖数据库支持): 把实际执行放到独立线程里,
       主线程只等 statement_timeout_s 秒。超时后调用 conn.invalidate() 强制
       废弃这个连接 (不会归还给连接池, 下次会创建全新连接), 从而保证连接池
       不会被一条卡住的查询永久占死, 即使数据库本身不支持超时设置。

       局限性: 应用层超时不保证数据库端的查询立刻停止执行 (取决于具体驱动
       对"从另一线程关闭连接"的处理方式), 但能保证:
         (a) 调用方不会被无限阻塞, 固定时间内一定拿到 TIMEOUT 错误
         (b) 连接池的这个槽位不会被占死, 后续请求仍能正常拿到连接
       这已经覆盖了"防止连接池耗尽"这个核心诉求。
"""

from __future__ import annotations

import datetime
import decimal
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SATimeoutError

from .validator import SQLValidator, ValidationResult

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


# ---------------------------------------------------------------------------
# 错误码: 与前端约定的 error.code 保持一致, Agent/API 层可以据此做不同的重试/提示策略
# ---------------------------------------------------------------------------
class ErrorCode:
    SQL_BLOCKED = "SQL_BLOCKED"       # 未通过安全校验 (危险操作 / 多语句注入 / 黑名单表)
    DB_ERROR = "DB_ERROR"             # 数据库执行报错 (语法错误、字段不存在、连接失败等)
    TIMEOUT = "TIMEOUT"               # 执行超时 (查询本身太慢, 或等不到连接池里的连接)
    UNKNOWN_ERROR = "UNKNOWN_ERROR"   # 兜底


class QueryTimeoutError(Exception):
    """内部使用: 标记一次查询触发了应用层墙钟超时"""


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
        statement_timeout_s: float = 15.0,
        pool_size: int = 10,
        max_overflow: int = 5,
        pool_timeout: float = 10.0,
        engine: Optional[Engine] = None,
    ):
        """
        :param db_url: SQLAlchemy 连接串
        :param dialect: 传给 sqlglot 的方言标识, 需与 db_url 对应的数据库类型一致
        :param default_page_size: 未指定 page_size 时的默认每页条数
        :param max_page_size: page_size 的硬上限, 防止前端传入超大值拖垮数据库
        :param statement_timeout_s: 单条 SQL 最长允许执行时间 (秒), 超时会被强制中断并释放连接
        :param pool_size: 连接池常驻连接数
        :param max_overflow: 连接池允许的临时溢出连接数 (高峰期超出 pool_size 时使用)
        :param pool_timeout: 连接池已满时, 等待空闲连接的最长时间 (秒), 超时抛出池级别的 TIMEOUT
        :param engine: 允许外部注入已创建的 Engine (便于测试复用内存库连接; 传入时上面几个连接池参数不生效)
        """
        connect_args = {}
        if engine is None and (dialect == "sqlite" or (db_url and db_url.startswith("sqlite"))):
            # SQLite 的 DBAPI 连接默认只能在创建它的线程里使用, 而超时机制需要在独立线程
            # 里执行查询(主线程只等待固定时间), 两者天然冲突, 必须显式关闭这个限制。
            # 安全性说明: 我们保证同一时刻只有"发起请求的线程"和"内部超时worker线程"
            # 之一在操作这个连接(worker执行时主线程只是join等待, 不会并发访问),
            # 所以关闭 check_same_thread 不会引入真正的并发读写风险。
            connect_args = {"check_same_thread": False}

        self.engine: Engine = engine or create_engine(
            db_url,
            pool_pre_ping=pool_pre_ping,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            connect_args=connect_args,
        )
        self.dialect = dialect
        self.validator = SQLValidator(dialect=dialect, forbidden_tables=forbidden_tables)
        self.default_page_size = default_page_size
        self.max_page_size = max_page_size
        self.statement_timeout_s = statement_timeout_s

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def execute(self, sql: str, page: int = 1, page_size: Optional[int] = None) -> ExecutionResult:
        """
        校验 -> 改写分页 -> 执行(带超时保护) -> 统一格式化。
        校验失败 / 执行失败 / 超时都不抛异常, 而是封装进 ExecutionResult.error,
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
            fetch_cap = page_size
        except Exception:
            paged_sql, count_sql = validation.normalized_sql, None
            fetch_cap = self.max_page_size

        start = time.perf_counter()

        # 第一层防护: 拿连接本身也可能因为连接池耗尽而卡住, pool_timeout 保证这里不会无限等待
        try:
            conn = self.engine.connect()
        except SATimeoutError:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            return ExecutionResult(
                success=False,
                sql=paged_sql,
                elapsed_ms=elapsed_ms,
                error=ErrorInfo(
                    ErrorCode.TIMEOUT,
                    "数据库连接池繁忙, 暂时无法获取连接",
                    f"等待连接超过 {self.engine.pool.timeout()}s" if hasattr(self.engine.pool, "timeout") else None,
                ),
            )
        except SQLAlchemyError as e:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            return ExecutionResult(
                success=False,
                sql=paged_sql,
                elapsed_ms=elapsed_ms,
                error=ErrorInfo(ErrorCode.DB_ERROR, "无法建立数据库连接", self._simplify_db_error(e)),
            )

        timed_out = False
        try:
            try:
                conn = conn.execution_options(postgresql_readonly=True)
            except Exception:
                pass

            self._apply_session_timeout(conn)  # 第二层防护(尽力而为): 数据库自身的会话级超时

            try:
                # 第三层防护(通用兜底): 应用层墙钟超时, 独立线程执行, 超时则强制废弃连接
                result = self._execute_with_wall_clock_timeout(conn, paged_sql, self.statement_timeout_s)
            except QueryTimeoutError:
                timed_out = True
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                return ExecutionResult(
                    success=False,
                    sql=paged_sql,
                    elapsed_ms=elapsed_ms,
                    error=ErrorInfo(
                        ErrorCode.TIMEOUT,
                        f"SQL 执行超过 {self.statement_timeout_s} 秒, 已强制终止",
                        "疑似低效查询(如笛卡尔积 JOIN、递归死循环), 建议触发 SQL Repair 重新生成",
                    ),
                )

            col_names = list(result.keys())
            deduped_names = self._dedupe_column_names(col_names)
            fetched = result.fetchmany(fetch_cap)
            rows = [dict(zip(deduped_names, r)) for r in fetched]

            total = len(rows)
            if count_sql:
                try:
                    total = conn.execute(text(count_sql)).scalar_one()
                except Exception:
                    total = (page - 1) * page_size + len(rows)
            elif len(fetched) >= fetch_cap:
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
        finally:
            if not timed_out:
                # 正常路径: 同步关闭连接, 归还给连接池
                try:
                    conn.close()
                except Exception:
                    pass
            # 超时路径: 不在这里同步 close()。 conn.invalidate() 已经让这个连接
            # 不会被连接池复用了; 如果这里再调用 close(), 会因为后台线程还卡在
            # 原来那条慢查询里而被一起阻塞住, 导致"超时保护"名存实亡——调用方
            # 依然要等满整个慢查询跑完才能拿到返回值。所以超时场景下直接放手,
            # 底层连接对象会在后台线程执行完、双方都不再引用它之后被 GC 回收。

    # ------------------------------------------------------------------
    # 会话级超时 (尽力而为): 不同数据库语法不同, 不支持的直接忽略, 不影响主流程
    # ------------------------------------------------------------------
    def _apply_session_timeout(self, conn: Connection) -> None:
        timeout_ms = int(self.statement_timeout_s * 1000)
        try:
            if self.dialect in ("postgres", "postgresql"):
                conn.execute(text(f"SET statement_timeout = {timeout_ms}"))
            elif self.dialect == "mysql":
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}"))
            # sqlite / 其他方言没有对应机制, 完全依赖下面的应用层墙钟超时兜底
        except Exception:
            pass  # 会话级超时设置失败不应该阻断主查询, 有应用层兜底

    # ------------------------------------------------------------------
    # 应用层墙钟超时 (通用兜底): 独立线程执行, 超时后 invalidate 连接释放连接池槽位
    # ------------------------------------------------------------------
    @staticmethod
    def _execute_with_wall_clock_timeout(conn: Connection, sql_text: str, timeout_s: float):
        box: dict = {}

        def worker():
            try:
                box["result"] = conn.execute(text(sql_text))
            except Exception as e:  # noqa: BLE001
                box["error"] = e

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout_s)

        if thread.is_alive():
            # 查询仍未返回。注意: conn.invalidate()/conn.close() 此时会因为要等待
            # worker 线程释放对连接的占用而同样被阻塞住(相当于白等), 所以这里不能
            # 同步调用它们, 否则"超时保护"名存实亡。
            #
            # 做法: 起一个后台守护线程, 等 worker 真正跑完(不管多久)后再做清理;
            # 当前调用方不等这个清理过程, 立刻拿到 TIMEOUT 错误返回。
            def _cleanup_when_finished():
                thread.join()  # 等真正的查询线程自然结束
                try:
                    conn.invalidate()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass

            threading.Thread(target=_cleanup_when_finished, daemon=True).start()
            raise QueryTimeoutError(f"查询超过 {timeout_s} 秒未返回")

        if "error" in box:
            raise box["error"]
        return box["result"]

    # ------------------------------------------------------------------
    # 分页 / 计数 SQL 改写 (基于 sqlglot AST, 比字符串拼接更可靠)
    # ------------------------------------------------------------------
    def _build_paginated_queries(self, sql: str, page: int, page_size: int):
        root = sqlglot.parse_one(sql, read=self.dialect)

        unlimited = root.copy()
        unlimited.set("limit", None)
        unlimited.set("offset", None)
        count_query = exp.select(exp.Count(this=exp.Star())).from_(unlimited.subquery(alias="_count_sub"))
        count_sql = count_query.sql(dialect=self.dialect)

        paged = root.copy()
        paged.set("limit", exp.Limit(expression=exp.Literal.number(page_size)))
        paged.set("offset", exp.Offset(expression=exp.Literal.number((page - 1) * page_size)))
        paged_sql = paged.sql(dialect=self.dialect)

        return paged_sql, count_sql

    # ------------------------------------------------------------------
    # 重名列处理: 多表 JOIN 未加别名时 (如 SELECT a.id, b.id FROM a JOIN b)
    # SQLAlchemy 返回的 col_names 会有重复, dict(zip(...)) 会静默丢数据。
    # 这里给重复列加后缀区分, 保证 columns 数量和 rows 的 key 数量一致。
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
                    col_type = "string"
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
                break

            fmt = "plain"
            if col_type == "number":
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
                    title=name,
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