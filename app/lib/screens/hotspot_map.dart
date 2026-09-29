import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../models/cluster.dart';
import '../models/outline.dart';
import '../models/snapshot.dart';
import '../services/agni_api.dart';
import '../widgets/ui.dart';
import 'name_dialog.dart';

/// Hotspot map with a time window: the 24-hour snapshot, the last 7 or 30
/// days (near-real-time), or one month from the archive.
class HotspotMap extends StatefulWidget {
  const HotspotMap({super.key, required this.api, required this.snapshot});
  final AgniApi api;
  final Snapshot snapshot;
  @override
  State<HotspotMap> createState() => _HotspotMapState();
}

class _HotspotMapState extends State<HotspotMap> {
  final controller = MapController();
  final hits = LayerHitNotifier<int>(null);

  /// Zoom the clusters were computed for (half-step buckets); null until the map is ready.
  double? clusterZoom;

  /// '24h', '7d', '30d' or an archive month 'YYYY-MM'.
  String window = '24h';
  Json? windowData;
  bool loading = false;
  String? error;
  int request = 0;
  Json? available;
  String source = 'All';

  /// Hide recent hotspots the model marks as likely industrial.
  bool hideIndustrial = false;
  Json? selected;
  bool framing = false;
  Size size = Size.zero;
  Outline? outline;

  @override
  void initState() {
    super.initState();
    Outline.load().then((value) {
      if (mounted) setState(() => outline = value);
    }, onError: (_) {});
  }

  @override
  void dispose() {
    controller.dispose();
    hits.dispose();
    super.dispose();
  }

  void notify(String text) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));

  bool get isMonth => !{'24h', '7d', '30d'}.contains(window);

  List<Json> get all => window == '24h'
      ? widget.snapshot.points
      : [
          for (final p in (windowData?['points'] as List?) ?? const [])
            Json.from(p as Map),
        ];

  List<Json> get shown => all
      .where((p) => source == 'All' || p['source'] == source)
      .where((p) => !(hideIndustrial && p['likely_static'] == true))
      .toList();

  /// Model summary for recent windows, or null.
  Json? get model => windowData?['classifier'] == null
      ? null
      : Json.from(windowData!['classifier'] as Map);

  int get flagged => all.where((p) => p['likely_static'] == true).length;

  String percent(dynamic v) => '${((v as num) * 100).round()}%';

  String get period => switch (window) {
    '24h' => 'the last 24 hours',
    '7d' => 'the last 7 days',
    '30d' => 'the last 30 days',
    _ => windowData?['label'] as String? ?? window,
  };

  Future<void> choose(String next) async {
    final ticket = ++request;
    setState(() {
      window = next;
      windowData = null;
      error = null;
      selected = null;
      source = 'All';
      hideIndustrial = false;
      framing = false;
      loading = next != '24h';
    });
    if (next == '24h') return;
    try {
      final result = await widget.api.hotspots(
        window: isMonth ? null : next,
        month: isMonth ? next : null,
      );
      if (mounted && ticket == request) setState(() => windowData = result);
    } on AuthException {
      // Session ended: the sign-in gate takes over.
    } catch (_) {
      if (mounted && ticket == request) {
        setState(
          () => error =
              'Could not load these hotspots. Check the server and retry.',
        );
      }
    } finally {
      if (mounted && ticket == request) setState(() => loading = false);
    }
  }

  Future<void> pickMonth() async {
    try {
      available ??= await widget.api.mapAvailable();
    } on AuthException {
      return;
    } catch (_) {
      notify('Cannot reach the server to list months.');
      return;
    }
    if (!mounted) return;
    final counts = {
      for (final m in available!['archive_months'] as List)
        (m as Map)['month'] as String: m['count'] as int,
    };
    if (counts.isEmpty) {
      notify('No archive months on the server yet.');
      return;
    }
    final years = counts.keys.map((m) => m.substring(0, 4)).toSet().toList()
      ..sort();
    final picked = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => _MonthPicker(
        years: years,
        counts: counts,
        initial: isMonth ? window : null,
      ),
    );
    if (picked != null) await choose(picked);
  }

  /// The on-screen frame is a centred square covering 60% of the map.
  Rect get frame {
    final side = size.shortestSide * 0.6;
    return Rect.fromCenter(
      center: size.center(Offset.zero),
      width: side,
      height: side,
    );
  }

  Future<void> saveFramedArea() async {
    final camera = controller.camera;
    final nw = camera.screenOffsetToLatLng(frame.topLeft);
    final se = camera.screenOffsetToLatLng(frame.bottomRight);
    double round(double v) => double.parse(v.toStringAsFixed(4));
    final box = [
      round(nw.longitude.clamp(-180.0, 180.0)),
      round(se.latitude.clamp(-90.0, 90.0)),
      round(se.longitude.clamp(-180.0, 180.0)),
      round(nw.latitude.clamp(-90.0, 90.0)),
    ];
    final name = await askName(
      context,
      title: 'Name this area',
      label: 'Area name',
      hint: 'e.g. My village',
    );
    if (name == null || name.isEmpty) return;
    try {
      await widget.api.createArea(name, box);
      if (mounted) setState(() => framing = false);
      notify('Saved "$name". See it under Account.');
    } on AuthException catch (e) {
      notify(e.message);
    } on ApiException catch (e) {
      notify(e.message);
    } catch (_) {
      notify('Cannot reach the server. The area was not saved.');
    }
  }

  LatLng at(Json p) => LatLng(
    (p['latitude'] as num).toDouble(),
    (p['longitude'] as num).toDouble(),
  );

  Widget dot(Json p) => Container(
    width: 18,
    height: 18,
    decoration: BoxDecoration(
      color: sensorColor(p['source'] as String),
      shape: BoxShape.circle,
      border: Border.all(color: Colors.white, width: 2),
    ),
  );

  Widget details(Json p) {
    final src = p['source'] as String;
    final frp = p['frp_mw'];
    final pass = switch (p['daynight']) {
      'D' => ' • day pass',
      'N' => ' • night pass',
      _ => '',
    };
    return Card(
      elevation: 4,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 8, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                dot(p),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    sourceLabel(src),
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                ),
                IconButton(
                  tooltip: 'Close',
                  icon: const Icon(Icons.close),
                  onPressed: () => setState(() => selected = null),
                ),
              ],
            ),
            Text('${utcTime(p['timestamp_utc'])}$pass'),
            Text('Confidence: ${p['quality']}'),
            if (p['likely_static'] == true)
              Text(
                'Likely industrial heat source (model: ${percent(p['static_probability'])})',
                style: const TextStyle(fontWeight: FontWeight.bold),
              )
            else if (p['static_probability'] != null)
              Text(
                'Model: ${percent(p['static_probability'])} chance of an industrial source',
              ),
            if (frp != null) Text('Fire power: $frp MW'),
            const SizedBox(height: 4),
            Text(
              'Marks the centre of a ${pixelSize(src)} pixel, not an exact fire location.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }

  /// One line under the filters: how many, from when, and what is special.
  String caption(int count) {
    final parts = <String>[
      '${thousands(count)} hotspot${count == 1 ? '' : 's'}',
      switch (window) {
        '24h' => 'last 24 hours',
        '7d' => 'last 7 days',
        '30d' => 'last 30 days',
        _ => period,
      },
    ];
    final data = windowData;
    if (data != null && data['fire_type_known'] == false) {
      parts.add(
        model == null
            ? 'may include industrial heat'
            : hideIndustrial
            ? '$flagged likely industrial hidden'
            : '$flagged likely industrial (grey)',
      );
    }
    final hidden = (data?['excluded_other_types'] as int?) ?? 0;
    if (hidden > 0) {
      parts.add('${thousands(hidden)} non-fire heat sources hidden');
    }
    final missing = ((data?['missing_days'] as List?) ?? const []).length;
    if (missing > 0) {
      parts.add('no data for $missing day${missing == 1 ? '' : 's'}');
    }
    return parts.join(' • ');
  }

  InfoButton info() => InfoButton(
    title: 'Reading the map',
    children: [
      const InfoSection(
        'Time',
        '24 h: the regular NASA snapshot, the same as Overview.\n'
            '7 / 30 days: NASA near-real-time data kept by our server. It does '
            'not say whether a hotspot is a vegetation fire or an industrial '
            'heat source such as a factory.\n'
            'Month: NASA\'s quality-checked archive (from 2000 until about 3 months ago), vegetation '
            'fires only; other heat sources are hidden and counted.\n'
            'Over several days, a fire that burns for days appears several times.',
      ),
      const InfoSection(
        'Dots',
        'Orange = MODIS, teal = VIIRS. Faded dots are low-confidence. Grey dots '
            'with a coloured ring are likely industrial heat sources (see '
            'below). Tap a dot for details. Use the satellite chips to see one '
            'at a time.',
      ),
      if (model != null)
        InfoSection(
          'Likely industrial (machine learning)',
          'Recent NASA data does not say whether a hotspot is a vegetation fire '
              'or an industrial heat source such as a factory, brick kiln or gas '
              'flare. A model trained on NASA-labelled archive years '
              '${(model!['train_years'] as List).join('–')} learns this from where '
              'and when hotspots keep appearing. Tested on ${model!['test_year']}, '
              'which it never saw: when it flagged a hotspot it was right '
              '${percent(model!['test_precision'])} of the time, and it found '
              '${percent(model!['test_recall'])} of the industrial hotspots. A simple '
              'rule (${model!['baseline_rule']}) was right only '
              '${percent(model!['baseline_precision'])} of the time. Treat grey dots '
              'as a hint, not a fact.',
        ),
      const InfoSection(
        'Numbers on dots',
        'A number means several hotspots at nearly the same place: the same '
            'fire seen by several satellites, or seen again on other days. Its '
            'colour is the sensor (blue-grey when MODIS and VIIRS are mixed). Tap '
            'it to zoom in, or to list the hotspots when they share one spot.',
      ),
      const InfoSection(
        'Saving an area',
        'Tap "Select area", move and zoom the map until the box covers your '
            'place, then Save. Open it later from Account.',
      ),
      ...basicsSections(),
    ],
  );

  static double bucket(double zoom) => (zoom * 2).floor() / 2;

  /// Singles and multi-hotspot clusters for the current zoom.
  List<Cluster> groups(List<Json> points) {
    final zoom = clusterZoom;
    if (zoom == null) {
      return [
        for (final p in points) Cluster([p]),
      ];
    }
    final camera = controller.camera;
    return clusterPoints(points, (at) => camera.projectAtZoom(at, zoom));
  }

  /// Zoom in to split a cluster, or list it when its hotspots share one spot
  /// (typically several satellites seeing the same fire).
  Future<void> openCluster(Cluster cluster) async {
    final camera = controller.camera;
    if (cluster.extent > 0.003 && camera.zoom < 14) {
      controller.fitCamera(
        CameraFit.coordinates(
          coordinates: [for (final p in cluster.points) at(p)],
          padding: const EdgeInsets.fromLTRB(60, 160, 60, 100),
          maxZoom: 15,
        ),
      );
      return;
    }
    final points = [...cluster.points]
      ..sort(
        (a, b) => '${a['timestamp_utc']}'.compareTo('${b['timestamp_utc']}'),
      );
    final picked = await showModalBottomSheet<Json>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => ConstrainedBox(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.of(context).size.height * 0.7,
        ),
        child: SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${cluster.size} hotspots at this spot',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    Text(
                      'Seen by ${(cluster.sources.map(sourceLabel).toList()..sort()).join(', ')}. '
                      'The same fire is often seen by several satellites or on several days.',
                    ),
                  ],
                ),
              ),
              Flexible(
                child: ListView(
                  shrinkWrap: true,
                  children: [
                    for (final p in points)
                      ListTile(
                        leading: CircleAvatar(
                          radius: 10,
                          backgroundColor: p['likely_static'] == true
                              ? Colors.grey.shade600
                              : sensorColor(p['source'] as String),
                        ),
                        title: Text(sourceLabel(p['source'] as String)),
                        subtitle: Text(
                          '${utcTime(p['timestamp_utc'])} • ${p['quality']}'
                          '${p['likely_static'] == true ? ' • likely industrial' : ''}',
                        ),
                        onTap: () => Navigator.pop(context, p),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
    if (picked != null && mounted) {
      setState(() {
        selected = picked;
        framing = false;
      });
    }
  }

  /// Count badge: sensor colour when one sensor type, neutral when mixed, grey when all flagged.
  Widget badge(Cluster cluster) {
    final types = cluster.sources.map(isModis).toSet();
    final fill = cluster.allLikelyStatic
        ? Colors.grey.shade600
        : types.length == 1
        ? sensorColor(cluster.sources.first)
        : Colors.blueGrey.shade700;
    return Container(
      decoration: BoxDecoration(
        color: fill,
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 2),
        boxShadow: const [BoxShadow(blurRadius: 3, color: Colors.black26)],
      ),
      alignment: Alignment.center,
      child: FittedBox(
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Text(
            cluster.size < 1000
                ? '${cluster.size}'
                : compactCount(cluster.size),
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.bold,
            ),
          ),
        ),
      ),
    );
  }

  static String compactCount(int n) => '${(n / 1000).toStringAsFixed(1)}k';

  Widget chip(String label, bool on, VoidCallback onTap, {Widget? avatar}) =>
      Padding(
        padding: const EdgeInsets.only(right: 6),
        child: ChoiceChip(
          avatar: avatar,
          label: Text(label),
          selected: on,
          onSelected: (_) => onTap(),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final bbox = (widget.snapshot.summary['bbox'] as List)
        .map((v) => (v as num).toDouble())
        .toList();
    final points = all;
    final visible = shown;
    final clusters = groups(visible);
    final singles = [
      for (final c in clusters)
        if (c.size == 1) c.points.first,
    ];
    final bunches = [
      for (final c in clusters)
        if (c.size > 1) c,
    ];
    final sources = points.map((p) => p['source'] as String).toSet().toList()
      ..sort();
    final surface = Theme.of(context).colorScheme.surface;
    final monthLabel = isMonth
        ? (windowData?['label'] as String? ?? window)
        : 'Month…';
    return LayoutBuilder(
      builder: (context, constraints) {
        size = constraints.biggest;
        return Stack(
          children: [
            FlutterMap(
              mapController: controller,
              options: MapOptions(
                initialCameraFit: CameraFit.bounds(
                  bounds: LatLngBounds(
                    LatLng(bbox[1], bbox[0]),
                    LatLng(bbox[3], bbox[2]),
                  ),
                  padding: const EdgeInsets.fromLTRB(12, 120, 12, 72),
                ),
                maxZoom: 18,
                onMapReady: () => setState(
                  () => clusterZoom = bucket(controller.camera.zoom),
                ),
                // Re-cluster only when the zoom bucket changes, not while panning.
                onPositionChanged: (camera, _) {
                  final next = bucket(camera.zoom);
                  if (clusterZoom != null && next != clusterZoom) {
                    setState(() => clusterZoom = next);
                  }
                },
                onTap: (_, _) {
                  if (hits.value == null) setState(() => selected = null);
                },
              ),
              children: [
                TileLayer(
                  urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                  userAgentPackageName: 'com.example.simple_todo.agnicalendar',
                  maxNativeZoom: 19,
                ),
                if (outline != null)
                  PolygonLayer(
                    polygons: [
                      for (final rings in outline!.polygons)
                        Polygon(
                          points: rings.first,
                          holePointsList: rings.skip(1).toList(),
                          color: Theme.of(context).colorScheme.primary
                              .withValues(alpha: 0.05),
                          borderColor: Theme.of(context).colorScheme.primary,
                          borderStrokeWidth: 1.5,
                        ),
                    ],
                  ),
                // Canvas-drawn dots stay fast with thousands of hotspots.
                CircleLayer(
                  circles: [
                    for (final p in singles)
                      CircleMarker(
                        point: at(p),
                        radius: 5,
                        // Likely industrial: grey fill, sensor colour kept in the ring.
                        color:
                            (p['likely_static'] == true
                                    ? Colors.grey.shade600
                                    : sensorColor(p['source'] as String))
                                .withValues(
                                  alpha: p['quality'] == 'low' ? 0.45 : 0.95,
                                ),
                        borderColor: p['likely_static'] == true
                            ? sensorColor(p['source'] as String)
                            : Colors.white,
                        borderStrokeWidth: p['likely_static'] == true ? 2 : 1,
                      ),
                  ],
                ),
                // Invisible, larger touch targets on top of the dots.
                GestureDetector(
                  key: const ValueKey('hotspot-hits'),
                  onTap: () {
                    final hit = hits.value;
                    if (hit == null || hit.hitValues.isEmpty) return;
                    setState(() {
                      selected = singles[hit.hitValues.first];
                      framing = false;
                    });
                  },
                  child: CircleLayer<int>(
                    hitNotifier: hits,
                    circles: [
                      for (var i = 0; i < singles.length; i++)
                        CircleMarker(
                          point: at(singles[i]),
                          radius: 16,
                          color: Colors.transparent,
                          hitValue: i,
                        ),
                    ],
                  ),
                ),
                MarkerLayer(
                  markers: [
                    for (var i = 0; i < bunches.length; i++)
                      Marker(
                        point: bunches[i].center,
                        // Grows gently with the count; at least a 30 px touch target.
                        width: 30 + 6 * (math.log(bunches[i].size) / math.ln10),
                        height:
                            30 + 6 * (math.log(bunches[i].size) / math.ln10),
                        child: GestureDetector(
                          key: ValueKey('cluster-$i'),
                          onTap: () => openCluster(bunches[i]),
                          child: badge(bunches[i]),
                        ),
                      ),
                  ],
                ),
                if (selected != null)
                  CircleLayer(
                    circles: [
                      CircleMarker(
                        point: at(selected!),
                        radius: 10,
                        color: sensorColor(selected!['source'] as String),
                        borderColor: Colors.white,
                        borderStrokeWidth: 3,
                      ),
                    ],
                  ),
                const SimpleAttributionWidget(
                  source: Text('© OpenStreetMap contributors'),
                ),
              ],
            ),
            Positioned(
              top: 8,
              left: 8,
              right: 8,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: SingleChildScrollView(
                          scrollDirection: Axis.horizontal,
                          child: Row(
                            children: [
                              chip(
                                '24 h',
                                window == '24h',
                                () => choose('24h'),
                              ),
                              chip(
                                '7 days',
                                window == '7d',
                                () => choose('7d'),
                              ),
                              chip(
                                '30 days',
                                window == '30d',
                                () => choose('30d'),
                              ),
                              chip(
                                monthLabel,
                                isMonth,
                                pickMonth,
                                avatar: const Icon(Icons.history, size: 18),
                              ),
                            ],
                          ),
                        ),
                      ),
                      Material(
                        shape: const CircleBorder(),
                        color: surface,
                        child: info(),
                      ),
                    ],
                  ),
                  if (model != null && flagged > 0)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: FilterChip(
                        avatar: const Icon(Icons.factory_outlined, size: 18),
                        label: Text('Hide likely industrial ($flagged)'),
                        selected: hideIndustrial,
                        onSelected: (on) => setState(() {
                          hideIndustrial = on;
                          selected = null;
                        }),
                      ),
                    ),
                  if (sources.length > 1)
                    SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(
                        children: [
                          for (final s in ['All', ...sources])
                            chip(
                              s == 'All'
                                  ? 'All satellites'
                                  : '${sourceLabel(s)} (${thousands(points.where((p) => p['source'] == s).length)})',
                              source == s,
                              () => setState(() {
                                source = s;
                                selected = null;
                              }),
                              avatar: s == 'All'
                                  ? null
                                  : CircleAvatar(
                                      backgroundColor: sensorColor(s),
                                    ),
                            ),
                        ],
                      ),
                    ),
                  if (!loading && error == null)
                    Container(
                      margin: const EdgeInsets.only(top: 4),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: surface.withValues(alpha: 0.92),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        caption(visible.length),
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ),
                ],
              ),
            ),
            if (loading)
              const Center(
                child: Card(
                  child: Padding(
                    padding: EdgeInsets.all(16),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        SizedBox.square(
                          dimension: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                        SizedBox(width: 12),
                        Text('Loading hotspots…'),
                      ],
                    ),
                  ),
                ),
              )
            else if (error != null)
              Center(
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(error!, textAlign: TextAlign.center),
                        const SizedBox(height: 8),
                        FilledButton.icon(
                          onPressed: () => choose(window),
                          icon: const Icon(Icons.refresh),
                          label: const Text('Retry'),
                        ),
                      ],
                    ),
                  ),
                ),
              )
            else if (visible.isEmpty && !framing)
              Positioned(
                left: 24,
                right: 24,
                top: 150,
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Text(
                      'No hotspots inside Bangladesh in $period. '
                      'That does not prove there were no fires. '
                      'See Calendar → Season for the usual pattern.',
                      textAlign: TextAlign.center,
                    ),
                  ),
                ),
              ),
            if (framing) ...[
              Positioned.fromRect(
                rect: frame,
                child: IgnorePointer(
                  child: Container(
                    decoration: BoxDecoration(
                      border: Border.all(
                        color: Theme.of(context).colorScheme.primary,
                        width: 3,
                      ),
                      color: Theme.of(context).colorScheme.primary
                          .withValues(alpha: 0.08),
                    ),
                  ),
                ),
              ),
              Positioned(
                left: 12,
                right: 12,
                bottom: 16,
                child: Card(
                  elevation: 4,
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Text(
                          'Move and zoom the map so the box covers your area.',
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 8),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.end,
                          children: [
                            TextButton(
                              onPressed: () => setState(() => framing = false),
                              child: const Text('Cancel'),
                            ),
                            const SizedBox(width: 8),
                            FilledButton.icon(
                              onPressed: saveFramedArea,
                              icon: const Icon(Icons.bookmark_add_outlined),
                              label: const Text('Save area'),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ] else if (selected != null)
              Positioned(
                left: 12,
                right: 12,
                bottom: 16,
                child: details(selected!),
              )
            else
              Positioned(
                right: 12,
                bottom: 28,
                child: FloatingActionButton.extended(
                  heroTag: 'select-area',
                  onPressed: () => setState(() => framing = true),
                  icon: const Icon(Icons.crop_free),
                  label: const Text('Select area'),
                ),
              ),
          ],
        );
      },
    );
  }
}

class _MonthPicker extends StatefulWidget {
  const _MonthPicker({required this.years, required this.counts, this.initial});
  final List<String> years;
  final Map<String, int> counts;
  final String? initial;
  @override
  State<_MonthPicker> createState() => _MonthPickerState();
}

class _MonthPickerState extends State<_MonthPicker> {
  late String year = widget.initial?.substring(0, 4) ?? widget.years.last;

  @override
  Widget build(BuildContext context) => SafeArea(
    child: SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Archive month',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
              DropdownButton<String>(
                value: year,
                items: [
                  for (final y in widget.years.reversed)
                    DropdownMenuItem(value: y, child: Text(y)),
                ],
                onChanged: (value) => setState(() => year = value!),
              ),
            ],
          ),
          const Text('Vegetation-fire hotspots per month, all satellites'),
          const SizedBox(height: 12),
          GridView(
            // Fixed row height: the sheet fits on any screen width.
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 4,
              mainAxisSpacing: 8,
              crossAxisSpacing: 8,
              mainAxisExtent: 56,
            ),
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            children: [
              for (var m = 1; m <= 12; m++)
                () {
                  final key = '$year-${m.toString().padLeft(2, '0')}';
                  final count = widget.counts[key];
                  return OutlinedButton(
                    onPressed: count == null
                        ? null
                        : () => Navigator.pop(context, key),
                    style: OutlinedButton.styleFrom(padding: EdgeInsets.zero),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text(monthName(m)),
                        Text(
                          count == null ? '–' : thousands(count),
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],
                    ),
                  );
                }(),
            ],
          ),
        ],
      ),
    ),
  );
}
