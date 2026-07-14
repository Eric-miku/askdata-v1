import time
from typing import Dict, Any, Callable
from functools import wraps

MAX_SNAPSHOT_LENGTH = 500


def _truncate_value(value: Any) -> Any:
    if isinstance(value, str):
        return (
            value[:MAX_SNAPSHOT_LENGTH] + "..."
            if len(value) > MAX_SNAPSHOT_LENGTH
            else value
        )
    return value


def traced(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(state: Any, *args, **kwargs) -> Dict[str, Any]:
        node_name = func.__name__.replace("_node", "")
        start_time = time.time()
        input_snapshot = (
            state.model_dump() if hasattr(state, "model_dump") else dict(state)
        )

        filtered_input = {
            k: _truncate_value(v)
            for k, v in input_snapshot.items()
            if k not in ("messages", "schema_context", "execution_result")
        }

        try:
            result = func(state, *args, **kwargs)
            duration_ms = (time.time() - start_time) * 1000

            filtered_output = {k: _truncate_value(v) for k, v in result.items()}

            trace_entry = {
                "node_name": node_name,
                "input_state": filtered_input,
                "output_state": filtered_output,
                "timestamp": time.time(),
                "duration_ms": round(duration_ms, 2),
                "status": "success",
            }

            result["trace"] = [trace_entry]
            return result
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            trace_entry = {
                "node_name": node_name,
                "input_state": filtered_input,
                "output_state": {"error": str(e)},
                "timestamp": time.time(),
                "duration_ms": round(duration_ms, 2),
                "status": "error",
            }

            return {"trace": [trace_entry]}

    return wrapper
