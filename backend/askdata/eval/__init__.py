"""
评测模块 — NL2SQL 评测指标和评测运行器
"""

from .demo_suite import DemoSuite
from .manual_regressions import (
    LoadManualRegressionCases,
    ManualRegressionCase,
    ManualRegressionRunner,
)
from .metrics import BirdResultComparer, ExactMatch, NormalizeSql
from .runner import EvalRunner

__all__ = [
    "BirdResultComparer",
    "DemoSuite",
    "EvalRunner",
    "ExactMatch",
    "LoadManualRegressionCases",
    "ManualRegressionCase",
    "ManualRegressionRunner",
    "NormalizeSql",
]
