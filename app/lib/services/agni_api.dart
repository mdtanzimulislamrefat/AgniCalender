import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../models/snapshot.dart';

/// Sign-in is required, or the session could not be renewed.
class AuthException implements Exception {
  const AuthException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// The server rejected a request with a message meant for the user.
class ApiException implements Exception {
  const ApiException(this.status, this.message);
  final int status;
  final String message;
  @override
  String toString() => message;
}

/// Every data request needs a signed-in user. A 15-minute access token is kept
/// in memory and a rotating refresh token in secure storage. Signing out (or a
/// revoked session) deletes the tokens and every cached snapshot/area.
class AgniApi {
  AgniApi({
    http.Client? client,
    FlutterSecureStorage? storage,
    this.baseUrl = const String.fromEnvironment(
      'API_BASE_URL',
      defaultValue: 'http://127.0.0.1:8001',
    ),
  }) : client = client ?? http.Client(),
       storage = storage ?? const FlutterSecureStorage();
  final http.Client client;
  final FlutterSecureStorage storage;
  final String baseUrl;

  /// Signed-in profile, or null when signed out.
  final user = ValueNotifier<Json?>(null);

  /// Called when the server ends the session (expired or revoked).
  VoidCallback? onSessionExpired;
  String? _access;
  String? _refresh;
  Future<bool>? _renewing;
  static const _timeout = Duration(seconds: 15);
  String get cacheKey => 'agni_snapshot_v1_$baseUrl';
  String get seasonKey => 'agni_season_v1_$baseUrl';
  String get refreshKey => 'agni_refresh_v1_$baseUrl';
  String get userKey => 'agni_user_v1_$baseUrl';
  String areasKey(Object userId) => 'agni_areas_v1_${baseUrl}_$userId';
  bool get signedIn => user.value != null;

  Future<bool> restoreSession() async {
    try {
      await storage.delete(key: 'agni_token_v1_$baseUrl'); // auth v1 token
      final refresh = await storage.read(key: refreshKey);
      final profile = await storage.read(key: userKey);
      if (refresh != null && profile != null) {
        _refresh = refresh;
        user.value = Json.from(jsonDecode(profile) as Map);
      }
    } catch (_) {
      await _clearSession();
    }
    return signedIn;
  }

  Future<void> signIn(
    String email,
    String password, {
    bool register = false,
    String? displayName,
  }) async {
    final response = await _send(
      'POST',
      '/v1/auth/${register ? 'register' : 'login'}',
      body: {
        'email': email,
        'password': password,
        if (register && displayName != null && displayName.trim().isNotEmpty)
          'display_name': displayName.trim(),
      },
      anonymous: true,
    );
    if (response.statusCode != 200 && response.statusCode != 201) {
      throw AuthException(switch (response.statusCode) {
        401 => 'Incorrect email or password.',
        409 => 'An account with this email already exists.',
        422 => 'Enter a valid email and a password of 8–128 characters.',
        429 => 'Too many attempts. Try again in 15 minutes.',
        _ => 'Sign-in failed (server ${response.statusCode}).',
      });
    }
    await _store(_decode(response));
  }

  /// Ends the session and removes all data cached on this device.
  Future<void> signOut({bool everywhere = false}) async {
    final refresh = _refresh;
    try {
      if (everywhere) {
        await _send('POST', '/v1/auth/logout-all', auth: true);
      } else if (refresh != null) {
        await _send(
          'POST',
          '/v1/auth/logout',
          body: {'refresh_token': refresh},
          anonymous: true,
        );
      }
    } catch (_) {
      // Offline: the server session expires on its own.
    }
    await _clearSession();
  }

  Future<void> _store(Json session) async {
    final profile = Json.from(session['user'] as Map);
    await storage.write(key: refreshKey, value: session['refresh_token']);
    await storage.write(key: userKey, value: jsonEncode(profile));
    _access = session['access_token'] as String;
    _refresh = session['refresh_token'] as String;
    user.value = profile;
  }

  Future<void> _clearSession() async {
    final id = user.value?['id'];
    _access = _refresh = null;
    user.value = null;
    try {
      await storage.delete(key: refreshKey);
      await storage.delete(key: userKey);
    } catch (_) {}
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(cacheKey);
      await prefs.remove(seasonKey);
      if (id != null) await prefs.remove(areasKey(id));
    } catch (_) {}
  }

  /// One refresh at a time: parallel 401s share it, so a rotated refresh
  /// token is never sent twice (the server treats that as theft).
  Future<bool> _renew() =>
      _renewing ??= _doRenew().whenComplete(() => _renewing = null);

  Future<bool> _doRenew() async {
    final refresh = _refresh;
    if (refresh == null) return false;
    final response = await _send(
      'POST',
      '/v1/auth/refresh',
      body: {'refresh_token': refresh},
      anonymous: true,
    );
    if (response.statusCode == 200) {
      await _store(_decode(response));
      return true;
    }
    if (response.statusCode == 401) {
      await _clearSession();
      onSessionExpired?.call();
      return false;
    }
    throw Exception('API ${response.statusCode}');
  }

  /// With [auth] the call needs a signed-in user. Otherwise the access token is
  /// sent when available and the call falls back to guest if it is rejected.
  /// [anonymous] calls (login, refresh, logout) never carry or renew a token.
  Future<http.Response> _send(
    String method,
    String path, {
    Map<String, String>? params,
    Object? body,
    bool auth = false,
    bool anonymous = false,
  }) async {
    if (auth && _access == null && !(signedIn && await _renew())) {
      throw const AuthException('Please sign in.');
    }
    final uri = Uri.parse('$baseUrl$path').replace(queryParameters: params);
    Future<http.Response> go() async {
      final request = http.Request(method, uri);
      if (_access != null && !anonymous) {
        request.headers['Authorization'] = 'Bearer $_access';
      }
      if (body != null) {
        request.headers['Content-Type'] = 'application/json';
        request.body = jsonEncode(body);
      }
      return http.Response.fromStream(
        await client.send(request).timeout(_timeout),
      );
    }

    final sent = anonymous ? null : _access;
    final response = await go();
    if (response.statusCode != 401 || sent == null) return response;
    // Another request may already have renewed the token.
    if (_access != null && _access != sent) return go();
    if (await _renew() || !auth) return go();
    throw const AuthException('Session expired. Please sign in again.');
  }

  Json _decode(http.Response response) =>
      Json.from(jsonDecode(response.body) as Map);

  ApiException _error(http.Response response) {
    try {
      final detail = _decode(response)['detail'];
      if (detail is String) return ApiException(response.statusCode, detail);
    } catch (_) {}
    return ApiException(
      response.statusCode,
      'Request failed (server ${response.statusCode}).',
    );
  }

  Future<Json> _get(String path, [Map<String, String>? params]) async {
    final response = await _send('GET', path, params: params, auth: true);
    if (response.statusCode != 200) {
      throw Exception('API ${response.statusCode}');
    }
    return _decode(response);
  }

  Future<Json> updateName(String? name) async {
    final response = await _send(
      'PATCH',
      '/v1/me',
      body: {'display_name': name},
      auth: true,
    );
    if (response.statusCode != 200) throw _error(response);
    final profile = _decode(response);
    await storage.write(key: userKey, value: jsonEncode(profile));
    user.value = profile;
    return profile;
  }

  /// Saved areas, from the server or (offline) from this device.
  Future<({List<Json> areas, bool offline})> areas() async {
    final id = user.value?['id'];
    if (id == null) throw const AuthException('Please sign in.');
    final key = areasKey(id);
    try {
      final response = await _send('GET', '/v1/me/areas', auth: true);
      // Server errors (5xx) fall through to the device copy like a network error.
      if (response.statusCode >= 500) {
        throw Exception('API ${response.statusCode}');
      }
      if (response.statusCode != 200) throw _error(response);
      final list = (jsonDecode(response.body) as List)
          .map((e) => Json.from(e as Map))
          .toList();
      try {
        await (await SharedPreferences.getInstance()).setString(
          key,
          jsonEncode(list),
        );
      } catch (_) {}
      return (areas: list, offline: false);
    } on AuthException {
      rethrow;
    } on ApiException {
      rethrow;
    } catch (_) {
      final saved = (await SharedPreferences.getInstance()).getString(key);
      if (saved == null) rethrow;
      return (
        areas: (jsonDecode(saved) as List)
            .map((e) => Json.from(e as Map))
            .toList(),
        offline: true,
      );
    }
  }

  /// Multi-year monthly hotspot counts (whole region, or inside [bbox]). The
  /// whole-region result is kept on the device for offline use.
  Future<({Json season, bool offline})> season({List<double>? bbox}) async {
    try {
      final season = await _get('/v1/archive/season', {
        if (bbox != null) 'bbox': bbox.join(','),
      });
      if (bbox == null) {
        try {
          await (await SharedPreferences.getInstance()).setString(
            seasonKey,
            jsonEncode(season),
          );
        } catch (_) {}
      }
      return (season: season, offline: false);
    } on AuthException {
      rethrow;
    } catch (_) {
      final saved = bbox == null
          ? (await SharedPreferences.getInstance()).getString(seasonKey)
          : null;
      if (saved == null) rethrow;
      return (season: Json.from(jsonDecode(saved) as Map), offline: true);
    }
  }

  /// Map points for a recent window ('7d', '30d') or an archive month ('YYYY-MM').
  Future<Json> hotspots({String? window, String? month}) =>
      _get('/v1/map/hotspots', {'window': ?window, 'month': ?month});

  /// Archive months with data and the recent-days range.
  Future<Json> mapAvailable() => _get('/v1/map/available');

  Future<Json> createArea(String name, List<double> bbox) async {
    final response = await _send(
      'POST',
      '/v1/me/areas',
      body: {
        'name': name,
        'west': bbox[0],
        'south': bbox[1],
        'east': bbox[2],
        'north': bbox[3],
      },
      auth: true,
    );
    if (response.statusCode != 201) throw _error(response);
    return _decode(response);
  }

  Future<void> deleteArea(int id) async {
    final response = await _send('DELETE', '/v1/me/areas/$id', auth: true);
    if (response.statusCode != 204) throw _error(response);
  }

  Future<Snapshot> fetch() async {
    final summary = await _get('/v1/summary');
    final id = summary['report_id'].toString();
    final calendar = await _get('/v1/calendar', {'report_id': id});
    if (calendar['report_id'].toString() != id) {
      throw const FormatException('Snapshot mismatch');
    }
    final points = <Json>[];
    int? expectedTotal;
    do {
      final page = await _get('/v1/observations', {
        'report_id': id,
        'offset': '${points.length}',
        'limit': '1000',
      });
      if (page['report_id'].toString() != id) {
        throw const FormatException('Snapshot mismatch');
      }
      final total = page['total'] as int;
      if (total < 0 || (expectedTotal != null && total != expectedTotal)) {
        throw const FormatException('Inconsistent pages');
      }
      expectedTotal = total;
      final rows = (page['observations'] as List)
          .map((e) => Json.from(e as Map))
          .toList();
      if (rows.isEmpty && points.length < total) {
        throw const FormatException('Incomplete page');
      }
      points.addAll(rows);
      if (points.length > total) {
        throw const FormatException('Invalid page count');
      }
    } while (points.length < expectedTotal);
    return Snapshot(
      summary,
      (calendar['weeks'] as List).map((e) => Json.from(e as Map)).toList(),
      points,
      DateTime.now().toUtc(),
    );
  }

  Future<void> save(Snapshot snapshot) async {
    final prefs = await SharedPreferences.getInstance();
    if (!await prefs.setString(cacheKey, jsonEncode(snapshot.toJson()))) {
      throw Exception('Cache unavailable');
    }
  }

  Future<Snapshot?> cached() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final value = prefs.getString(cacheKey);
      return value == null
          ? null
          : Snapshot.fromJson(Json.from(jsonDecode(value) as Map));
    } catch (_) {
      return null;
    }
  }

  void close() {
    client.close();
    user.dispose();
  }
}
