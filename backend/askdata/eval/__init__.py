"""
评测模块
包含 NL2SQL 评测指标和评测运行器
"""

from .metrics import (
    BirdResultComparer,
    ExactMatch,
    exact_match,
    exact_match_accuracy,
    execution_accuracy,
    execution_accuracy_batch,
    normalize_sql,
    validate_sql_structure,
    candidate_hit_rate,
    batch_candidate_hit_rate,
)

from .runner import EvalRunner

__all__ = [
    "exact_match",
    "ExactMatch",
    "BirdResultComparer",
    "exact_match_accuracy",
    "execution_accuracy",
    "execution_accuracy_batch",
    "normalize_sql",
    "validate_sql_structure",
    "candidate_hit_rate",
    "batch_candidate_hit_rate",
    "EvalRunner",
]
