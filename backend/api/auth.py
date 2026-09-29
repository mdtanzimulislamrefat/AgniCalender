"""Registration, login, JWT access tokens and rotating refresh sessions."""
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from backend.api.database import Connection
from backend.config import jwt_secret
from backend.storage import users

ACCESS_LIFETIME = timedelta(minutes=15)
ISSUER, AUDIENCE, ALGORITHM = 'agnicalendar', 'agnicalendar-app', 'HS256'
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
bearer = HTTPBearer(auto_error=False, description='Access token from /v1/auth/login, /register or /refresh')
router = APIRouter(prefix='/v1/auth', tags=['auth'])


def create_access(user_id):
    now = datetime.now(timezone.utc)
    claims = {'sub': str(user_id), 'iat': now, 'exp': now + ACCESS_LIFETIME,
              'iss': ISSUER, 'aud': AUDIENCE, 'typ': 'access'}
    return jwt.encode(claims, jwt_secret(), algorithm=ALGORITHM)


def decode_access(token):
    """Return the user id of a valid access token, else None."""
    try:
        # A fixed algorithm list rejects 'none' and key-confusion tokens.
        claims = jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM], audience=AUDIENCE, issuer=ISSUER,
                            leeway=30, options={'require': ['exp', 'iat', 'sub', 'iss', 'aud']})
        return int(claims['sub']) if claims.get('typ') == 'access' else None
    except (jwt.InvalidTokenError, ValueError):
        return None


class RateLimiter:
    """Small in-process sliding window per client address; one server process only."""

    def __init__(self, limit, seconds):
        self.limit, self.seconds, self.hits = limit, seconds, defaultdict(deque)

    def check(self, key):
        now = time.monotonic()
        hits = self.hits[key]
        while hits and hits[0] <= now - self.seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            raise HTTPException(429, 'Too many attempts. Try again later.',
                                headers={'Retry-After': str(self.seconds)})
        hits.append(now)

    def reset(self):
        self.hits.clear()


limiter = RateLimiter(30, 300)


def rate_limited(request: Request):
    limiter.check(request.client.host if request.client else 'unknown')


def unauthorized(detail='Not authenticated'):
    return HTTPException(401, detail, headers={'WWW-Authenticate': 'Bearer'})


def optional_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    """Guests (no token) get None. A token that is present but invalid is a 401 so clients refresh it."""
    if credentials is None:
        return None
    user_id = decode_access(credentials.credentials)
    if user_id is None:
        raise unauthorized('Access token expired or invalid')
    return user_id


def current_user(user_id: Annotated[int | None, Depends(optional_user)]):
    if user_id is None:
        raise unauthorized()
    return user_id


UserID = Annotated[int, Depends(current_user)]


def normalize_email(value):
    value = value.strip().lower()
    if not EMAIL.match(value):
        raise ValueError('Enter a valid email address')
    return value


def normalize_name(value):
    value = (value or '').strip()
    return value or None


class Profile(BaseModel):
    id: int
    email: str
    display_name: str | None
    created_at: datetime
    last_login_at: datetime | None


class Session(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    user: Profile


class Login(BaseModel):
    email: str = Field(max_length=254, examples=['user@example.com'])
    password: str = Field(min_length=1, max_length=128)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class Register(Login):
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=80)

    @field_validator('display_name')
    @classmethod
    def clean_name(cls, value):
        return normalize_name(value)


class Refresh(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=200)


def session(user, token, expires_at):
    return {'access_token': create_access(user['id']), 'expires_in': int(ACCESS_LIFETIME.total_seconds()),
            'refresh_token': token, 'refresh_expires_at': expires_at, 'user': user}


def new_session(connection, user, request):
    token, _, expires_at = users.create_refresh(connection, user['id'], request.headers.get('user-agent'))
    # Commit before responding so the client can use the tokens immediately.
    connection.commit()
    return session(user, token, expires_at)


@router.post('/register', response_model=Session, status_code=201, dependencies=[Depends(rate_limited)])
def register(connection: Connection, body: Register, request: Request):
    user = users.create_user(connection, body.email, body.password, body.display_name)
    if user is None:
        raise HTTPException(409, 'Email already registered')
    return new_session(connection, user, request)


@router.post('/login', response_model=Session, dependencies=[Depends(rate_limited)])
def login(connection: Connection, body: Login, request: Request):
    try:
        user = users.authenticate(connection, body.email, body.password)
    except users.AccountLocked:
        raise HTTPException(429, 'Too many failed attempts. Try again in 15 minutes.') from None
    if user is None:
        raise unauthorized('Invalid email or password')
    return new_session(connection, user, request)


@router.post('/refresh', response_model=Session)
def refresh(connection: Connection, body: Refresh, request: Request):
    result = users.rotate_refresh(connection, body.refresh_token, request.headers.get('user-agent'))
    if result is None:
        raise unauthorized('Refresh token expired or revoked')
    return session(*result)


@router.post('/logout', status_code=204)
def logout(connection: Connection, body: Refresh):
    users.logout(connection, body.refresh_token)
    return Response(status_code=204)


@router.post('/logout-all', status_code=204)
def logout_all(connection: Connection, user_id: UserID):
    users.logout_all(connection, user_id)
    return Response(status_code=204)
