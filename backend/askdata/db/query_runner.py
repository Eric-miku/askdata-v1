"""
query_runner.py
动态字符编码适配 (P3: 底层数据基建)

职责:
    在 SQLAlchemy 挂载 SQLite 之前, 先用 cchardet 探知数据库文件的字节编码,
    动态配置连接的编码处理参数, 防止 UTF-8 / GBK 混用的异构数据源让底层
    执行器崩溃。这是整个 db 模块里唯一负责"组装 engine"的地方——executor.py
    不再自己调用 create_engine, 统一由这里的 build_sqlite_engine() 产出。

背景:
    SQLite 的 TEXT 存储类型本身不校验字节内容是不是合法 UTF-8——如果数据是
    从 GBK 编码的 CSV/Excel 直接导入(比如用 sqlite3 命令行工具 .import, 或者
    某些第三方 ETL 工具没做转码), 非 UTF-8 的原始字节会被原样存进 TEXT 列。
    Python 的 sqlite3 驱动默认按 UTF-8 解码, 一旦读到这种"脏"数据会直接抛
    UnicodeDecodeError / OperationalError, 导致整条 SELECT 失败。

方案 (两步):
    1. detect_file_encoding(): 挂载数据库之前, 对文件做字节采样, 用 cchardet
       嗅探这个文件整体大概率是什么编码。置信度不够时默认按 UTF-8 处理, 不瞎猜。
    2. build_sqlite_engine(): 创建 SQLAlchemy engine, 并把"多编码兜底"的
       text_factory 通过 connect 事件挂到底层 sqlite3 连接上。真正读取每一行
       数据时按 [UTF-8, 探测到的编码, GB18030] 依次尝试解码, 全部失败才用
       errors='replace' 兜底——保证任何情况下都不会再直接抛异常崩掉查询。

依赖:
    官方 PyPI 上的 `cchardet` 包在 Python 3.11+/3.12/3.13 上编译不通过 (依赖的
    CPython 内部头文件 longintrepr.h 已被移除)。项目里请安装社区维护的替代包
    `faust-cchardet`:
        uv add faust-cchardet
    import 名字不变, 仍然是 `import cchardet`, 不用改任何调用代码。

局限性说明 (如实告知, 不夸大效果):
    这是运行时的"兜底容错", 不是"数据清洗"——脏数据解码出来的内容本身可能
    还是不准确的乱码, 只是不会再让整个查询崩溃。真正治本应该由数据预处理组
    在 prepare-bird 那一步就统一转码成 UTF-8, 这里只是最后一道安全网。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cchardet
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# 探测置信度低于这个阈值时, 不采信探测结果, 直接按 UTF-8 处理
_CONFIDENCE_THRESHOLD = 0.6

# 采样字节数: 太小容易误判(几个字的样本置信度可能只有 0.2), 太大拖慢启动速度,
# 64KB 对判断文件整体编码基本足够
_DEFAULT_SAMPLE_SIZE = 65536

# 常见中文遗留编码别名归一化, cchardet 有时候会返回不太常用的别名写法
_ENCODING_ALIASES = {
    "gb2312": "gbk",
    "gb-2312": "gbk",
    "gb18030": "gb18030",
    "big5": "big5",
}


def detect_file_encoding(file_path: str, sample_size: int = _DEFAULT_SAMPLE_SIZE) -> tuple[str, float]:
    """
    对文件做字节采样, 探知大概率的字符编码。

    :return: (encoding, confidence)。探测失败或文件不存在时兜底返回 ("utf-8", 0.0)
    """
    try:
        with open(file_path, "rb") as f:
            sample = f.read(sample_size)
    except OSError as e:
        logger.warning("编码探测失败, 无法读取文件 %s: %s, 按 UTF-8 兜底", file_path, e)
        return "utf-8", 0.0

    if not sample:
        return "utf-8", 1.0

    result = cchardet.detect(sample)
    encoding = (result.get("encoding") or "utf-8").lower()
    confidence = result.get("confidence") or 0.0
    encoding = _ENCODING_ALIASES.get(encoding, encoding)

    logger.info("文件 %s 编码探测结果: %s (置信度 %.2f)", file_path, encoding, confidence)
    return encoding, confidence


def _build_resilient_text_factory(detected_encoding: Optional[str] = None):
    """
    生成"多编码兜底"的 sqlite3 text_factory。
    每一行 TEXT 数据都按 [utf-8, 探测到的编码, gb18030] 依次尝试解码,
    全部失败才用 errors='replace' 兜底(不会再抛异常导致查询直接失败)。
    """
    candidates = ["utf-8"]
    if detected_encoding and detected_encoding not in candidates:
        candidates.append(detected_encoding)
    if "gb18030" not in candidates:
        candidates.append("gb18030")  # gb18030 兼容 gbk/gb2312, 放最后兜一层更保险

    def factory(raw: bytes):
        if raw is None:
            return None
        for enc in candidates:
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")

    return factory


def _extract_sqlite_file_path(db_url: str) -> Optional[str]:
    """
    从 SQLAlchemy 的 sqlite db_url 里解析出实际磁盘文件路径。
    "sqlite:////home/x/data.db" -> "/home/x/data.db"
    "sqlite:///:memory:" 或没有文件路径 -> None (跳过探测)
    """
    if not db_url or not db_url.startswith("sqlite"):
        return None
    path_part = db_url.split("///", 1)[-1] if "///" in db_url else ""
    if not path_part or path_part == ":memory:":
        return None
    return path_part


def build_sqlite_engine(db_url: str, **create_engine_kwargs) -> Engine:
    """
    唯一的 SQLite engine 组装入口。

    在真正 create_engine() 挂载数据库之前先探知文件编码, 引擎创建完成后立即
    挂载容错 text_factory, 确保从第一次连接开始就受到保护。

    :param db_url: SQLAlchemy 连接串, 例如 "sqlite:////home/x/data.db" 或 "sqlite:///:memory:"
    :param create_engine_kwargs: 透传给 sqlalchemy.create_engine 的其他参数
                                  (pool_size / pool_pre_ping / connect_args 等)
    :return: 已挂载好编码适配的 Engine
    """
    # SQLite 连接默认只能在创建它的线程里用, 上层 SQLExecutor 的超时保护机制需要
    # 在独立线程里执行查询, 这里统一兜底打开 check_same_thread=False。
    connect_args = dict(create_engine_kwargs.pop("connect_args", None) or {})
    connect_args.setdefault("check_same_thread", False)

    engine = create_engine(db_url, connect_args=connect_args, **create_engine_kwargs)

    file_path = _extract_sqlite_file_path(db_url)
    detected_encoding = None
    if file_path:
        encoding, confidence = detect_file_encoding(file_path)
        if confidence >= _CONFIDENCE_THRESHOLD and encoding != "utf-8":
            detected_encoding = encoding

    text_factory = _build_resilient_text_factory(detected_encoding)

    @event.listens_for(engine, "connect")
    def _set_text_factory(dbapi_conn, conn_record):  # noqa: ANN001
        dbapi_conn.text_factory = text_factory

    return engine


def Execute(sql: str, database_path: str, page_size: int = 1000, access_mode: str = "query") -> dict:
    """Execute a read-only SQLite query using the shared resilient executor.

    This small adapter is the application-facing query contract used by the
    ReAct agent and evaluation runner.  It deliberately verifies the path
    before constructing a SQLite URL: SQLite otherwise creates a new empty
    database for a misspelled path, which turns a configuration error into a
    misleading SQL error.
    """
    from askdata.security.permissions import PrepareCurrentSql

    allowed, reason, executable_sql = PrepareCurrentSql(sql, access_mode)
    if not allowed:
        return {
            "success": False,
            "error": reason or "SQL permission denied.",
            "error_code": "PERMISSION_DENIED",
        }

    path = Path(database_path)
    if not path.is_file():
        return {
            "success": False,
            "error": f"SQLite database does not exist: {path}",
            "error_code": "DB_NOT_FOUND",
        }

    from askdata.db.executor import SQLExecutor
    from askdata.core.config import settings

    row_limit = settings.EXPORT_MAX_ROWS if access_mode == "export" else settings.QUERY_MAX_ROWS
    effective_page_size = min(page_size, row_limit)

    result = SQLExecutor(
        f"sqlite:///{path.resolve()}",
        dialect="sqlite",
        default_page_size=effective_page_size,
        max_page_size=effective_page_size,
        max_joins=settings.SQL_MAX_JOINS,
        max_subquery_depth=settings.SQL_MAX_SUBQUERY_DEPTH,
        max_result_bytes=settings.MAX_RESULT_BYTES,
        statement_timeout_s=settings.SQL_STATEMENT_TIMEOUT_SECONDS,
        slow_query_ms=settings.SLOW_QUERY_MS,
    ).execute(executable_sql, page_size=effective_page_size)
    if not result.success:
        error = result.error
        return {
            "success": False,
            "error": (error.detail or error.message) if error else "SQL execution failed.",
            "error_code": error.code if error else "UNKNOWN_ERROR",
            "elapsed_ms": result.elapsed_ms,
        }
    return {
        "success": True,
        "columns": [column.key for column in result.columns],
        "rows": result.rows,
        "sql": sql,
        "pagination": result.pagination.to_dict() if result.pagination else None,
        "elapsed_ms": result.elapsed_ms,
        "warnings": result.warnings,
    }
