"""Country boundary test for detections (Natural Earth 1:10m, public domain)."""
import functools
import json

from backend.paths import BACKEND_DIR

BOUNDARY_DIR = BACKEND_DIR / 'data' / 'boundaries'
REGIONS = {'bangladesh': 'Bangladesh'}


class Boundary:
    """MultiPolygon with holes; even-odd ray casting per polygon."""

    def __init__(self, name, polygons, source):
        self.name, self.polygons, self.source = name, polygons, source
        xs = [x for polygon in polygons for ring in polygon for x, _ in ring]
        ys = [y for polygon in polygons for ring in polygon for _, y in ring]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _in_ring(lon, lat, ring):
        inside = False
        j = len(ring) - 1
        for i in range(len(ring)):
            (xi, yi), (xj, yj) = ring[i], ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def contains(self, lon, lat):
        west, south, east, north = self.bbox
        if not (west <= lon <= east and south <= lat <= north):
            return False
        for outer, *holes in self.polygons:
            if self._in_ring(lon, lat, outer) and not any(self._in_ring(lon, lat, h) for h in holes):
                return True
        return False


@functools.cache
def boundary(region):
    """Load a bundled boundary by key, e.g. 'bangladesh'."""
    if region not in REGIONS:
        raise ValueError(f'Unknown region {region!r}; choose from {", ".join(REGIONS)}')
    feature = json.loads((BOUNDARY_DIR / f'{region}.geojson').read_text())['features'][0]
    geometry = feature['geometry']
    polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
    return Boundary(REGIONS[region], [[[tuple(p[:2]) for p in ring] for ring in polygon] for polygon in polygons],
                    feature['properties']['source'])
