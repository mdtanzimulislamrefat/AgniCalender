"""User accounts, Argon2id passwords, rotating refresh sessions and the auth audit log."""
import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

REFRESH_LIFETIME = timedelta(days=30)
MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)
PROFILE = 'id, email, display_name, created_at, last_login_at'

hasher = PasswordHasher()  # Argon2id, RFC 9106 low-memory profile


def hash_password(password):
    return hasher.hash(password)


def _verify_legacy_scrypt(password, stored):
    # Hashes written by auth v1 (002_auth.sql); upgraded to Argon2id on next login.
    try:
        _, n, r, p, salt, key = stored.split('$')
        expected = base64.b64decode(key)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_password(password, stored):
    """Return (valid, replacement hash or None)."""
    if stored.startswith('scrypt$'):
        valid = _verify_legacy_scrypt(password, stored)
        return valid, hash_password(password) if valid else None
    try:
        hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    return True, hash_password(password) if hasher.check_needs_rehash(stored) else None


# Unknown emails still pay the hashing cost so response time does not reveal registered accounts.
DUMMY_HASH = hash_password(secrets.token_hex(16))


class AccountLocked(Exception):
    pass


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def log_event(connection, user_id, event):
    connection.execute('INSERT INTO auth_events (user_id, event) VALUES (%s,%s)', (user_id, event))


def create_user(connection, email, password, display_name=None):
    """Return the new user profile, or None when the email is already registered."""
    try:
        user = connection.execute(
            f'INSERT INTO users (email, password_hash, display_name, last_login_at) VALUES (%s,%s,%s, now()) '
            f'RETURNING {PROFILE}', (email, hash_password(password), display_name)).fetchone()
    except psycopg.errors.UniqueViolation:
        connection.rollback()
        return None
    log_event(connection, user['id'], 'register')
    return user


def authenticate(connection, email, password):
    """Return the profile, None for bad credentials, or raise AccountLocked."""
    row = connection.execute(
        'SELECT id, password_hash, locked_until > now() AS locked, disabled_at IS NOT NULL AS disabled '
        'FROM users WHERE email=%s FOR UPDATE', (email,)).fetchone()
    if row is None:
        check_password(password, DUMMY_HASH)
        log_event(connection, None, 'login_fail')
        connection.commit()
        return None
    if row['locked']:
        log_event(connection, row['id'], 'login_locked')
        connection.commit()
        raise AccountLocked()
    valid, upgraded = check_password(password, row['password_hash'])
    if not valid or row['disabled']:
        connection.execute(
            'UPDATE users SET failed_logins = CASE WHEN failed_logins + 1 >= %(max)s THEN 0 ELSE failed_logins + 1 END, '
            'locked_until = CASE WHEN failed_logins + 1 >= %(max)s THEN now() + %(lockout)s ELSE locked_until END '
            'WHERE id=%(id)s', {'max': MAX_FAILED_LOGINS, 'lockout': LOCKOUT, 'id': row['id']})
        log_event(connection, row['id'], 'login_fail')
        connection.commit()
        return None
    user = connection.execute(
        f'UPDATE users SET failed_logins=0, locked_until=NULL, last_login_at=now(), '
        f'password_hash=COALESCE(%s, password_hash) WHERE id=%s RETURNING {PROFILE}',
        (upgraded, row['id'])).fetchone()
    log_event(connection, user['id'], 'login_ok')
    return user


def get_profile(connection, user_id):
    return connection.execute(f'SELECT {PROFILE} FROM users WHERE id=%s AND disabled_at IS NULL',
                              (user_id,)).fetchone()


def update_profile(connection, user_id, display_name):
    user = connection.execute(f'UPDATE users SET display_name=%s, updated_at=now() WHERE id=%s RETURNING {PROFILE}',
                              (display_name, user_id)).fetchone()
    connection.commit()
    return user


def create_refresh(connection, user_id, user_agent=None, family_id=None):
    """Insert a refresh session; caller commits. Returns (token, session id, expires_at)."""
    token = secrets.token_urlsafe(32)
    row = connection.execute(
        'INSERT INTO refresh_sessions (user_id, family_id, token_hash, expires_at, user_agent) '
        'VALUES (%s, COALESCE(%s, gen_random_uuid()), %s, now() + %s, %s) RETURNING id, expires_at',
        (user_id, family_id, token_digest(token), REFRESH_LIFETIME, (user_agent or '')[:200] or None)).fetchone()
    return token, row['id'], row['expires_at']


def rotate_refresh(connection, token, user_agent=None):
    """Exchange a refresh token for a new one. Returns (profile, token, expires_at) or None."""
    old = connection.execute(
        'SELECT s.id, s.user_id, s.family_id, s.revoked_at, s.revoked_reason, s.expires_at > now() AS live '
        'FROM refresh_sessions s WHERE s.token_hash=%s FOR UPDATE', (token_digest(token),)).fetchone()
    if old is None:
        return None
    if old['revoked_at'] is not None:
        if old['revoked_reason'] == 'rotated':
            # A rotated token came back: it was copied. End every session of that sign-in.
            revoke(connection, 'family_id=%s', old['family_id'], 'reuse')
            log_event(connection, old['user_id'], 'refresh_reuse')
            connection.commit()
        return None
    user = get_profile(connection, old['user_id'])
    if not old['live'] or user is None:
        return None
    new_token, new_id, expires_at = create_refresh(connection, old['user_id'], user_agent, old['family_id'])
    connection.execute("UPDATE refresh_sessions SET revoked_at=now(), revoked_reason='rotated', replaced_by=%s, "
                       'last_used_at=now() WHERE id=%s', (new_id, old['id']))
    connection.commit()
    return user, new_token, expires_at


def revoke(connection, where, value, reason):
    """Revoke live sessions matching a fixed clause; caller commits."""
    assert where in ('family_id=%s', 'user_id=%s', 'token_hash=%s')
    return connection.execute(f'UPDATE refresh_sessions SET revoked_at=now(), revoked_reason=%s '
                              f'WHERE {where} AND revoked_at IS NULL RETURNING user_id', (reason, value)).fetchall()


def logout(connection, token):
    rows = revoke(connection, 'token_hash=%s', token_digest(token), 'logout')
    if rows:
        log_event(connection, rows[0]['user_id'], 'logout')
    connection.commit()


def logout_all(connection, user_id):
    revoke(connection, 'user_id=%s', user_id, 'logout_all')
    log_event(connection, user_id, 'logout_all')
    connection.commit()


def change_password(connection, user_id, current, new):
    """Verify the current password, store the new hash and end every session. Caller commits."""
    row = connection.execute('SELECT password_hash FROM users WHERE id=%s FOR UPDATE', (user_id,)).fetchone()
    if row is None or not check_password(current, row['password_hash'])[0]:
        return False
    connection.execute('UPDATE users SET password_hash=%s, updated_at=now() WHERE id=%s', (hash_password(new), user_id))
    revoke(connection, 'user_id=%s', user_id, 'password_change')
    log_event(connection, user_id, 'password_change')
    return True
