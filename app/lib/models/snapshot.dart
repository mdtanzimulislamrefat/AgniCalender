typedef Json = Map<String, dynamic>;

class Snapshot {
  Snapshot(this.summary, this.weeks, this.points, this.savedAt);
  final Json summary;
  final List<Json> weeks;
  final List<Json> points;
  final DateTime savedAt;
  int get id => summary['report_id'] as int;
  bool get stale {
    final end = DateTime.tryParse(summary['data_end_utc'] as String? ?? '');
    return summary['stale'] == true ||
        end == null ||
        DateTime.now().toUtc().difference(end).inHours >= 48;
  }

  factory Snapshot.fromJson(Json json) => Snapshot(
    Json.from(json['summary'] as Map),
    (json['weeks'] as List).map((e) => Json.from(e as Map)).toList(),
    (json['points'] as List).map((e) => Json.from(e as Map)).toList(),
    DateTime.parse(json['saved_at'] as String),
  );
  Json toJson() => {
    'summary': summary,
    'weeks': weeks,
    'points': points,
    'saved_at': savedAt.toIso8601String(),
  };
}
