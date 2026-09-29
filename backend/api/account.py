"""Signed-in user's profile, password and saved areas."""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.api.auth import Profile, Session, UserID, new_session, normalize_name, unauthorized
from backend.api.database import Connection
from backend.storage import areas, users

router = APIRouter(prefix='/v1/me', tags=['account'])


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)

    @field_validator('display_name')
    @classmethod
    def clean_name(cls, value):
        return normalize_name(value)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def coordinate(limit):
    return Field(ge=-limit, le=limit, allow_inf_nan=False)


class AreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    west: float = coordinate(180)
    south: float = coordinate(90)
    east: float = coordinate(180)
    north: float = coordinate(90)

    @field_validator('name')
    @classmethod
    def strip_name(cls, value):
        if not value.strip():
            raise ValueError('Name is required')
        return value.strip()

    @model_validator(mode='after')
    def ordered(self):
        if not (self.west < self.east and self.south < self.north):
            raise ValueError('Need west < east and south < north; antimeridian areas are unsupported')
        return self


class Area(AreaIn):
    id: int
    created_at: datetime
    updated_at: datetime


def profile(connection, user_id):
    row = users.get_profile(connection, user_id)
    if row is None:
        raise unauthorized('Account no longer available')
    return row


@router.get('', response_model=Profile)
def me(connection: Connection, user_id: UserID):
    return profile(connection, user_id)


@router.patch('', response_model=Profile)
def update_me(connection: Connection, user_id: UserID, body: ProfileUpdate):
    profile(connection, user_id)
    return users.update_profile(connection, user_id, body.display_name)


@router.post('/password', response_model=Session)
def change_password(connection: Connection, user_id: UserID, body: PasswordChange, request: Request):
    """Ends every session, then signs this device in again."""
    if not users.change_password(connection, user_id, body.current_password, body.new_password):
        connection.rollback()
        raise HTTPException(403, 'Current password is incorrect')
    return new_session(connection, profile(connection, user_id), request)


@router.get('/areas', response_model=list[Area])
def list_areas(connection: Connection, user_id: UserID):
    return areas.list_areas(connection, user_id)


@router.post('/areas', response_model=Area, status_code=201)
def create_area(connection: Connection, user_id: UserID, body: AreaIn):
    try:
        row = areas.create_area(connection, user_id, body.model_dump())
    except areas.AreaLimit:
        raise HTTPException(409, f'At most {areas.MAX_AREAS} saved areas') from None
    if row is None:
        raise HTTPException(409, 'You already have an area with this name')
    return row


@router.put('/areas/{area_id}', response_model=Area)
def update_area(connection: Connection, user_id: UserID, area_id: int, body: AreaIn):
    row = areas.update_area(connection, user_id, area_id, body.model_dump())
    if row is False:
        raise HTTPException(409, 'You already have an area with this name')
    if row is None:
        raise HTTPException(404, 'Area not found')
    return row


@router.delete('/areas/{area_id}', status_code=204)
def delete_area(connection: Connection, user_id: UserID, area_id: int):
    if not areas.delete_area(connection, user_id, area_id):
        raise HTTPException(404, 'Area not found')
    return Response(status_code=204)
