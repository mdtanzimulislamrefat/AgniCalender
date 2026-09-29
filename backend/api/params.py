"""Query parameters shared by several routers."""
from typing import Annotated

from fastapi import HTTPException, Query

BBox = Annotated[str | None, Query(max_length=100, description='west,south,east,north in degrees')]


def parse_bbox(text):
    if text is None:
        return None
    try:
        west, south, east, north = map(float, text.split(','))
    except ValueError:
        raise HTTPException(422, 'bbox must be west,south,east,north') from None
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(422, 'bbox needs west < east and south < north within valid degrees')
    return west, south, east, north
