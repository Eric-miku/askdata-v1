"""
评测指标模块
实现 NL2SQL 评测的核心指标
"""

import re
import json
from typing import List, Tuple, Any, Optional
from difflib import SequenceMatcher


def normalize_sql(sql: str) -> str:
    """
    标准化SQL字符串，用于精确匹配比较
    
    处理：去除多余空格、换行、转换为小写
    """
    if not sql:
        return ""
    # 移除注释
    sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    # 合并空白字符
    sql = ' '.join(sql.strip().split())
    return sql.lower()


def exact_match(pred_sql: str, gold_sql: str) -> bool:
    """
    Exact Match (EM) 指标
    
    比较预测SQL和标准SQL是否完全一致（忽略格式和大小写）
    
    Returns:
        True 如果完全匹配，否则 False
    """
    return normalize_sql(pred_sql) == normalize_sql(gold_sql)


def exact_match_accuracy(pred_sqls: List[str], gold_sqls: List[str]) -> float:
    """
    批量计算 Exact Match 准确率
    
    Returns:
        准确率百分比 (0-100)
    """
    if len(pred_sqls) != len(gold_sqls):
        raise ValueError("预测SQL列表和标准SQL列表长度不一致")
    
    if len(pred_sqls) == 0:
        return 100.0
    
    matches = sum(1 for p, g in zip(pred_sqls, gold_sqls) if exact_match(p, g))
    return (matches / len(pred_sqls)) * 100


def execution_accuracy(
    pred_result: List[Tuple],
    gold_result: List[Tuple],
    ignore_order: bool = True
) -> bool:
    """
    Execution Accuracy (EA) 指标
    
    比较两个SQL查询的执行结果是否一致
    
    Args:
        pred_result: 预测SQL的执行结果（行列表）
        gold_result: 标准SQL的执行结果（行列表）
        ignore_order: 是否忽略行顺序（默认 True）
    
    Returns:
        True 如果结果一致，否则 False
    """
    if len(pred_result) != len(gold_result):
        return False
    
    if len(pred_result) == 0:
        return True
    
    if ignore_order:
        return set(pred_result) == set(gold_result)
    else:
        return pred_result == gold_result


def execution_accuracy_batch(
    pred_results: List[List[Tuple]],
    gold_results: List[List[Tuple]]
) -> float:
    """
    批量计算 Execution Accuracy 准确率
    
    Returns:
        准确率百分比 (0-100)
    """
    if len(pred_results) != len(gold_results):
        raise ValueError("预测结果列表和标准结果列表长度不一致")
    
    if len(pred_results) == 0:
        return 100.0
    
    matches = sum(
        1 for p, g in zip(pred_results, gold_results)
        if execution_accuracy(p, g)
    )
    return (matches / len(pred_results)) * 100


def sql_similarity(pred_sql: str, gold_sql: str) -> float:
    """
    计算两个SQL字符串的相似度 (0-1)
    
    使用SequenceMatcher，用于语义相似度分析
    """
    return SequenceMatcher(None, normalize_sql(pred_sql), normalize_sql(gold_sql)).ratio()


def validate_sql_structure(pred_sql: str) -> dict:
    """
    验证SQL的基本结构（安全校验）
    
    Returns:
        dict: {
            "valid": bool,
            "has_drop": bool,
            "has_delete": bool,
            "has_update": bool,
            "warnings": list
        }
    """
    sql_upper = pred_sql.upper()
    warnings = []
    
    # 检查危险操作
    dangerous = []
    if "DROP" in sql_upper:
        dangerous.append("DROP")
    if "DELETE" in sql_upper and "WHERE" not in sql_upper:
        dangerous.append("DELETE without WHERE")
    if "UPDATE" in sql_upper and "WHERE" not in sql_upper:
        dangerous.append("UPDATE without WHERE")
    if "TRUNCATE" in sql_upper:
        dangerous.append("TRUNCATE")
    
    return {
        "valid": len(dangerous) == 0,
        "has_drop": "DROP" in sql_upper,
        "has_delete": "DELETE" in sql_upper,
        "has_update": "UPDATE" in sql_upper,
        "dangerous_operations": dangerous,
        "warnings": warnings
    }
