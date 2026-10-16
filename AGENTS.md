# Agent guide

Read `VISION.md`, `README.md`, `CONTRIBUTING.md` and `SECURITY.md` before changing behaviour. `octopus.py` owns data retrieval and calculations; QML owns presentation. Keep the adapter stdlib-only and the plugin independent of OpenClaw, a backend or core Omarchy changes.

## Invariants

- Octopus access is read-only. Never add load control, exports, analytics, telemetry or credential-bearing redirects.
- Never commit or print real API keys, account/meter identifiers, tokens, caches or customer readings. Tests and screenshots must use synthetic data. Follow `SECURITY.md` for vulnerability reports.
- Preserve negative Agile prices and costs. Distinguish live watts/kW from interval kWh; use VAT-inclusive rates and exclude standing charges from estimates.
- Missing or stale data is not zero. Keep incomplete coverage and refresh failures explicit, retaining last-good values where appropriate. Handle Europe/London daylight-saving days and UTC interval matching.
- Do not claim a tariff, account type, installation path or QML interaction was live-tested unless it was. The standard-meter fallback is not yet live-verified.

## Before a PR

Run `python3 -m unittest discover -s tests -v`, `python3 -m py_compile octopus.py`, and `python3 docs/demo.py > /dev/null`. On a compatible Omarchy desktop, also run `omarchy plugin validate .` and manually check the panel interactions listed in `CONTRIBUTING.md`. In the PR, state which desktop checks were not possible. Review the diff and screenshots for private data.
