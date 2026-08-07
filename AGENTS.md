# Agent Commands and Configuration

## Code Checking Commands
- Lint/Typecheck: `python3 -m py_compile` (for syntax check) or `python -m pytest` (when pytest is installed)

## Notes
- Use Python 3 for compilation checks
- Ensure requirements.txt dependencies are installed before running tests

## P7 release safety
- Set `observability.p7.enabled: true` only in shadow and paper environments;
  keep it `false` for live and restricted-live configurations.
- Observability, alert, health-check, and checkpoint failures are non-fatal; do
  not place trading decisions inside telemetry callbacks.
- Live trading remains prohibited by default. Restricted-live approval requires
  the gate evidence and rollback drill documented in `README.md`.
- Emergency flatten is an execution operation, not a telemetry operation. Use
  the tested broker/execution flatten path, reconcile broker state, and retain
  correlation IDs in the incident record.
