"""Per-user saved geographic areas (bounding boxes)."""
import psycopg

MAX_AREAS = 20
COLUMNS = 'id, name, west, south, east, north, created_at, updated_at'


class AreaLimit(Exception):
    pass


def list_areas(connection, user_id):
    return connection.execute(f'SELECT {COLUMNS} FROM saved_areas WHERE user_id=%s ORDER BY name, id',
                              (user_id,)).fetchall()


def create_area(connection, user_id, area):
    """Return the new area, None for a duplicate name, or raise AreaLimit."""
    # Lock the user row so concurrent creates cannot pass the limit together.
    connection.execute('SELECT id FROM users WHERE id=%s FOR UPDATE', (user_id,))
    if connection.execute('SELECT count(*) AS n FROM saved_areas WHERE user_id=%s', (user_id,)).fetchone()['n'] >= MAX_AREAS:
        connection.rollback()
        raise AreaLimit()
    try:
        row = connection.execute(
            f'INSERT INTO saved_areas (user_id, name, west, south, east, north) VALUES (%s,%s,%s,%s,%s,%s) '
            f'RETURNING {COLUMNS}', (user_id, area['name'], area['west'], area['south'], area['east'], area['north'])
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        connection.rollback()
        return None
    connection.commit()
    return row


def update_area(connection, user_id, area_id, area):
    """Return the updated area, None when missing (or not this user's), or False for a duplicate name."""
    try:
        row = connection.execute(
            f'UPDATE saved_areas SET name=%s, west=%s, south=%s, east=%s, north=%s, updated_at=now() '
            f'WHERE id=%s AND user_id=%s RETURNING {COLUMNS}',
            (area['name'], area['west'], area['south'], area['east'], area['north'], area_id, user_id)).fetchone()
    except psycopg.errors.UniqueViolation:
        connection.rollback()
        return False
    connection.commit()
    return row


def delete_area(connection, user_id, area_id):
    row = connection.execute('DELETE FROM saved_areas WHERE id=%s AND user_id=%s RETURNING id',
                             (area_id, user_id)).fetchone()
    connection.commit()
    return row is not None
