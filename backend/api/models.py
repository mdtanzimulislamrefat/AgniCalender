from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field


class Metadata(BaseModel):
    report_id: int
    generated_at_utc: datetime
    bbox: list[float]
    region: str | None = None  # country outline applied, if any; None = whole bbox
    timezone: str
    limitations: list[str]
    data_start_utc: datetime | None = None
    data_end_utc: datetime | None = None
    stale: bool


class Summary(Metadata):
    schema_version: int
    counts: dict[str, int]
    counts_by_source: dict[str, int]


class Week(BaseModel):
    source: str
    satellite: str
    version: str
    week_start_utc: date
    detection_count: int
    quality_counts: dict[str, int]
    dates_with_detections: int
    observation_coverage: str
    harmonized: bool


class Calendar(Metadata):
    weeks: list[Week]


class Observation(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timestamp_utc: datetime
    source: str
    instrument: Literal['MODIS', 'VIIRS']
    satellite: str
    version: str
    quality: Literal['low', 'nominal', 'high']
    confidence_raw: str
    frp_mw: float | None
    daynight: str


class ObservationPage(Metadata):
    total: int
    offset: int
    limit: int
    observations: list[Observation]
