"""Flag likely non-vegetation heat sources (industry, brick kilns, gas flares) in recent NRT hotspots.

NASA's recent (NRT) API files have no fire `type`; the archive does. We learn from the archive
labels using only *where and when* hotspots occur, never the label itself, and history strictly
from earlier years:

    train 2018-2023  ->  pick threshold on 2024  ->  report once on 2025 (never seen)

Usage: python -m backend.ml.static_sources train
"""
import argparse
import functools
import json
import math
from collections import defaultdict
from datetime import datetime, timezone

from backend.paths import OUTPUT_DIR
from backend.storage.reports import connect

MODEL_DIR = OUTPUT_DIR / 'models'
MODEL_FILE, METRICS_FILE = MODEL_DIR / 'static_source.joblib', MODEL_DIR / 'static_source.json'
CELL = 0.005  # degrees, ~550 m; features use the 3x3 neighbourhood (~1.6 km)
HISTORY_YEARS = 5
TRAIN_YEARS, VALIDATION_YEAR, TEST_YEAR = range(2018, 2024), 2024, 2025
FEATURES = ['years_active', 'log_detections', 'months_active', 'night', 'log_frp', 'frp_missing',
            'month_sin', 'month_cos', 'modis',
            'cell_years_active', 'cell_log_detections', 'cell_months_active', 'cell_monsoon_months']
MONSOON = 0b001111100000  # June-October: few vegetation fires, so activity then hints at industry


def cell(lat, lon):
    return round(lat / CELL), round(lon / CELL)


class History:
    """Per grid cell: detections and active months per year. Built from positions and times only."""

    def __init__(self):
        self.counts = defaultdict(lambda: defaultdict(int))
        self.months = defaultdict(lambda: defaultdict(int))  # bitmask of months

    @classmethod
    def from_rows(cls, rows):
        """rows: iterable of (latitude, longitude, year, month)."""
        history = cls()
        for lat, lon, year, month in rows:
            key = cell(lat, lon)
            history.counts[key][year] += 1
            history.months[key][year] |= 1 << (month - 1)
        return history

    def around(self, lat, lon, year):
        """(years active, detections, months active) in the HISTORY_YEARS before `year`."""
        ci, cj = cell(lat, lon)
        years, total, mask = set(), 0, 0
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                counts = self.counts.get((ci + di, cj + dj))
                if not counts:
                    continue
                months = self.months[(ci + di, cj + dj)]
                for y in range(year - HISTORY_YEARS, year):
                    if counts.get(y):
                        years.add(y)
                        total += counts[y]
                        mask |= months[y]
        return len(years), total, bin(mask).count('1')

    def own(self, lat, lon, year):
        """Like around() for the point's own cell only, plus active monsoon months."""
        key = cell(lat, lon)
        counts, months = self.counts.get(key, {}), self.months.get(key, {})
        years = [y for y in range(year - HISTORY_YEARS, year) if counts.get(y)]
        mask = 0
        for y in years:
            mask |= months[y]
        return (len(years), sum(counts[y] for y in years), bin(mask).count('1'),
                bin(mask & MONSOON).count('1'))


def features(history, point, year, month):
    """Feature vector for one detection; `point` needs latitude, longitude, frp_mw, daynight, source."""
    years_active, total, months_active = history.around(point['latitude'], point['longitude'], year)
    own_years, own_total, own_months, own_monsoon = history.own(point['latitude'], point['longitude'], year)
    frp = point.get('frp_mw')
    return [years_active, math.log1p(total), months_active, 1.0 if point.get('daynight') == 'N' else 0.0,
            math.log1p(frp) if frp is not None else 0.0, 1.0 if frp is None else 0.0,
            math.sin(2 * math.pi * month / 12), math.cos(2 * math.pi * month / 12),
            1.0 if point['source'].startswith('MODIS') else 0.0,
            own_years, math.log1p(own_total), own_months, own_monsoon]


def load_archive(connection):
    rows = connection.execute(
        'SELECT f.source, d.latitude, d.longitude, d.frp_mw, d.daynight, d.fire_type, '
        'extract(year FROM d.observed_at)::int AS year, extract(month FROM d.observed_at)::int AS month '
        'FROM archive_detections d JOIN archive_files f ON f.id = d.file_id').fetchall()
    return rows, History.from_rows((r['latitude'], r['longitude'], r['year'], r['month']) for r in rows)


def dataset(rows, history, years):
    import numpy as np
    chosen = [r for r in rows if r['year'] in years]
    x = np.array([features(history, r, r['year'], r['month']) for r in chosen], dtype=float)
    y = np.array([r['fire_type'] != 0 for r in chosen], dtype=int)
    return x, y


def scores(y, predicted):
    tp = int(((predicted == 1) & (y == 1)).sum())
    fp = int(((predicted == 1) & (y == 0)).sum())
    fn = int(((predicted == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {'precision': round(precision, 3), 'recall': round(recall, 3), 'f1': round(f1, 3),
            'flagged': tp + fp, 'true_positives': tp}


def best_threshold(y, probability):
    """Threshold with the best F1 on validation data (never on the test year)."""
    candidates = [t / 100 for t in range(5, 96)]
    return max(candidates, key=lambda t: (scores(y, (probability >= t).astype(int))['f1'], -t))


def train(rows, history, model_dir=MODEL_DIR, now=None):
    """Train, pick the threshold on the validation year, evaluate once on the test year, save."""
    import joblib
    import numpy as np
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import average_precision_score

    x_train, y_train = dataset(rows, history, set(TRAIN_YEARS))
    x_val, y_val = dataset(rows, history, {VALIDATION_YEAR})
    x_test, y_test = dataset(rows, history, {TEST_YEAR})
    if min(y_train.sum(), y_val.sum(), y_test.sum()) == 0:
        raise ValueError('Every split needs labelled non-vegetation detections; import the archive first')
    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, random_state=0)
    model.fit(x_train, y_train)
    threshold = best_threshold(y_val, model.predict_proba(x_val)[:, 1])
    test_probability = model.predict_proba(x_test)[:, 1]
    # Baseline: "hotspots here in at least k of the previous 5 years", k chosen on validation too.
    k = max(range(1, HISTORY_YEARS + 1), key=lambda k: scores(y_val, (x_val[:, 0] >= k).astype(int))['f1'])
    metrics = {
        'trained_at_utc': (now or datetime.now(timezone.utc)).isoformat(),
        'model': 'HistGradientBoostingClassifier', 'sklearn': sklearn.__version__,
        'label': 'FIRMS type != 0 (static land source, offshore, volcano) vs 0 (presumed vegetation fire)',
        'features': FEATURES, 'train_years': [min(TRAIN_YEARS), max(TRAIN_YEARS)],
        'validation_year': VALIDATION_YEAR, 'test_year': TEST_YEAR,
        'rows': {'train': len(y_train), 'validation': len(y_val), 'test': len(y_test)},
        'positive_rate_test': round(float(y_test.mean()), 4), 'threshold': threshold,
        'test': scores(y_test, (test_probability >= threshold).astype(int))
        | {'average_precision': round(float(average_precision_score(y_test, test_probability)), 3)},
        'baseline': {'rule': f'hotspots in >= {k} of the previous {HISTORY_YEARS} years'}
        | scores(y_test, (x_test[:, 0] >= k).astype(int)),
        'note': 'Features were chosen on the 2024 validation year. An earlier feature set (first 9 features) '
                'was also scored once on 2025 (average precision 0.603) before the cell-level features were added.',
    }
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump({'model': model, 'threshold': threshold, 'features': FEATURES}, model_dir / MODEL_FILE.name)
    (model_dir / METRICS_FILE.name).write_text(json.dumps(metrics, indent=2) + '\n')
    return metrics


class Classifier:
    """The saved model plus the archive history it needs for features."""

    def __init__(self, bundle, metrics, history):
        self.model, self.threshold = bundle['model'], bundle['threshold']
        self.metrics, self.history = metrics, history

    def annotate(self, points):
        """Add static_probability and likely_static to each point (dicts with timestamp_utc)."""
        if not points:
            return points
        rows = []
        for p in points:
            stamp = datetime.fromisoformat(str(p['timestamp_utc']))
            rows.append(features(self.history, p, stamp.year, stamp.month))
        for p, probability in zip(points, self.model.predict_proba(rows)[:, 1]):
            p['static_probability'] = round(float(probability), 2)
            p['likely_static'] = bool(probability >= self.threshold)
        return points

    def summary(self):
        m = self.metrics
        return {'model': m['model'], 'trained_at_utc': m['trained_at_utc'],
                'train_years': m['train_years'], 'test_year': m['test_year'], 'threshold': m['threshold'],
                'test_precision': m['test']['precision'], 'test_recall': m['test']['recall'],
                'baseline_rule': m['baseline']['rule'], 'baseline_precision': m['baseline']['precision'],
                'baseline_recall': m['baseline']['recall']}


@functools.cache
def classifier(model_dir=MODEL_DIR):
    """Load once per process; None when no model has been trained."""
    if not (model_dir / MODEL_FILE.name).exists():
        return None
    import joblib  # our own file, written by train()
    bundle = joblib.load(model_dir / MODEL_FILE.name)
    metrics = json.loads((model_dir / METRICS_FILE.name).read_text())
    with connect() as connection:
        history = History.from_rows(
            (r['latitude'], r['longitude'], r['year'], r['month']) for r in connection.execute(
                'SELECT latitude, longitude, extract(year FROM observed_at)::int AS year, '
                'extract(month FROM observed_at)::int AS month FROM archive_detections'))
    return Classifier(bundle, metrics, history)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['train'])
    parser.parse_args()
    with connect() as connection:
        rows, history = load_archive(connection)
    m = train(rows, history)
    t, b = m['test'], m['baseline']
    print(f"Test year {m['test_year']}: {m['rows']['test']} detections, "
          f"{m['positive_rate_test']:.1%} non-vegetation")
    print(f"  model    precision {t['precision']:.1%}  recall {t['recall']:.1%}  F1 {t['f1']:.3f}  "
          f"(threshold {m['threshold']}, average precision {t['average_precision']})")
    print(f"  baseline precision {b['precision']:.1%}  recall {b['recall']:.1%}  F1 {b['f1']:.3f}  ({b['rule']})")
    print(f'Saved {MODEL_FILE}')


if __name__ == '__main__':
    main()
