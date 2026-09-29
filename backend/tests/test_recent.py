import os
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('AUTH_JWT_SECRET', 'test-only-secret-' + 'x' * 40)

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from backend.api import cache
from backend.api.auth import create_access
from backend.api.server import app
from backend.config import database_url
from backend.geo import boundary
from backend.processing import recent
from backend.storage import hotspots
from backend.storage.reports import connect, migrate
from backend.tests.test_archive import archive_csv
from backend.tests.test_pipeline import data

TODAY = date(2026, 9, 29)


class PlanTests(unittest.TestCase):
    def test_first_run_fetches_everything_in_chunks_of_five(self):
        chunks = recent.plan(set(), TODAY, 30)
        self.assertEqual(len(chunks), 6)
        self.assertEqual(chunks[0], (TODAY - timedelta(days=29), 5))
        self.assertEqual(sum(n for _, n in chunks), 30)

    def test_complete_days_skipped_but_today_and_yesterday_refetched(self):
        covered = {TODAY - timedelta(days=n) for n in range(30)}
        self.assertEqual(recent.plan(covered, TODAY, 30), [(TODAY - timedelta(days=1), 2)])
        gap = covered - {TODAY - timedelta(days=10)}
        self.assertEqual(recent.plan(gap, TODAY, 30), [(TODAY - timedelta(days=10), 1), (TODAY - timedelta(days=1), 2)])

    def test_parse_keeps_only_bangladesh(self):
        rows = data({'latitude': '23.81', 'longitude': '90.41'}, {'latitude': '22.57', 'longitude': '88.36'},
                    {'confidence': 'bad'})
        records, stats = recent.parse(rows, 'VIIRS_NOAA21_NRT', boundary('bangladesh'))
        self.assertEqual(stats, {'retained': 1, 'invalid': 1, 'outside_region': 1})
        self.assertEqual(records[0]['latitude'], 23.81)

    def test_disabled_without_key(self):
        with patch.object(recent, 'firms_map_key', return_value=None):
            self.assertEqual(recent.refresh_recent(), {'status': 'disabled'})

    def test_refresh_all_survives_one_failure(self):
        with patch.object(recent, 'fetch_latest', side_effect=OSError('down')), \
                patch.object(recent, 'refresh_recent', return_value={'status': 'ok'}):
            self.assertEqual(recent.refresh_all(), {'status': 'failed', 'recent': {'status': 'ok'}})
        with patch.object(recent, 'fetch_latest', return_value={'status': 'unchanged'}), \
                patch.object(recent, 'refresh_recent', side_effect=ValueError('bad key')):
            self.assertEqual(recent.refresh_all(), {'status': 'unchanged', 'recent': {'status': 'failed'}})
        with patch.object(recent, 'fetch_latest', side_effect=OSError('down')), \
                patch.object(recent, 'refresh_recent', side_effect=OSError('down')):
            with self.assertRaises(OSError):
                recent.refresh_all()


class MapApiContractTests(unittest.TestCase):
    def test_login_and_validation(self):
        client = TestClient(app)
        self.assertEqual(client.get('/v1/map/hotspots?window=7d').status_code, 401)
        headers = {'Authorization': 'Bearer ' + create_access(1)}
        for query in ['', 'window=7d&month=2024-04', 'window=1y', 'month=2024-13', 'month=April']:
            self.assertEqual(client.get('/v1/map/hotspots?' + query, headers=headers).status_code, 422, query)


@unittest.skipUnless(os.environ.get('RUN_POSTGRES_TESTS') == '1', 'Set RUN_POSTGRES_TESTS=1 for real PostgreSQL integration tests')
class RecentPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_url = database_url()
        cls.name = 'agni_test_' + uuid.uuid4().hex[:12]
        with psycopg.connect(cls.admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.config_patch = patch('backend.storage.reports.database_url',
                                 return_value=make_conninfo(cls.admin_url, dbname=cls.name))
        cls.config_patch.start()
        migrate()

    @classmethod
    def tearDownClass(cls):
        cls.config_patch.stop()
        with psycopg.connect(cls.admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))

    def setUp(self):
        cache.clear()
        with connect() as connection:
            connection.execute('TRUNCATE recent_detections, recent_coverage, archive_detections, archive_files '
                               'RESTART IDENTITY CASCADE')
        self.calls = []

    def fetch(self, key, source, start, days):
        self.calls.append((source, start, days))
        # One detection per requested day inside Bangladesh, plus Kolkata (outside).
        rows = [{'acq_date': (start + timedelta(days=n)).isoformat(), 'latitude': '23.81', 'longitude': '90.41'}
                for n in range(days)] + [{'acq_date': start.isoformat(), 'latitude': '22.57', 'longitude': '88.36'}]
        if source == 'MODIS_NRT':
            rows = [r | {'instrument': 'MODIS', 'satellite': 'Aqua', 'confidence': '80', 'version': '6.1NRT'}
                    for r in rows]
        return data(*rows)

    def refresh(self, today=TODAY):
        return recent.refresh_recent(30, key='test-key', fetch=self.fetch, today=today,
                                     raw_dir=Path(tempfile.mkdtemp()))

    def test_backfill_then_incremental_and_retention(self):
        first = self.refresh()
        self.assertEqual(first, {'status': 'ok', 'requests': 24, 'retained': 120})
        self.calls.clear()
        second = self.refresh()
        self.assertEqual(second['requests'], 4)  # only yesterday+today per source
        self.assertTrue(all(start == TODAY - timedelta(days=1) and days == 2 for _, start, days in self.calls))
        with connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM recent_detections').fetchone()['n'], 120)
        self.refresh(TODAY + timedelta(days=60))
        with connect() as connection:
            oldest = connection.execute('SELECT min(observed_at) AS t FROM recent_detections').fetchone()['t']
        self.assertGreaterEqual(oldest.date(), TODAY + timedelta(days=60 - recent.KEEP_DAYS))

    def test_windows_and_months(self):
        self.refresh()
        now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        with connect() as connection:
            week = hotspots.recent(connection, 7, now)
            month = hotspots.recent(connection, 30, now)
            connection.execute('DELETE FROM recent_coverage WHERE day = %s', (date(2026, 9, 20),))
            gap = hotspots.recent(connection, 30, now)
        self.assertEqual(week['counts_by_source'], {s: 7 for s in recent.SOURCES})
        self.assertEqual(month['counts_by_source']['VIIRS_NOAA21_NRT'], 30)
        self.assertEqual(month['missing_days'], [])
        self.assertEqual(gap['missing_days'], [date(2026, 9, 20)])
        self.assertFalse(week['fire_type_known'])

        from backend.processing.archive import import_year
        csv_2024 = archive_csv({'acq_date': '2024-04-10'}, {'acq_date': '2024-04-11', 'type': '2'},
                               {'acq_date': '2024-05-01'})
        import_year('viirs-snpp', 2024, 'Bangladesh', Path(tempfile.mkdtemp()), fetch=lambda url: csv_2024)
        client = TestClient(app)
        headers = {'Authorization': 'Bearer ' + create_access(1)}
        april = client.get('/v1/map/hotspots?month=2024-04', headers=headers).json()
        self.assertEqual(april['label'], 'April 2024')
        self.assertEqual(april['counts_by_source'], {'VIIRS_SNPP_ARCHIVE': 1})
        self.assertEqual(april['excluded_other_types'], 1)
        self.assertEqual(april['points'][0]['timestamp_utc'], '2024-04-10T07:45:00+00:00')
        self.assertEqual(client.get('/v1/map/hotspots?month=2024-12', headers=headers).json()['points'], [])
        available = client.get('/v1/map/available', headers=headers).json()
        self.assertEqual(available['archive_months'], [{'month': '2024-04', 'count': 1}, {'month': '2024-05', 'count': 1}])
        self.assertEqual(available['recent_last_day'], '2026-09-29')
        big = client.get('/v1/map/hotspots?window=30d', headers=headers | {'Accept-Encoding': 'gzip'})
        self.assertEqual(big.status_code, 200)
