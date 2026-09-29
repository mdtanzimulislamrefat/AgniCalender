import 'package:flutter/material.dart';

import 'models/snapshot.dart';
import 'screens/home.dart';
import 'screens/login.dart';
import 'services/agni_api.dart';

void main() => runApp(const AgniApp());

class AgniApp extends StatelessWidget {
  const AgniApp({super.key, this.api});
  final AgniApi? api;
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'AgniCalendar',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFFB64E24)),
      scaffoldBackgroundColor: const Color(0xFFF7F5EF),
    ),
    home: AuthGate(api: api),
  );
}

/// Nothing is shown without a signed-in user. When the session ends (sign out,
/// revoked or expired) every open screen closes and the login screen returns.
class AuthGate extends StatefulWidget {
  const AuthGate({super.key, this.api});
  final AgniApi? api;
  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  late final AgniApi api = widget.api ?? AgniApi();
  bool restored = false;
  String? notice;

  @override
  void initState() {
    super.initState();
    api.onSessionExpired = () =>
        setState(() => notice = 'Your session ended. Please sign in again.');
    api.user.addListener(onUser);
    api.restoreSession().then((_) {
      if (mounted) setState(() => restored = true);
    });
  }

  void onUser() {
    if (!mounted) return;
    if (api.user.value == null) {
      Navigator.of(context).popUntil((route) => route.isFirst);
    } else {
      notice = null;
    }
  }

  @override
  void dispose() {
    api.user.removeListener(onUser);
    api.onSessionExpired = null;
    if (widget.api == null) api.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => !restored
      ? const Scaffold(body: Center(child: CircularProgressIndicator()))
      : ValueListenableBuilder<Json?>(
          valueListenable: api.user,
          builder: (context, user, _) => user == null
              ? LoginScreen(api: api, notice: notice)
              // A new key per account so no state leaks between users.
              : AgniHome(key: ValueKey(user['id']), api: api),
        );
}
