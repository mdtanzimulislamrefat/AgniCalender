import 'package:flutter/material.dart';

bool isModis(String source) => source.startsWith('MODIS');

Color sensorColor(String source) =>
    isModis(source) ? Colors.deepOrange : Colors.teal;

/// Short label: MODIS, VIIRS S-NPP, VIIRS NOAA20 …
String sourceLabel(String source) {
  if (isModis(source)) return 'MODIS';
  final parts = source.split('_');
  if (parts.length < 2) return source;
  return 'VIIRS ${parts[1] == 'SNPP' ? 'S-NPP' : parts[1]}';
}

String pixelSize(String source) => isModis(source) ? '1 km' : '375 m';

const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', //
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

String shortDate(DateTime d) => '${d.day} ${_months[d.month - 1]}';

String monthName(int month) => _months[month - 1];

/// 12,345
String thousands(num value) => value.round().toString().replaceAllMapped(
  RegExp(r'\B(?=(\d{3})+(?!\d))'),
  (_) => ',',
);

/// "29 Sep, 07:45 UTC"
String utcTime(dynamic value) {
  final t = DateTime.tryParse(value?.toString() ?? '')?.toUtc();
  if (t == null) return 'Unknown time';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${shortDate(t)}, ${two(t.hour)}:${two(t.minute)} UTC';
}

/// "3 h ago", "2 days ago"
String ago(dynamic value) {
  final t = DateTime.tryParse(value?.toString() ?? '');
  if (t == null) return 'unknown';
  final d = DateTime.now().toUtc().difference(t.toUtc());
  if (d.inMinutes < 60) return '${d.inMinutes.clamp(0, 59)} min ago';
  if (d.inHours < 48) return '${d.inHours} h ago';
  return '${d.inDays} days ago';
}

/// Plain-language science notes shown behind the ⓘ buttons.
const hotspotBasics = [
  (
    'What is a hotspot?',
    'A spot the satellite saw as unusually hot. Usually a fire, but one fire '
        'can appear as several hotspots, and a few hotspots are not fires.',
  ),
  (
    'Why two satellites?',
    'MODIS sees the ground in 1 km squares, VIIRS in 375 m squares, and they '
        'pass over at different times. So VIIRS usually finds more. Their '
        'numbers are shown separately and should never be added together.',
  ),
  (
    'No hotspot does not mean no fire',
    'Clouds, smoke, small or short fires and the timing of satellite passes '
        'can hide fires.',
  ),
  (
    'Confidence',
    'Each hotspot is labelled low, nominal or high confidence. Low-confidence '
        'hotspots are kept and shown faded, not deleted.',
  ),
];

class InfoSection extends StatelessWidget {
  const InfoSection(this.title, this.body, {super.key});
  final String title;
  final String body;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 14),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: const TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(height: 2),
        Text(body),
      ],
    ),
  );
}

/// ⓘ button that opens a scrollable explanation sheet.
class InfoButton extends StatelessWidget {
  const InfoButton({super.key, required this.title, required this.children});
  final String title;
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => IconButton(
    tooltip: 'What does this mean?',
    icon: const Icon(Icons.info_outline),
    onPressed: () => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxHeight: MediaQuery.of(context).size.height * 0.8,
          ),
          child: ListView(
            shrinkWrap: true,
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
            children: [
              Text(title, style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 12),
              ...children,
            ],
          ),
        ),
      ),
    ),
  );
}

List<Widget> basicsSections() => [
  for (final (title, body) in hotspotBasics) InfoSection(title, body),
];

/// One sensor's count, large. Sensors are never summed.
class SensorTile extends StatelessWidget {
  const SensorTile({
    super.key,
    required this.source,
    required this.count,
    this.subtitle,
  });
  final String source;
  final int count;
  final String? subtitle;
  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Row(
        children: [
          CircleAvatar(
            backgroundColor: sensorColor(source).withValues(alpha: 0.15),
            child: Icon(
              Icons.local_fire_department,
              color: sensorColor(source),
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  sourceLabel(source),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                Text(
                  subtitle ?? '${pixelSize(source)} pixels',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          Text(
            '$count',
            // Identity comes from the coloured icon; text stays in text ink.
            style: Theme.of(context).textTheme.displaySmall
                ?.copyWith(fontWeight: FontWeight.bold),
          ),
        ],
      ),
    ),
  );
}

class NoticeBar extends StatelessWidget {
  const NoticeBar(this.text, {super.key, this.icon = Icons.schedule});
  final String text;
  final IconData icon;
  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    color: const Color(0xFFFFE9B9),
    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
    child: Row(
      children: [
        Icon(icon, size: 18),
        const SizedBox(width: 8),
        Expanded(child: Text(text)),
      ],
    ),
  );
}
