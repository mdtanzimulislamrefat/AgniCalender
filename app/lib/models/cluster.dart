import 'dart:math' as math;
import 'dart:ui';

import 'package:latlong2/latlong.dart';

import 'snapshot.dart';

/// Hotspots that fall within one grid cell on screen at the current zoom.
class Cluster {
  Cluster(this.points)
    : center = LatLng(
        points.map(_lat).reduce((a, b) => a + b) / points.length,
        points.map(_lon).reduce((a, b) => a + b) / points.length,
      );
  final List<Json> points;
  final LatLng center;

  static double _lat(Json p) => (p['latitude'] as num).toDouble();
  static double _lon(Json p) => (p['longitude'] as num).toDouble();

  int get size => points.length;
  Set<String> get sources => {for (final p in points) p['source'] as String};
  bool get allLikelyStatic => points.every((p) => p['likely_static'] == true);

  /// Largest latitude/longitude span in degrees; ~0 when every satellite saw
  /// the same spot, so zooming in would not separate them.
  double get extent {
    final lats = points.map(_lat), lons = points.map(_lon);
    return math.max(
      lats.reduce(math.max) - lats.reduce(math.min),
      lons.reduce(math.max) - lons.reduce(math.min),
    );
  }
}

/// Groups points whose projected positions share a [cellPx]-sized grid cell.
/// [project] maps a position to pixels at the current zoom (independent of
/// panning), so clusters only change when the zoom does. Order is stable.
List<Cluster> clusterPoints(
  List<Json> points,
  Offset Function(LatLng) project, {
  double cellPx = 40,
}) {
  final cells = <(int, int), List<Json>>{};
  for (final p in points) {
    final at = project(LatLng(Cluster._lat(p), Cluster._lon(p)));
    cells
        .putIfAbsent((
          (at.dx / cellPx).floor(),
          (at.dy / cellPx).floor(),
        ), () => [])
        .add(p);
  }
  return [for (final members in cells.values) Cluster(members)];
}
