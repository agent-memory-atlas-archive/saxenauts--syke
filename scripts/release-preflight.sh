#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[preflight] repo: $REPO_DIR"
cd "$REPO_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required for release preflight" >&2
  exit 1
fi

echo "[preflight] lockfile and diff hygiene"
uv lock --check
git diff --check
git diff --cached --check

untracked_files="$(git ls-files --others --exclude-standard)"
if [[ -n "$untracked_files" ]]; then
  echo "[preflight] untracked files would be omitted from a release commit:" >&2
  printf '%s\n' "$untracked_files" >&2
  echo "[preflight] add intentional files or update .gitignore before release." >&2
  exit 1
fi

PYTHON_BIN="$(uv run python - <<'PY'
import sys

print(sys.executable)
PY
)"
export PYTHON_BIN

pi_test_home=""
pytest_base=""
cleanup() {
  if [[ -n "$pi_test_home" && -d "$pi_test_home" ]]; then
    rm -rf "$pi_test_home"
  fi
  if [[ -n "$pytest_base" && -d "$pytest_base" ]]; then
    rm -rf "$pytest_base"
  fi
}
trap cleanup EXIT

if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "[preflight] prepare macOS sandbox Pi test runtime"
  pi_test_home="$(mktemp -d "${TMPDIR:-/tmp}/syke-pi-home.XXXXXX")"
  HOME="$pi_test_home" "$PYTHON_BIN" - <<'PY'
from pathlib import Path

from syke.llm.pi_client import ensure_pi_binary

ensure_pi_binary()
print(Path.home() / ".syke" / "pi" / "node_modules")
PY
  export SYKE_TEST_PI_NODE_MODULES="$pi_test_home/.syke/pi/node_modules"
else
  unset SYKE_TEST_PI_NODE_MODULES || true
fi

echo "[preflight] repository quality"
uv run ruff format --check .
uv run ruff check .
pytest_base="$(mktemp -d "${TMPDIR:-/tmp}/syke-pytest.XXXXXX")"
uv run pytest tests/ -q --basetemp="$pytest_base"

macos_sandbox_pi_bash_proof="UNRUN (requires macOS)"
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "[preflight] prepared macOS sandbox and Pi bash tests"
  uv run pytest -m platform tests/test_sandbox.py tests/test_pi_tools_bash.py -q \
    --basetemp="$pytest_base"
  macos_sandbox_pi_bash_proof="PASS"
fi

echo "[preflight] build release artifacts"
rm -rf dist
mkdir -p build/lib/syke
printf 'CANARY = True\n' > build/lib/syke/_stale_build_canary.py
uv run python -m build

echo "[preflight] package metadata"
uv run --with twine twine check dist/*

WHEEL_PATH="$(uv run python - <<'PY'
from pathlib import Path
wheels = sorted(Path('dist').glob('*.whl'))
if not wheels:
    raise SystemExit('no wheel built in dist/')
print(wheels[0].resolve())
PY
)"
echo "[preflight] smoke artifact install: $WHEEL_PATH"
bash "$SCRIPT_DIR/smoke-artifact-install.sh" "$WHEEL_PATH"

SDIST_PATH="$(uv run python - <<'PY'
from pathlib import Path
sdists = sorted(Path('dist').glob('*.tar.gz'))
if not sdists:
    raise SystemExit('no sdist built in dist/')
print(sdists[0].resolve())
PY
)"
echo "[preflight] smoke sdist install: $SDIST_PATH"
bash "$SCRIPT_DIR/smoke-artifact-install.sh" "$SDIST_PATH"

echo "[preflight] smoke isolated uv tool install"
bash "$SCRIPT_DIR/smoke-tool-install.sh"

echo "[preflight] fresh agent setup smoke"
bash "$SCRIPT_DIR/fresh-install-test.sh" --run --allow-needs-runtime

echo
echo "[preflight] proof summary"
echo "[preflight] PASS  deterministic repository checks"
echo "[preflight] PASS  wheel and sdist installation"
echo "[preflight] PASS  isolated tool installation"
echo "[preflight] PASS  fresh agent setup smoke"
echo "[preflight] $macos_sandbox_pi_bash_proof  macOS sandbox and Pi bash tests"
echo "[preflight] UNRUN live provider and Pi runtime"
echo "[preflight] UNRUN Linux managed service"
echo "[preflight] UNRUN clean-user or revoked macOS TCC"
echo "[preflight] UNRUN physical sleep and wake"
echo "[preflight] completed; external proofs remain UNRUN"
