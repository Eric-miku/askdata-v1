"""
Trace 日志工具 —— 标准化 Agent 执行轨迹记录

每个 API 请求都会创建一个 TraceLogger 实例，记录从收到请求到返回响应的
完整执行链路，包括：SQL 生成、SQL 验证、SQL 执行、结果分析等各阶段耗时。

Trace 日志使用统一的字典格式，与 AgentGraph 层的 _TraceStep 输出的格式一致，
确保前端只需要解析一种 Trace 结构:
    {"step": "步骤名", "status": "success|error", "message": "详情"}

Trace 日志最终会放入 QueryResponse.trace 字段返回给前端，
帮助前端（和开发者）了解整个查询的处理过程。
"""

import time
import uuid
from typing import List, Dict


class TraceLogger:
    """统一的 Trace 日志记录器

    用法:
        trace = TraceLogger()
        trace.log("收到查询", "加州学生总数")
        trace.log("调用 LLM 生成 SQL")
        trace.log("执行 SQL", "返回 1 行结果")
        response.trace = trace.get_logs()
    """

    def __init__(self):
        # 每个请求生成一个短 Trace ID，方便在日志中关联查询
        self.trace_id = str(uuid.uuid4())[:8]
        self._logs: List[Dict[str, str]] = []
        self._start_time = time.time()

    def log(self, step: str, detail: str = "", status: str = "info") -> None:
        """记录一条 Trace 日志

        与 AgentGraph._TraceStep 保持相同的字典输出格式:
            {"step": "步骤名", "status": "状态", "message": "详情"}

        Args:
            step: 步骤名称，如 "创建会话"、"收到查询请求"
            detail: 步骤详情，如请求参数、SQL 语句等
            status: 状态标识，默认为 "info"，查询失败时为 "error"
        """
        elapsed = time.time() - self._start_time
        prefix = f"[{self.trace_id}][+{elapsed:.2f}s]"
        message = f"{prefix} {step}: {detail}" if detail else f"{prefix} {step}"
        self._logs.append({
            "step": step,
            "status": status,
            "message": message,
        })

    def get_logs(self) -> List[Dict[str, str]]:
        """获取所有 Trace 日志"""
        return self._logs

    def get_trace_id(self) -> str:
        """获取 Trace ID"""
        return self.trace_id
