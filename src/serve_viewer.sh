#!/usr/bin/env bash
# THROWAWAY SPIKE: serve the viewer on http://127.0.0.1:8765 with the live FalkorDB API.
# Only data/viewer/ is served (never the project root, which holds .env).
set -euo pipefail
cd "$(dirname "$0")/.."
cp -R src/viewer/. data/viewer/
exec .venv/bin/python src/server.py
