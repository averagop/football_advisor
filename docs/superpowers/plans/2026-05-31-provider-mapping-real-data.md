# Provider Mapping 与真实结构化数据接入实施计划

> **给执行代理：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项执行。本计划使用复选框语法跟踪进度。

**目标：** 建立真实 provider 数据进入 DuckDB `core.*` 前的最小可信映射、同步和 No Bet 审计闭环。

**架构：** 新增一个独立的 provider mapping 导入器，只负责把本地 CSV 映射字典写入 `core.dim_provider_*_mapping`。现有 `FootballDataClient` 与 `ApiFootballClient` 继续作为结构化 provider 边界，API-Football 在真实 token 路径下改为调用独立赔率和统计端点，不再要求 fixture 响应内嵌 mock 字段。`DataSyncCoordinator` 只汇总 provider 审计结果，不引入预测或实体猜测逻辑。

**技术栈：** Python 3.14、DuckDB、unittest、FastAPI 后端现有配置体系。

---

## 文件结构

- 新建 `football_advisor/provider_mapping_importer.py`：读取 `provider_league_mappings.csv` 和 `provider_team_mappings.csv`，校验内部 ID 是否存在，写入 provider mapping 表并返回审计计数。
- 新建 `tests/test_provider_mapping_importer.py`：覆盖正常导入、内部 ID 缺失跳过、重复 provider key 覆盖、空目录明确报错。
- 修改 `football_advisor/api_football_client.py`：真实 token 路径下按 fixture id 调用赔率和统计独立端点，解析后写入 staging 与 core，并返回缺失市场/统计审计。
- 修改 `football_advisor/sync.py`：把 API-Football 返回的 `missing_market_stats_count`、`provider_failures` 等字段传入 `SyncResult.details`。
- 修改 `tests/test_api_football_sync.py`：新增真实形态 fixture + 独立赔率/统计 fake 响应测试，覆盖缺赔率/统计时的状态。
- 修改 `tests/test_external_sync.py`：复用新 mapping 导入器，确认 `football-data.org` 仍通过显式 mapping 合并。
- 修改 `docs/PROGRESS.md`：按项目规则记录完成内容、验证、已知限制和下一步。

---

### 任务 1：Provider Mapping 导入器

**文件：**
- 新建：`football_advisor/provider_mapping_importer.py`
- 新建：`tests/test_provider_mapping_importer.py`

- [ ] **步骤 1：写失败测试**

在 `tests/test_provider_mapping_importer.py` 写入以下测试骨架：

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from football_advisor.db_schema import apply_schema
from football_advisor.provider_mapping_importer import import_provider_mappings


class ProviderMappingImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.mapping_dir = self.root / "provider_mappings"
        self.mapping_dir.mkdir()
        self.connection = duckdb.connect(":memory:")
        apply_schema(self.connection)
        self.connection.execute("""
            INSERT INTO core.dim_league_mapping (system_league_id, league_standard_name)
            VALUES ('SYS_EPL', 'English Premier League');

            INSERT INTO core.dim_team_mapping (system_team_id, team_standard_name)
            VALUES
                ('SYS_ARS', 'Arsenal'),
                ('SYS_CHE', 'Chelsea');
        """)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_imports_valid_provider_mappings(self):
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n"
            "API-Football,39,SYS_EPL,Premier League\n",
            encoding="utf-8",
        )
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,42,SYS_ARS,Arsenal\n"
            "API-Football,49,SYS_CHE,Chelsea\n",
            encoding="utf-8",
        )

        result = import_provider_mappings(self.connection, self.mapping_dir)

        self.assertEqual(result.league_inserted, 1)
        self.assertEqual(result.team_inserted, 2)
        self.assertEqual(result.league_skipped, 0)
        self.assertEqual(result.team_skipped, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT system_team_id FROM core.dim_provider_team_mapping "
                "WHERE provider_name = 'API-Football' AND provider_team_id = '42'"
            ).fetchone()[0],
            "SYS_ARS",
        )

    def test_skips_rows_with_missing_internal_ids(self):
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n"
            "API-Football,39,SYS_UNKNOWN,Premier League\n",
            encoding="utf-8",
        )
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,42,SYS_UNKNOWN,Arsenal\n",
            encoding="utf-8",
        )

        result = import_provider_mappings(self.connection, self.mapping_dir)

        self.assertEqual(result.league_inserted, 0)
        self.assertEqual(result.team_inserted, 0)
        self.assertEqual(result.league_skipped, 1)
        self.assertEqual(result.team_skipped, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM core.dim_provider_team_mapping"
            ).fetchone()[0],
            0,
        )

    def test_duplicate_provider_key_updates_existing_mapping(self):
        (self.mapping_dir / "provider_league_mappings.csv").write_text(
            "provider_name,provider_league_id,system_league_id,provider_league_name\n",
            encoding="utf-8",
        )
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,42,SYS_ARS,Old Arsenal\n",
            encoding="utf-8",
        )
        import_provider_mappings(self.connection, self.mapping_dir)
        (self.mapping_dir / "provider_team_mappings.csv").write_text(
            "provider_name,provider_team_id,system_team_id,provider_team_name\n"
            "API-Football,42,SYS_ARS,Arsenal FC\n",
            encoding="utf-8",
        )

        result = import_provider_mappings(self.connection, self.mapping_dir)

        self.assertEqual(result.team_inserted, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT provider_team_name FROM core.dim_provider_team_mapping "
                "WHERE provider_name = 'API-Football' AND provider_team_id = '42'"
            ).fetchone()[0],
            "Arsenal FC",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_provider_mapping_importer -v
```

预期：失败，错误包含 `ModuleNotFoundError` 或无法导入 `football_advisor.provider_mapping_importer`。

- [ ] **步骤 3：实现最小导入器**

新建 `football_advisor/provider_mapping_importer.py`：

```python
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderMappingImportResult:
    league_inserted: int = 0
    league_skipped: int = 0
    team_inserted: int = 0
    team_skipped: int = 0

    def as_details(self) -> dict[str, str]:
        return {
            "provider_league_mapping_inserted": str(self.league_inserted),
            "provider_league_mapping_skipped": str(self.league_skipped),
            "provider_team_mapping_inserted": str(self.team_inserted),
            "provider_team_mapping_skipped": str(self.team_skipped),
        }


def import_provider_mappings(
    connection: Any,
    mapping_dir: str | Path,
) -> ProviderMappingImportResult:
    root = Path(mapping_dir)
    league_rows = _read_csv(
        root / "provider_league_mappings.csv",
        ["provider_name", "provider_league_id", "system_league_id", "provider_league_name"],
    )
    team_rows = _read_csv(
        root / "provider_team_mappings.csv",
        ["provider_name", "provider_team_id", "system_team_id", "provider_team_name"],
    )

    league_inserted, league_skipped = _upsert_league_mappings(connection, league_rows)
    team_inserted, team_skipped = _upsert_team_mappings(connection, team_rows)
    return ProviderMappingImportResult(
        league_inserted=league_inserted,
        league_skipped=league_skipped,
        team_inserted=team_inserted,
        team_skipped=team_skipped,
    )


def _read_csv(path: Path, required_fields: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in required_fields if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name} missing required fields: {', '.join(missing)}")
        return [
            {field: (row.get(field) or "").strip() for field in required_fields}
            for row in reader
        ]


def _upsert_league_mappings(
    connection: Any,
    rows: list[dict[str, str]],
) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        if not row["provider_name"] or not row["provider_league_id"] or not row["system_league_id"]:
            skipped += 1
            continue
        exists = connection.execute(
            "SELECT 1 FROM core.dim_league_mapping WHERE system_league_id = ?",
            [row["system_league_id"]],
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        connection.execute(
            """
            INSERT INTO core.dim_provider_league_mapping (
                provider_name, provider_league_id, system_league_id,
                provider_league_name, updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (provider_name, provider_league_id) DO UPDATE SET
                system_league_id = excluded.system_league_id,
                provider_league_name = excluded.provider_league_name,
                updated_at = excluded.updated_at
            """,
            [
                row["provider_name"],
                row["provider_league_id"],
                row["system_league_id"],
                row["provider_league_name"],
            ],
        )
        inserted += 1
    return inserted, skipped


def _upsert_team_mappings(
    connection: Any,
    rows: list[dict[str, str]],
) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        if not row["provider_name"] or not row["provider_team_id"] or not row["system_team_id"]:
            skipped += 1
            continue
        exists = connection.execute(
            "SELECT 1 FROM core.dim_team_mapping WHERE system_team_id = ?",
            [row["system_team_id"]],
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        connection.execute(
            """
            INSERT INTO core.dim_provider_team_mapping (
                provider_name, provider_team_id, system_team_id,
                provider_team_name, updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (provider_name, provider_team_id) DO UPDATE SET
                system_team_id = excluded.system_team_id,
                provider_team_name = excluded.provider_team_name,
                updated_at = excluded.updated_at
            """,
            [
                row["provider_name"],
                row["provider_team_id"],
                row["system_team_id"],
                row["provider_team_name"],
            ],
        )
        inserted += 1
    return inserted, skipped
```

- [ ] **步骤 4：运行测试确认通过**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_provider_mapping_importer -v
```

预期：`3` 个测试通过。

- [ ] **步骤 5：提交**

运行：

```powershell
git add football_advisor/provider_mapping_importer.py tests/test_provider_mapping_importer.py
git commit -m "feat: add provider mapping importer"
```

---

### 任务 2：API-Football 真实赔率与统计端点解析

**文件：**
- 修改：`football_advisor/api_football_client.py`
- 修改：`tests/test_api_football_sync.py`

- [ ] **步骤 1：写真实端点 fake 测试**

在 `tests/test_api_football_sync.py` 的 `ApiFootballSyncTests` 中新增：

```python
    def test_real_api_football_fetches_independent_odds_and_stats_endpoints(self):
        class RealEndpointApiFootballClient(ApiFootballClient):
            def _http_get(self, path):
                if path.startswith("/fixtures?"):
                    return {
                        "response": [
                            {
                                "fixture": {"id": 888884, "date": "2026-05-01T12:00:00Z"},
                                "teams": {
                                    "home": {"id": 42, "name": "Arsenal"},
                                    "away": {"id": 49, "name": "Chelsea"},
                                },
                            }
                        ]
                    }
                if path == "/odds?fixture=888884":
                    return {
                        "response": [
                            {
                                "bookmakers": [
                                    {
                                        "name": "API Book",
                                        "bets": [
                                            {
                                                "name": "Match Winner",
                                                "values": [
                                                    {"value": "Home", "odd": "2.10"},
                                                    {"value": "Draw", "odd": "3.40"},
                                                    {"value": "Away", "odd": "3.50"},
                                                ],
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                if path == "/fixtures/statistics?fixture=888884":
                    return {
                        "response": [
                            {
                                "team": {"id": 42},
                                "statistics": [
                                    {"type": "Expected Goals", "value": "1.8"},
                                    {"type": "Shots on Goal", "value": 6},
                                ],
                            },
                            {
                                "team": {"id": 49},
                                "statistics": [
                                    {"type": "Expected Goals", "value": "1.1"},
                                    {"type": "Shots on Goal", "value": 3},
                                ],
                            },
                        ]
                    }
                return {"response": []}

        client = RealEndpointApiFootballClient(api_token="real_token")

        res = client.fetch_match_data(
            self.connection,
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            "Arsenal",
            "Chelsea",
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["odds_count"], 1)
        self.assertEqual(res["stats_count"], 2)
        self.assertEqual(res["missing_market_stats_count"], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT bookmaker_name, home_odds, draw_odds, away_odds "
                "FROM core.fact_odds_capital_flow WHERE match_id = 'M_AF_888884'"
            ).fetchall(),
            [("API Book", 2.1, 3.4, 3.5)],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT team_id, rolling_xg_for FROM core.fact_team_rolling_stats "
                "ORDER BY team_id"
            ).fetchall(),
            [("SYS_ARS", 1.8), ("SYS_CHE", 1.1)],
        )
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_api_football_sync.ApiFootballSyncTests.test_real_api_football_fetches_independent_odds_and_stats_endpoints -v
```

预期：失败，当前真实 token 路径会返回 `fixture_matched_no_market_stats`。

- [ ] **步骤 3：实现真实端点解析**

在 `football_advisor/api_football_client.py` 中增加真实端点流程。保留 mock 路径 `_process_mocked_response`，真实路径新增以下方法并在 `fetch_match_data()` 中调用：

```python
        if self._uses_mock_response():
            return self._process_mocked_response(connection, fixtures)

        fixture = self._select_fixture(fixtures, home_team, away_team)
        if fixture is None:
            return {
                "status": "match_not_found",
                "odds_count": 0,
                "stats_count": 0,
                "staged_count": 0,
                "merged_count": 0,
                "skipped_unmapped_count": 0,
                "missing_market_stats_count": 0,
            }

        return self._process_real_fixture_response(connection, fixture)
```

新增方法：

```python
    def _process_real_fixture_response(
        self,
        connection: Any,
        fixture: dict[str, Any],
    ) -> dict[str, Any]:
        fixture_id = str(fixture["fixture"]["id"])
        odds_payload = self._http_get(f"/odds?fixture={fixture_id}") or {"response": []}
        stats_payload = self._http_get(f"/fixtures/statistics?fixture={fixture_id}") or {"response": []}
        stg_odds_rows = self._extract_real_odds_rows(fixture_id, odds_payload)
        stg_stats_rows = self._extract_real_stats_rows(fixture_id, stats_payload)
        if not stg_odds_rows and not stg_stats_rows:
            return {
                "status": "fixture_matched_no_market_stats",
                "odds_count": 0,
                "stats_count": 0,
                "staged_count": 0,
                "merged_count": 0,
                "skipped_unmapped_count": 0,
                "missing_market_stats_count": 1,
            }
        return self._sink_rows(connection, stg_odds_rows, stg_stats_rows)

    def _extract_real_odds_rows(
        self,
        fixture_id: str,
        payload: dict[str, Any],
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for item in payload.get("response", []):
            for bookmaker in item.get("bookmakers", []):
                for bet in bookmaker.get("bets", []):
                    if str(bet.get("name", "")).casefold() not in {"match winner", "1x2"}:
                        continue
                    prices = {str(v.get("value", "")).casefold(): v.get("odd") for v in bet.get("values", [])}
                    home = _to_float(prices.get("home"))
                    draw = _to_float(prices.get("draw"))
                    away = _to_float(prices.get("away"))
                    if home is None or draw is None or away is None:
                        continue
                    rows.append(
                        (
                            fixture_id,
                            "1X2",
                            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            bookmaker.get("name") or self.PROVIDER_NAME,
                            home,
                            draw,
                            away,
                        )
                    )
        return rows

    def _extract_real_stats_rows(
        self,
        fixture_id: str,
        payload: dict[str, Any],
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for item in payload.get("response", []):
            provider_team_id = str(item.get("team", {}).get("id", ""))
            stats = {
                str(stat.get("type", "")).casefold(): stat.get("value")
                for stat in item.get("statistics", [])
            }
            xg_for = _to_float(stats.get("expected goals"))
            shots_on_goal = _to_float(stats.get("shots on goal"))
            rows.append(
                (
                    fixture_id,
                    provider_team_id,
                    None,
                    None,
                    xg_for,
                    None,
                    self.PROVIDER_NAME,
                    shots_on_goal,
                )
            )
        return rows
```

把当前 `_process_mocked_response()` 的 staging 和 merge 逻辑抽成 `_sink_rows()`，并修正 `fact_team_rolling_stats` 插入列，不能写不存在的 `rolling_points`：

```python
    def _sink_rows(
        self,
        connection: Any,
        stg_odds_rows: list[tuple[Any, ...]],
        stg_stats_rows: list[tuple[Any, ...]],
    ) -> dict[str, Any]:
        if stg_odds_rows:
            connection.executemany("""
                INSERT INTO staging.stg_odds (
                    provider_match_id, odds_type, snapshot_time, bookmaker_name,
                    home_odds, draw_odds, away_odds, source_provider
                ) VALUES (?, ?, CAST(? AS TIMESTAMP), ?, ?, ?, ?, ?)
            """, [(*row, self.PROVIDER_NAME) for row in stg_odds_rows])

        if stg_stats_rows:
            connection.executemany("""
                INSERT INTO staging.stg_team_stats (
                    provider_match_id, provider_team_id,
                    goals_for, goals_against, xg_for, xg_against, source_provider
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [row[:7] for row in stg_stats_rows])

        provider_match_ids = sorted({row[0] for row in stg_stats_rows})
        staged_count = len(stg_stats_rows)
        eligible_stats_count = 0
        stats_filter_sql = ""
        stats_filter_params: list[Any] = [self.PROVIDER_NAME]
        if provider_match_ids:
            placeholders = ",".join("?" for _ in provider_match_ids)
            stats_filter_sql = f"AND s.provider_match_id IN ({placeholders})"
            stats_filter_params.extend(provider_match_ids)
            eligible_stats_count = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM staging.stg_team_stats s
                    JOIN core.dim_provider_team_mapping m
                      ON s.source_provider = m.provider_name
                     AND s.provider_team_id = m.provider_team_id
                    WHERE s.source_provider = ?
                    {stats_filter_sql}
                    """,
                    stats_filter_params,
                ).fetchone()[0]
            )

        connection.execute("""
            INSERT INTO core.fact_odds_capital_flow (
                match_id, snapshot_time, odds_type, bookmaker_name,
                home_odds, draw_odds, away_odds, source_provider
            )
            SELECT
                'M_AF_' || provider_match_id,
                snapshot_time,
                odds_type,
                bookmaker_name,
                home_odds,
                draw_odds,
                away_odds,
                source_provider
            FROM staging.stg_odds
            WHERE source_provider = 'API-Football'
            ON CONFLICT (match_id, snapshot_time, odds_type, bookmaker_name) DO NOTHING
        """)

        if provider_match_ids:
            connection.execute(f"""
                INSERT INTO core.fact_team_rolling_stats (
                    match_id, team_id, record_date, source_provider,
                    rolling_goals_for, rolling_goals_against,
                    rolling_xg_for, rolling_xg_against, data_quality_flag
                )
                SELECT
                    'M_AF_' || s.provider_match_id,
                    m.system_team_id,
                    CURRENT_TIMESTAMP,
                    s.source_provider,
                    s.goals_for,
                    s.goals_against,
                    s.xg_for,
                    s.xg_against,
                    'API_FOOTBALL_SINGLE_MATCH_STATS'
                FROM staging.stg_team_stats s
                JOIN core.dim_provider_team_mapping m
                  ON s.source_provider = m.provider_name
                 AND s.provider_team_id = m.provider_team_id
                WHERE s.source_provider = ?
                {stats_filter_sql}
                ON CONFLICT (match_id, team_id, record_date, source_provider) DO UPDATE SET
                    rolling_goals_for = excluded.rolling_goals_for,
                    rolling_goals_against = excluded.rolling_goals_against,
                    rolling_xg_for = excluded.rolling_xg_for,
                    rolling_xg_against = excluded.rolling_xg_against,
                    data_quality_flag = excluded.data_quality_flag
            """, stats_filter_params)

        return {
            "status": "success",
            "odds_count": len(stg_odds_rows),
            "stats_count": len(stg_stats_rows),
            "staged_count": staged_count,
            "merged_count": eligible_stats_count,
            "skipped_unmapped_count": max(0, staged_count - eligible_stats_count),
            "missing_market_stats_count": 0 if stg_odds_rows or stg_stats_rows else 1,
        }
```

新增转换函数：

```python
def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
```

- [ ] **步骤 4：运行目标测试**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_api_football_sync -v
```

预期：API-Football 同步测试全部通过。

- [ ] **步骤 5：提交**

运行：

```powershell
git add football_advisor/api_football_client.py tests/test_api_football_sync.py
git commit -m "feat: sync api football market stats endpoints"
```

---

### 任务 3：同步审计字段传递与 No Bet 回归

**文件：**
- 修改：`football_advisor/sync.py`
- 修改：`tests/test_pipeline.py`

- [ ] **步骤 1：写同步审计传递测试**

在 `tests/test_pipeline.py` 中新增：

```python
    def test_missing_market_stats_sync_status_is_carried_into_no_bet_policy(self):
        class MissingMarketStatsSync:
            def sync_before_prediction(self, request: MatchRequest):
                return [
                    SimpleNamespace(
                        source="API-Football",
                        status="fixture_matched_no_market_stats",
                        details={"missing_market_stats_count": "1"},
                    )
                ]

        class FreshCompleteFeatureBuilder:
            def build(self, request: MatchRequest, sync_results):
                return MatchFeatures(
                    home=TeamFeatures(name="Arsenal", elo=1620, attack_strength=1.12),
                    away=TeamFeatures(name="Chelsea", elo=1570, attack_strength=1.01),
                    odds_1x2={"home": 2.60, "draw": 3.25, "away": 3.10},
                    updated_at=datetime.now(timezone.utc),
                    sources=["TEST_DUCKDB"],
                    context={
                        "feature_source": "duckdb_view",
                        "match_id": "MISSING_MARKET_STATS",
                        "news_source_count": 2,
                    },
                )

        pipeline = PredictionPipeline(
            sync=MissingMarketStatsSync(),
            news_tool=FakeNewsTool(),
            feature_builder=FreshCompleteFeatureBuilder(),
            llm_router=FakeLLMRouter(),
        )

        report = pipeline.predict(
            MatchRequest(query="Arsenal vs Chelsea", match_id="MISSING_MARKET_STATS")
        )

        self.assertIn("Sync result did not satisfy pre-prediction requirements.", report)
        self.assertIn("API-Football:fixture_matched_no_market_stats", report)
```

- [ ] **步骤 2：运行测试确认当前行为**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_pipeline.PipelineTests.test_missing_market_stats_sync_status_is_carried_into_no_bet_policy -v
```

预期：通过。如果失败，先修正 `_is_acceptable_sync_status()` 或报告上下文传递，不改 No Bet 门槛。

- [ ] **步骤 3：扩展 `DataSyncCoordinator` details**

在 `football_advisor/sync.py` 的 API-Football 成功返回块中，把 details 改为包含缺失市场统计计数：

```python
                    details={
                        "query": request.query,
                        "odds_count": str(res.get("odds_count", 0)),
                        "stats_count": str(res.get("stats_count", 0)),
                        "staged_count": str(res.get("staged_count", 0)),
                        "merged_count": str(res.get("merged_count", 0)),
                        "skipped_unmapped_count": str(
                            res.get("skipped_unmapped_count", 0)
                        ),
                        "missing_market_stats_count": str(
                            res.get("missing_market_stats_count", 0)
                        ),
                    },
```

如果 `res.get("provider_failures")` 存在，也加入：

```python
                        **(
                            {"provider_failures": str(res.get("provider_failures"))}
                            if res.get("provider_failures")
                            else {}
                        ),
```

- [ ] **步骤 4：运行相关测试**

运行：

```powershell
.\.runtime\python\python.exe -m unittest tests.test_pipeline tests.test_api_football_sync tests.test_external_sync -v
```

预期：相关测试全部通过。

- [ ] **步骤 5：提交**

运行：

```powershell
git add football_advisor/sync.py tests/test_pipeline.py
git commit -m "fix: expose structured sync audit details"
```

---

### 任务 4：文档进度与全量验证

**文件：**
- 修改：`docs/PROGRESS.md`

- [ ] **步骤 1：运行全量测试**

运行：

```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
```

预期：所有测试通过。

- [ ] **步骤 2：运行差异检查**

运行：

```powershell
git diff --check
```

预期：无错误；Windows CRLF warning 可接受。

- [ ] **步骤 3：更新 `docs/PROGRESS.md`**

在文件顶部 `## 2026-05-31` 下追加本轮记录：

```markdown
## 2026-05-31

### 已完成（Provider mapping 与真实结构化数据接入最小闭环）

- 新增 provider mapping 字典导入器，支持本地 CSV 写入 `core.dim_provider_league_mapping` 与 `core.dim_provider_team_mapping`。
- 导入器会校验内部联赛和球队 ID 是否存在，缺失时跳过并计数，不自动创建实体。
- API-Football 真实 token 路径改为 fixture 命中后调用独立赔率和统计端点，不再要求 fixture 响应内嵌 mock 结构。
- 同步审计补充 `missing_market_stats_count`，缺赔率、缺统计或未映射实体继续进入 No Bet 数据质量门禁。

### 验证（Provider mapping 与真实结构化数据接入最小闭环）

- 已执行 `.\.runtime\python\python.exe -m unittest tests.test_provider_mapping_importer tests.test_api_football_sync tests.test_external_sync tests.test_pipeline -v`。
- 已执行 `.\.runtime\python\python.exe -m unittest discover -s tests -v`。
- 已执行 `git diff --check`。

### 已知限制（Provider mapping 与真实结构化数据接入最小闭环）

- 本轮测试使用 fake provider 响应，未调用真实远端 API。
- API-Football 伤停和阵容端点仍未落地，只完成赔率和统计端点的最小闭环。
- 真实 provider mapping CSV 内容需要后续由本地配置、人工审核或可信数据源维护。

### 下一步建议（Provider mapping 与真实结构化数据接入最小闭环）

- 补充真实 `data/provider_mappings/` CSV 样本并用本地 DuckDB 做一次手动导入。
- 在配置真实 API token 后，对一场未来比赛执行端到端同步烟测。
- 继续接入 API-Football 伤停和阵容端点，并保持缺失即 No Bet 的门禁策略。
```

如果全量测试数量与实际输出不同，不写具体数量，只写命令已执行和结果。

- [ ] **步骤 4：提交**

运行：

```powershell
git add docs/PROGRESS.md
git commit -m "docs: record provider mapping sync progress"
```

---

## 自检记录

- 规格覆盖：本计划覆盖 provider mapping 导入、真实结构化 provider 接入、同步审计、No Bet 回归和进度文档。
- 范围控制：不实现真实交易所客户端、新闻入库、模型调参、OpenWebUI 自动注册或大规模实体消歧。
- 测试策略：所有外部 API 测试均使用 fake 响应，不依赖真实 API Key，不输出敏感信息。
- 风险点：API-Football 真实响应字段可能与 fake 样本存在差异；实现时必须保持缺字段跳过并审计，不得猜测补齐。
