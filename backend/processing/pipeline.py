"""Conservative FIRMS CSV preparation. Python standard library only."""
import argparse
import csv
import hashlib
import io
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

from backend.storage.reports import latest_report, save_report
import psycopg
from backend.geo import REGIONS, boundary
from backend.paths import OUTPUT_DIR, RAW_DIR

FEEDS = {
    'VIIRS_SNPP_NRT': 'https://firms.modaps.eosdis.nasa.gov/data/active_fire/viirs/csv/SUOMI_VIIRS_C2_South_Asia_24h.csv',
    'MODIS_NRT': 'https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis/csv/MODIS_C6_1_South_Asia_24h.csv',
}
REQUIRED = {'latitude', 'longitude', 'acq_date', 'acq_time', 'satellite', 'confidence', 'version'}


def read_csv(data):
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
    if not REQUIRED.issubset(reader.fieldnames or []):
        raise ValueError('Not a supported FIRMS CSV: required columns missing')
    return list(reader)


def bbox_value(text):
    try:
        west, south, east, north = map(float, text.split(','))
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError()
        return west, south, east, north
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use west,south,east,north; antimeridian boxes unsupported') from exc


def normalize(row, source):
    lat, lon = float(row['latitude']), float(row['longitude'])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('invalid coordinate')
    day = date.fromisoformat(row['acq_date'].strip())
    time = row['acq_time'].strip().zfill(4)
    if len(time) != 4 or not time.isdigit():
        raise ValueError('invalid time')
    stamp = datetime(day.year, day.month, day.day, int(time[:2]), int(time[2:]), tzinfo=timezone.utc)
    instrument = row.get('instrument', source.split('_')[0]).strip().upper()
    if instrument not in ('MODIS', 'VIIRS') or not source.startswith(instrument):
        raise ValueError('source/instrument mismatch')
    satellite = row['satellite'].strip()
    version = row['version'].strip()
    if not satellite or not version:
        raise ValueError('missing provenance')
    confidence = row['confidence'].strip().lower()
    if instrument == 'MODIS':
        number = float(confidence)
        if not 0 <= number <= 100:
            raise ValueError('invalid MODIS confidence')
        quality = 'low' if number < 30 else 'nominal' if number < 80 else 'high'
    else:
        quality = {'l': 'low', 'n': 'nominal', 'h': 'high',
                   'low': 'low', 'nominal': 'nominal', 'high': 'high'}.get(confidence)
        if quality is None:
            raise ValueError('invalid VIIRS confidence')
    frp = row.get('frp', '').strip()
    frp = float(frp) if frp else None
    if frp is not None and (not math.isfinite(frp) or frp < 0):
        raise ValueError('invalid FRP')
    return dict(latitude=lat, longitude=lon, timestamp_utc=stamp.isoformat(),
                source=source, instrument=instrument, satellite=satellite,
                version=version, quality=quality, confidence_raw=confidence,
                frp_mw=frp, daynight=row.get('daynight', '').strip())


def process(inputs, bbox, region=None):
    """inputs: iterable of (product label, CSV bytes). Never merge sensors.
    region: optional geo.Boundary; detections outside it are counted and dropped."""
    stats = Counter(rows_read=0, invalid=0, outside_bbox=0, outside_region=0, exact_duplicates=0, retained=0)
    reasons = Counter()
    seen = set()
    observations = []
    west, south, east, north = bbox
    for source, data in inputs:
        for row in read_csv(data):
            stats['rows_read'] += 1
            try:
                record = normalize(row, source)
            except (ValueError, TypeError, AttributeError) as exc:
                stats['invalid'] += 1
                reasons[str(exc)] += 1
                continue
            if not (west <= record['longitude'] <= east and south <= record['latitude'] <= north):
                stats['outside_bbox'] += 1
                continue
            if region is not None and not region.contains(record['longitude'], record['latitude']):
                stats['outside_region'] += 1
                continue
            # Only exact duplicate rows within the same source are removed.
            # Different times, platforms, versions or overlapping pixels survive.
            identity = (source, json.dumps(row, sort_keys=True))
            if identity in seen:
                stats['exact_duplicates'] += 1
                continue
            seen.add(identity)
            observations.append(record)
            stats['retained'] += 1
    observations.sort(key=lambda r: (r['timestamp_utc'], r['source'], r['satellite'], r['latitude'], r['longitude']))
    groups = defaultdict(list)
    for record in observations:
        day = date.fromisoformat(record['timestamp_utc'][:10])
        monday = day - timedelta(days=day.weekday())
        key = (record['source'], record['satellite'], record['version'], str(monday))
        groups[key].append(record)
    calendar = []
    for (source, satellite, version, week), records in sorted(groups.items()):
        quality = Counter(r['quality'] for r in records)
        calendar.append(dict(source=source, satellite=satellite, version=version,
                             week_start_utc=week, detection_count=len(records),
                             quality_counts=dict(quality),
                             dates_with_detections=len({r['timestamp_utc'][:10] for r in records}),
                             observation_coverage='unknown', harmonized=False))
    area_notes = [
        f'Only detections inside the {region.name} boundary ({region.source}) are kept; '
        'pixels straddling the border may fall on either side.',
        'Very small islands (such as Saint Martin\'s Island) are not in this outline.',
    ] if region is not None else ['Bounding box is not an administrative boundary.']
    return dict(schema_version=1, bbox=list(bbox), timezone='UTC',
                region=dict(name=region.name, source=region.source) if region is not None else None,
                counts=dict(stats), invalid_reasons=dict(reasons),
                observations=observations, weekly_calendar=calendar,
                limitations=[
                    *area_notes,
                    'Counts are detected pixels, not distinct fires or burned area.',
                    'No cross-sensor harmonization or anomaly prediction has been applied.',
                    'No-detection dates are not evidence of no fire; coverage is unknown.',
                    'Weeks may be partial. Different versions/products remain separate.',
                    'Low-confidence detections are retained and labeled, not silently discarded.',
                ])


DEFAULT_BBOX = (88.0, 20.5, 92.7, 26.7)


def ingest(source, data, origin, raw_dir, retrieved=None):
    """Validate one CSV, keep its raw bytes by SHA-256; returns (input, manifest entry)."""
    read_csv(data)  # reject error pages before saving
    digest = hashlib.sha256(data).hexdigest()
    snapshot = raw_dir / f'{digest}.csv'
    if not snapshot.exists():
        snapshot.write_bytes(data)
    return (source, data), dict(source=source, origin=origin, sha256=digest,
                                snapshot=str(snapshot), retrieved_at_utc=retrieved)


def download_feeds(raw_dir):
    inputs, manifest = [], []
    for source, url in FEEDS.items():
        with urlopen(url, timeout=60) as response:
            data = response.read()
        item, entry = ingest(source, data, url, raw_dir, datetime.now(timezone.utc).isoformat())
        inputs.append(item)
        manifest.append(entry)
    return inputs, manifest


def publish(inputs, manifest, bbox, region, output_dir):
    """Process, write report.json and store the report; returns (report id, report)."""
    result = process(inputs, bbox, None if region == 'none' else boundary(region))
    result['inputs'] = manifest
    result['generated_at_utc'] = datetime.now(timezone.utc).isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / 'report.json'
    temporary = output_dir / 'report.json.tmp'
    temporary.write_text(json.dumps(result, indent=2) + '\n')
    report_id = save_report(result)
    temporary.replace(target)
    return report_id, result


def same_as_latest(manifest, bbox, region):
    """True when NASA's feeds are byte-identical to the newest stored report's inputs."""
    report = latest_report()
    if report is None:
        return False
    region_name = None if region == 'none' else boundary(region).name
    return (report.get('bbox') == list(bbox)
            and (report.get('region') or {}).get('name') == region_name
            and sorted((i['source'], i['sha256']) for i in report.get('inputs', []))
            == sorted((i['source'], i['sha256']) for i in manifest))


def fetch_latest(raw_dir=RAW_DIR, output_dir=OUTPUT_DIR, bbox=DEFAULT_BBOX, region='bangladesh'):
    """Download the rolling feeds and publish a report unless they are unchanged.

    Returns {'status': 'new', 'report_id', 'counts'} or {'status': 'unchanged'}.
    Raises OSError/ValueError (download or bad file) and psycopg.Error (database).
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    inputs, manifest = download_feeds(raw_dir)
    if same_as_latest(manifest, bbox, region):
        return {'status': 'unchanged'}
    report_id, result = publish(inputs, manifest, bbox, region, output_dir)
    return {'status': 'new', 'report_id': report_id, 'counts': result['counts']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', action='append', default=[], metavar='SOURCE=FILE')
    parser.add_argument('--fetch', action='store_true', help='Fetch public rolling 24-hour South Asia feeds')
    parser.add_argument('--bbox', type=bbox_value, default=DEFAULT_BBOX)
    parser.add_argument('--region', choices=[*REGIONS, 'none'], default='bangladesh',
                        help='Keep only detections inside this country outline (none = whole bbox)')
    parser.add_argument('--output', type=Path, default=OUTPUT_DIR)
    parser.add_argument('--raw-dir', type=Path, default=RAW_DIR)
    args = parser.parse_args()
    if bool(args.input) == args.fetch:
        parser.error('Provide either --input SOURCE=FILE or --fetch')
    try:
        if args.fetch:
            outcome = fetch_latest(args.raw_dir, args.output, args.bbox, args.region)
            if outcome['status'] == 'unchanged':
                print('NASA feeds are unchanged since the latest report; nothing new stored.')
                return
            counts = outcome['counts']
        else:
            args.raw_dir.mkdir(parents=True, exist_ok=True)
            inputs, manifest = [], []
            for item in args.input:
                source, filename = item.split('=', 1)
                data, entry = ingest(source, Path(filename).read_bytes(), filename, args.raw_dir)
                inputs.append(data)
                manifest.append(entry)
            counts = publish(inputs, manifest, args.bbox, args.region, args.output)[1]['counts']
        print(json.dumps(counts, indent=2))
        print(f'Report: {args.output / "report.json"}')
    except psycopg.Error:
        parser.exit(1, 'PostgreSQL unavailable or schema missing; run the migration command.\n')
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f'Processing failed: {exc}\n')


if __name__ == '__main__':
    main()
