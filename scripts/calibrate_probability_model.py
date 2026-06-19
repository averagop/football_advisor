"""
Walk-forward 概率模型校准脚本。

按时间顺序滚动训练和验证，输出分桶校准误差和超额指标。
必须显式指定 --db-path 以避免误操作生产库。

用法:
    python scripts/calibrate_probability_model.py --db-path football_system.db
    python scripts/calibrate_probability_model.py --db-path football_system.db --limit 100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from football_advisor.backtest_engine import (
    BacktestEngine,
    BacktestResult,
    DuckDBBacktestMatchLoader,
    format_backtest_summary,
)
from football_advisor.calibration_engine import CalibrationEngine
from football_advisor.odds_value_engine import OddsValueEngine
from football_advisor.probability_engine import ProbabilityEngine


def calibration_gate_approved(metrics, baseline_metrics) -> bool:
    return (
        metrics.sample_size >= 50
        and metrics.brier_score < 0.25
        and metrics.brier_score < baseline_metrics.brier_score
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Walk-forward 概率模型校准",
    )
    parser.add_argument(
        "--db-path",
        required=True,
        help="DuckDB 数据库路径（必填）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="最大加载样本数（默认 50）",
    )
    parser.add_argument(
        "--min-train",
        type=int,
        default=10,
        help="walk-forward 最小训练样本数（默认 10）",
    )
    parser.add_argument(
        "--value-threshold",
        type=float,
        default=0.03,
        help="赔率价值阈值（默认 0.03）",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"错误: 数据库文件不存在: {args.db_path}")
        return 1

    print(f"加载历史比赛数据（最多 {args.limit} 场）...")
    loader = DuckDBBacktestMatchLoader(database_path=str(db_path))
    matches = loader.load_walk_forward_matches(limit=args.limit)

    if not matches:
        print("错误: 未找到可用的历史比赛数据。")
        print("请确保数据库中存在已完成比赛且包含赔率数据。")
        return 1

    print(f"已加载 {len(matches)} 场比赛")

    # 基线回测
    print("\n=== 基线回测（默认参数）===")
    baseline_engine = BacktestEngine(
        probability_engine=ProbabilityEngine(),
        odds_value_engine=OddsValueEngine(value_threshold=args.value_threshold),
    )
    baseline_result = baseline_engine.run(matches)
    print(format_backtest_summary(baseline_result))

    # Walk-forward 校准
    print(f"\n=== Walk-forward 校准（最少 {args.min_train} 场训练）===")
    cal_engine = CalibrationEngine()
    cal_metrics = cal_engine.walk_forward_calibrate(
        matches, min_train_samples=args.min_train
    )

    if "__uncalibrated__" in cal_metrics.bucket_errors:
        print(f"状态: 样本不足（需要至少 {args.min_train + 1} 场），未校准")
    else:
        print(f"Brier Score: {cal_metrics.brier_score:.6f}")
        print(f"LogLoss: {cal_metrics.log_loss:.6f}")
        print(f"命中率: {cal_metrics.accuracy:.2%}")
        print(f"验证样本数: {cal_metrics.sample_size}")
        if cal_metrics.bucket_errors:
            print("\n分桶校准误差 (预测均值 - 实际命中率):")
            for bucket, error in sorted(cal_metrics.bucket_errors.items()):
                print(f"  {bucket}: {error:+.4f}")

    # 校准阈值检查
    print("\n=== 校准门禁检查 ===")
    if calibration_gate_approved(cal_metrics, baseline_result.metrics):
        print("通过: 样本量、Brier Score 和基线改进均满足要求。")
        print("批准参数: calibrated=true")
        return 0
    else:
        reasons = []
        if cal_metrics.sample_size < 50:
            reasons.append(f"样本不足（{cal_metrics.sample_size} < 50）")
        if cal_metrics.brier_score >= 0.25:
            reasons.append(f"Brier Score 过高（{cal_metrics.brier_score:.4f} >= 0.25）")
        if cal_metrics.brier_score >= baseline_result.metrics.brier_score:
            reasons.append(
                "Brier Score 未优于基线"
                f"（{cal_metrics.brier_score:.4f} >= "
                f"{baseline_result.metrics.brier_score:.4f}）"
            )
        print(f"未通过: {'; '.join(reasons)}")
        print('建议: 保持基线模型，标记为"未校准"，不输出高置信度投注建议。')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
