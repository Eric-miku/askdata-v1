"""
评测模块
包含 NL2SQL 评测指标和评测运行器
"""

from .metrics import (
    exact_match,
    exact_match_accuracy,
    execution_accuracy,
    execution_accuracy_batch,
    normalize_sql,
    validate_sql_structure
)

from .runner import (
    load_bird_dataset,
    run_evaluation,
    save_report,
    call_llm_for_sql
)

__all__ = [
    "exact_match",
    "exact_match_accuracy",
    "execution_accuracy",
    "execution_accuracy_batch",
    "normalize_sql",
    "validate_sql_structure",
    "load_bird_dataset",
    "run_evaluation",
    "save_report",
    "call_llm_for_sql"
]
