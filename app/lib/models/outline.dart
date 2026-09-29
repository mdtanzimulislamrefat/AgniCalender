import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:latlong2/latlong.dart';

/// Bangladesh outline (Natural Earth 1:10m, public domain) bundled with the
/// app; the backend keeps only detections inside the same outline.
class Outline {
  Outline(this.polygons);

  /// Each polygon: outer ring first, then any holes.
  final List<List<List<LatLng>>> polygons;

  // Cache the parsed value, not a Future, so no caller awaits a Future
  // created in another zone.
  static Outline? _cached;
  static Future<Outline> load() async => _cached ??= parse(
    await rootBundle.loadString('assets/bangladesh.geojson'),
  );

  static Outline parse(String geojson) {
    final geometry =
        ((jsonDecode(geojson) as Map)['features'] as List).first['geometry']
            as Map;
    final coords = geometry['coordinates'] as List;
    final polygons = geometry['type'] == 'MultiPolygon' ? coords : [coords];
    return Outline([
      for (final polygon in polygons)
        [
          for (final ring in polygon as List)
            [
              for (final p in ring as List)
                LatLng(
                  ((p as List)[1] as num).toDouble(),
                  (p[0] as num).toDouble(),
                ),
            ],
        ],
    ]);
  }
}
