from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RiskState(str, Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class VolState(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    ELEVATED = "elevated"
    EXTREME = "extreme"


class UsdBias(str, Enum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class PairRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    CHOPPY = "choppy"


class EventRisk(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ----- Inputs -----


class CalendarEvent(BaseModel):
    time: datetime
    ccy: str
    event: str
    impact: Literal["low", "medium", "high"]


class Headline(BaseModel):
    id: str  # required for citation; classifier must reference these ids
    ts: datetime
    source: str
    title: str
    tickers: list[str] = Field(default_factory=list)


class CBSpeech(BaseModel):
    id: str
    ts: datetime
    speaker: str
    venue: str | None = None
    summary: str


class PairContext(BaseModel):
    atr_14d: float
    atr_pct_of_3m_avg: float
    trend_20d: Literal["up", "down", "flat"]
    last_close: float


class MacroState(BaseModel):
    us_10y: float | None = None
    us_10y_change_1d: float | None = None
    dxy: float | None = None
    dxy_change_1d: float | None = None
    vix: float | None = None
    vix_change_1d: float | None = None
    spx_change_1d: float | None = None


class ClassifierInput(BaseModel):
    as_of: datetime
    lookback_hours: int = 24
    pairs: list[str]
    macro: MacroState
    calendar_next_24h: list[CalendarEvent]
    headlines: list[Headline]
    cb_speeches: list[CBSpeech]
    price_context: dict[str, PairContext]


# ----- Outputs -----


class GlobalRegime(BaseModel):
    risk: RiskState
    volatility: VolState
    usd_bias: UsdBias
    confidence: float = Field(ge=0.0, le=1.0)


class PairRegimeOut(BaseModel):
    regime: PairRegime
    vol_state: VolState
    event_risk_next_8h: EventRisk
    suggested_size_mult: float = Field(ge=0.0, le=1.5)
    suggested_stop_mult: float = Field(ge=0.5, le=3.0)
    trade_allowed: bool
    confidence: float = Field(ge=0.0, le=1.0)


class RegimeOutput(BaseModel):
    as_of: datetime
    valid_until: datetime
    global_: GlobalRegime = Field(alias="global")
    pairs: dict[str, PairRegimeOut]
    rationale: str = Field(max_length=400)
    flags: list[str] = Field(default_factory=list)
    cited_headline_ids: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @field_validator("rationale")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


def conservative_default(as_of: datetime, valid_until: datetime, pairs: list[str]) -> RegimeOutput:
    """Fallback regime when classifier fails or output is stale.

    Trades are blocked; sizes are halved. Strategies that read this still see a
    well-formed object instead of having to special-case None.
    """
    return RegimeOutput(
        as_of=as_of,
        valid_until=valid_until,
        **{"global": GlobalRegime(
            risk=RiskState.NEUTRAL,
            volatility=VolState.ELEVATED,
            usd_bias=UsdBias.NEUTRAL,
            confidence=0.0,
        )},
        pairs={
            p: PairRegimeOut(
                regime=PairRegime.CHOPPY,
                vol_state=VolState.ELEVATED,
                event_risk_next_8h=EventRisk.MEDIUM,
                suggested_size_mult=0.5,
                suggested_stop_mult=1.5,
                trade_allowed=False,
                confidence=0.0,
            )
            for p in pairs
        },
        rationale="Conservative default: classifier unavailable or output stale.",
        flags=["fallback"],
        cited_headline_ids=[],
    )
