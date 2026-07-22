"""
NL2SQL 评测指标模块
"""

import re
from typing import List, Tuple, Any, Optional


def normalize_sql(sql: str) -> str:
    """
    标准化SQL字符串，用于精确匹配比较
    """
    if not sql:
        return ""
    sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = ' '.join(sql.strip().split())
    return sql.lower()


def exact_match(pred_sql: str, gold_sql: str) -> bool:
    """Exact Match (EM) 指标"""
    return normalize_sql(pred_sql) == normalize_sql(gold_sql)


def exact_match_accuracy(pred_sqls: List[str], gold_sqls: List[str]) -> float:
    """批量计算 Exact Match 准确率"""
    if len(pred_sqls) != len(gold_sqls):
        raise ValueError("预测SQL列表和标准SQL列表长度不一致")
    if len(pred_sqls) == 0:
        return 100.0
    matches = sum(1 for p, g in zip(pred_sqls, gold_sqls) if exact_match(p, g))
    return (matches / len(pred_sqls)) * 100


def execution_accuracy(pred_result: List[Tuple], gold_result: List[Tuple], ignore_order: bool = True) -> bool:
    """Execution Accuracy (EA) 指标"""
    if len(pred_result) != len(gold_result):
        return False
    if len(pred_result) == 0:
        return True
    if ignore_order:
        return set(pred_result) == set(gold_result)
    return pred_result == gold_result


def execution_accuracy_batch(pred_results: List[List[Tuple]], gold_results: List[List[Tuple]]) -> float:
    """批量计算 Execution Accuracy 准确率"""
    if len(pred_results) != len(gold_results):
        raise ValueError("预测结果列表和标准结果列表长度不一致")
    if len(pred_results) == 0:
        return 100.0
    matches = sum(1 for p, g in zip(pred_results, gold_results) if execution_accuracy(p, g))
    return (matches / len(pred_results)) * 100


def validate_sql_structure(pred_sql: str) -> dict:
    """验证SQL的基本结构（安全校验）"""
    sql_upper = pred_sql.upper()
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
        "warnings": []
    }


# ============================================================
# 候选 SQL 命中率指标（新增）
# ============================================================

def candidate_hit_rate(
    candidate_sqls: List[str],
    gold_sql: str,
    gold_result: List[Tuple]
) -> bool:
    """
    候选 SQL 命中率：候选集合中至少有一条执行结果与 Gold SQL 一致

    Args:
        candidate_sqls: 候选 SQL 字符串列表
        gold_sql: 标准答案 SQL（仅用于日志）
        gold_result: 标准 SQL 的执行结果（预先执行好传入）

    Returns:
        True 如果至少一条候选 SQL 执行结果与标准一致，否则 False
    """
    if not candidate_sqls:
        return False
    if not gold_result:
        return False

    gold_set = set(gold_result)

    for sql in candidate_sqls:
        # 注意：这里的 pred_result 需要由调用方预先执行并传入
        # 在 EvalRunner 中，我们使用 _ExecuteSql 执行每个候选 SQL
        pass

    return False


def batch_candidate_hit_rate(
    candidates_results: List[List[Tuple]],
    gold_results: List[List[Tuple]]
) -> float:
    """
    批量计算候选 SQL 命中率（基于已执行的结果）

    Args:
        candidates_results: 每条数据对应的候选 SQL 执行结果列表
        gold_results: 标准 SQL 执行结果列表

    Returns:
        命中率百分比 (0-100)
    """
    if len(candidates_results) != len(gold_results):
        raise ValueError("候选结果列表和标准结果列表长度不一致")
    if len(candidates_results) == 0:
        return 100.0

    hits = 0
    for pred_results, gold_result in zip(candidates_results, gold_results):
        if not gold_result:
            continue
        gold_set = set(gold_result)
        hit = any(set(pred_result) == gold_set for pred_result in pred_results if pred_result)
        if hit:
            hits += 1

    return (hits / len(candidates_results)) * 1001~"""
NL2SQL 评测指标模块
"""

import re
from typing import List, Tuple, Any, Optional


def normalize_sql(sql: str) -> str:
    """
    标准化SQL字符串，用于精确匹配比较
    """
    if not sql:
        return ""
    sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = ' '.join(sql.strip().split())
    return sql.lower()


def exact_match(pred_sql: str, gold_sql: str) -> bool:
    """Exact Match (EM) 指标"""
    return normalize_sql(pred_sql) == normalize_sql(gold_sql)


def exact_match_accuracy(pred_sqls: List[str], gold_sqls: List[str]) -> float:
    """批量计算 Exact Match 准确率"""
    if len(pred_sqls) != len(gold_sqls):
        raise ValueError("预测SQL列表和标准SQL列表长度不一致")
    if len(pred_sqls) == 0:
        return 100.0
    matches = sum(1 for p, g in zip(pred_sqls, gold_sqls) if exact_match(p, g))
    return (matches / len(pred_sqls)) * 100


def execution_accuracy(pred_result: List[Tuple], gold_result: List[Tuple], ignore_order: bool = True) -> bool:
    """Execution Accuracy (EA) 指标"""
    if len(pred_result) != len(gold_result):
        return False
    if len(pred_result) == 0:
        return True
    if ignore_order:
        return set(pred_result) == set(gold_result)
    return pred_result == gold_result


def execution_accuracy_batch(pred_results: List[List[Tuple]], gold_results: List[List[Tuple]]) -> float:
    """批量计算 Execution Accuracy 准确率"""
    if len(pred_results) != len(gold_results):
        raise ValueError("预测结果列表和标准结果列表长度不一致")
    if len(pred_results) == 0:
        return 100.0
    matches = sum(1 for p, g in zip(pred_results, gold_results) if execution_accuracy(p, g))
    return (matches / len(pred_results)) * 100


def validate_sql_structure(pred_sql: str) -> dict:
    """验证SQL的基本结构（安全校验）"""
    sql_upper = pred_sql.upper()
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
        "warnings": []
    }


# ============================================================
# 候选 SQL 命中率指标（新增）
# ============================================================

def candidate_hit_rate(
    candidate_sqls: List[str],
    gold_sql: str,
    gold_result: List[Tuple]
) -> bool:
    """
    候选 SQL 命中率：候选集合中至少有一条执行结果与 Gold SQL 一致

    Args:
        candidate_sqls: 候选 SQL 字符串列表
        gold_sql: 标准答案 SQL（仅用于日志）
        gold_result: 标准 SQL 的执行结果（预先执行好传入）

    Returns:
        True 如果至少一条候选 SQL 执行结果与标准一致，否则 False
    """
    if not candidate_sqls:
        return False
    if not gold_result:
        return False

    gold_set = set(gold_result)

    for sql in candidate_sqls:
        # 注意：这里的 pred_result 需要由调用方预先执行并传入
        # 在 EvalRunner 中，我们使用 _ExecuteSql 执行每个候选 SQL
        pass

    return False


def batch_candidate_hit_rate(
    candidates_results: List[List[Tuple]],
    gold_results: List[List[Tuple]]
) -> float:
    """
    批量计算候选 SQL 命中率（基于已执行的结果）

    Args:
        candidates_results: 每条数据对应的候选 SQL 执行结果列表
        gold_results: 标准 SQL 执行结果列表

    Returns:
        命中率百分比 (0-100)
    """
    if len(candidates_results) != len(gold_results):
        raise ValueError("候选结果列表和标准结果列表长度不一致")
    if len(candidates_results) == 0:
        return 100.0

    hits = 0
    for pred_results, gold_result in zip(candidates_results, gold_results):
        if not gold_result:
            continue
        gold_set = set(gold_result)
        hit = any(set(pred_result) == gold_set for pred_result in pred_results if pred_result)
        if hit:
            hits += 1

    return (hits / len(candidates_results)) * 100
