from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AnalysisMode(str, Enum):
    STANDARD = "standard"
    DEEP = "deep"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Recommendation(str, Enum):
    BET = "bet"
    NO_BET = "no_bet"


@dataclass(frozen=True)
class MatchRequest:
    query: str
    mode: AnalysisMode = AnalysisMode.STANDARD
    match_id: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    kickoff_time: datetime | None = None
    market: str = "1x2"


@dataclass(frozen=True)
class TeamFeatures:
    name: str
    elo: float = 1500.0
    attack_strength: float = 1.0
    defense_strength: float = 1.0
    recent_points_per_match: float = 1.4
    home_advantage: float = 0.0
    injury_penalty: float = 0.0


@dataclass(frozen=True)
class MatchFeatures:
    home: TeamFeatures
    away: TeamFeatures
    league_avg_home_goals: float = 1.45
    league_avg_away_goals: float = 1.15
    odds_1x2: dict[str, float] = field(default_factory=dict)
    odds_rqspf: dict[str, float] = field(default_factory=dict)
    odds_correct_score: dict[str, float] = field(default_factory=dict)
    odds_total_goals: dict[str, float] = field(default_factory=dict)
    odds_half_full: dict[str, float] = field(default_factory=dict)
    rqspf_handicap_line: float | None = None
    totals_line: float = 2.5
    capital_flow_volume: float = 0.0
    capital_flow_sharp_ratio: float = 0.0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sources: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbabilityResult:
    home_win: float
    draw: float
    away_win: float
    over_2_5: float
    under_2_5: float
    expected_home_goals: float
    expected_away_goals: float
    most_likely_scores: list[tuple[str, float]]
    score_probabilities: dict[tuple[int, int], float] = field(default_factory=dict)
    total_goals_probabilities: dict[str, float] = field(default_factory=dict)
    half_full_probabilities: dict[str, float] = field(default_factory=dict)

    def outcome_probabilities(self) -> dict[str, float]:
        return {
            "home": self.home_win,
            "draw": self.draw,
            "away": self.away_win,
        }


@dataclass(frozen=True)
class OddsValue:
    outcome: str
    decimal_odds: float
    implied_probability: float
    model_probability: float
    edge: float
    value: bool


@dataclass(frozen=True)
class ValueAssessment:
    values: list[OddsValue]
    best_value: OddsValue | None
    overround: float
    threshold: float


@dataclass(frozen=True)
class PolicyDecision:
    recommendation: Recommendation
    risk_level: RiskLevel
    confidence: float
    reasons: list[str]


@dataclass(frozen=True)
class DataReadinessStatus:
    ready: bool
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_critical: list[str] = field(default_factory=list)
    stale_critical: list[str] = field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "components": self.components,
            "missing_critical": self.missing_critical,
            "stale_critical": self.stale_critical,
        }


@dataclass(frozen=True)
class PredictionBundle:
    request: MatchRequest
    features: MatchFeatures
    probabilities: ProbabilityResult
    value_assessment: ValueAssessment
    policy: PolicyDecision
    news: list[dict[str, Any]]
    secondary_value_assessments: dict[str, ValueAssessment] = field(default_factory=dict)
    readiness: DataReadinessStatus | None = None
    generated_by: str = "external"


@dataclass(frozen=True)
class ReportNarrative:
    """模型仅允许生成的叙述字段，其余事实由代码确定性渲染。"""
    key_factors: str
    main_risks: str
    reasoning_summary: str


# 验收状态枚举
VERIFICATION_STATUS_VALUES = ("INCOMPLETE", "COMPLETE")
FINAL_STATUS_VALUES = ("PRODUCTION_READY", "SAFE_DEGRADED", "EXTERNAL_BLOCKED", "FAILED")


@dataclass(frozen=True)
class ExecutionResult:
    """验收执行结果，强制状态契约。

    契约规则：
    - verification_status=INCOMPLETE 时，final_status 必须为 None
    - verification_status=COMPLETE 时，final_status 必须为四种互斥状态之一
    - 缺失证据必须逐项进入 missing_evidence
    """
    verification_status: str
    final_status: str | None
    missing_evidence: list[str] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 验证 verification_status
        if self.verification_status not in VERIFICATION_STATUS_VALUES:
            raise ValueError(
                f"verification_status 必须为 {VERIFICATION_STATUS_VALUES} 之一，"
                f"当前为: {self.verification_status}"
            )

        # INCOMPLETE 时 final_status 必须为 None
        if self.verification_status == "INCOMPLETE" and self.final_status is not None:
            raise ValueError(
                f"verification_status=INCOMPLETE 时 final_status 必须为 None，"
                f"当前为: {self.final_status}"
            )

        # COMPLETE 时 final_status 不能为 None 且必须为有效状态
        if self.verification_status == "COMPLETE":
            if self.final_status is None:
                raise ValueError(
                    "verification_status=COMPLETE 时 final_status 不能为 None"
                )
            if self.final_status not in FINAL_STATUS_VALUES:
                raise ValueError(
                    f"final_status 必须为 {FINAL_STATUS_VALUES} 之一，"
                    f"当前为: {self.final_status}"
                )
