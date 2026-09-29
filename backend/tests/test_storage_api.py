import base64
import copy
import hashlib
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault('AUTH_JWT_SECRET', 'test-only-secret-' + 'x' * 40)

import jwt
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from backend.api.auth import create_access, current_user, decode_access, limiter
from backend.api.server import app, get_connection
from backend.config import database_url, jwt_secret
from backend.processing.pipeline import process
from backend.storage.reports import connect, get_report, migrate, save_report
from backend.storage.users import check_password, hash_password
from backend.tests.test_pipeline import data


def bearer(value):
    return {'Authorization': 'Bearer ' + value}


def fixture():
    report = process([('VIIRS_SNPP_NRT', data({}, {'acq_time': '0145'}))], (88, 20, 93, 27))
    report['generated_at_utc'] = '2026-09-29T02:00:00+00:00'
    report['inputs'] = [{'snapshot': '/private/path'}]
    return report


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_connection] = lambda: None
        app.dependency_overrides[current_user] = lambda: 1
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def test_health_and_documentation(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.assertEqual(self.client.get('/docs').status_code, 200)
        spec = self.client.get('/openapi.json').json()
        self.assertIn('/v1/observations', spec['paths'])

    def test_bad_pagination_and_dates(self):
        for query in ['limit=0', 'limit=1001', 'offset=-1', 'limit=abc', 'start=bad',
                      'start=2026-10-01&end=2026-01-01', 'report_id=0']:
            self.assertEqual(self.client.get('/v1/observations?'+query).status_code, 422)

    def test_missing_data_and_report(self):
        with patch('backend.api.server.get_report', return_value=None):
            self.assertEqual(self.client.get('/v1/summary').status_code, 503)
            self.assertEqual(self.client.get('/v1/summary?report_id=999').status_code, 404)

    def test_metadata_and_no_private_paths(self):
        with patch('backend.api.server.get_report', return_value={'id': 1, 'payload': fixture()}):
            response = self.client.get('/v1/summary')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['counts_by_source'], {'VIIRS_SNPP_NRT': 2})
            self.assertNotIn('/private/path', response.text)
            self.assertIn('stale', response.json())

    def test_database_outage_returns_safe_503(self):
        with patch('backend.api.server.get_report', side_effect=psycopg.OperationalError('secret connection string')):
            response = self.client.get('/v1/summary')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('secret', response.text)


def token(**overrides):
    now = datetime.now(timezone.utc)
    claims = {'sub': '1', 'iat': now, 'exp': now + timedelta(minutes=5), 'iss': 'agnicalendar',
              'aud': 'agnicalendar-app', 'typ': 'access'} | overrides
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, jwt_secret(), algorithm='HS256')


class AuthContractTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_connection] = lambda: None
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        limiter.reset()

    def test_data_requires_login(self):
        with patch('backend.api.server.get_report', return_value={'id': 1, 'payload': fixture()}):
            for path in ['/v1/summary', '/v1/calendar', '/v1/observations']:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers['www-authenticate'], 'Bearer')
                if path != '/v1/observations':  # needs a real connection; covered by PostgresTests
                    self.assertEqual(self.client.get(path, headers=bearer(token())).status_code, 200)
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_bad_access_tokens_rejected(self):
        header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b'=').decode()
        payload = base64.urlsafe_b64encode(json.dumps({'sub': '1', 'exp': 9999999999, 'iat': 1, 'iss': 'agnicalendar',
                                                        'aud': 'agnicalendar-app', 'typ': 'access'}).encode()).rstrip(b'=').decode()
        bad = [
            'garbage',
            token(exp=datetime.now(timezone.utc) - timedelta(minutes=5)),
            token(aud='someone-else'), token(iss='someone-else'), token(typ='refresh'), token(typ=None),
            token(sub='not-a-number'), token(exp=None),
            jwt.encode({'sub': '1'}, 'wrong-secret-' + 'x' * 40, algorithm='HS256'),
            f'{header}.{payload}.',
        ]
        for value in bad:
            with self.subTest(value=value[:30]):
                response = self.client.get('/v1/summary', headers=bearer(value))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers['www-authenticate'], 'Bearer')

    def test_account_routes_require_login(self):
        for method, path in [('get', '/v1/me'), ('get', '/v1/me/areas'), ('post', '/v1/auth/logout-all')]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 401)

    def test_access_token_round_trip(self):
        self.assertEqual(decode_access(create_access(42)), 42)

    def test_validation(self):
        cases = [
            ('/v1/auth/register', {'email': 'bad', 'password': 'long enough'}),
            ('/v1/auth/register', {'email': 'a@example.com', 'password': 'short'}),
            ('/v1/auth/register', {'email': 'a@example.com', 'password': 'x' * 129}),
            ('/v1/auth/register', {'email': 'a@example.com', 'password': 'long enough', 'display_name': 'n' * 81}),
            ('/v1/auth/login', {'email': 'a@example.com'}),
            ('/v1/auth/refresh', {'refresh_token': ''}),
        ]
        for path, body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post(path, json=body).status_code, 422)
        headers = bearer(token())
        for body in [{'name': 'x', 'west': 92, 'south': 20, 'east': 90, 'north': 21},
                     {'name': 'x', 'west': 88, 'south': 20, 'east': 90, 'north': 91},
                     {'name': '  ', 'west': 88, 'south': 20, 'east': 90, 'north': 21}]:
            self.assertEqual(self.client.post('/v1/me/areas', json=body, headers=headers).status_code, 422)
        for bbox in ['1,2,3', '92,20,90,21', 'a,b,c,d', '88,20,190,21']:
            self.assertEqual(self.client.get('/v1/observations?bbox=' + bbox, headers=headers).status_code, 422)

    def test_rate_limit(self):
        with patch('backend.api.auth.users.authenticate', return_value=None):
            codes = [self.client.post('/v1/auth/login', json={'email': 'a@example.com', 'password': 'x'}).status_code
                     for _ in range(31)]
        self.assertEqual(codes[:30], [401] * 30)
        self.assertEqual(codes[30], 429)

    def test_password_hashing(self):
        stored = hash_password('correct horse')
        self.assertTrue(stored.startswith('$argon2id$'))
        self.assertNotIn('correct horse', stored)
        self.assertNotEqual(stored, hash_password('correct horse'))  # unique salt
        self.assertEqual(check_password('correct horse', stored), (True, None))
        self.assertEqual(check_password('wrong horse', stored), (False, None))
        self.assertEqual(check_password('correct horse', 'garbage'), (False, None))

    def test_legacy_scrypt_verifies_and_upgrades(self):
        salt = b'0123456789abcdef'
        key = hashlib.scrypt(b'old password', salt=salt, n=2**14, r=8, p=1, dklen=32)
        legacy = f'scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}'
        valid, upgraded = check_password('old password', legacy)
        self.assertTrue(valid)
        self.assertTrue(upgraded.startswith('$argon2id$'))
        self.assertEqual(check_password('new password', legacy), (False, None))

    def test_missing_secret_fails_closed(self):
        jwt_secret.cache_clear()
        self.addCleanup(jwt_secret.cache_clear)
        with patch.dict(os.environ, {'AUTH_JWT_SECRET': 'short'}), patch('backend.config.load_dotenv'):
            with self.assertRaises(RuntimeError):
                jwt_secret()


@unittest.skipUnless(os.environ.get('RUN_POSTGRES_TESTS') == '1', 'Set RUN_POSTGRES_TESTS=1 for real PostgreSQL integration tests')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_url = database_url()
        cls.name = 'agni_test_' + uuid.uuid4().hex[:12]
        with psycopg.connect(cls.admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.url = make_conninfo(cls.admin_url, dbname=cls.name)
        cls.config_patch = patch('backend.storage.reports.database_url', return_value=cls.url)
        cls.config_patch.start()
        migrate()
        migrate()  # Migration must be repeatable.

    @classmethod
    def tearDownClass(cls):
        cls.config_patch.stop()
        with psycopg.connect(cls.admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))

    def setUp(self):
        with connect() as connection:
            connection.execute('TRUNCATE observations, reports, auth_events, saved_areas, refresh_sessions, users '
                               'RESTART IDENTITY')
        self.client = TestClient(app)
        limiter.reset()

    def register(self, email='Reader@Example.com ', password='password123', **extra):
        return self.client.post('/v1/auth/register', json={'email': email, 'password': password} | extra)

    def login(self, password='password123', email='READER@example.com'):
        return self.client.post('/v1/auth/login', json={'email': email, 'password': password})

    def refresh(self, token):
        return self.client.post('/v1/auth/refresh', json={'refresh_token': token})

    def test_register_login_profile_and_no_secrets(self):
        response = self.register(display_name='  Reader ')
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['user']['email'], 'reader@example.com')
        self.assertEqual(body['user']['display_name'], 'Reader')
        self.assertEqual(body['expires_in'], 900)
        for text in [response.text, self.login().text]:
            self.assertNotIn('password', text)
            self.assertNotIn('argon2', text)
        headers = bearer(body['access_token'])
        self.assertEqual(self.client.patch('/v1/me', json={'display_name': 'Agni'}, headers=headers).json()['display_name'], 'Agni')
        profile = self.client.get('/v1/me', headers=headers).json()
        self.assertEqual(set(profile), {'id', 'email', 'display_name', 'created_at', 'last_login_at'})
        self.assertEqual(self.register(email='reader@EXAMPLE.com', password='password456').status_code, 409)
        self.assertEqual(self.login('password124').status_code, 401)
        self.assertEqual(self.login(email='nobody@example.com').status_code, 401)
        with connect() as connection:
            stored = connection.execute('SELECT password_hash FROM users').fetchone()['password_hash']
            self.assertTrue(stored.startswith('$argon2id$'))
            tokens = connection.execute('SELECT token_hash FROM refresh_sessions').fetchall()
            self.assertNotIn(body['refresh_token'], [row['token_hash'] for row in tokens])
            events = [row['event'] for row in connection.execute('SELECT event FROM auth_events ORDER BY id')]
            self.assertEqual(events, ['register', 'login_ok', 'login_fail', 'login_fail'])

    def test_refresh_rotation_and_reuse_detection(self):
        first = self.register().json()['refresh_token']
        rotated = self.refresh(first)
        self.assertEqual(rotated.status_code, 200)
        second = rotated.json()['refresh_token']
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get('/v1/me', headers=bearer(rotated.json()['access_token'])).status_code, 200)
        other_device = self.login().json()['refresh_token']
        # Replaying a rotated token revokes that sign-in's whole family, not other devices.
        self.assertEqual(self.refresh(first).status_code, 401)
        self.assertEqual(self.refresh(second).status_code, 401)
        self.assertEqual(self.refresh(other_device).status_code, 200)
        self.assertEqual(self.refresh('never-issued').status_code, 401)

    def test_logout_expiry_and_logout_all(self):
        token = self.register().json()['refresh_token']
        self.assertEqual(self.client.post('/v1/auth/logout', json={'refresh_token': token}).status_code, 204)
        self.assertEqual(self.client.post('/v1/auth/logout', json={'refresh_token': token}).status_code, 204)
        self.assertEqual(self.refresh(token).status_code, 401)
        expiring = self.login().json()['refresh_token']
        with connect() as connection:
            connection.execute("UPDATE refresh_sessions SET expires_at = now() - INTERVAL '1 second' WHERE revoked_at IS NULL")
        self.assertEqual(self.refresh(expiring).status_code, 401)
        phone, tablet = self.login().json(), self.login().json()
        self.assertEqual(self.client.post('/v1/auth/logout-all', headers=bearer(phone['access_token'])).status_code, 204)
        self.assertEqual(self.refresh(tablet['refresh_token']).status_code, 401)

    def test_password_change_revokes_other_sessions(self):
        session = self.register().json()
        other = self.login().json()
        headers = bearer(session['access_token'])
        wrong = self.client.post('/v1/me/password', headers=headers,
                                 json={'current_password': 'nope', 'new_password': 'newpassword1'})
        self.assertEqual(wrong.status_code, 403)
        changed = self.client.post('/v1/me/password', headers=headers,
                                   json={'current_password': 'password123', 'new_password': 'newpassword1'})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.refresh(other['refresh_token']).status_code, 401)
        self.assertEqual(self.refresh(changed.json()['refresh_token']).status_code, 200)
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login('newpassword1').status_code, 200)

    def test_lockout_after_repeated_failures(self):
        self.register()
        for _ in range(10):
            self.assertEqual(self.login('wrong-password').status_code, 401)
        self.assertEqual(self.login().status_code, 429)  # even the right password
        with connect() as connection:
            connection.execute("UPDATE users SET locked_until = now() - INTERVAL '1 second'")
        self.assertEqual(self.login().status_code, 200)

    def test_legacy_scrypt_account_upgrades_on_login(self):
        salt = b'0123456789abcdef'
        key = hashlib.scrypt(b'password123', salt=salt, n=2**14, r=8, p=1, dklen=32)
        legacy = f'scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}'
        with connect() as connection:
            connection.execute('INSERT INTO users (email, password_hash) VALUES (%s,%s)', ('reader@example.com', legacy))
        self.assertEqual(self.login().status_code, 200)
        with connect() as connection:
            self.assertTrue(connection.execute('SELECT password_hash FROM users').fetchone()['password_hash'].startswith('$argon2id$'))

    def test_saved_areas_are_private(self):
        mine = bearer(self.register().json()['access_token'])
        theirs = bearer(self.register(email='other@example.com').json()['access_token'])
        area = {'name': ' Sundarbans ', 'west': 88.9, 'south': 21.5, 'east': 89.9, 'north': 22.5}
        created = self.client.post('/v1/me/areas', json=area, headers=mine)
        self.assertEqual(created.status_code, 201)
        area_id = created.json()['id']
        self.assertEqual(created.json()['name'], 'Sundarbans')
        self.assertEqual(self.client.post('/v1/me/areas', json=area, headers=mine).status_code, 409)
        self.assertEqual(self.client.post('/v1/me/areas', json=area, headers=theirs).status_code, 201)
        self.assertEqual(self.client.get('/v1/me/areas', headers=theirs).json()[0]['name'], 'Sundarbans')
        self.assertEqual(self.client.put(f'/v1/me/areas/{area_id}', json=area, headers=theirs).status_code, 404)
        self.assertEqual(self.client.delete(f'/v1/me/areas/{area_id}', headers=theirs).status_code, 404)
        renamed = self.client.put(f'/v1/me/areas/{area_id}', json=area | {'name': 'Khulna'}, headers=mine)
        self.assertEqual(renamed.json()['name'], 'Khulna')
        for i in range(19):
            self.client.post('/v1/me/areas', json=area | {'name': f'Area {i}'}, headers=mine)
        self.assertEqual(self.client.post('/v1/me/areas', json=area | {'name': 'One too many'}, headers=mine).status_code, 409)
        self.assertEqual(self.client.delete(f'/v1/me/areas/{area_id}', headers=mine).status_code, 204)
        self.assertEqual(len(self.client.get('/v1/me/areas', headers=mine).json()), 19)

    def test_real_api_pagination_filters_and_snapshot_pin(self):
        self.assertEqual(self.client.get('/v1/summary').status_code, 401)
        self.client.headers.update(bearer(self.register().json()['access_token']))
        first = save_report(fixture())
        self.assertEqual(self.client.get('/ready').status_code, 200)
        self.assertEqual(self.client.get('/v1/calendar').json()['weeks'][0]['detection_count'], 2)
        page = self.client.get(f'/v1/observations?limit=1&offset=1&report_id={first}').json()
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(page['observations']), 1)
        self.assertEqual(page['observations'][0]['timestamp_utc'], '2026-09-29T01:45:00Z')
        for query in ['source=MODIS_NRT', 'start=2026-09-30', 'end=2026-09-28']:
            self.assertEqual(self.client.get('/v1/observations?'+query).json()['total'], 0)
        self.assertEqual(self.client.get('/v1/observations?start=2026-09-29&end=2026-09-29').json()['total'], 2)
        self.assertEqual(self.client.get('/v1/observations?bbox=88,20,93,27').json()['total'], 2)
        self.assertEqual(self.client.get('/v1/observations?bbox=80,10,81,11').json()['total'], 0)
        newer = fixture()
        newer['generated_at_utc'] = '2026-09-30T00:00:00+00:00'
        second = save_report(newer)
        self.assertEqual(self.client.get('/v1/summary').json()['report_id'], second)
        self.assertEqual(self.client.get(f'/v1/summary?report_id={first}').json()['report_id'], first)
