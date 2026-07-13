# Replaceable Assisted Python runtime

DockStart Assisted Stable uses a separate CPython 3.11 runtime in this
directory. Meeko and its dependencies remain ordinary packages under
`Lib/site-packages`; they are not linked into or frozen inside the DockStart
executable.

Preparation Python resolution order is:

1. user-configured compatible Python environment;
2. bundled Assisted Python runtime;
3. current development environment.

Users may replace Meeko with a compatible modified version or configure a
different Python environment. Manifest SHA256 values support provenance and
diagnostics; a package mismatch should produce a warning, not a lockout.

DockStart does not modify the bundled Meeko, Gemmi, or tqdm sources.
