# Security and privacy

Do not include API keys, account numbers, MPANs, meter serials, tokens or cache files
in issues, pull requests, screenshots or logs. Screenshots in this repository use
synthetic readings. Runtime configuration and caches belong outside the checkout.

Credentials are stored in a local mode-0600 file, not a desktop keyring. They are
protected from other ordinary users, not from software running as your own user
or root. The plugin makes read-only requests to api.octopus.energy over HTTPS.
It has no analytics, external telemetry, cloud backend or OpenClaw dependency.

Each HTTPS response (one REST page or GraphQL response) is limited to 1 MiB
(1,048,576 bytes). The adapter reads at most 1,048,577 bytes, using the extra
byte to detect overflow, and rejects oversized responses before JSON parsing.
This bound does not rely on Content-Length, including for chunked responses.
The limit leaves headroom for the current 200-record REST pages and short
telemetry queries; it is not a limit on the size of parsed Python objects.
Oversized responses retain cached readings, show an error and use the existing
60-second retry backoff. Requests also have a 12-second socket timeout (not a
total wall-clock deadline), and REST pagination is capped at ten pages.

For a suspected credential leak, revoke/rotate the Octopus key before sharing
sanitised details. Do not publish a working exploit or secret in a public issue;
use GitHub private vulnerability reporting when available on the Security tab.

Only the latest main branch is currently maintained. This is an early community
release, not an official Octopus Energy product or a billing source of truth.
