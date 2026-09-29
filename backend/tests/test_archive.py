import csv
import io
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

from backend.api.auth import create_access
from backend.api.server import app
from backend.config import database_url
from backend.processing.archive import api_year, import_year, parse
from backend.storage.archive import season
from backend.storage.reports import connect, migrate


def archive_csv(*changes, instrument='VIIRS'):
    base = dict(latitude='23', longitude='90', brightness='330', scan='0.4', track='0.6', acq_date='2023-04-10',
                acq_time='0745', satellite='N', instrument=instrument, confidence='n', version='2', bright_t31='300',
                frp='5', daynight='D', type='0')
    if instrument == 'MODIS':
        base |= dict(satellite='Aqua', confidence='80', version='61.03')
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(base))
    writer.writeheader()
    for change in changes:
        writer.writerow(base | change)
    return output.getvalue().encode()


class ParseTests(unittest.TestCase):
    def test_types_kept_and_bad_rows_counted(self):
        records, stats = parse(archive_csv({}, {'type': '2'}, {'type': '3'}, {'type': '9'},
                                           {'confidence': 'x'}, {}), 'VIIRS_SNPP_ARCHIVE')
        self.assertEqual([r['fire_type'] for r in records], [0, 2, 3])
        self.assertEqual(stats, {'rows_read': 6, 'retained': 3, 'invalid': 2, 'exact_duplicates': 1})
        self.assertEqual(records[0]['quality'], 'nominal')
        self.assertEqual(records[0]['version'], '2')

    def test_rejects_non_archive_files(self):
        with self.assertRaises(ValueError):
            parse(b'<html>Service unavailable</html>', 'MODIS_ARCHIVE')
        nrt = archive_csv({}).replace(b',type', b',kind')
        with self.assertRaises(ValueError):
            parse(nrt, 'VIIRS_SNPP_ARCHIVE')

    def test_sensor_mismatch_is_invalid(self):
        _, stats = parse(archive_csv({}, instrument='MODIS'), 'VIIRS_SNPP_ARCHIVE')
        self.assertEqual(stats['invalid'], 1)


class ApiYearTests(unittest.TestCase):
    def test_chunks_of_five_days_joined_once(self):
        calls = []

        def fetch(key, product, start, days):
            calls.append((product, start, days))
            return archive_csv({'acq_date': start.isoformat()})

        data = api_year('k', 'viirs-snpp', 2025, date(2025, 1, 12), fetch)
        self.assertEqual(calls, [('VIIRS_SNPP_SP', date(2025, 1, 1), 5), ('VIIRS_SNPP_SP', date(2025, 1, 6), 5),
                                 ('VIIRS_SNPP_SP', date(2025, 1, 11), 2)])
        self.assertEqual(data.count(b'latitude'), 1)  # a single header
        self.assertEqual(len(data.splitlines()), 4)

    def test_changed_columns_rejected(self):
        answers = iter([archive_csv({}), archive_csv({}).replace(b',type', b',kind')])
        with self.assertRaises(ValueError):
            api_year('k', 'modis', 2025, date(2025, 1, 10), lambda *a: next(answers))


class ArchiveApiContractTests(unittest.TestCase):
    def test_requires_login_and_validates(self):
        client = TestClient(app)
        self.assertEqual(client.get('/v1/archive/season').status_code, 401)
        headers = {'Authorization': 'Bearer ' + create_access(1)}
        self.assertEqual(client.get('/v1/archive/season?types=bogus', headers=headers).status_code, 422)
        self.assertEqual(client.get('/v1/archive/season?bbox=1,2,3', headers=headers).status_code, 422)


@unittest.skipUnless(os.environ.get('RUN_POSTGRES_TESTS') == '1', 'Set RUN_POSTGRES_TESTS=1 for real PostgreSQL integration tests')
class ArchivePostgresTests(unittest.TestCase):
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
        with connect() as connection:
            connection.execute('TRUNCATE archive_detections, archive_files RESTART IDENTITY CASCADE')
        self.raw = Path(tempfile.mkdtemp())
        self.files = {
            2012: archive_csv({'acq_date': '2012-04-01'}),  # partial mission year
            2013: archive_csv({'acq_date': '2013-04-01'}, {'acq_date': '2013-04-02'}, {'acq_date': '2013-03-02'},
                              {'acq_date': '2013-04-03', 'type': '2'}, {'acq_date': '2013-04-04', 'latitude': '25'}),
            2014: archive_csv({'acq_date': '2014-04-01'}, {'acq_date': '2014-04-02', 'type': '3'}),
            2015: None,  # not published
        }

    def fetch(self, url):
        year = int(url.split('/')[-2])
        return self.files[year]

    def run_import(self, *years, force=False):
        return [import_year('viirs-snpp', year, 'Bangladesh', self.raw, force, fetch=self.fetch)[0] for year in years]

    def test_import_is_idempotent_and_replaces_changed_files(self):
        self.assertEqual(self.run_import(2012, 2013, 2014, 2015), ['imported', 'imported', 'imported', 'unavailable'])
        self.assertEqual(self.run_import(2013, 2015), ['unchanged', 'unavailable'])
        self.assertEqual(len(list(self.raw.glob('*.csv'))), 3)
        self.files[2013] = archive_csv({'acq_date': '2013-04-01'})
        self.assertEqual(self.run_import(2013), ['imported'])
        with connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM archive_detections').fetchone()['n'], 4)

    def test_failed_file_keeps_previous_data(self):
        self.run_import(2013)
        self.files[2013] = b'<html>error</html>'
        with self.assertRaises(ValueError):
            self.run_import(2013)
        with connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM archive_detections').fetchone()['n'], 5)

    def test_unpublished_year_comes_from_the_api(self):
        calls = []

        def api_fetch(key, product, start, days):
            calls.append(start)
            return archive_csv({'acq_date': start.isoformat(), 'latitude': '23.81', 'longitude': '90.41'},
                               {'acq_date': start.isoformat(), 'latitude': '22.57', 'longitude': '88.36'})

        def run(through, key='k'):
            return import_year('viirs-snpp', 2025, 'Bangladesh', self.raw, fetch=lambda url: None, key=key,
                               sp_end={'VIIRS_SNPP_SP': through}, api_fetch=api_fetch)

        self.assertEqual(run(date(2025, 1, 10), key=None), ('unavailable', None))
        status, stats = run(date(2025, 1, 10))
        self.assertEqual(status, 'imported via API')
        self.assertEqual((stats['retained'], stats['outside_region']), (2, 2))  # Kolkata dropped
        self.assertEqual(run(date(2025, 1, 10)), ('unchanged', None))
        self.assertEqual(len(calls), 2)  # the repeat run made no requests
        self.assertEqual(run(date(2025, 1, 15))[0], 'imported via API')  # NASA published more days
        with connect() as connection:
            row = connection.execute('SELECT url, retained FROM archive_files WHERE year=2025').fetchone()
        self.assertEqual(row, {'url': 'firms-api:VIIRS_SNPP_SP/2025?through=2025-01-15', 'retained': 3})
        # Once NASA publishes the yearly file it replaces the API copy.
        self.files[2025] = archive_csv({'acq_date': '2025-04-01'})
        self.assertEqual(self.run_import(2025), ['imported'])

    def test_season_statistics(self):
        self.run_import(2012, 2013, 2014, 2015)
        now = datetime(2026, 9, 29, tzinfo=timezone.utc)
        with connect() as connection:
            product = season(connection, 'Bangladesh', now=now)['products'][0]
            everything = season(connection, 'Bangladesh', (0, 1, 2, 3), now=now)['products'][0]
            boxed = season(connection, 'Bangladesh', bbox=(89, 22, 91, 24), now=now)['products'][0]
            later = season(connection, 'Bangladesh', now=datetime(2014, 6, 1, tzinfo=timezone.utc))['products'][0]
        self.assertEqual(product['source'], 'VIIRS_SNPP_ARCHIVE')
        self.assertEqual(product['versions'], [{'version': '2', 'first_year': 2012, 'last_year': 2014}])
        self.assertEqual(product['complete_years'], [2013, 2014])
        self.assertEqual(product['partial_years'], [2012])
        self.assertEqual(product['unavailable_years'], [2015])
        april, march, may = product['months'][3], product['months'][2], product['months'][4]
        self.assertEqual((april['mean'], april['min'], april['max']), (2.0, 1, 3))  # 2013: 3, 2014: 1
        self.assertEqual((march['mean'], march['min'], march['max']), (0.5, 0, 1))  # zero-filled year
        self.assertEqual(may['mean'], 0.0)
        self.assertEqual(product['peak_month'], 4)
        self.assertEqual(product['by_year']['2012'][3], 1)
        self.assertEqual(product['other_types'], {'static_land': 1, 'offshore': 1})
        self.assertEqual(everything['months'][3]['max'], 4)
        self.assertEqual(boxed['by_year']['2013'][3], 2)  # latitude 25 is outside
        self.assertEqual(later['complete_years'], [2013])  # current year is never complete

    def test_api(self):
        client = TestClient(app)
        headers = {'Authorization': 'Bearer ' + create_access(1)}
        self.assertEqual(client.get('/v1/archive/season', headers=headers).status_code, 503)
        self.run_import(2013, 2014)
        body = client.get('/v1/archive/season?bbox=88,20,93,27', headers=headers).json()
        self.assertEqual(body['products'][0]['peak_month'], 4)
        self.assertIn('Months are in UTC.', body['limitations'])
