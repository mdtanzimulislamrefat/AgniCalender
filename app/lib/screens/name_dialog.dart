import 'package:flutter/material.dart';

/// Asks for a short name; pops the trimmed text, or null on cancel.
///
/// The dialog owns its controller so it is disposed only after the closing
/// animation, when the TextField is gone.
Future<String?> askName(
  BuildContext context, {
  required String title,
  String initial = '',
  String? label,
  String? hint,
}) => showDialog<String>(
  context: context,
  builder: (_) =>
      _NameDialog(title: title, initial: initial, label: label, hint: hint),
);

class _NameDialog extends StatefulWidget {
  const _NameDialog({
    required this.title,
    required this.initial,
    this.label,
    this.hint,
  });
  final String title;
  final String initial;
  final String? label;
  final String? hint;
  @override
  State<_NameDialog> createState() => _NameDialogState();
}

class _NameDialogState extends State<_NameDialog> {
  late final controller = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.title),
    content: TextField(
      controller: controller,
      maxLength: 80,
      autofocus: true,
      decoration: InputDecoration(
        labelText: widget.label,
        hintText: widget.hint,
      ),
      onSubmitted: (value) => Navigator.pop(context, value.trim()),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () => Navigator.pop(context, controller.text.trim()),
        child: const Text('Save'),
      ),
    ],
  );
}
