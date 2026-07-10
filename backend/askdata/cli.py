"""
命令行入口
"""

import argparse
import sys
import os
from pathlib import Path

from .eval import load_bird_dataset, run_evaluation, save_report


def main():
    parser = argparse.ArgumentParser(description="AskData - NL2SQL 评测工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # evaluate 命令
    eval_parser = subparsers.add_parser("evaluate", help="运行评测")
    eval_parser.add_argument("--dataset", "-d", required=True, help="数据集路径")
    eval_parser.add_argument("--subset", "-s", default="demo", help="数据集子集 (demo/dev/train)")
    eval_parser.add_argument("--output", "-o", default="eval_result.json", help="输出文件路径")
    eval_parser.add_argument("--db", help="数据库连接字符串 (optional)")
    eval_parser.add_argument("--limit", "-n", type=int, help="限制评测数量 (用于快速测试)")
    eval_parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    
    args = parser.parse_args()
    
    if args.command == "evaluate":
        # 加载数据集
        dataset = load_bird_dataset(args.dataset, args.subset)
        
        if args.limit:
            dataset = dataset[:args.limit]
            print(f"限制为 {len(dataset)} 条数据")
        
        if not dataset:
            print("错误: 未找到任何数据")
            sys.exit(1)
        
        print(f"加载数据集: {len(dataset)} 条")
        
        # 数据库连接（如果提供了）
        db_engine = None
        if args.db:
            from sqlalchemy import create_engine
            db_engine = create_engine(args.db)
            print(f"数据库连接: {args.db}")
        
        # 运行评测
        summary = run_evaluation(
            dataset=dataset,
            db_engine=db_engine,
            verbose=args.verbose
        )
        
        # 保存报告
        save_report(summary, args.output)
        
    elif args.command is None:
        parser.print_help()
    else:
        print(f"未知命令: {args.command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
