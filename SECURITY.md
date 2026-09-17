# Security and privacy

Do not include API keys, account numbers, MPANs, meter serials, tokens or cache files
in issues, pull requests, screenshots or logs. Screenshots in this repository use
synthetic readings. Runtime configuration and caches belong outside the checkout.

Credentials are stored in a local mode-0600 file, not a desktop keyring. They are
protected from other ordinary users, not from software running as your own user
or root. The plugin makes read-only requests to api.octopus.energy over HTTPS.
It has no analytics, external telemetry, cloud backend or OpenClaw dependency.

For a suspected credential leak, revoke/rotate the Octopus key before sharing
sanitised details. Do not publish a working exploit or secret in a public issue;
use GitHub private vulnerability reporting when available on the Security tab.

Only the latest main branch is currently maintained. This is an early community
release, not an official Octopus Energy product or a billing source of truth.
