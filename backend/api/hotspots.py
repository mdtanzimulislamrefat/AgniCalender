"""Map hotspots for a chosen time window."""
import re
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.auth import current_user
from backend.api.cache import cached_json
from backend.api.database import Connection
from backend.config import firms_map_key
from backend.ml.static_sources import classifier
from backend.storage import hotspots

router = APIRouter(prefix='/v1/map', tags=['map'], dependencies=[Depends(current_user)])
WINDOWS = {'7d': 7, '30d': 30}


class Point(BaseModel):
    source: str
    satellite: str
    version: str
    latitude: float
    longitude: float
    quality: str
    confidence_raw: str
    frp_mw: float | None
    daynight: str
    timestamp_utc: str
    # Recent windows only, when a model is trained: likelihood of a non-vegetation heat source.
    static_probability: float | None = None
    likely_static: bool | None = None


class ClassifierInfo(BaseModel):
    model: str
    trained_at_utc: datetime
    train_years: list[int]
    test_year: int
    threshold: float
    test_precision: float
    test_recall: float
    baseline_rule: str
    baseline_precision: float
    baseline_recall: float


class Hotspots(BaseModel):
    kind: Literal['recent', 'archive']
    label: str
    start_utc: datetime
    end_utc: datetime
    fire_type_known: bool
    excluded_other_types: int
    missing_days: list[date]
    counts_by_source: dict[str, int]
    points: list[Point]
    classifier: ClassifierInfo | None = None


class MonthCount(BaseModel):
    month: str
    count: int


class Available(BaseModel):
    recent_enabled: bool
    recent_first_day: date | None
    recent_last_day: date | None
    archive_months: list[MonthCount]


@router.get('/hotspots', response_model=Hotspots)
def map_hotspots(connection: Connection,
                 window: Annotated[Literal['7d', '30d'] | None, Query(description='Recent NRT days')] = None,
                 month: Annotated[str | None, Query(description='Archive month, YYYY-MM')] = None):
    """Vegetation-fire hotspots for one archive month, or recent NRT hotspots (fire type unknown)."""
    if (window is None) == (month is None):
        raise HTTPException(422, 'Give exactly one of window or month')
    if window is not None:
        def produce():
            result = hotspots.recent(connection, WINDOWS[window])
            # NRT data lacks NASA's fire type; flag likely industrial sources with the archive-trained model.
            model = classifier()
            if model is not None:
                result['points'] = model.annotate([dict(p) for p in result['points']])
                result['classifier'] = model.summary()
            return result
        # Cleared by the scheduler whenever new NASA data arrives.
        return cached_json(('recent', window), 600, produce, Hotspots)
    match = re.fullmatch(r'(\d{4})-(\d{2})', month)
    if not match or not 1 <= int(match[2]) <= 12:
        raise HTTPException(422, 'month must be YYYY-MM')
    return cached_json(('archive', 'month', month), 3600,
                       lambda: hotspots.archive_month(connection, int(match[1]), int(match[2])), Hotspots)


@router.get('/available', response_model=Available)
def map_available(connection: Connection):
    """Archive months with data, and the recent-days range."""
    return cached_json(('recent', 'available'), 600,
                       lambda: {'recent_enabled': firms_map_key() is not None} | hotspots.available(connection),
                       Available)
