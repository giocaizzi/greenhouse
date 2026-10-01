#!/usr/bin/env bash
# Build the three workspace wheels into a temp dir and print their sorted contents.
#
# Usage: refactor/scripts/check_wheels.sh [OUTPUT_FILE]
#   Without OUTPUT_FILE the listing goes to stdout. Compare against
#   refactor/baseline/wheel-contents.txt to prove a refactor did not drop or add
#   shipped files (templates, static assets, alembic scripts, profiles, tcss, ...).
#
# The package version (bumped by release-please) is normalized to <VERSION> in
# wheel and dist-info names so the listing survives releases. Read-only for the
# repo: wheels are built into a mktemp dir that is removed on exit.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
out="${1:-/dev/stdout}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cd "$repo_root"
for pkg in greenhouse-core greenhouse-server greenhouse-cli; do
  uv build --package "$pkg" --wheel -o "$tmp" >"$tmp/build-$pkg.log" 2>&1 || {
    echo "uv build failed for $pkg:" >&2
    cat "$tmp/build-$pkg.log" >&2
    exit 1
  }
done

version="$(uv run --no-sync python -c 'import importlib.metadata as m; print(m.version("greenhouse-core"))')"

uv run --no-sync python - "$tmp" "$version" >"$out" <<'PY'
import sys
import zipfile
from pathlib import Path

tmp, version = Path(sys.argv[1]), sys.argv[2]
lines = []
for wheel in sorted(tmp.glob("*.whl")):
    wheel_name = wheel.name.replace(version, "<VERSION>")
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            lines.append(f"{wheel_name}\t{name.replace(version, '<VERSION>')}")
print("\n".join(sorted(lines)))
PY
