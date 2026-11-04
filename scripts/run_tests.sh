#!/usr/bin/env bash
# Optional SCHEDULE_TEST_PYTHON selects an isolated workspace interpreter.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
exec "${SCHEDULE_TEST_PYTHON:-python3}" -m pytest "$@"
