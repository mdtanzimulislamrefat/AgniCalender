import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/area.dart';
import '../models/snapshot.dart';
import '../services/agni_api.dart';
import '../widgets/ui.dart';
import 'name_dialog.dart';
import 'season.dart';

class AccountScreen extends StatefulWidget {
  const AccountScreen({super.key, required this.api, this.snapshot});
  final AgniApi api;
  final Snapshot? snapshot;
  @override
  State<AccountScreen> createState() => _AccountScreenState();
}

class _AccountScreenState extends State<AccountScreen> {
  List<Json>? areas;
  bool offline = false;
  bool busy = false;
  String? message;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() => message = null);
    try {
      final result = await widget.api.areas();
      if (mounted) {
        setState(() {
          areas = result.areas;
          offline = result.offline;
        });
      }
    } on AuthException {
      // Session ended: the sign-in gate closes this screen.
    } catch (e) {
      if (mounted) {
        setState(
          () => message = e is ApiException
              ? e.message
              : 'Cannot reach the server to load saved areas.',
        );
      }
    }
  }

  Future<void> delete(Json area) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete "${area['name']}"?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.api.deleteArea(area['id'] as int);
      await load();
    } catch (e) {
      if (mounted) setState(() => message = 'Could not delete: $e');
    }
  }

  Future<void> rename() async {
    final name = await askName(
      context,
      title: 'Display name',
      initial: widget.api.user.value?['display_name'] as String? ?? '',
    );
    if (name == null) return;
    try {
      await widget.api.updateName(name);
    } catch (e) {
      if (mounted) setState(() => message = 'Could not update name: $e');
    }
  }

  Future<void> signOut({bool everywhere = false}) async {
    setState(() => busy = true);
    // The sign-in gate closes this screen once the user is cleared.
    await widget.api.signOut(everywhere: everywhere);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Account')),
    body: ValueListenableBuilder<Json?>(
      valueListenable: widget.api.user,
      builder: (context, user, _) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (user != null)
            Card(
              child: ListTile(
                leading: const Icon(Icons.account_circle, size: 40),
                title: Text(
                  user['display_name'] as String? ?? 'Add a display name',
                ),
                subtitle: Text(user['email'] as String),
                trailing: IconButton(
                  tooltip: 'Edit name',
                  icon: const Icon(Icons.edit_outlined),
                  onPressed: busy ? null : rename,
                ),
              ),
            ),
          const SizedBox(height: 16),
          Text('Saved areas', style: Theme.of(context).textTheme.titleLarge),
          const Text('Use "Select area" on the Map tab to add one.'),
          if (offline)
            Container(
              margin: const EdgeInsets.only(top: 8),
              padding: const EdgeInsets.all(12),
              color: const Color(0xFFFFE9B9),
              child: const Text('Offline • showing areas saved on this device'),
            ),
          if (message != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                message!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          if (areas == null && message == null)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (areas != null && areas!.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 16),
              child: Text('No saved areas yet.'),
            ),
          for (final area in areas ?? <Json>[])
            Card(
              child: ListTile(
                leading: const Icon(Icons.crop_square),
                title: Text(area['name'] as String),
                subtitle: Text(areaSize(area)),
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute<void>(
                    builder: (_) => AreaScreen(
                      area: area,
                      snapshot: widget.snapshot,
                      api: widget.api,
                    ),
                  ),
                ),
                trailing: IconButton(
                  tooltip: 'Delete area',
                  icon: const Icon(Icons.delete_outline),
                  onPressed: busy || offline ? null : () => delete(area),
                ),
              ),
            ),
          const SizedBox(height: 24),
          FilledButton.tonalIcon(
            onPressed: busy ? null : signOut,
            icon: const Icon(Icons.logout),
            label: const Text('Sign out'),
          ),
          TextButton(
            onPressed: busy ? null : () => signOut(everywhere: true),
            child: const Text('Sign out on all devices'),
          ),
        ],
      ),
    ),
  );
}

class AreaScreen extends StatelessWidget {
  const AreaScreen({super.key, required this.area, this.snapshot, this.api});
  final Json area;
  final Snapshot? snapshot;

  /// Loads this area's multi-year season when given.
  final AgniApi? api;

  @override
  Widget build(BuildContext context) {
    final box = areaBox(area);
    final data = snapshot;
    final stats = data == null ? null : areaStats(data, box);
    return Scaffold(
      appBar: AppBar(
        title: Text(area['name'] as String),
        actions: [
          InfoButton(
            title: 'About this area',
            children: [
              InfoSection('Size', areaSize(area)),
              InfoSection('Bounds (W, S, E, N)', box.join(', ')),
              if (data != null)
                InfoSection(
                  'Data',
                  'Report #${data.id}: hotspots seen '
                      '${utcTime(data.summary['data_start_utc'])} to '
                      '${utcTime(data.summary['data_end_utc'])}',
                ),
              ...basicsSections(),
            ],
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (data == null || stats == null)
            const Text('Open the Overview tab to load data first.')
          else ...[
            if (stats.coverage == Coverage.outside)
              const NoticeBar(
                'Outside the area we process, so there is no data here. '
                'This is not the same as zero fires.',
                icon: Icons.block,
              )
            else ...[
              if (stats.coverage == Coverage.partial)
                const NoticeBar(
                  'Only part of this area has data; numbers cover that part.',
                  icon: Icons.crop,
                ),
              const SizedBox(height: 8),
              for (final entry in stats.bySource.entries)
                SensorTile(
                  source: entry.key,
                  count: entry.value.count,
                  subtitle:
                      '${entry.value.quality.entries.map((q) => '${q.value} ${q.key}').join(', ')}\n'
                      'newest ${utcTime(entry.value.latest)}',
                ),
              if (stats.bySource.isEmpty)
                const Card(
                  child: ListTile(
                    leading: Icon(Icons.cloud_outlined),
                    title: Text('No hotspots here in the last 24 hours'),
                    subtitle: Text('That does not prove there were no fires.'),
                  ),
                ),
              const SizedBox(height: 8),
              const Text('Each satellite is counted separately.'),
            ],
          ],
          if (api != null) ...[
            const SizedBox(height: 24),
            Text(
              'Burning season here',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            SeasonView(api: api!, bbox: box, header: false),
          ],
        ],
      ),
    );
  }
}

/// Rough size, e.g. "about 104 × 111 km".
String areaSize(Json area) {
  final b = areaBox(area);
  final midLat = (b[1] + b[3]) / 2 * math.pi / 180;
  final width = (b[2] - b[0]) * 111.32 * math.cos(midLat);
  final height = (b[3] - b[1]) * 110.57;
  String km(double v) => v < 10 ? v.toStringAsFixed(1) : v.round().toString();
  return 'about ${km(width)} × ${km(height)} km';
}
