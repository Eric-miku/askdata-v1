"""
评测运行器
加载数据集、调用模型、计算指标、输出报告
"""

import json
import os
import sys
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from pathlib import Path

from .metrics import (
    exact_match,
    execution_accuracy,
    exact_match_accuracy,
    execution_accuracy_batch,
    validate_sql_structure,
    normalize_sql
)


def load_bird_dataset(data_dir: str, subset: str = "demo") -> List[Dict[str, Any]]:
    """
    加载 BIRD 格式的数据集
    
    Args:
        data_dir: 数据集根目录
        subset: 子集名称 (demo, dev, train, etc.)
    
    Returns:
        数据集列表，每个元素包含:
        {
            "id": "q001",
            "question": "自然语言问题",
            "gold_sql": "标准SQL",
            "db_name": "数据库名称",
            "schema": {...} (可选)
        }
    """
    dataset = []
    
    # 查找预处理后的JSON文件
    processed_dir = Path(data_dir) / "processed"
    raw_dir = Path(data_dir) / "raw"
    
    # 先尝试从 processed 目录加载
    for json_file in processed_dir.glob(f"*{subset}*.json"):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                dataset.extend(data)
            elif isinstance(data, dict) and "questions" in data:
                dataset.extend(data["questions"])
            elif isinstance(data, dict) and "data" in data:
                dataset.extend(data["data"])
            else:
                # 兼容单条格式
                if "question" in data and "gold_sql" in data:
                    dataset.append(data)
    
    # 如果 processed 目录没有，尝试从 demo 目录加载
    if not dataset:
        demo_dir = Path(data_dir) / "demo"
        for json_file in demo_dir.glob("*.json"):
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    dataset.extend(data)
                elif isinstance(data, dict) and "data" in data:
                    dataset.extend(data["data"])
    
    return dataset


def call_llm_for_sql(
    question: str,
    db_schema: Optional[str] = None,
    api_base: str = None,
    api_key: str = None,
    model: str = None
) -> str:
    """
    调用大模型将自然语言转换为SQL
    
    Args:
        question: 自然语言问题
        db_schema: 数据库Schema描述（可选）
        api_base: LLM API地址
        api_key: API密钥
        model: 模型名称
    
    Returns:
        生成的SQL语句
    """
    import requests
    
    # 从环境变量获取配置
    api_base = api_base or os.getenv("LLM_API_BASE", "http://113.134.239.144:9001/v1")
    api_key = api_key or os.getenv("LLM_API_KEY", "sxdzaizbllm")
    model = model or os.getenv("LLM_MODEL_NAME", "Qwen3.5-397B-A17B")
    
    # 构建Prompt
    schema_prompt = ""
    if db_schema:
        schema_prompt = f"数据库结构如下:\n{db_schema}\n\n"
    
    prompt = f"""{schema_prompt}请将以下自然语言问题转换为SQL查询语句。
只返回SQL语句本身，不要添加任何解释或额外文字。

问题: {question}
SQL:"""
    
    try:
        response = requests.post(
            f"{api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是一个SQL专家，擅长将自然语言转换为SQL查询语句。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 512
            },
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            sql = result["choices"][0]["message"]["content"]
            # 清理SQL（去掉markdown代码块等）
            sql = sql.strip()
            # 移除可能的 ```sql 标记
            if sql.startswith("```"):
                sql = sql.split("```")[1] if "```" in sql else sql
                if sql.startswith("sql"):
                    sql = sql[3:]
            return sql.strip()
        else:
            print(f"API调用失败: HTTP {response.status_code} - {response.text}")
            return ""
            
    except Exception as e:
        print(f"API调用异常: {e}")
        return ""


def execute_sql(sql: str, db_conn: Any) -> List[Tuple]:
    """
    执行SQL并返回结果
    
    Args:
        sql: SQL语句
        db_conn: SQLAlchemy连接对象
    
    Returns:
        结果行列表
    """
    from sqlalchemy import text
    
    if not sql:
        return []
    
    try:
        with db_conn.connect() as conn:
            result = conn.execute(text(sql))
            rows = result.fetchall()
            return [tuple(row) for row in rows]
    except Exception as e:
        print(f"SQL执行失败: {e}")
        print(f"SQL: {sql[:200]}...")
        return []


def run_evaluation(
    dataset: List[Dict[str, Any]],
    db_engine: Any,
    api_base: str = None,
    api_key: str = None,
    model: str = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    运行完整的评测流程
    
    Returns:
        评测结果汇总
    """
    results = []
    total = len(dataset)
    
    if verbose:
        print(f"\n{'='*70}")
        print(f"开始评测，共 {total} 条数据")
        print(f"{'='*70}\n")
    
    for idx, item in enumerate(dataset):
        question = item.get("question", "")
        gold_sql = item.get("gold_sql", "")
        db_schema = item.get("schema", None)
        item_id = item.get("id", f"q{idx+1:04d}")
        
        if verbose and (idx + 1) % 10 == 0:
            print(f"进度: {idx+1}/{total}")
        
        # 调用模型生成SQL
        pred_sql = call_llm_for_sql(question, db_schema, api_base, api_key, model)
        
        # 执行SQL获取结果（需要db_engine支持）
        pred_result = []
        gold_result = []
        try:
            if db_engine and gold_sql:
                gold_result = execute_sql(gold_sql, db_engine)
            if db_engine and pred_sql:
                pred_result = execute_sql(pred_sql, db_engine)
        except Exception as e:
            if verbose:
                print(f"  SQL执行错误: {e}")
        
        # 计算指标
        em = exact_match(pred_sql, gold_sql)
        ea = execution_accuracy(pred_result, gold_result) if db_engine else None
        sql_valid = validate_sql_structure(pred_sql)
        
        results.append({
            "id": item_id,
            "question": question,
            "gold_sql": gold_sql,
            "pred_sql": pred_sql,
            "exact_match": em,
            "execution_accuracy": ea,
            "sql_valid": sql_valid["valid"],
            "pred_result_rows": len(pred_result),
            "gold_result_rows": len(gold_result)
        })
    
    # 计算汇总指标
    em_score = exact_match_accuracy(
        [r["gold_sql"] for r in results],
        [r["pred_sql"] for r in results]
    ) if results else 0
    
    # 计算有效SQL的比例
    valid_ratio = sum(1 for r in results if r["sql_valid"]) / len(results) * 100 if results else 0
    
    # 计算EA（只统计有执行结果的）
    ea_results = [r for r in results if r["execution_accuracy"] is not None]
    ea_score = execution_accuracy_batch(
        [[(r["pred_result_rows"],)] for r in ea_results],
        [[(r["gold_result_rows"],)] for r in ea_results]
    ) if ea_results else 0
    
    summary = {
        "total": total,
        "exact_match_accuracy": em_score,
        "execution_accuracy": ea_score,
        "valid_sql_ratio": valid_ratio,
        "timestamp": datetime.now().isoformat(),
        "details": results
    }
    
    if verbose:
        print(f"\n{'='*70}")
        print("评测结果汇总")
        print(f"{'='*70}")
        print(f"总测试数: {total}")
        print(f"Exact Match 准确率: {em_score:.2f}%")
        if db_engine:
            print(f"Execution Accuracy: {ea_score:.2f}%")
        print(f"有效SQL比例: {valid_ratio:.2f}%")
        print(f"{'='*70}\n")
    
    return summary


def save_report(summary: Dict[str, Any], output_path: str):
    """保存评测报告到JSON文件"""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"评测报告已保存: {output_path}")
