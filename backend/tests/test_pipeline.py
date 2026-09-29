import csv
import io
import unittest

from backend.geo import Boundary, boundary
from backend.processing.pipeline import process, read_csv


def data(*changes):
    base = dict(latitude='23', longitude='90', acq_date='2026-09-29', acq_time='30',
                satellite='N', instrument='VIIRS', confidence='n', version='2.0NRT', frp='3')
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(base))
    writer.writeheader()
    for change in changes:
        writer.writerow(base | change)
    return output.getvalue().encode()


class PipelineTests(unittest.TestCase):
    def test_duplicates_and_observations(self):
        csv_data = data({}, {}, {'acq_time': '0130'}, {'satellite': 'N20'})
        report = process([('VIIRS_SNPP_NRT', csv_data)], (88, 20, 93, 27))
        self.assertEqual(report['counts']['exact_duplicates'], 1)
        self.assertEqual(report['counts']['retained'], 3)
        self.assertEqual(len(report['weekly_calendar']), 2)
        self.assertIn('T00:30:00+00:00', report['observations'][0]['timestamp_utc'])

    def test_invalid_and_bbox(self):
        report = process([('VIIRS_SNPP_NRT', data(
            {'latitude': 'nan'}, {'longitude': '200'}, {'acq_time': '2460'},
            {'acq_date': '2026-02-30'}, {'confidence': '75'},
            {'frp': 'inf'}, {'latitude': '15'}, {}))], (88, 20, 93, 27))
        self.assertEqual(report['counts']['invalid'], 6)
        self.assertEqual(report['counts']['outside_bbox'], 1)
        self.assertEqual(report['counts']['retained'], 1)

    def test_sensors_versions_and_confidence_remain_separate(self):
        report = process([
            ('VIIRS_SNPP_NRT', data({'confidence': 'l'}, {'version': '2.0'})),
            ('MODIS_NRT', data({'instrument': 'MODIS', 'satellite': 'Terra', 'confidence': '80'}))
        ], (88, 20, 93, 27))
        self.assertEqual(len(report['weekly_calendar']), 3)
        self.assertEqual({r['quality'] for r in report['observations']}, {'low', 'nominal', 'high'})
        self.assertTrue(all(r['harmonized'] is False for r in report['weekly_calendar']))

    def test_no_observations_not_fabricated_zero_calendar(self):
        report = process([('VIIRS_SNPP_NRT', data())], (88, 20, 93, 27))
        self.assertEqual(report['weekly_calendar'], [])

    def test_public_feed_without_instrument_and_full_confidence(self):
        feed = b'latitude,longitude,acq_date,acq_time,satellite,confidence,version\n23,90,2026-09-29,0015,N,nominal,2.0NRT\n'
        report = process([('VIIRS_SNPP_NRT', feed)], (88, 20, 93, 27))
        self.assertEqual(report['counts']['retained'], 1)
        self.assertEqual(report['observations'][0]['instrument'], 'VIIRS')

    def test_error_page_rejected(self):
        with self.assertRaises(ValueError):
            read_csv(b'<html>Service unavailable</html>')


if __name__ == '__main__':
    unittest.main()


class RegionTests(unittest.TestCase):
    def test_bangladesh_outline(self):
        bangladesh = boundary('bangladesh')
        for lon, lat in [(90.41, 23.81), (91.87, 24.89), (91.98, 21.43), (90.648, 22.686), (92.30, 20.86)]:
            self.assertTrue(bangladesh.contains(lon, lat), (lon, lat))  # Dhaka, Sylhet, Cox's Bazar, Bhola, Teknaf
        for lon, lat in [(88.36, 22.57), (91.28, 23.83), (91.88, 25.57), (92.90, 20.15), (90.5, 21.0)]:
            self.assertFalse(bangladesh.contains(lon, lat), (lon, lat))  # Kolkata, Agartala, Shillong, Sittwe, sea
        with self.assertRaises(ValueError):
            boundary('atlantis')

    def test_polygon_holes(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        hole = [(4, 4), (6, 4), (6, 6), (4, 6), (4, 4)]
        donut = Boundary('Donut', [[square, hole]], 'test')
        self.assertTrue(donut.contains(2, 2))
        self.assertFalse(donut.contains(5, 5))
        self.assertFalse(donut.contains(11, 5))

    def test_pipeline_keeps_only_detections_inside_region(self):
        csv_data = data({}, {'latitude': '23.81', 'longitude': '90.41'},
                        {'latitude': '22.57', 'longitude': '88.36'}, {'latitude': '23.83', 'longitude': '91.28'})
        report = process([('VIIRS_SNPP_NRT', csv_data)], (88, 20.5, 92.7, 26.7), boundary('bangladesh'))
        self.assertEqual(report['counts']['outside_region'], 2)
        self.assertEqual(report['counts']['retained'], 2)
        self.assertEqual(report['region']['name'], 'Bangladesh')
        self.assertIn('Bangladesh boundary', report['limitations'][0])
        unfiltered = process([('VIIRS_SNPP_NRT', csv_data)], (88, 20.5, 92.7, 26.7))
        self.assertEqual(unfiltered['counts']['retained'], 4)
        self.assertIsNone(unfiltered['region'])
