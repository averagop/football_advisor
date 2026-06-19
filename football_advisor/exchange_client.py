import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any



@dataclass(frozen=True)
class ExchangeCapitalFlow:
    match_id: str
    snapshot_time: datetime
    bookmaker_name: str
    matched_volume: float
    home_odds: float | None = None
    draw_odds: float | None = None
    away_odds: float | None = None
    sharp_money_ratio: float | None = None

class ExchangeClient(ABC):
    @abstractmethod
    def fetch_capital_flow(self, conn: Any, query: str) -> dict[str, Any]:
        pass

class MockBetfairClient(ExchangeClient):
    """
    Mock implementation of a betting exchange client (e.g. Betfair API).
    In a real system, this would authenticate and fetch the traded volume
    and price data for a market (e.g., Match Odds).
    """
    def __init__(self, should_fail: bool = False, delay_seconds: float = 0.0):
        self.should_fail = should_fail
        self.delay_seconds = delay_seconds

    def fetch_capital_flow(self, conn: Any, query: str) -> dict[str, Any]:
        if self.should_fail:
            return {"status": "error", "message": "Mock API Failure", "inserted_records": 0}

        match_id = f"M_BF_{abs(hash(query)) % 10000}" if "M_AF_" not in query and "SQLITE_MATCH_" not in query else query
        
        # Simulate generating mock exchange data for the match
        snapshot_time = datetime.now(timezone.utc)
        matched_volume = 1500000.0 # 1.5M traded
        home_odds = 2.15
        draw_odds = 3.40
        away_odds = 3.75
        sharp_money_ratio = 0.65 # 65% money on the sharp side
        
        conn.execute(
            """
            INSERT INTO core.fact_odds_capital_flow (
                match_id, snapshot_time, odds_type, bookmaker_name,
                home_odds, draw_odds, away_odds, sharp_money_ratio, matched_volume
            )
            VALUES (?, ?, '1X2', 'Betfair', ?, ?, ?, ?, ?)
            ON CONFLICT (match_id, snapshot_time, odds_type, bookmaker_name) DO UPDATE SET
                home_odds = excluded.home_odds,
                draw_odds = excluded.draw_odds,
                away_odds = excluded.away_odds,
                sharp_money_ratio = excluded.sharp_money_ratio,
                matched_volume = excluded.matched_volume
            """,
            (match_id, snapshot_time, home_odds, draw_odds, away_odds, sharp_money_ratio, matched_volume)
        )
        
        return {
            "status": "ok",
            "source": "betfair_mock",
            "inserted_records": 1,
            "match_id": match_id
        }
