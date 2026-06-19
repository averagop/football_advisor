"""验证 PDF staging 管线入库数据。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb

DB_PATH = ROOT / "football_system.db"


def main() -> int:
    conn = duckdb.connect(str(DB_PATH))

    # 统计 staging 表
    print("=" * 60)
    print("STAGING 表统计")
    for table in ["stg_referee_candidates", "stg_player_candidates", "stg_schedule_candidates"]:
        cnt = conn.execute(f"SELECT COUNT(*) FROM staging.{table}").fetchone()[0]
        print(f"  {table}: {cnt} 行")

    # 统计 core 表
    print("\n" + "=" * 60)
    print("CORE 表统计 (PDF 来源)")
    cnt = conn.execute(
        "SELECT COUNT(*) FROM core.dim_player_mapping WHERE system_player_id LIKE 'PDF_%'"
    ).fetchone()[0]
    print(f"  dim_player_mapping (PDF): {cnt} 行")

    cnt = conn.execute(
        "SELECT COUNT(*) FROM core.dim_referee_profile WHERE referee_id LIKE 'REF_%'"
    ).fetchone()[0]
    print(f"  dim_referee_profile (PDF): {cnt} 行")

    # 按球队统计球员
    print("\n" + "=" * 60)
    print("按球队统计球员数 (前 15)")
    for row in conn.execute(
        "SELECT team_id, COUNT(*) as cnt FROM core.dim_player_mapping "
        "WHERE system_player_id LIKE 'PDF_%' "
        "GROUP BY team_id ORDER BY cnt DESC LIMIT 15"
    ).fetchall():
        print(f"  {row[0]}: {row[1]} 人")

    # 样例球员
    print("\n" + "=" * 60)
    print("样例球员 (前 5)")
    for row in conn.execute(
        "SELECT player_standard_name, team_id, primary_position "
        "FROM core.dim_player_mapping WHERE system_player_id LIKE 'PDF_%' LIMIT 5"
    ).fetchall():
        print(f"  {row[0]} ({row[1]}) - {row[2]}")

    # 样例裁判
    print("\n" + "=" * 60)
    print("样例裁判 (前 5)")
    for row in conn.execute(
        "SELECT referee_id, referee_name FROM core.dim_referee_profile "
        "WHERE referee_id LIKE 'REF_%' LIMIT 5"
    ).fetchall():
        print(f"  {row[0]} - {row[1]}")

    # 交叉校验状态
    print("\n" + "=" * 60)
    print("交叉校验状态")
    for row in conn.execute(
        "SELECT cross_validation_status, COUNT(*) FROM staging.stg_player_candidates GROUP BY cross_validation_status"
    ).fetchall():
        print(f"  {row[0]}: {row[1]}")

    for row in conn.execute(
        "SELECT cross_validation_status, COUNT(*) FROM staging.stg_referee_candidates GROUP BY cross_validation_status"
    ).fetchall():
        print(f"  {row[0]}: {row[1]}")

    print("\n" + "=" * 60)
    print("验证完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())