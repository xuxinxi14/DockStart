# Assisted Stable source bundle

The release stage copies the exact archives listed in `SOURCE_MANIFEST.json`
to `resources/sources/` after SHA256 verification.

- `meeko-0.7.1.tar.gz` is the complete corresponding source for the bundled
  Meeko wheel.
- `gemmi-0.7.5.tar.gz` provides the same-version source for the bundled
  MPL-2.0 Gemmi wheel.
- `tqdm-4.67.1.tar.gz` preserves same-version source and dual-license
  provenance (`MPL-2.0 AND MIT`).

These are unmodified upstream PyPI artifacts. DockStart does not rebuild them
and does not apply patches. The downloader is an explicit maintainer action;
the release builder itself never accesses the network.
