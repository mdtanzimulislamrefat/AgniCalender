"""Multi-year burning season from the FIRMS yearly archive."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.auth import current_user
from backend.api.database import Connection
from backend.api.params import BBox, parse_bbox
from backend.storage.archive import season

router = APIRouter(prefix='/v1/archive', tags=['archive'], dependencies=[Depends(current_user)])


class Month(BaseModel):
    month: int
    mean: float | None
    median: float | None
    min: int | None
    max: int | None


class VersionSpan(BaseModel):
    version: str
    first_year: int
    last_year: int


class Product(BaseModel):
    source: str
    versions: list[VersionSpan]
    complete_years: list[int]
    partial_years: list[int]
    unavailable_years: list[int]
    peak_month: int | None
    months: list[Month]
    by_year: dict[str, list[int]]
    other_types: dict[str, int]


class Season(BaseModel):
    region: str
    fire_types: list[str]
    timezone: str
    bbox: list[float] | None
    products: list[Product]
    limitations: list[str]


@router.get('/season', response_model=Season)
def burning_season(connection: Connection, bbox: BBox = None,
                   types: Annotated[Literal['vegetation', 'all'], Query(
                       description='vegetation = FIRMS type 0 only; all = every type')] = 'vegetation',
                   region: Annotated[str, Query(max_length=80)] = 'Bangladesh'):
    """Monthly hotspot counts per satellite product: every year, plus mean/median/min/max over complete years."""
    result = season(connection, region, (0,) if types == 'vegetation' else (0, 1, 2, 3), parse_bbox(bbox))
    if not result['products']:
        raise HTTPException(503, 'No archive imported. Run: python -m backend.processing.archive')
    return result
