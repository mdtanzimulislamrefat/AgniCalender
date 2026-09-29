import 'package:flutter/material.dart';

import '../models/snapshot.dart';
import '../services/agni_api.dart';
import '../widgets/ui.dart';
import 'account.dart';
import 'hotspot_map.dart';
import 'season.dart';

class AgniHome extends StatefulWidget {
  const AgniHome({super.key, this.api});
  final AgniApi? api;
  @override
  State<AgniHome> createState() => _AgniHomeState();
}

class _AgniHomeState extends State<AgniHome> {
  late final AgniApi api = widget.api ?? AgniApi();
  Snapshot? data;
  bool busy = false;
  bool offline = false;
  String? message;
  int tab = 0;

  /// Calendar tab: multi-year season (default) or recent weeks.
  bool recentWeeks = false;

  @override
  void initState() {
    super.initState();
    load();
  }

  void notify(String text) {
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
    }
  }

  Future<void> openAccount() => Navigator.push<void>(
    context,
    MaterialPageRoute(
      builder: (_) => AccountScreen(api: api, snapshot: data),
    ),
  );

  Future<void> load() async {
    if (busy) return;
    setState(() {
      busy = true;
      message = null;
    });
    if (data == null) {
      final cached = await api.cached();
      if (!mounted) return;
      setState(() {
        data = cached;
        offline = cached != null;
      });
    }
    try {
      final fresh = await api.fetch();
      if (!mounted) return;
      setState(() {
        data = fresh;
        offline = false;
      });
      try {
        await api.save(fresh);
      } catch (_) {
        if (mounted) {
          setState(
            () => message =
                'Data loaded, but could not be saved for offline use.',
          );
        }
      }
    } on AuthException {
      // Session ended: the sign-in gate replaces this screen.
    } catch (_) {
      if (mounted) {
        setState(() {
          offline = true;
          message = data == null
              ? 'Cannot reach the server. Start the backend and connect your phone, then retry.'
              : 'Server unavailable. Showing saved data.';
        });
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  void dispose() {
    if (widget.api == null) api.close();
    super.dispose();
  }

  /// The backend has not fetched NASA data for over two days.
  bool get fetchedLongAgo {
    final at = DateTime.tryParse('${data!.summary['generated_at_utc']}');
    return at != null && DateTime.now().toUtc().difference(at).inHours >= 48;
  }

  Widget heading(String title, InfoButton info) => Row(
    children: [
      Expanded(
        child: Text(
          title,
          style: Theme.of(context).textTheme.headlineSmall
              ?.copyWith(fontWeight: FontWeight.bold),
        ),
      ),
      info,
    ],
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('AgniCalendar'),
      backgroundColor: Colors.transparent,
      actions: [
        IconButton(
          onPressed: busy ? null : load,
          tooltip: 'Refresh data',
          icon: const Icon(Icons.refresh),
        ),
        IconButton(
          onPressed: openAccount,
          tooltip: 'Account',
          icon: const Icon(Icons.account_circle),
        ),
      ],
    ),
    bottomNavigationBar: NavigationBar(
      selectedIndex: tab,
      onDestinationSelected: (i) => setState(() => tab = i),
      destinations: const [
        NavigationDestination(
          icon: Icon(Icons.dashboard_outlined),
          label: 'Overview',
        ),
        NavigationDestination(icon: Icon(Icons.map_outlined), label: 'Map'),
        NavigationDestination(
          icon: Icon(Icons.calendar_month_outlined),
          label: 'Calendar',
        ),
      ],
    ),
    body: SafeArea(
      child: Column(
        children: [
          if (busy) const LinearProgressIndicator(),
          if (message != null)
            NoticeBar(message!, icon: Icons.cloud_off_outlined)
          else if (data != null && offline)
            NoticeBar('Offline • saved ${ago(data!.savedAt)}')
          else if (data != null && fetchedLongAgo)
            NoticeBar(
              'Satellite data was last fetched ${ago(data!.summary['generated_at_utc'])}',
            )
          else if (data != null && data!.points.isNotEmpty && data!.stale)
            const NoticeBar('Newest hotspot is more than 2 days old'),
          Expanded(
            child: data == null
                ? Center(
                    child: busy
                        ? const Text('Loading NASA observations…')
                        : FilledButton.icon(
                            onPressed: load,
                            icon: const Icon(Icons.refresh),
                            label: const Text('Retry'),
                          ),
                  )
                : switch (tab) {
                    1 => HotspotMap(api: api, snapshot: data!),
                    2 => calendar(),
                    _ => overview(),
                  },
          ),
        ],
      ),
    ),
  );

  Widget overview() {
    final d = data!;
    final counts = Json.from(d.summary['counts_by_source'] as Map);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        heading(
          'Fire hotspots',
          InfoButton(
            title: 'About these numbers',
            children: [
              ...basicsSections(),
              const Divider(),
              InfoSection(
                'Latest satellite data',
                'Report #${d.id}, fetched from NASA ${utcTime(d.summary['generated_at_utc'])}. '
                    'It covers the 24 hours before that.\n'
                    'Hotspots seen ${utcTime(d.summary['data_start_utc'])} '
                    'to ${utcTime(d.summary['data_end_utc'])}\n'
                    'Processed ${utcTime(d.summary['generated_at_utc'])}\n'
                    'Area (W, S, E, N): ${(d.summary['bbox'] as List).join(', ')}',
              ),
              InfoSection(
                'Limitations',
                (d.summary['limitations'] as List)
                    .map((n) => '• $n')
                    .join('\n'),
              ),
            ],
          ),
        ),
        Text(
          '${d.summary['region'] ?? 'Bangladesh area'} • '
          'data fetched ${ago(d.summary['generated_at_utc'])}',
          style: Theme.of(context).textTheme.bodyMedium,
        ),
        const SizedBox(height: 16),
        for (final entry in counts.entries)
          SensorTile(source: entry.key, count: entry.value as int),
        if (counts.isEmpty)
          const Card(
            child: ListTile(
              leading: Icon(Icons.cloud_outlined),
              title: Text('No hotspots in the last 24 hours'),
              subtitle: Text(
                'That does not prove there were no fires. '
                'See Calendar → Season for the usual pattern.',
              ),
            ),
          ),
        const SizedBox(height: 8),
        const Row(
          children: [
            Icon(Icons.info_outline, size: 16),
            SizedBox(width: 6),
            Expanded(child: Text('Each satellite is counted separately.')),
          ],
        ),
      ],
    );
  }

  Widget calendar() {
    final byWeek = <String, List<Json>>{};
    for (final week in data!.weeks) {
      byWeek.putIfAbsent(week['week_start_utc'] as String, () => []).add(week);
    }
    final starts = byWeek.keys.toList()..sort((a, b) => b.compareTo(a));
    final most = data!.weeks.fold<int>(
      1,
      (m, w) => (w['detection_count'] as int) > m ? w['detection_count'] : m,
    );
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        calendarSwitch(),
        const SizedBox(height: 12),
        if (!recentWeeks)
          SeasonView(api: api)
        else ...[
          heading(
            'Weekly hotspots',
            InfoButton(
              title: 'About the calendar',
              children: [
                const InfoSection(
                  'Weeks',
                  'Weeks start on Monday (UTC). The newest week may still be '
                      'filling up. Weeks without data are left out, not '
                      'shown as zero.',
                ),
                const InfoSection(
                  'Bars',
                  'Each bar is one satellite. Bars are never added together.',
                ),
                ...basicsSections(),
              ],
            ),
          ),
          const SizedBox(height: 8),
          if (starts.isEmpty)
            const Text(
              'No hotspots in the last 24 hours, so there are no recent weeks '
              'to show. See Season for the usual pattern.',
            ),
          for (final start in starts)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(() {
                      final d = DateTime.parse(start);
                      return '${shortDate(d)} – ${shortDate(d.add(const Duration(days: 6)))}';
                    }(), style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 10),
                    for (final week in byWeek[start]!)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Row(
                          children: [
                            SizedBox(
                              width: 96,
                              child: Text(
                                sourceLabel(week['source'] as String),
                              ),
                            ),
                            Expanded(
                              child: ClipRRect(
                                borderRadius: BorderRadius.circular(6),
                                child: LinearProgressIndicator(
                                  value:
                                      (week['detection_count'] as int) / most,
                                  minHeight: 12,
                                  color: sensorColor(week['source'] as String),
                                  backgroundColor: Colors.black12,
                                ),
                              ),
                            ),
                            SizedBox(
                              width: 48,
                              child: Text(
                                '${week['detection_count']}',
                                textAlign: TextAlign.end,
                                style: const TextStyle(
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ),
        ],
      ],
    );
  }

  Widget calendarSwitch() => SegmentedButton<bool>(
    segments: const [
      ButtonSegment(
        value: false,
        icon: Icon(Icons.local_fire_department_outlined),
        label: Text('Season (since 2000)'),
      ),
      ButtonSegment(
        value: true,
        icon: Icon(Icons.view_week_outlined),
        label: Text('Recent weeks'),
      ),
    ],
    selected: {recentWeeks},
    onSelectionChanged: (value) => setState(() => recentWeeks = value.first),
  );
}
