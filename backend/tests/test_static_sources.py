import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('AUTH_JWT_SECRET', 'test-only-secret-' + 'x' * 40)

import joblib
from fastapi.testclient import TestClient

from backend.api import hotspots as hotspots_api
from backend.api.auth import create_access
from backend.api.server import app, get_connection
from backend.ml import static_sources as ml


def detection(lat, lon, year, month, fire_type, source='VIIRS_SNPP_ARCHIVE', daynight='D', frp=5.0):
    return {'latitude': lat, 'longitude': lon, 'year': year, 'month': month, 'fire_type': fire_type,
            'source': source, 'daynight': daynight, 'frp_mw': frp}


def synthetic_archive(seed=0):
    """Factories burn year-round at night in fixed spots; crop fires move around in March-April."""
    rng = random.Random(seed)
    factories = [(22.47 + i * 0.05, 91.73) for i in range(6)]
    farms = [(23.0 + rng.random(), 89.5 + rng.random()) for _ in range(300)]
    rows = []
    for year in range(2013, 2026):
        for lat, lon in factories:
            for month in rng.sample(range(1, 13), 8):
                rows.append(detection(lat, lon, year, month, 2, daynight='N', frp=1.2))
        for lat, lon in rng.sample(farms, 120):
            for _ in range(3):
                rows.append(detection(lat + rng.uniform(-0.01, 0.01), lon + rng.uniform(-0.01, 0.01), year,
                                      rng.choice([3, 4]), 0, frp=rng.uniform(5, 40)))
    return rows


class FeatureTests(unittest.TestCase):
    def test_history_only_uses_earlier_years(self):
        history = ml.History.from_rows([(23.0, 90.0, y, m) for y in (2019, 2020, 2021, 2024, 2025) for m in (1, 7)])
        # For 2024: previous five years are 2019-2023, so 2024 and 2025 are never counted.
        self.assertEqual(history.around(23.0, 90.0, 2024), (3, 6, 2))
        self.assertEqual(history.own(23.0, 90.0, 2024), (3, 6, 2, 1))  # July is a monsoon month
        self.assertEqual(history.around(23.0, 90.0, 2019), (0, 0, 0))

    def test_neighbourhood_versus_own_cell(self):
        history = ml.History.from_rows([(23.0 + ml.CELL, 90.0, 2020, 4)])  # next cell north
        self.assertEqual(history.around(23.0, 90.0, 2021)[0], 1)
        self.assertEqual(history.own(23.0, 90.0, 2021)[0], 0)
        self.assertEqual(history.around(23.0 + 3 * ml.CELL, 90.0, 2021)[0], 0)

    def test_features_never_see_the_label(self):
        history = ml.History.from_rows([])
        point = detection(23, 90, 2024, 4, 0)
        self.assertEqual(ml.features(history, point, 2024, 4), ml.features(history, point | {'fire_type': 2}, 2024, 4))
        self.assertEqual(len(ml.features(history, point, 2024, 4)), len(ml.FEATURES))


class TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        rows = synthetic_archive()
        history = ml.History.from_rows((r['latitude'], r['longitude'], r['year'], r['month']) for r in rows)
        cls.metrics = ml.train(rows, history, model_dir=cls.dir)
        cls.history = history

    def test_evaluated_on_unseen_year_and_saved(self):
        m = self.metrics
        self.assertEqual((m['train_years'], m['validation_year'], m['test_year']), ([2018, 2023], 2024, 2025))
        self.assertGreater(m['test']['precision'], 0.9)
        self.assertGreater(m['test']['recall'], 0.9)
        self.assertIn('rule', m['baseline'])
        self.assertEqual(json.loads((self.dir / ml.METRICS_FILE.name).read_text())['test'], m['test'])

    def test_annotate_flags_the_factory_not_the_farm(self):
        bundle = joblib.load(self.dir / ml.MODEL_FILE.name)
        model = ml.Classifier(bundle, self.metrics, self.history)
        points = model.annotate([
            {'source': 'VIIRS_NOAA21_NRT', 'latitude': 22.47, 'longitude': 91.73, 'frp_mw': 1.1, 'daynight': 'N',
             'timestamp_utc': '2026-09-20T19:00:00+00:00'},
            {'source': 'VIIRS_NOAA21_NRT', 'latitude': 25.9, 'longitude': 88.3, 'frp_mw': 20.0, 'daynight': 'D',
             'timestamp_utc': '2026-04-10T07:00:00+00:00'},
        ])
        self.assertTrue(points[0]['likely_static'])
        self.assertFalse(points[1]['likely_static'])
        self.assertGreater(points[0]['static_probability'], points[1]['static_probability'])
        self.assertEqual(model.summary()['test_year'], 2025)

    def test_refuses_to_train_without_labels(self):
        rows = [r | {'fire_type': 0} for r in synthetic_archive()]
        with self.assertRaises(ValueError):
            ml.train(rows, self.history, model_dir=Path(tempfile.mkdtemp()))


class ApiTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_connection] = lambda: None
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.headers = {'Authorization': 'Bearer ' + create_access(1)}
        point = {'source': 'VIIRS_NOAA21_NRT', 'satellite': 'N21', 'version': '2.0NRT', 'latitude': 22.47,
                 'longitude': 91.73, 'quality': 'nominal', 'confidence_raw': 'n', 'frp_mw': 1.1, 'daynight': 'N',
                 'timestamp_utc': '2026-09-20T19:00:00+00:00'}
        self.recent = {'kind': 'recent', 'label': 'Last 7 days', 'start_utc': '2026-09-23T00:00:00+00:00',
                       'end_utc': '2026-09-29T12:00:00+00:00', 'fire_type_known': False, 'excluded_other_types': 0,
                       'missing_days': [], 'counts_by_source': {'VIIRS_NOAA21_NRT': 1}, 'points': [point]}

    def get(self):
        with patch.object(hotspots_api.hotspots, 'recent', return_value=dict(self.recent)):
            return self.client.get('/v1/map/hotspots?window=7d', headers=self.headers).json()

    def test_without_model_points_are_unflagged(self):
        with patch.object(hotspots_api, 'classifier', return_value=None):
            body = self.get()
        self.assertIsNone(body['classifier'])
        self.assertIsNone(body['points'][0]['likely_static'])

    def test_with_model_points_are_flagged_and_metrics_shown(self):
        class Fake:
            def annotate(self, points):
                return [p | {'static_probability': 0.97, 'likely_static': True} for p in points]

            def summary(self):
                return {'model': 'HistGradientBoostingClassifier', 'trained_at_utc': '2026-09-29T00:00:00+00:00',
                        'train_years': [2018, 2023], 'test_year': 2025, 'threshold': 0.43, 'test_precision': 0.595,
                        'test_recall': 0.481, 'baseline_rule': 'r', 'baseline_precision': 0.03,
                        'baseline_recall': 0.77}
        with patch.object(hotspots_api, 'classifier', return_value=Fake()):
            body = self.get()
        self.assertTrue(body['points'][0]['likely_static'])
        self.assertEqual(body['classifier']['test_precision'], 0.595)
