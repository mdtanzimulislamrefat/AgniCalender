import 'snapshot.dart';

/// How a saved area relates to the region the backend actually processed.
enum Coverage { inside, partial, outside }

class SensorStats {
  int count = 0;
  final quality = <String, int>{};
  String? latest;
}

class AreaStats {
  AreaStats(this.coverage, this.bySource);
  final Coverage coverage;

  /// Per source, never summed across sensors.
  final Map<String, SensorStats> bySource;
}

List<double> areaBox(Json area) => [
  for (final k in ['west', 'south', 'east', 'north'])
    (area[k] as num).toDouble(),
];

AreaStats areaStats(Snapshot snapshot, List<double> box) {
  final region = (snapshot.summary['bbox'] as List)
      .map((v) => (v as num).toDouble())
      .toList();
  final overlaps =
      box[0] < region[2] &&
      box[2] > region[0] &&
      box[1] < region[3] &&
      box[3] > region[1];
  final inside =
      box[0] >= region[0] &&
      box[1] >= region[1] &&
      box[2] <= region[2] &&
      box[3] <= region[3];
  final bySource = <String, SensorStats>{};
  for (final p in snapshot.points) {
    final lon = (p['longitude'] as num).toDouble();
    final lat = (p['latitude'] as num).toDouble();
    if (lon < box[0] || lon > box[2] || lat < box[1] || lat > box[3]) continue;
    final stats = bySource.putIfAbsent(p['source'] as String, SensorStats.new);
    stats.count++;
    final quality = p['quality'] as String? ?? 'unknown';
    stats.quality[quality] = (stats.quality[quality] ?? 0) + 1;
    final time = p['timestamp_utc'] as String?;
    if (time != null &&
        (stats.latest == null || time.compareTo(stats.latest!) > 0)) {
      stats.latest = time;
    }
  }
  return AreaStats(
    inside
        ? Coverage.inside
        : overlaps
        ? Coverage.partial
        : Coverage.outside,
    Map.fromEntries(
      bySource.entries.toList()..sort((a, b) => a.key.compareTo(b.key)),
    ),
  );
}
