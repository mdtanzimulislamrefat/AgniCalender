// Renders the launcher icon PNGs. Regenerate with:
//   flutter test tool/icon/render_icon_test.dart --update-goldens
// then: dart run flutter_launcher_icons
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const brand = Color(0xFFB64E24); // app seed colour, also the adaptive background
const page = Color(0xFFFFF8EE);
const ink = Color(0xFF3B1F14);
const flameOuter = Color(0xFFFF5722);
const flameInner = Color(0xFFFFC107);

enum Variant { full, foreground, monochrome }

/// Calendar page with a flame, drawn in a unit box scaled to [box].
class IconPainter extends CustomPainter {
  IconPainter(this.variant);
  final Variant variant;

  Path flame(Rect b, {required bool inner}) {
    Offset p(double x, double y) =>
        Offset(b.left + x * b.width, b.top + y * b.height);
    final path = Path();
    void cubic(Offset c1, Offset c2, Offset e) =>
        path.cubicTo(c1.dx, c1.dy, c2.dx, c2.dy, e.dx, e.dy);
    if (!inner) {
      path.moveTo(p(0.50, 0.86).dx, p(0.50, 0.86).dy);
      cubic(p(0.35, 0.86), p(0.28, 0.76), p(0.30, 0.64));
      cubic(p(0.32, 0.54), p(0.40, 0.50), p(0.42, 0.40));
      cubic(p(0.47, 0.45), p(0.49, 0.50), p(0.49, 0.55));
      cubic(p(0.54, 0.49), p(0.57, 0.43), p(0.55, 0.34));
      cubic(p(0.67, 0.43), p(0.72, 0.55), p(0.70, 0.66));
      cubic(p(0.69, 0.78), p(0.62, 0.86), p(0.50, 0.86));
    } else {
      path.moveTo(p(0.50, 0.86).dx, p(0.50, 0.86).dy);
      cubic(p(0.43, 0.86), p(0.39, 0.80), p(0.41, 0.74));
      cubic(p(0.43, 0.68), p(0.48, 0.66), p(0.49, 0.60));
      cubic(p(0.56, 0.65), p(0.61, 0.71), p(0.59, 0.77));
      cubic(p(0.58, 0.83), p(0.55, 0.86), p(0.50, 0.86));
    }
    return path..close();
  }

  @override
  void paint(Canvas canvas, Size size) {
    final full = Offset.zero & size;
    if (variant == Variant.full) {
      canvas.drawRect(full, Paint()..color = brand);
    }
    // Adaptive icons keep art inside the central 66 %; use ~58 % for margin.
    final side = size.width * (variant == Variant.full ? 0.70 : 0.58);
    final b = Rect.fromCenter(center: full.center, width: side, height: side);
    Rect r(double l, double t, double rr, double bb) => Rect.fromLTRB(
      b.left + l * b.width,
      b.top + t * b.height,
      b.left + rr * b.width,
      b.top + bb * b.height,
    );
    final mono = variant == Variant.monochrome;
    final sheet = RRect.fromRectAndRadius(
      r(0.10, 0.16, 0.90, 0.94),
      Radius.circular(b.width * 0.11),
    );
    canvas.saveLayer(full, Paint());
    canvas.drawRRect(sheet, Paint()..color = mono ? Colors.white : page);
    // Header band clipped to the sheet's rounded top.
    canvas.save();
    canvas.clipRRect(sheet);
    canvas.drawRect(
      r(0.10, 0.16, 0.90, 0.34),
      Paint()
        ..color = mono ? Colors.white : ink
        ..blendMode = mono ? BlendMode.srcOver : BlendMode.srcOver,
    );
    canvas.restore();
    if (mono) {
      // Themed icons are one colour: cut a gap under the header instead.
      canvas.drawRect(
        r(0.10, 0.34, 0.90, 0.37),
        Paint()..blendMode = BlendMode.clear,
      );
    }
    // Binder rings.
    for (final x in [0.30, 0.70]) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          r(x - 0.04, 0.07, x + 0.04, 0.26),
          Radius.circular(b.width * 0.04),
        ),
        Paint()..color = mono ? Colors.white : ink,
      );
    }
    final body = r(0.02, 0.14, 0.98, 1.00); // flame fills the page below the header
    if (mono) {
      canvas.drawPath(flame(body, inner: false), Paint()..blendMode = BlendMode.clear);
    } else {
      canvas.drawPath(flame(body, inner: false), Paint()..color = flameOuter);
      canvas.drawPath(flame(body, inner: true), Paint()..color = flameInner);
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(IconPainter old) => old.variant != variant;
}

void main() {
  for (final variant in Variant.values) {
    testWidgets('render ${variant.name}', (tester) async {
      tester.view.physicalSize = const Size(1024, 1024);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        Center(
          child: RepaintBoundary(
            key: const Key('icon'),
            child: CustomPaint(
              size: const Size(1024, 1024),
              painter: IconPainter(variant),
            ),
          ),
        ),
      );
      await expectLater(
        find.byKey(const Key('icon')),
        matchesGoldenFile('../../assets/icon/${variant.name}.png'),
      );
    });
  }
}
