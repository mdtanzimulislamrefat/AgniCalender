import asyncio
import os
import tempfile
import time
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('AUTH_JWT_SECRET', 'test-only-secret-' + 'x' * 40)

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from backend.api.scheduler import FetchScheduler, parse_interval
from backend.api.server import app
from backend.config import database_url
from backend.processing import pipeline
from backend.storage.reports import connect, migrate
from backend.tests.test_pipeline import data


class IntervalTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_interval('3h'), timedelta(hours=3))
        self.assertEqual(parse_interval('30m'), timedelta(minutes=30))
        self.assertEqual(parse_interval('1d'), timedelta(days=1))
        self.assertIsNone(parse_interval('off'))
        for bad in ['5m', '0h', 'abc', '3', '-1h', '']:
            with self.assertRaises(ValueError, msg=bad):
                parse_interval(bad)


class SchedulerTests(unittest.TestCase):
    def run_loop(self, scheduler, seconds=0.05):
        async def go():
            task = asyncio.create_task(scheduler.loop())
            await asyncio.sleep(seconds)
            task.cancel()
        asyncio.run(go())

    def test_statuses_and_survives_failures(self):
        outcomes = iter([OSError('NASA down'), {'status': 'unchanged'},
                         {'status': 'new', 'report_id': 9, 'counts': {'retained': 3}}])
        calls = []

        def fetch():
            calls.append(1)
            outcome = next(outcomes, {'status': 'unchanged'})
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        scheduler = FetchScheduler(timedelta(milliseconds=5), fetch=fetch)
        results = []
        original = scheduler.run_once

        async def recording():
            ok = await original()
            results.append(scheduler.status['last_result'])
            return ok
        scheduler.run_once = recording
        self.run_loop(scheduler)
        self.assertEqual(results[:3], ['failed', 'unchanged', 'new_report'])
        self.assertGreater(len(calls), 3)
        self.assertIsNotNone(scheduler.status['next_run_utc'])

    def test_server_starts_scheduler_and_reports_status(self):
        calls = []
        scheduler = FetchScheduler(timedelta(hours=3), fetch=lambda: calls.append(1) or {'status': 'unchanged'})
        app.state.scheduler = scheduler
        self.addCleanup(setattr, app.state, 'scheduler', None)
        with TestClient(app) as client:
            for _ in range(50):
                if scheduler.status['last_result']:
                    break
                time.sleep(0.01)  # the app runs in TestClient's own thread
            status = client.get('/health').json()['nasa_fetch']
        self.assertEqual(calls, [1])  # once at startup, next one in 3 h
        self.assertEqual(status['last_result'], 'unchanged')
        self.assertEqual(status['interval_minutes'], 180)

    def test_disabled(self):
        app.state.scheduler = FetchScheduler(None)
        self.addCleanup(setattr, app.state, 'scheduler', None)
        with TestClient(app) as client:
            self.assertEqual(client.get('/health').json()['nasa_fetch']['enabled'], False)


@unittest.skipUnless(os.environ.get('RUN_POSTGRES_TESTS') == '1', 'Set RUN_POSTGRES_TESTS=1 for real PostgreSQL integration tests')
class FetchLatestTests(unittest.TestCase):
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

    def test_unchanged_feeds_do_not_create_reports(self):
        raw, output = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        feeds = {'VIIRS_SNPP_NRT': data({}), 'MODIS_NRT': data({'instrument': 'MODIS', 'satellite': 'Aqua',
                                                                  'confidence': '80', 'version': '6.1NRT'})}

        def fake_download(raw_dir):
            inputs, manifest = [], []
            for source, content in feeds.items():
                item, entry = pipeline.ingest(source, content, 'test', raw_dir)
                inputs.append(item)
                manifest.append(entry)
            return inputs, manifest

        with patch.object(pipeline, 'download_feeds', fake_download):
            first = pipeline.fetch_latest(raw, output)
            second = pipeline.fetch_latest(raw, output)
            other_region = pipeline.fetch_latest(raw, output, region='none')
            feeds['VIIRS_SNPP_NRT'] = data({}, {'acq_time': '0130'})
            changed = pipeline.fetch_latest(raw, output, region='none')
        self.assertEqual(first['status'], 'new')
        self.assertEqual(first['counts']['retained'], 2)
        self.assertEqual(second, {'status': 'unchanged'})
        self.assertEqual(other_region['status'], 'new')  # different filter is a different report
        self.assertEqual(changed['status'], 'new')
        self.assertTrue((output / 'report.json').exists())
        with connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM reports').fetchone()['n'], 3)


@unittest.skipUnless(os.environ.get('RUN_POSTGRES_TESTS') == '1', 'Set RUN_POSTGRES_TESTS=1 for real PostgreSQL integration tests')
class PublishTests(unittest.TestCase):
    def setUp(self):
        self.admin_url = database_url()
        self.names = ['agni_test_' + uuid.uuid4().hex[:12] for _ in range(2)]
        with psycopg.connect(self.admin_url, autocommit=True) as connection:
            for name in self.names:
                connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        self.source, self.target = (make_conninfo(self.admin_url, dbname=n) for n in self.names)

    def tearDown(self):
        with psycopg.connect(self.admin_url, autocommit=True) as connection:
            for name in self.names:
                connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))

    def test_copies_data_not_accounts_and_keeps_ids_going(self):
        from backend.processing.archive import import_year
        from backend.storage.publish import publish
        from backend.storage.users import create_user
        from backend.tests.test_archive import archive_csv
        from backend.tests.test_storage_api import fixture
        from backend.storage.reports import save_report
        with patch('backend.storage.reports.database_url', return_value=self.source):
            migrate()
            import_year('viirs-snpp', 2024, 'Bangladesh', Path(tempfile.mkdtemp()),
                        fetch=lambda url: archive_csv({'acq_date': '2024-04-01'}, {'type': '2'}))
            save_report(fixture())
            with connect() as connection:
                create_user(connection, 'source-user@example.com', 'password123')
                connection.commit()
        migrate(self.target)
        with connect(self.target) as connection:
            create_user(connection, 'hosted-user@example.com', 'password123')
            connection.commit()
        counts = publish(self.target, self.source)
        self.assertEqual(counts['archive_detections'], 2)
        self.assertEqual(counts['reports'], 1)
        self.assertEqual(counts['observations'], 2)
        publish(self.target, self.source)  # repeatable: replaces, never duplicates
        with connect(self.target) as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM archive_detections').fetchone()['n'], 2)
            emails = [r['email'] for r in connection.execute('SELECT email FROM users')]
        self.assertEqual(emails, ['hosted-user@example.com'])  # target accounts kept, source accounts not copied
        with patch('backend.storage.reports.database_url', return_value=self.target):
            report = fixture()
            report['generated_at_utc'] = '2026-10-01T00:00:00+00:00'
            self.assertEqual(save_report(report), 2)  # identity continues after copied ids
        with self.assertRaises(ValueError):
            publish(self.source, self.source)
