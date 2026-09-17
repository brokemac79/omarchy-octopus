# Contributing

Small, focused issues and pull requests are welcome. This is an independent,
early-stage community plugin; compatibility with every Omarchy release is not guaranteed.

## Local checks

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile octopus.py
omarchy plugin validate .
```

The last command requires an Omarchy desktop. QML imports depend on Omarchy's
running Quickshell environment; generic QML lint alone is not a runtime test.

Test UI changes in the real shell: all three tabs, kWh/£, keyboard navigation,
Escape/reopen, a failed refresh and incomplete data. Include your Omarchy version.
Use synthetic fixtures for tests/screenshots, never your account or consumption cache.
`python3 docs/demo.py` prints a fully synthetic snapshot without accessing settings
or the network. The published screenshots render the actual panel with this data.

Preserve negative prices/costs, distinguish watts from kWh, use VAT-inclusive rates,
and never substitute zero for missing readings. Cost estimates exclude standing charges.
The adapter must remain read-only and keep credentials out of stdout and errors.

Contributions are provided under this repository's MIT licence. Do not add code or
assets copied from other projects without checking their licence and attribution.
