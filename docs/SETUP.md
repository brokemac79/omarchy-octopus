# Setup, updates and troubleshooting

## Find your tariff and account details

Use your Octopus online account and its developer/API page to find your API key,
account number, electricity MPAN and meter serial. The setup prompt masks the key;
never paste it into an issue or shell command.

Product and tariff are different values. For example, `AGILE-24-10-01` is a product
and `E-1R-AGILE-24-10-01-J` is a full tariff code including a region suffix.
These are examples, **not a recommendation or automatic account detection**.
Use the active import electricity tariff from your own agreement. When your
agreement changes, rerun setup. Fixed tariffs, gas and export are not supported.

## Modes

- **Prices only:** supply product/tariff and leave the API key blank.
- **Home Mini:** answer yes, supply API key, account number and electricity MPAN.
  Live telemetry is retrieved from Octopus's cloud, not your LAN. No Mini IP needed.
- **Standard smart meter:** answer no, supply key, MPAN and meter serial. Usage is
  delayed; the panel uses the latest available 24-hour period within its three-day
  history request. The date range is explicit. No live watts are available.

Standard-meter fallback has mocked regression coverage, not yet a separate
live-account acceptance test. Home Mini is the live-tested configuration.

## Updating

For Git-managed installations:

```sh
omarchy plugin update community.octopus-energy
```

For a manual copy, `git pull --ff-only` in your source checkout, then repeat the
five-file copy command in the README. Do not copy config or cache files into the
plugin directory. Existing settings remain outside the checkout.

If the shell retains old QML after an update, use Omarchy's shell restart command
(`omarchy-restart-shell`) from the desktop. This restarts the bar/panels, not your
application windows. Avoid restarting while interacting with a shell menu.

## Troubleshooting

- **No widget:** run `omarchy plugin validate` on the installed directory, check
  `omarchy plugin list`, and confirm it is enabled on the right bar.
- **No current price:** verify both product and tariff; check internet access.
  Missing future prices can be normal until Octopus publishes them.
- **Authentication error:** rerun the masked setup; confirm account access and key.
- **No live power:** confirm Home Mini is enabled and sending readings in Octopus's
  own app. Standard smart meters do not provide this live view.
- **Partial usage/cost:** missing intervals are not treated as zero. £ cost needs
  both consumption and a matching half-hour tariff rate. Check the displayed range.
- **Stale data:** cached values remain visible with warnings; check connectivity,
  then Refresh/R. Retries are intentionally rate-limited.
- **Unsupported imports:** this requires Omarchy's Quickshell plugin runtime, not
  a Waybar-only installation. Tested with Omarchy 4.0.4-1.

For a local adapter check, run the installed `octopus.py`. Its output omits account
identifiers and keys but **contains your household usage**; do not attach it raw to
public issues. Report a sanitised error and versions instead.

## Removing local data

After disabling/removing the plugin, optionally remove `omarchy-octopus` under
`$XDG_CONFIG_HOME` and `$XDG_CACHE_HOME` (normally `~/.config` and `~/.cache`) using
your file manager. This erases local settings/cache but does not revoke the API key.
