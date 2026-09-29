import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/snapshot.dart';
import 'ui.dart';

/// One satellite product's burning season: a column per month for the average
/// over complete years, a thin min–max range line, and an optional dot for one
/// chosen year. Tap a month to read its numbers.
class SeasonChart extends StatefulWidget {
  const SeasonChart({super.key, required this.product, this.year});
  final Json product;
  final String? year;
  @override
  State<SeasonChart> createState() => _SeasonChartState();
}

class _SeasonChartState extends State<SeasonChart> {
  int? picked;

  List<Json> get months => (widget.product['months'] as List)
      .map((m) => Json.from(m as Map))
      .toList();

  List<int>? get yearValues {
    final values = (widget.product['by_year'] as Map)[widget.year];
    return values == null ? null : List<int>.from(values as List);
  }

  String caption(int month) {
    final m = months[month - 1];
    final parts = <String>[
      monthName(month),
      if (m['mean'] != null) 'average ${thousands(m['mean'] as num)}',
      if (m['min'] != null && m['min'] != m['max'])
        'range ${thousands(m['min'] as num)}–${thousands(m['max'] as num)}',
      if (yearValues != null)
        '${widget.year}: ${thousands(yearValues![month - 1])}',
    ];
    return parts.join(' • ');
  }

  @override
  Widget build(BuildContext context) {
    final source = widget.product['source'] as String;
    final peak = widget.product['peak_month'] as int?;
    final month = picked ?? peak;
    final ink = Theme.of(context).colorScheme.onSurface;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          month == null ? 'No hotspots in these years' : caption(month),
          style: Theme.of(context).textTheme.bodyMedium,
        ),
        const SizedBox(height: 8),
        Semantics(
          label:
              '${sourceLabel(source)} average hotspots per month. '
              '${peak == null ? '' : 'Peak ${monthName(peak)}.'}',
          child: SizedBox(
            height: 150,
            child: LayoutBuilder(
              builder: (context, constraints) => GestureDetector(
                key: ValueKey('season-$source'),
                behavior: HitTestBehavior.opaque,
                onTapDown: (d) {
                  final slot = (constraints.maxWidth - _axisWidth) / 12;
                  final i = ((d.localPosition.dx - _axisWidth) / slot).floor();
                  if (i >= 0 && i < 12) setState(() => picked = i + 1);
                },
                child: CustomPaint(
                  size: constraints.biggest,
                  painter: _SeasonPainter(
                    months: months,
                    year: yearValues,
                    color: sensorColor(source),
                    ink: ink,
                    surface: Theme.of(context).colorScheme.surface,
                    selected: month,
                  ),
                ),
              ),
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(left: _axisWidth),
          child: Row(
            children: [
              for (var m = 1; m <= 12; m++)
                Expanded(
                  child: Text(
                    monthName(m)[0],
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      fontWeight: m == month ? FontWeight.bold : null,
                    ),
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

const _axisWidth = 40.0;

/// Short tick label: 500, 2.5k, 10k.
String compact(num v) {
  if (v < 1000) return thousands(v);
  final k = v / 1000;
  return '${k == k.roundToDouble() ? k.round() : k.toStringAsFixed(1)}k';
}

/// Rounds up to 1, 2 or 5 × 10^n for clean axis ticks.
double niceCeil(double v) {
  if (v <= 0) return 1;
  final magnitude = math.pow(10, (math.log(v) / math.ln10).floor()).toDouble();
  for (final step in [1, 2, 5, 10]) {
    if (step * magnitude >= v) return step * magnitude;
  }
  return 10 * magnitude;
}

class _SeasonPainter extends CustomPainter {
  _SeasonPainter({
    required this.months,
    required this.year,
    required this.color,
    required this.ink,
    required this.surface,
    required this.selected,
  });
  final List<Json> months;
  final List<int>? year;
  final Color color;
  final Color ink;
  final Color surface;
  final int? selected;

  @override
  void paint(Canvas canvas, Size size) {
    num v(Json m, String k) => (m[k] as num?) ?? 0;
    final top = niceCeil(
      [
        ...months.map((m) => v(m, 'max').toDouble()),
        ...?year?.map((y) => y.toDouble()),
      ].fold(0.0, math.max),
    );
    const bottomPad = 2.0;
    final plotH = size.height - bottomPad - 8;
    double y(num value) => size.height - bottomPad - value / top * plotH;
    final slot = (size.width - _axisWidth) / 12;
    final grid = Paint()
      ..color = ink.withValues(alpha: 0.12)
      ..strokeWidth = 1;

    // Recessive hairline grid with clean ticks.
    for (final tick in [0.0, top / 2, top]) {
      canvas.drawLine(
        Offset(_axisWidth, y(tick)),
        Offset(size.width, y(tick)),
        grid,
      );
      final label = TextPainter(
        text: TextSpan(
          text: compact(tick),
          style: TextStyle(fontSize: 10, color: ink.withValues(alpha: 0.6)),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      label.paint(
        canvas,
        Offset(_axisWidth - 6 - label.width, y(tick) - label.height / 2),
      );
    }

    for (var i = 0; i < 12; i++) {
      final m = months[i];
      final cx = _axisWidth + slot * i + slot / 2;
      if (selected == i + 1) {
        canvas.drawRect(
          Rect.fromLTWH(_axisWidth + slot * i + 1, 0, slot - 2, size.height),
          Paint()..color = ink.withValues(alpha: 0.05),
        );
      }
      // Column: ≤24px, 4px rounded data end, square at the baseline.
      final w = math.min(24.0, slot - 4);
      final mean = v(m, 'mean');
      if (mean > 0) {
        canvas.drawRRect(
          RRect.fromRectAndCorners(
            Rect.fromLTRB(cx - w / 2, y(mean), cx + w / 2, y(0)),
            topLeft: const Radius.circular(4),
            topRight: const Radius.circular(4),
          ),
          Paint()..color = color,
        );
      }
      // Range across complete years: 2px line with short caps.
      if (m['min'] != null && v(m, 'max') > v(m, 'min')) {
        final range = Paint()
          ..color = ink.withValues(alpha: 0.55)
          ..strokeWidth = 2
          ..strokeCap = StrokeCap.round;
        canvas.drawLine(
          Offset(cx, y(v(m, 'min'))),
          Offset(cx, y(v(m, 'max'))),
          range,
        );
        canvas.drawLine(
          Offset(cx - 3, y(v(m, 'max'))),
          Offset(cx + 3, y(v(m, 'max'))),
          range,
        );
      }
      // Chosen year: ≥8px dot with a 2px surface ring.
      if (year != null) {
        final c = Offset(cx, y(year![i]));
        canvas.drawCircle(c, 6, Paint()..color = surface);
        canvas.drawCircle(c, 4.5, Paint()..color = ink);
      }
    }
  }

  @override
  bool shouldRepaint(_SeasonPainter old) =>
      old.months != months ||
      old.year != year ||
      old.selected != selected ||
      old.color != color;
}
