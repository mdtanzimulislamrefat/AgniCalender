import 'package:flutter/material.dart';

import '../models/snapshot.dart';
import '../services/agni_api.dart';
import '../widgets/season_chart.dart';
import '../widgets/ui.dart';

/// Multi-year burning season from the FIRMS archive, one chart per satellite.
class SeasonView extends StatefulWidget {
  const SeasonView({
    super.key,
    required this.api,
    this.bbox,
    this.header = true,
  });
  final AgniApi api;

  /// Limit to a saved area; null for the whole region.
  final List<double>? bbox;
  final bool header;
  @override
  State<SeasonView> createState() => _SeasonViewState();
}

class _SeasonViewState extends State<SeasonView> {
  Json? season;
  bool offline = false;
  bool loading = false;
  String? error;
  String? year;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final result = await widget.api.season(bbox: widget.bbox);
      if (mounted) {
        setState(() {
          season = result.season;
          offline = result.offline;
        });
      }
    } on AuthException {
      // Session ended: the sign-in gate takes over.
    } catch (_) {
      if (mounted) {
        setState(
          () => error = 'Season data is not available. Check the server (the archive import may not have run), then retry.',
        );
      }
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  List<Json> get products =>
      (season!['products'] as List).map((p) => Json.from(p as Map)).toList();

  InfoButton info() => InfoButton(
    title: 'About the burning season',
    children: [
      const InfoSection(
        'What the columns show',
        'Each column is the average number of hotspots in that month. The thin '
            'line runs from the quietest to the busiest year. Pick a year to see '
            'it as a dot. Tap a month to read its numbers.',
      ),
      const InfoSection(
        'Which years are averaged',
        'Only years when the satellite watched all 12 months: MODIS from 2003, '
            'VIIRS S-NPP from 2013, VIIRS NOAA-20 from 2019. Earlier years are '
            'kept but not averaged. The current year is never averaged.',
      ),
      const InfoSection(
        'Region',
        "Uses NASA's Bangladesh country boundary. Overview and Map use a "
            'rectangle around Bangladesh, so their numbers are not comparable.',
      ),
      const InfoSection(
        'Not counted',
        'Hotspots NASA marks as static sources (such as industry) or offshore '
            'are listed separately under each chart, not mixed in.',
      ),
      ...basicsSections(),
      if (season != null)
        InfoSection(
          'Limitations',
          (season!['limitations'] as List).map((n) => '• $n').join('\n'),
        ),
    ],
  );

  Widget card(Json product) {
    final source = product['source'] as String;
    final years = List<int>.from(product['complete_years'] as List);
    final peak = product['peak_month'] as int?;
    final other = Json.from(product['other_types'] as Map);
    final excluded = other.values.fold<int>(0, (sum, n) => sum + (n as int));
    final months = (product['months'] as List)
        .map((m) => Json.from(m as Map))
        .toList();
    final chosen = year == null
        ? null
        : (product['by_year'] as Map)[year] as List?;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                CircleAvatar(radius: 6, backgroundColor: sensorColor(source)),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    sourceLabel(source),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (peak != null)
                  Chip(
                    label: Text('Peak: ${monthName(peak)}'),
                    visualDensity: VisualDensity.compact,
                  ),
              ],
            ),
            Text(
              years.isEmpty
                  ? 'No complete years yet'
                  : 'Average of ${years.first}–${years.last} (${years.length} years)',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            if ((product['versions'] as List).length > 1)
              Text(
                'Processing versions: ${(product['versions'] as List).map((v) => '${v['version']} (${v['first_year']}–${v['last_year']})').join(', ')}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            if (year != null && chosen == null)
              Text(
                'No $year data for this satellite',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            const SizedBox(height: 12),
            SeasonChart(product: product, year: year),
            if (excluded > 0)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  '${thousands(excluded)} static-source or offshore hotspots not counted',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: const Text('Show numbers'),
              children: [
                Table(
                  columnWidths: const {0: FlexColumnWidth(0.8)},
                  children: [
                    TableRow(
                      children: [
                        for (final h in [
                          'Month',
                          'Average',
                          'Range',
                          if (chosen != null) year!,
                        ])
                          Padding(
                            padding: const EdgeInsets.only(bottom: 4),
                            child: Text(
                              h,
                              style: const TextStyle(
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                      ],
                    ),
                    for (final m in months)
                      TableRow(
                        children: [
                          Text(monthName(m['month'] as int)),
                          Text(
                            m['mean'] == null
                                ? '–'
                                : thousands(m['mean'] as num),
                          ),
                          Text(
                            m['min'] == null
                                ? '–'
                                : '${thousands(m['min'] as num)}–${thousands(m['max'] as num)}',
                          ),
                          if (chosen != null)
                            Text(
                              thousands(chosen[(m['month'] as int) - 1] as num),
                            ),
                        ],
                      ),
                  ],
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (season == null) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: error == null
            ? const Center(child: CircularProgressIndicator())
            : Column(
                children: [
                  Text(error!),
                  const SizedBox(height: 8),
                  FilledButton.icon(
                    onPressed: loading ? null : load,
                    icon: const Icon(Icons.refresh),
                    label: const Text('Retry'),
                  ),
                ],
              ),
      );
    }
    final years =
        products
            .expand((p) => (p['by_year'] as Map).keys.cast<String>())
            .toSet()
            .toList()
          ..sort((a, b) => b.compareTo(a));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (widget.header)
          Row(
            children: [
              Expanded(
                child: Text(
                  'Burning season',
                  style: Theme.of(context).textTheme.headlineSmall
                      ?.copyWith(fontWeight: FontWeight.bold),
                ),
              ),
              info(),
            ],
          ),
        Text(
          '${season!['region']} • vegetation-fire hotspots per month, by satellite',
        ),
        if (offline)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: NoticeBar('Offline • saved season data'),
          ),
        Row(
          children: [
            const Text('Compare a year'),
            const SizedBox(width: 12),
            DropdownButton<String?>(
              value: year,
              hint: const Text('None'),
              items: [
                const DropdownMenuItem<String?>(child: Text('None')),
                for (final y in years)
                  DropdownMenuItem<String?>(value: y, child: Text(y)),
              ],
              onChanged: (value) => setState(() => year = value),
            ),
            if (!widget.header) ...[const Spacer(), info()],
          ],
        ),
        for (final product in products) card(product),
      ],
    );
  }
}
