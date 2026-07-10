"""Command-line entry point for NL2SQL evaluation."""

import argparse

from .eval import EvalRunner


def main():
    parser = argparse.ArgumentParser(description="AskData — NL2SQL evaluation tool")
    parser.add_argument("--processed-dir", help="BIRD processed directory, defaults to BIRD_DATA_DIR/processed")
    parser.add_argument("--database-id", "-d", help="Evaluate only a specific database")
    parser.add_argument("--limit", "-n", type=int, help="Limit evaluation count")
    parser.add_argument("--out", "-o", default="reports/bird-eval.json", help="JSON report output path")

    args = parser.parse_args()

    report = EvalRunner(processed_dir=args.processed_dir).Run(
        database_id=args.database_id,
        limit=args.limit,
        out=args.out,
    )
    summary = report["summary"]
    print(f"Total: {summary['total']}")
    print(f"Execution Accuracy: {summary['executionAccuracy']:.2%}")
    print(f"Valid SQL Rate: {summary['validSqlRate']:.2%}")
    print(f"Exact Match Rate: {summary['exactMatchRate']:.2%}")
    print(f"Report: {args.out}")
