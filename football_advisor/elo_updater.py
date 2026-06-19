from __future__ import annotations

from typing import Any


class EloUpdater:
    """Updates team Elo ratings dynamically based on match results."""

    def __init__(
        self,
        k_factor: float = 20.0,
        elo_scale: float = 400.0,
        home_advantage: float = 65.0,
    ) -> None:
        self.k_factor = k_factor
        self.elo_scale = elo_scale
        self.home_advantage = home_advantage

    def update_unprocessed_matches(self, connection: Any) -> int:
        """Processes all FINISHED matches that haven't been scored yet, in chronological order."""
        # Find matches not yet in fact_team_elo_history
        unprocessed = connection.execute("""
            SELECT
                s.match_id,
                s.match_time,
                s.home_team_id,
                s.away_team_id,
                s.home_score,
                s.away_score
            FROM core.fact_match_schedule s
            LEFT JOIN core.fact_team_elo_history e
              ON s.match_id = e.match_id
            WHERE s.status = 'FINISHED'
              AND s.home_score IS NOT NULL
              AND s.away_score IS NOT NULL
              AND e.match_id IS NULL
            ORDER BY s.match_time ASC
        """).fetchall()

        if not unprocessed:
            return 0

        updated_count = 0
        for row in unprocessed:
            match_id = row[0]
            match_time = row[1]
            home_team_id = row[2]
            away_team_id = row[3]
            home_score = row[4]
            away_score = row[5]

            # Get current elos
            home_elo = self._get_latest_elo(connection, home_team_id)
            away_elo = self._get_latest_elo(connection, away_team_id)

            # Calculate new elos
            new_home_elo, new_away_elo = self._calculate_new_elos(
                home_elo, away_elo, home_score, away_score
            )

            # Insert history
            connection.execute("""
                INSERT INTO core.fact_team_elo_history (
                    team_id, match_id, record_date,
                    elo_rating_before, elo_rating_after, updated_at
                ) VALUES
                    (?, ?, CAST(? AS TIMESTAMP) + INTERVAL 2 HOUR, ?, ?, CURRENT_TIMESTAMP),
                    (?, ?, CAST(? AS TIMESTAMP) + INTERVAL 2 HOUR, ?, ?, CURRENT_TIMESTAMP)
            """, (
                home_team_id, match_id, match_time, home_elo, new_home_elo,
                away_team_id, match_id, match_time, away_elo, new_away_elo
            ))
            updated_count += 1

        return updated_count

    def _get_latest_elo(self, connection: Any, team_id: str) -> float:
        # Check history first
        row = connection.execute("""
            SELECT elo_rating_after
            FROM core.fact_team_elo_history
            WHERE team_id = ?
            ORDER BY record_date DESC, updated_at DESC
            LIMIT 1
        """, (team_id,)).fetchone()

        if row:
            return float(row[0])

        # Fallback to dim_team_mapping
        base_row = connection.execute("""
            SELECT COALESCE(elo_rating_base, 1500.0)
            FROM core.dim_team_mapping
            WHERE system_team_id = ?
        """, (team_id,)).fetchone()
        
        if base_row:
            return float(base_row[0])
            
        return 1500.0

    def _calculate_new_elos(
        self,
        home_elo: float,
        away_elo: float,
        home_score: int,
        away_score: int,
    ) -> tuple[float, float]:
        home_expected = 1.0 / (
            1.0 + 10.0 ** ((away_elo - (home_elo + self.home_advantage)) / self.elo_scale)
        )
        away_expected = 1.0 - home_expected

        if home_score > away_score:
            home_actual = 1.0
            away_actual = 0.0
        elif home_score < away_score:
            home_actual = 0.0
            away_actual = 1.0
        else:
            home_actual = 0.5
            away_actual = 0.5

        new_home_elo = home_elo + self.k_factor * (home_actual - home_expected)
        new_away_elo = away_elo + self.k_factor * (away_actual - away_expected)

        return round(new_home_elo, 4), round(new_away_elo, 4)
