import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:simple_todo/main.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import 'package:simple_todo/models/area.dart';
import 'package:simple_todo/models/cluster.dart';
import 'package:simple_todo/models/outline.dart';
import 'package:simple_todo/models/snapshot.dart';
import 'package:simple_todo/screens/account.dart';
import 'package:simple_todo/widgets/season_chart.dart';
import 'package:simple_todo/widgets/ui.dart';
import 'package:simple_todo/services/agni_api.dart';

const base = 'http://127.0.0.1:8001';
const refreshKey = 'agni_refresh_v1_$base';
const userKey = 'agni_user_v1_$base';
const profile = {'id': 3, 'email': 'a@example.com', 'display_name': null};

Map<String, dynamic> summary() => {
  'report_id': 7,
  'counts_by_source': {'MODIS_NRT': 2},
  'stale': false,
  'data_start_utc': DateTime.now().toUtc().toIso8601String(),
  'data_end_utc': DateTime.now().toUtc().toIso8601String(),
  'generated_at_utc': DateTime.now().toUtc().toIso8601String(),
  'bbox': [88, 20.5, 92.7, 26.7],
  'limitations': ['Coverage unknown.'],
};
final weeks = [
  {
    'week_start_utc': '2026-09-28',
    'source': 'MODIS_NRT',
    'satellite': 'T',
    'detection_count': 2,
    'dates_with_detections': 1,
    'observation_coverage': 'unknown',
  },
];

List<int> monthly(int april) => [
  for (var m = 1; m <= 12; m++) m == 4 ? april : 10,
];

Map<String, dynamic> seasonData() => {
  'region': 'Bangladesh',
  'fire_types': ['vegetation'],
  'timezone': 'UTC',
  'bbox': null,
  'products': [
    {
      'source': 'MODIS_ARCHIVE',
      'versions': [
        {'version': '6.2', 'first_year': 2003, 'last_year': 2017},
        {'version': '61.03', 'first_year': 2023, 'last_year': 2024},
      ],
      'complete_years': [2023, 2024],
      'partial_years': [],
      'unavailable_years': [2025],
      'peak_month': 4,
      'months': [
        for (var m = 1; m <= 12; m++)
          {
            'month': m,
            'mean': m == 4 ? 1817.5 : 10.0,
            'median': m == 4 ? 1817.5 : 10.0,
            'min': m == 4 ? 1536 : 10,
            'max': m == 4 ? 2099 : 10,
          },
      ],
      'by_year': {'2023': monthly(2099), '2024': monthly(1536)},
      'other_types': {'static_land': 6, 'offshore': 4},
    },
  ],
  'limitations': ['Months are in UTC.'],
};

/// In-memory stand-in for the FastAPI backend's auth and data contract.
class FakeServer {
  bool down = false;
  bool refreshRevoked = false;
  String access = 'none';
  String refresh = 'none';
  int issued = 0;
  int refreshes = 0;
  final calls = <String>[];
  final areas = <Map<String, dynamic>>[];
  final seasonQueries = <String?>[];

  /// Observations in the current snapshot (0 = a quiet day).
  int total = 2;

  /// Map time windows.
  bool mapDown = false;
  List<Map<String, dynamic>> recentPoints = [
    point('VIIRS_NOAA21_NRT', 90.4, 23.8, 'nominal'),
    point('VIIRS_SNPP_NRT', 91.5, 22.9, 'low'),
  ];
  final mapQueries = <String>[];

  /// Include the industrial-source model's flags in recent windows.
  bool withModel = false;

  http.Response json(Object body, [int status = 200]) =>
      http.Response(jsonEncode(body), status);

  Map<String, dynamic> session() {
    issued++;
    access = 'a$issued';
    refresh = 'r$issued';
    return {
      'access_token': access,
      'refresh_token': refresh,
      'expires_in': 900,
      'refresh_expires_at': '2026-10-29T00:00:00Z',
      'user': profile,
    };
  }

  Future<http.Response> handle(http.Request request) async {
    final path = request.url.path;
    calls.add('${request.method} $path');
    if (down) return json({}, 503);
    final body = request.body.isEmpty ? {} : jsonDecode(request.body) as Map;
    final auth = request.headers['Authorization'];
    switch (path) {
      case '/v1/auth/login':
        return body['password'] == 'password123'
            ? json(session())
            : json({'detail': 'Invalid email or password'}, 401);
      case '/v1/auth/register':
        return json(session(), 201);
      case '/v1/auth/refresh':
        refreshes++;
        if (refreshRevoked || body['refresh_token'] != refresh) {
          return json({'detail': 'Refresh token expired or revoked'}, 401);
        }
        return json(session());
      case '/v1/auth/logout':
        return http.Response('', 204);
    }
    if (auth != null && auth != 'Bearer $access') {
      return json({'detail': 'Access token expired or invalid'}, 401);
    }
    if (path.startsWith('/v1/me') || path == '/v1/auth/logout-all') {
      if (auth == null) return json({'detail': 'Not authenticated'}, 401);
      if (path == '/v1/auth/logout-all') return http.Response('', 204);
      if (request.method == 'PATCH') {
        return json({...profile, 'display_name': body['display_name']});
      }
      if (request.method == 'POST') {
        final area = {...body, 'id': areas.length + 1};
        areas.add(Map<String, dynamic>.from(area));
        return json(area, 201);
      }
      if (request.method == 'DELETE') {
        areas.removeWhere((a) => '${a['id']}' == path.split('/').last);
        return http.Response('', 204);
      }
      return json(areas);
    }
    if (auth == null) return json({'detail': 'Not authenticated'}, 401);
    if (path == '/v1/map/hotspots') {
      mapQueries.add(request.url.query);
      if (mapDown) return json({}, 503);
      final month = request.url.queryParameters['month'];
      return json({
        'kind': month == null ? 'recent' : 'archive',
        'label': month == null ? 'Last 7 days' : 'April 2024',
        'start_utc': '2024-04-01T00:00:00Z',
        'end_utc': '2024-05-01T00:00:00Z',
        'fire_type_known': month != null,
        'excluded_other_types': month == null ? 0 : 53,
        'missing_days': month == null ? ['2026-09-20'] : [],
        'counts_by_source': {},
        if (month == null && withModel)
          'classifier': {
            'model': 'HistGradientBoostingClassifier',
            'trained_at_utc': '2026-09-29T00:00:00+00:00',
            'train_years': [2018, 2023],
            'test_year': 2025,
            'threshold': 0.43,
            'test_precision': 0.595,
            'test_recall': 0.481,
            'baseline_rule': 'hotspots in >= 4 of the previous 5 years',
            'baseline_precision': 0.029,
            'baseline_recall': 0.772,
          },
        'points': month == null
            ? [
                for (final p in recentPoints)
                  if (withModel)
                    {
                      ...p,
                      'static_probability': p['latitude'] == 23.8 ? 0.97 : 0.04,
                      'likely_static': p['latitude'] == 23.8,
                    }
                  else
                    p,
              ]
            : [
                point('VIIRS_SNPP_ARCHIVE', 91.9, 22.8, 'high'),
                point('MODIS_ARCHIVE', 92.1, 23.0, 'nominal'),
                point('VIIRS_NOAA20_ARCHIVE', 92.0, 22.9, 'high'),
              ],
      });
    }
    if (path == '/v1/map/available') {
      return json({
        'recent_enabled': true,
        'recent_first_day': '2026-08-31',
        'recent_last_day': '2026-09-29',
        'archive_months': [
          {'month': '2023-04', 'count': 11000},
          {'month': '2024-03', 'count': 900},
          {'month': '2024-04', 'count': 13307},
        ],
      });
    }
    if (path == '/v1/archive/season') {
      seasonQueries.add(request.url.queryParameters['bbox']);
      return json(seasonData());
    }
    if (path.endsWith('summary')) {
      return json(
        total == 0 ? {...summary(), 'counts_by_source': {}} : summary(),
      );
    }
    expect(request.url.queryParameters['report_id'], '7');
    if (path.endsWith('calendar')) {
      return json({'report_id': 7, 'weeks': weeks});
    }
    final offset = int.parse(request.url.queryParameters['offset']!);
    if (total == 0) {
      return json({'report_id': 7, 'total': 0, 'observations': []});
    }
    return json({
      'report_id': 7,
      'total': total,
      'observations': [
        {...point('MODIS_NRT', 90 + offset * 2.0, 23, 'high'), 'index': offset},
      ],
    });
  }
}

AgniApi api(FakeServer server) =>
    AgniApi(client: MockClient((r) => server.handle(r)));

/// A signed-in client whose session survives an app restart.
Future<AgniApi> signedIn(FakeServer server) async {
  final service = api(server);
  addTearDown(service.close);
  await service.signIn('a@example.com', 'password123');
  return service;
}

Future<void> signInThroughUi(WidgetTester tester, String password) async {
  await tester.enterText(find.byType(TextFormField).at(0), 'a@example.com');
  await tester.enterText(find.byType(TextFormField).at(1), password);
  await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
  await tester.pumpAndSettle();
}

Map<String, dynamic> point(String source, double lon, double lat, String q) => {
  'source': source,
  'longitude': lon,
  'latitude': lat,
  'quality': q,
  'timestamp_utc': '2026-09-29T0${lon.round() % 10}:00:00+00:00',
};

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({});
  });

  group('signed out', () {
    testWidgets('app opens on the login screen, not data', (tester) async {
      final server = FakeServer();
      final service = api(server);
      addTearDown(service.close);
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      expect(
        find.text('Sign in to see satellite fire hotspots'),
        findsOneWidget,
      );
      expect(find.text('Fire hotspots'), findsNothing);
      expect(server.calls, isEmpty);
    });
    test('data calls without a session are refused locally', () async {
      final service = api(FakeServer());
      addTearDown(service.close);
      await expectLater(service.fetch(), throwsA(isA<AuthException>()));
    });
    testWidgets('wrong password, then sign in shows dashboard', (tester) async {
      final server = FakeServer();
      final service = api(server);
      addTearDown(service.close);
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      await signInThroughUi(tester, 'wrongpass');
      expect(find.text('Incorrect email or password.'), findsOneWidget);
      await signInThroughUi(tester, 'password123');
      expect(find.text('Fire hotspots'), findsOneWidget);
      expect(find.text('MODIS'), findsOneWidget);
      const storage = FlutterSecureStorage();
      expect(await storage.read(key: refreshKey), server.refresh);
      expect((await storage.readAll()).values, isNot(contains(server.access)));
    });
    testWidgets('sign out returns to login and deletes every cached data', (
      tester,
    ) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      expect(await service.cached(), isNotNull);
      await tester.tap(find.byTooltip('Account'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sign out'));
      await tester.pumpAndSettle();
      expect(
        find.text('Sign in to see satellite fire hotspots'),
        findsOneWidget,
      );
      expect(find.text('Account'), findsNothing);
      expect(server.calls, contains('POST /v1/auth/logout'));
      expect(await service.cached(), isNull);
      expect(await const FlutterSecureStorage().readAll(), isEmpty);
    });
    testWidgets('revoked session returns to login with a notice', (
      tester,
    ) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await service.save(await service.fetch());
      server
        ..access = 'rotated-on-server'
        ..refreshRevoked = true;
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      expect(
        find.text('Your session ended. Please sign in again.'),
        findsOneWidget,
      );
      expect(await service.cached(), isNull);
    });
  });

  group('data (signed in)', () {
    test(
      'fetch pins snapshot, reads every page, and cache round-trips',
      () async {
        final service = await signedIn(FakeServer());
        final snapshot = await service.fetch();
        expect(snapshot.points.length, 2);
        expect(snapshot.points.last['index'], 1);
        await service.save(snapshot);
        expect((await service.cached())!.id, 7);
      },
    );
    test('incomplete page fails instead of saving partial data', () async {
      final server = FakeServer();
      final service = AgniApi(
        client: MockClient((request) async {
          if (request.url.path.startsWith('/v1/auth')) {
            return server.handle(request);
          }
          if (request.url.path.endsWith('summary')) {
            return http.Response(jsonEncode(summary()), 200);
          }
          if (request.url.path.endsWith('calendar')) {
            return http.Response('{"report_id":7,"weeks":[]}', 200);
          }
          return http.Response(
            '{"report_id":7,"total":1,"observations":[]}',
            200,
          );
        }),
      );
      addTearDown(service.close);
      await service.signIn('a@example.com', 'password123');
      await expectLater(service.fetch(), throwsFormatException);
    });
    test('corrupt cache is ignored', () async {
      final service = api(FakeServer());
      addTearDown(service.close);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(service.cacheKey, 'broken json');
      expect(await service.cached(), isNull);
    });
    testWidgets('overview shows big per-sensor numbers and weekly bars', (
      tester,
    ) async {
      final service = await signedIn(FakeServer());
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      expect(find.text('MODIS'), findsOneWidget);
      expect(find.text('2'), findsOneWidget);
      await tester.tap(find.byTooltip('What does this mean?'));
      await tester.pumpAndSettle();
      expect(find.text('What is a hotspot?'), findsOneWidget);
      await tester.tapAt(const Offset(10, 10));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Calendar'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Recent weeks'));
      await tester.pumpAndSettle();
      expect(find.text('28 Sep – 4 Oct'), findsOneWidget);
    });
    testWidgets('server failure displays cached data and warning', (
      tester,
    ) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await service.save(
        Snapshot(summary(), weeks, [], DateTime.now().toUtc()),
      );
      server.down = true;
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      expect(
        find.text('Server unavailable. Showing saved data.'),
        findsOneWidget,
      );
      expect(find.text('MODIS'), findsOneWidget);
    });
    testWidgets('first launch without server offers retry', (tester) async {
      final server = FakeServer();
      final service = await signedIn(server);
      server.down = true;
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(FilledButton, 'Retry'), findsOneWidget);
    });
  });

  group('sessions', () {
    test('parallel 401s share one refresh, then retry', () async {
      final server = FakeServer();
      final service = await signedIn(server);
      server.access = 'rotated-on-server'; // our access token has expired
      final results = await Future.wait([service.areas(), service.areas()]);
      expect(results.every((r) => !r.offline), isTrue);
      expect(server.refreshes, 1);
      expect(service.signedIn, isTrue);
    });

    test('restored session renews before the first call', () async {
      FlutterSecureStorage.setMockInitialValues({
        refreshKey: 'r9',
        userKey: jsonEncode(profile),
      });
      final server = FakeServer()..refresh = 'r9';
      final service = api(server);
      addTearDown(service.close);
      expect(await service.restoreSession(), isTrue);
      expect((await service.fetch()).id, 7);
      expect(server.calls.first, 'POST /v1/auth/refresh');
    });

    test('revoked session signs out and refuses data', () async {
      final server = FakeServer();
      final service = await signedIn(server);
      var expired = 0;
      service.onSessionExpired = () => expired++;
      server
        ..access = 'rotated-on-server'
        ..refreshRevoked = true;
      await expectLater(service.fetch(), throwsA(isA<AuthException>()));
      expect(service.signedIn, isFalse);
      expect(expired, 1);
      expect(await const FlutterSecureStorage().read(key: refreshKey), isNull);
    });

    test('saved areas are readable offline', () async {
      final server = FakeServer();
      final service = await signedIn(server);
      await service.createArea('Home', [89, 22, 90, 23]);
      expect((await service.areas()).offline, isFalse);
      server.down = true;
      final result = await service.areas();
      expect(result.offline, isTrue);
      expect(result.areas.single['name'], 'Home');
    });
  });

  group('map', () {
    Future<void> openMap(WidgetTester tester, AgniApi service) async {
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Map'));
      await tester.pumpAndSettle();
    }

    Future<void> tapHotspot(WidgetTester tester, double lon, double lat) async {
      final camera = MapCamera.of(
        tester.element(find.byType(CircleLayer).first),
      );
      final origin = tester.getTopLeft(find.byType(FlutterMap));
      await tester.tapAt(
        origin + camera.latLngToScreenOffset(LatLng(lat, lon)),
      );
      // flutter_map waits briefly to tell a tap from a double tap.
      await tester.pump(const Duration(milliseconds: 500));
      await tester.pumpAndSettle();
    }

    testWidgets('tapping a hotspot opens its details', (tester) async {
      final service = await signedIn(FakeServer());
      await openMap(tester, service);
      await tapHotspot(tester, 90, 23);
      expect(find.text('Confidence: high'), findsOneWidget);
      expect(find.textContaining('1 km pixel'), findsOneWidget);
      await tester.tap(find.byTooltip('Close'));
      await tester.pumpAndSettle();
      expect(find.text('Confidence: high'), findsNothing);
      // Tapping empty map does not open anything.
      await tapHotspot(tester, 89, 25.5);
      expect(find.textContaining('Confidence:'), findsNothing);
    });

    testWidgets('7 and 30 day windows load recent hotspots', (tester) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await openMap(tester, service);
      expect(find.textContaining('2 hotspots • last 24 hours'), findsOneWidget);
      await tester.tap(find.text('7 days'));
      await tester.pumpAndSettle();
      expect(server.mapQueries.last, 'window=7d');
      expect(
        find.text(
          '2 hotspots • last 7 days • may include industrial heat • no data for 1 day',
        ),
        findsOneWidget,
      );
      await tester.tap(find.textContaining('VIIRS NOAA21'));
      await tester.pumpAndSettle();
      expect(find.textContaining('1 hotspot • last 7 days'), findsOneWidget);
      await tapHotspot(tester, 90.4, 23.8);
      expect(find.text('VIIRS NOAA21'), findsOneWidget);
      server.recentPoints = [];
      await tester.tap(find.text('30 days'));
      await tester.pumpAndSettle();
      expect(server.mapQueries.last, 'window=30d');
      expect(
        find.textContaining(
          'No hotspots inside Bangladesh in the last 30 days',
        ),
        findsOneWidget,
      );
    });

    testWidgets('model flags likely industrial hotspots', (tester) async {
      final server = FakeServer()..withModel = true;
      final service = await signedIn(server);
      await openMap(tester, service);
      await tester.tap(find.text('7 days'));
      await tester.pumpAndSettle();
      expect(
        find.text(
          '2 hotspots • last 7 days • 1 likely industrial (grey) • no data for 1 day',
        ),
        findsOneWidget,
      );
      await tapHotspot(tester, 90.4, 23.8);
      expect(
        find.text('Likely industrial heat source (model: 97%)'),
        findsOneWidget,
      );
      await tester.tap(find.byTooltip('Close'));
      await tester.pumpAndSettle();
      await tapHotspot(tester, 91.5, 22.9);
      expect(
        find.text('Model: 4% chance of an industrial source'),
        findsOneWidget,
      );
      await tester.tap(find.byTooltip('Close'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Hide likely industrial (1)'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining(
          '1 hotspot • last 7 days • 1 likely industrial hidden',
        ),
        findsOneWidget,
      );
      // The info sheet reports the model's measured test scores.
      await tester.tap(find.byTooltip('What does this mean?'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.textContaining('right 60% of the time'),
        100,
        scrollable: find.byType(Scrollable).last,
      );
      expect(
        find.textContaining('found 48% of the industrial'),
        findsOneWidget,
      );
    });

    testWidgets('stacked hotspots show one count badge and a list', (
      tester,
    ) async {
      final server = FakeServer()
        ..recentPoints = [
          // Three satellites saw the same fire.
          point('VIIRS_SNPP_NRT', 90.71, 22.48, 'nominal'),
          point('VIIRS_NOAA20_NRT', 90.71, 22.48, 'high'),
          point('VIIRS_NOAA21_NRT', 90.71, 22.48, 'nominal'),
          point('VIIRS_SNPP_NRT', 88.6, 25.6, 'nominal'),
        ];
      final service = await signedIn(server);
      await openMap(tester, service);
      await tester.tap(find.text('7 days'));
      await tester.pumpAndSettle();
      expect(find.textContaining('4 hotspots • last 7 days'), findsOneWidget);
      // Badge count + the single dot add up to the caption.
      expect(find.byKey(const ValueKey('cluster-0')), findsOneWidget);
      expect(find.text('3'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('cluster-0')));
      await tester.pumpAndSettle();
      expect(find.text('3 hotspots at this spot'), findsOneWidget);
      expect(
        find.textContaining('Seen by VIIRS NOAA20, VIIRS NOAA21, VIIRS S-NPP'),
        findsOneWidget,
      );
      await tester.tap(find.text('VIIRS NOAA20').last);
      await tester.pumpAndSettle();
      expect(find.text('Confidence: high'), findsOneWidget); // details card
    });

    testWidgets('a spread-out cluster zooms in and splits', (tester) async {
      final server = FakeServer()
        ..recentPoints = [
          point('VIIRS_SNPP_NRT', 90.40, 23.80, 'nominal'),
          point('VIIRS_SNPP_NRT', 90.46, 23.84, 'nominal'),
        ];
      final service = await signedIn(server);
      await openMap(tester, service);
      await tester.tap(find.text('7 days'));
      await tester.pumpAndSettle();
      expect(find.text('2'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('cluster-0')));
      await tester.pump(const Duration(milliseconds: 500));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('cluster-0')), findsNothing);
      expect(find.text('2 hotspots at this spot'), findsNothing);
    });

    testWidgets('archive month picker shows a past season', (tester) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await openMap(tester, service);
      await tester.tap(find.text('Month…'));
      await tester.pumpAndSettle();
      expect(find.text('Archive month'), findsOneWidget);
      expect(find.text('13,307'), findsOneWidget); // 2024 shown first
      await tester.tap(find.text('Apr'));
      await tester.pumpAndSettle();
      expect(server.mapQueries.last, 'month=2024-04');
      expect(find.text('April 2024'), findsOneWidget); // the chip
      expect(
        find.text('3 hotspots • April 2024 • 53 non-fire heat sources hidden'),
        findsOneWidget,
      );
    });

    testWidgets('a failed window offers retry', (tester) async {
      final server = FakeServer()..mapDown = true;
      final service = await signedIn(server);
      await openMap(tester, service);
      await tester.tap(find.text('7 days'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('Could not load these hotspots'),
        findsOneWidget,
      );
      server.mapDown = false;
      await tester.tap(find.widgetWithText(FilledButton, 'Retry'));
      await tester.pumpAndSettle();
      expect(find.textContaining('2 hotspots • last 7 days'), findsOneWidget);
    });

    testWidgets('Bangladesh outline is drawn', (tester) async {
      final service = await signedIn(FakeServer());
      await tester.runAsync(Outline.load);
      await openMap(tester, service);
      expect(find.byType(PolygonLayer), findsOneWidget);
      expect(
        find.textContaining('No hotspots inside Bangladesh'),
        findsNothing,
      );
    });

    testWidgets('a quiet day says so instead of an empty map', (tester) async {
      final service = await signedIn(FakeServer()..total = 0);
      await openMap(tester, service);
      expect(
        find.textContaining(
          'No hotspots inside Bangladesh in the last 24 hours',
        ),
        findsOneWidget,
      );
      // A quiet day is not stale data: no misleading "old hotspot" banner.
      expect(find.textContaining('more than 2 days old'), findsNothing);
      expect(find.textContaining('last fetched'), findsNothing);
      await tester.tap(find.text('Overview'));
      await tester.pumpAndSettle();
      expect(find.text('No hotspots in the last 24 hours'), findsOneWidget);
      expect(find.textContaining('data fetched'), findsOneWidget);
    });

    test('outline asset parses into the country polygons', () async {
      TestWidgetsFlutterBinding.ensureInitialized();
      final outline = await Outline.load();
      expect(outline.polygons.length, 22);
      final mainland = outline.polygons.reduce(
        (a, b) => a.first.length >= b.first.length ? a : b,
      );
      final lats = mainland.first.map((p) => p.latitude);
      expect(lats.reduce((a, b) => a > b ? a : b), closeTo(26.62, 0.01));
    });

    testWidgets('select area frames the map and saves it', (tester) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await openMap(tester, service);
      await tester.tap(find.text('Select area'));
      await tester.pumpAndSettle();
      expect(find.textContaining('box covers your area'), findsOneWidget);
      await tester.tap(find.text('Save area'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, ' Home ');
      await tester.tap(find.widgetWithText(FilledButton, 'Save'));
      await tester.pumpAndSettle();
      final area = server.areas.single;
      expect(area['name'], 'Home');
      // The frame is inside the initial view, centred on the processed region.
      expect(area['west'] < 90.35 && 90.35 < area['east'], isTrue);
      expect(area['south'] < 23.6 && 23.6 < area['north'], isTrue);
      expect(find.textContaining('Saved "Home"'), findsOneWidget);
      expect(find.text('Save area'), findsNothing);
    });
  });

  group('burning season', () {
    testWidgets('calendar opens on the multi-year season', (tester) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Calendar'));
      await tester.pumpAndSettle();
      expect(find.text('Burning season'), findsOneWidget);
      expect(find.text('Peak: Apr'), findsOneWidget);
      expect(find.text('Average of 2023–2024 (2 years)'), findsOneWidget);
      expect(
        find.text('Processing versions: 6.2 (2003–2017), 61.03 (2023–2024)'),
        findsOneWidget,
      );
      // Caption defaults to the peak month.
      expect(
        find.text('Apr • average 1,818 • range 1,536–2,099'),
        findsOneWidget,
      );
      // Compare one year: its value joins the caption.
      await tester.tap(find.text('None'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('2023').last);
      await tester.pumpAndSettle();
      expect(
        find.text('Apr • average 1,818 • range 1,536–2,099 • 2023: 2,099'),
        findsOneWidget,
      );
      // Tapping January's column reads January.
      final chart = find.byKey(const ValueKey('season-MODIS_ARCHIVE'));
      final box = tester.getRect(chart);
      final slot = (box.width - 40) / 12;
      await tester.tapAt(Offset(box.left + 40 + slot / 2, box.center.dy));
      await tester.pumpAndSettle();
      expect(find.text('Jan • average 10 • 2023: 10'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.text('10 static-source or offshore hotspots not counted'),
        100,
      );
      expect(server.seasonQueries, [null]);
    });

    test('season is cached offline and removed on sign out', () async {
      final server = FakeServer();
      final service = await signedIn(server);
      expect((await service.season()).offline, isFalse);
      server.down = true;
      final offline = await service.season();
      expect(offline.offline, isTrue);
      expect(offline.season['region'], 'Bangladesh');
      await expectLater(
        service.season(bbox: [89, 22, 90, 23]),
        throwsA(isA<Exception>()),
      );
      server.down = false;
      await service.signOut();
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(service.seasonKey), isNull);
    });

    testWidgets('saved area shows its own season', (tester) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await tester.pumpWidget(
        MaterialApp(
          home: AreaScreen(
            api: service,
            area: {
              'name': 'Hills',
              'west': 91.5,
              'south': 21.5,
              'east': 92.5,
              'north': 23.5,
            },
            snapshot: Snapshot(summary(), weeks, [], DateTime.now().toUtc()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Peak: Apr'), 200);
      expect(find.text('Burning season here'), findsOneWidget);
      expect(server.seasonQueries, ['91.5,21.5,92.5,23.5']);
    });

    test('axis ticks and numbers are clean', () {
      expect(niceCeil(2683), 5000);
      expect(niceCeil(1817.5), 2000);
      expect(niceCeil(0), 1);
      expect(thousands(8444), '8,444');
      expect(thousands(1817.5), '1,818');
      expect(compact(10000), '10k');
      expect(compact(2500), '2.5k');
      expect(compact(500), '500');
    });
  });

  group('account', () {
    testWidgets('edit display name', (tester) async {
      final server = FakeServer();
      final service = await signedIn(server);
      await tester.pumpWidget(AgniApp(api: service));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Account'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Edit name'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, 'Agni');
      await tester.tap(find.widgetWithText(FilledButton, 'Save'));
      await tester.pumpAndSettle();
      expect(server.calls, contains('PATCH /v1/me'));
      expect(find.text('Agni'), findsOneWidget);
    });
  });

  group('clusters', () {
    Offset project(LatLng at) => Offset(at.longitude * 100, at.latitude * 100);

    test('points in one grid cell merge; counts are kept', () {
      final clusters = clusterPoints([
        point('MODIS_NRT', 90.00, 23.00, 'high'),
        point('VIIRS_SNPP_NRT', 90.10, 23.10, 'low'),
        point('VIIRS_SNPP_NRT', 91.00, 23.00, 'low'),
      ], project);
      expect(clusters.map((c) => c.size).toList()..sort(), [1, 2]);
      final pair = clusters.firstWhere((c) => c.size == 2);
      expect(pair.center.latitude, closeTo(23.05, 1e-9));
      expect(pair.sources, {'MODIS_NRT', 'VIIRS_SNPP_NRT'});
      expect(pair.extent, closeTo(0.1, 1e-9));
      expect(clusters.fold<int>(0, (n, c) => n + c.size), 3);
    });

    test('same-spot stack has zero extent; flags roll up', () {
      final stack = Cluster([
        {...point('VIIRS_SNPP_NRT', 91.73, 22.47, 'n'), 'likely_static': true},
        {
          ...point('VIIRS_NOAA20_NRT', 91.73, 22.47, 'n'),
          'likely_static': true,
        },
      ]);
      expect(stack.extent, 0);
      expect(stack.allLikelyStatic, isTrue);
    });
  });

  group('saved area statistics', () {
    final snapshot = Snapshot(summary(), weeks, [
      point('MODIS_NRT', 89.5, 22.5, 'high'),
      point('MODIS_NRT', 89.6, 22.6, 'low'),
      point('VIIRS_SNPP_NRT', 89.5, 22.5, 'nominal'),
      point('VIIRS_SNPP_NRT', 91.5, 25.5, 'high'),
    ], DateTime.now().toUtc());

    test('sensors stay separate and coverage is explicit', () {
      final stats = areaStats(snapshot, [89, 22, 90, 23]);
      expect(stats.coverage, Coverage.inside);
      expect(stats.bySource['MODIS_NRT']!.count, 2);
      expect(stats.bySource['MODIS_NRT']!.quality, {'high': 1, 'low': 1});
      expect(stats.bySource['VIIRS_SNPP_NRT']!.count, 1);
      expect(areaStats(snapshot, [87, 22, 90, 23]).coverage, Coverage.partial);
      final outside = areaStats(snapshot, [80, 10, 81, 11]);
      expect(outside.coverage, Coverage.outside);
      expect(outside.bySource, isEmpty);
    });

    test('labels are short and plain', () {
      expect(sourceLabel('VIIRS_SNPP_NRT'), 'VIIRS S-NPP');
      expect(sourceLabel('MODIS_NRT'), 'MODIS');
      expect(
        areaSize({'west': 89, 'south': 22, 'east': 90, 'north': 23}),
        'about 103 × 111 km',
      );
    });

    testWidgets('area outside the processed region is not shown as zero', (
      tester,
    ) async {
      await tester.pumpWidget(
        MaterialApp(
          home: AreaScreen(
            area: {
              'name': 'Delhi',
              'west': 76.8,
              'south': 28.4,
              'east': 77.4,
              'north': 28.9,
            },
            snapshot: snapshot,
          ),
        ),
      );
      expect(find.textContaining('not the same as zero fires'), findsOneWidget);
      expect(find.byType(SensorTile), findsNothing);
    });
  });
}
