# Dervo harness core, vendored copy

This directory holds the `dervo_harness` library, version 0.8.2, copied unmodified from the harness-core source tree on 2026-09-25T12:49:52+00:00. Every `.py` file is byte for byte the original; `PROVENANCE.json` records a sha256 for each one and the app refuses to load the library if any file differs. The app-side adapter lives outside this directory (`backend/harness_adapter.py`) and uses the library through its public API only.

The library is Dervo's own code. No licence file exists in the harness-core source tree at vendoring time, so this notice adds no terms of its own.

Seven helpers in `upstream_adapted.py` are rewrites of code from the Hermes agent, Copyright (c) 2025 Nous Research, MIT License. The upstream licence text is in `LICENSE-Hermes.txt` and the adaptation map, listing each symbol and what changed, is in `THIRD_PARTY_NOTICES.md`. No upstream module is imported, vendored or executed.
