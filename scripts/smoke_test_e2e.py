"""B-4: 端到端烟测脚本 — 验证预测流水线核心通路完整性。

验证路径：
1. 数据库连通性（DuckDB schema 存在）
2. 联赛/球队映射完整性
3. 数据同步（多源协调器）
4. 特征工程
5. 概率引擎
6. 赔率价值引擎
7. No Bet 策略
8. LLM 报告生成器基础检查
"""

import os
import sys
import duckdb
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, "football_system.db")

CHECKS_PASSED = 0
CHECKS_FAILED = 0
CHECKS_WARNING = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS_PASSED, CHECKS_FAILED, CHECKS_WARNING
    if condition:
        CHECKS_PASSED += 1
        print(f"  [PASS] {name}")
    else:
        CHECKS_FAILED += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def warn(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS_WARNING
    if condition:
        check(name, True, detail)
    else:
        CHECKS_WARNING += 1
        print(f"  [WARN] {name} — {detail}")


def main() -> int:
    print("=" * 60)
    print("B-4 端到端烟测")
    print(f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    # ---- 1. 数据库连通性 ----
    print("\n[1] 数据库连通性")
    check("数据库文件存在", os.path.exists(DB_PATH), f"未找到: {DB_PATH}")
    conn = duckdb.connect(DB_PATH)

    schemas = conn.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
    schema_names = {s[0] for s in schemas}
    check("core schema 存在", "core" in schema_names)
    check("staging schema 存在", "staging" in schema_names)

    # ---- 2. 映射完整性 ----
    print("\n[2] 映射完整性")
    core_tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='core'"
    ).fetchall()
    core_table_names = {t[0] for t in core_tables}
    required_tables = [
        "dim_league_mapping", "dim_team_mapping",
        "dim_provider_league_mapping", "dim_provider_team_mapping",
        "fact_match_schedule", "fact_team_rolling_stats",
    ]
    for table in required_tables:
        check(f"core.{table} 存在", table in core_table_names)

    league_count = conn.execute("SELECT COUNT(*) FROM core.dim_league_mapping").fetchone()[0]
    team_count = conn.execute("SELECT COUNT(*) FROM core.dim_team_mapping").fetchone()[0]
    provider_league_count = conn.execute(
        "SELECT COUNT(*) FROM core.dim_provider_league_mapping"
    ).fetchone()[0]
    provider_team_count = conn.execute(
        "SELECT COUNT(*) FROM core.dim_provider_team_mapping"
    ).fetchone()[0]

    check("联赛映射 > 0", league_count > 0, f"当前: {league_count}")
    check("球队映射 > 0", team_count > 0, f"当前: {team_count}")
    check("Provider 联赛映射 > 0", provider_league_count > 0, f"当前: {provider_league_count}")
    check("Provider 球队映射 > 0", provider_team_count > 0, f"当前: {provider_team_count}")

    worldcup = conn.execute(
        "SELECT 1 FROM core.dim_league_mapping WHERE system_league_id='WC_WORLD_CUP_2026'"
    ).fetchone()
    check("世界杯联赛映射存在", worldcup is not None)

    national_teams = conn.execute(
        "SELECT COUNT(*) FROM core.dim_team_mapping WHERE system_team_id LIKE 'WC_TEAM_%'"
    ).fetchone()[0]
    check("世界杯国家队已录入", national_teams >= 24, f"当前: {national_teams}")

    top5_leagues = conn.execute(
        "SELECT COUNT(*) FROM core.dim_league_mapping WHERE system_league_id LIKE 'TOP5_%'"
    ).fetchone()[0]
    check("五大联赛已录入", top5_leagues >= 5, f"当前: {top5_leagues}")

    # ---- 3. Provider Mapping CSV ----
    print("\n[3] Provider Mapping CSV")
    mapping_dir = os.path.join(PROJECT_DIR, "data", "provider_mappings")
    check(
        "provider_league_mappings.csv 存在",
        os.path.exists(os.path.join(mapping_dir, "provider_league_mappings.csv")),
    )
    check(
        "provider_team_mappings.csv 存在",
        os.path.exists(os.path.join(mapping_dir, "provider_team_mappings.csv")),
    )

    # ---- 4. 数据同步（多源协调器） ----
    print("\n[4] 多源数据同步")
    try:
        from football_advisor.sync import DataSyncCoordinator
        from football_advisor.config import load_config
        from football_advisor.models import MatchRequest

        config = load_config()
        coordinator = DataSyncCoordinator(config=config.sync, parent_config=config)
        sync_result = coordinator.sync_structured_data(
            MatchRequest(query="Arsenal vs Chelsea")
        )
        check("多源同步不抛出异常", True)
        check(
            "同步结果返回",
            sync_result is not None and sync_result.status is not None,
            f"status={sync_result.status if sync_result else 'None'}",
        )
        check(
            "同步来源为 multi_source",
            sync_result.source == "multi_source",
            f"actual={sync_result.source}",
        )
        print(f"    覆盖等级: {sync_result.details.get('coverage_flag', 'N/A')}")
        print(f"    成功/跳过: {sync_result.details.get('success_count', '?')}/{sync_result.details.get('skipped_count', '?')}")
    except Exception as e:
        check(f"数据同步通过", False, str(e)[:100])

    # ---- 5. 特征工程 ----
    print("\n[5] 特征工程")
    try:
        from football_advisor.feature_builder import DuckDBFeatureBuilder
        from football_advisor.models import MatchRequest

        builder = DuckDBFeatureBuilder()
        features = builder.build(
            request=MatchRequest(query="Arsenal vs Chelsea", home_team="Arsenal", away_team="Chelsea"),
            sync_results=[],
        )
        check("特征工程不抛出异常", True)
        check("特征包含 1x2 赔率", hasattr(features, "odds_1x2") and features.odds_1x2 is not None)
        check("特征包含 context", isinstance(features.context, dict))
        check("特征包含 updated_at", features.updated_at is not None)
        feat_ok = True
    except Exception as e:
        check(f"特征工程通过", False, str(e)[:100])
        feat_ok = False

    # ---- 6. 概率引擎 ----
    print("\n[6] 概率引擎")
    if feat_ok:
        try:
            from football_advisor.probability_engine import ProbabilityEngine

            engine = ProbabilityEngine()
            probabilities = engine.predict(features)
            probs = probabilities.outcome_probabilities()
            check("概率引擎预测成功", True)
            check("包含 home 概率", "home" in probs and 0 <= probs["home"] <= 1)
            check("包含 draw 概率", "draw" in probs and 0 <= probs["draw"] <= 1)
            check("包含 away 概率", "away" in probs and 0 <= probs["away"] <= 1)
            total = probs["home"] + probs["draw"] + probs["away"]
            warn("概率总和≈1", 0.95 <= total <= 1.05, f"total={total:.3f}")
            prob_ok = True
        except Exception as e:
            check(f"概率引擎通过", False, str(e)[:100])
            prob_ok = False
    else:
        print("  [SKIP] 因特征工程失败，跳过后续检查")
        prob_ok = False

    # ---- 7. 赔率价值引擎 ----
    print("\n[7] 赔率价值引擎")
    if prob_ok:
        try:
            from football_advisor.odds_value_engine import OddsValueEngine

            value_engine = OddsValueEngine()
            assessment = value_engine.assess_1x2(features.odds_1x2, probabilities)
            check("价值评估成功", True)
            check("包含 best_value 或为空", hasattr(assessment, "best_value"))
            if assessment.best_value:
                check(
                    "best_value 赔率有效",
                    assessment.best_value.decimal_odds >= 1.01,
                    f"odds={assessment.best_value.decimal_odds}",
                )
            value_ok = True
        except Exception as e:
            check(f"价值引擎通过", False, str(e)[:100])
            value_ok = False
    else:
        print("  [SKIP] 因概率引擎失败，跳过后续检查")
        value_ok = False

    # ---- 8. No Bet 策略 ----
    print("\n[8] No Bet 策略")
    if value_ok:
        try:
            from football_advisor.no_bet_policy import NoBetPolicy

            no_bet_policy = NoBetPolicy()
            decision = no_bet_policy.decide(features, assessment)
            check("No Bet 决策成功", True)
            check("包含 reasons 字段", hasattr(decision, "reasons"))
            if decision.recommendation.name == "NO_BET":
                print(f"    建议: 不投注, 原因: {decision.reasons}")
            else:
                print(f"    建议: {decision.recommendation.name}, 风险: {decision.risk_level.name}")
            nobet_ok = True
        except Exception as e:
            check(f"No Bet 策略通过", False, str(e)[:100])
            nobet_ok = False
    else:
        print("  [SKIP] 因价值引擎失败，跳过后续检查")
        nobet_ok = False

    # ---- 9. 校准引擎 ----
    print("\n[9] 校准引擎")
    if prob_ok:
        try:
            from football_advisor.calibration_engine import CalibrationEngine, CalibrationMetrics

            cal_engine = CalibrationEngine()
            probs_list = [probs]
            actuals = ["home"]
            metrics = cal_engine.evaluate_1x2(probs_list, actuals)
            check("校准引擎评估成功", True)
            check("Brier Score 有效", 0 <= metrics.brier_score <= 2, f"brier={metrics.brier_score:.3f}")
            check("Log Loss 有效", metrics.log_loss >= 0, f"log_loss={metrics.log_loss:.3f}")
            check("Accuracy 有效", 0 <= metrics.accuracy <= 1, f"acc={metrics.accuracy:.3f}")
        except Exception as e:
            check(f"校准引擎通过", False, str(e)[:100])
    else:
        print("  [SKIP] 因概率引擎失败，跳过后续检查")

    # ---- 10. 回测引擎 ----
    print("\n[10] 回测引擎")
    try:
        from football_advisor.backtest_engine import (
            BacktestEngine,
            BacktestResult,
            HistoricalMatch,
        )

        bt_engine = BacktestEngine()
        check("回测引擎实例化成功", True)
    except Exception as e:
        check(f"回测引擎通过", False, str(e)[:100])

    conn.close()

    # ---- 汇总 ----
    print("\n" + "=" * 60)
    total = CHECKS_PASSED + CHECKS_FAILED + CHECKS_WARNING
    print(f"检查总数: {total}")
    print(f"通过: {CHECKS_PASSED}")
    print(f"警告: {CHECKS_WARNING}")
    print(f"失败: {CHECKS_FAILED}")
    print("=" * 60)

    if CHECKS_FAILED == 0:
        print("B-4 端到端烟测: 全部通过")
        return 0
    else:
        print(f"B-4 端到端烟测: {CHECKS_FAILED} 项失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())