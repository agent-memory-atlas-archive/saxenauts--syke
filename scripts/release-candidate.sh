#!/usr/bin/env bash
# Local release-candidate gate.
#
# This is the pre-push proof step. GitHub Actions should confirm a candidate
# that already passed locally; it should not be the first place release bugs
# are discovered.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ALLOW_DIRTY=false
RUN_PREFLIGHT=true
RUN_LOCAL_WEB=true
RUN_LINUX_PRODUCT_QA=false
RUN_LINUX_MANAGED_SERVICE=false
PROVIDER_STATE=""
TAG_NAME=""

usage() {
  cat <<'EOF'
usage: scripts/release-candidate.sh [options]

Default gate:
  - require a clean git worktree
  - run repository-wide formatting, lint, and tests
  - run scripts/release-preflight.sh
  - verify local loopback /api/health and /api/timeline if the daemon is serving
  - print version/tag/publication state

Options:
  --allow-dirty                 allow a dirty tree while developing this script
  --skip-preflight              skip scripts/release-preflight.sh
  --skip-local-web              skip local loopback web API smoke
  --with-linux-product-qa       run Dockerized Linux product QA on the built wheel
  --provider-state <dir>        provider state for installed/live and Linux proof
  --with-linux-managed-service  run Linux user-systemd smoke on the current host
  --for-tag <vX.Y.Z>            verify package version matches the intended tag
  -h, --help                    show this help

Release order:
  1. Run this script locally before pushing.
  2. Push only after it passes.
  3. Let GitHub Actions confirm the pushed commit.
  4. Bump version/changelog, then run --for-tag with provider state and Linux QA.
EOF
}

require_value() {
  local flag="$1"
  local value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    echo "$flag requires a value" >&2
    usage >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-dirty)
      ALLOW_DIRTY=true
      shift
      ;;
    --skip-preflight)
      RUN_PREFLIGHT=false
      shift
      ;;
    --skip-local-web)
      RUN_LOCAL_WEB=false
      shift
      ;;
    --with-linux-product-qa)
      RUN_LINUX_PRODUCT_QA=true
      shift
      ;;
    --provider-state)
      require_value "$1" "${2:-}"
      PROVIDER_STATE="${2:-}"
      shift 2
      ;;
    --with-linux-managed-service)
      RUN_LINUX_MANAGED_SERVICE=true
      shift
      ;;
    --for-tag)
      require_value "$1" "${2:-}"
      TAG_NAME="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "$REPO_DIR"

if [[ -n "$PROVIDER_STATE" ]]; then
  if [[ ! -d "$PROVIDER_STATE" || ! -f "$PROVIDER_STATE/auth.json" ]]; then
    echo "[candidate] provider state must contain auth.json: $PROVIDER_STATE" >&2
    exit 2
  fi
fi

if [[ -n "$TAG_NAME" ]]; then
  if [[ "$RUN_PREFLIGHT" != true ]]; then
    echo "[candidate] tag proof cannot skip preflight" >&2
    exit 2
  fi
  if [[ -z "$PROVIDER_STATE" ]]; then
    echo "[candidate] tag proof requires --provider-state" >&2
    exit 2
  fi
  if [[ "$RUN_LINUX_PRODUCT_QA" != true ]]; then
    echo "[candidate] tag proof requires --with-linux-product-qa" >&2
    exit 2
  fi
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[candidate] missing required command: $1" >&2
    exit 1
  fi
}

step() {
  echo
  echo "[candidate] $*"
}

need_cmd git
need_cmd uv

step "repo: $REPO_DIR"
git status --short --branch

if [[ "$ALLOW_DIRTY" != true ]]; then
  if [[ -n "$(git status --porcelain=v1)" ]]; then
    echo "[candidate] dirty worktree; commit or stash before freezing a release candidate." >&2
    exit 1
  fi
fi

if [[ -n "$TAG_NAME" ]]; then
  step "checking intended release tag: $TAG_NAME"
  uv run python "$SCRIPT_DIR/check_release_tag.py" "$TAG_NAME"
  if git rev-parse -q --verify "refs/tags/$TAG_NAME" >/dev/null; then
    tagged_commit="$(git rev-list -n 1 "$TAG_NAME")"
    head_commit="$(git rev-parse HEAD)"
    if [[ "$tagged_commit" != "$head_commit" ]]; then
      echo "[candidate] tag $TAG_NAME already exists on $tagged_commit, not HEAD $head_commit" >&2
      exit 1
    fi
  fi
fi

if [[ "$RUN_PREFLIGHT" == true ]]; then
  step "running local release preflight"
  bash "$SCRIPT_DIR/release-preflight.sh"
  deterministic_proof="PASS"
  artifact_proof="PASS"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    macos_sandbox_pi_bash_proof="PASS"
  else
    macos_sandbox_pi_bash_proof="UNRUN (requires macOS)"
  fi
else
  step "running repository quality without preflight"
  uv run ruff format --check .
  uv run ruff check .
  pytest_base="$(mktemp -d "${TMPDIR:-/tmp}/syke-pytest.XXXXXX")"
  trap 'rm -rf "$pytest_base"' EXIT
  uv run pytest tests/ -q --basetemp="$pytest_base"
  deterministic_proof="PASS"
  artifact_proof="UNRUN (--skip-preflight)"
  macos_sandbox_pi_bash_proof="UNRUN (--skip-preflight)"
fi

wheel_path="$(uv run python - <<'PY'
from pathlib import Path
import tomllib

version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
wheel_version = version.replace("-", "_")
wheels = sorted(Path("dist").glob(f"syke-{wheel_version}-*.whl"))
print(wheels[0].resolve() if wheels else "")
PY
)"

if [[ -z "$wheel_path" && "$RUN_LINUX_PRODUCT_QA" == true ]]; then
  echo "[candidate] Linux product QA needs a built wheel; run preflight or build first." >&2
  exit 1
fi

if [[ -n "$PROVIDER_STATE" ]]; then
  if [[ -z "$wheel_path" ]]; then
    echo "[candidate] provider proof needs a built wheel; run preflight first." >&2
    exit 1
  fi
  step "running provider-backed installed and live runtime proof"
  bash "$SCRIPT_DIR/fresh-install-test.sh" \
    --run \
    --wheel "$wheel_path"
  live_pytest_base="$(mktemp -d "${TMPDIR:-/tmp}/syke-live-pytest.XXXXXX")"
  if ! env \
    SYKE_RUN_PI_INTEGRATION=1 \
    SYKE_LIVE_PI_AGENT_DIR="$PROVIDER_STATE" \
    uv run pytest -m live tests/test_pi_integration.py -q \
      --basetemp="$live_pytest_base"; then
    rm -rf "$live_pytest_base"
    exit 1
  fi
  rm -rf "$live_pytest_base"
  live_provider_proof="PASS"
else
  live_provider_proof="UNRUN (use --provider-state)"
fi

if [[ "$RUN_LOCAL_WEB" == true ]]; then
  step "checking local timeline API"
  uv run python - <<'PY'
import json
import os
import sys
import urllib.request

port = os.getenv("SYKE_WEB_PORT", "8765")
base = f"http://127.0.0.1:{port}"

try:
    with urllib.request.urlopen(f"{base}/api/health", timeout=5) as response:
        health = json.load(response)
    with urllib.request.urlopen(f"{base}/api/timeline?days=7", timeout=5) as response:
        timeline = json.load(response)
except Exception as exc:
    print(f"[candidate] local timeline API not reachable at {base}: {exc}", file=sys.stderr)
    raise SystemExit(1)

if health.get("db_present") is not True:
    print(f"[candidate] /api/health did not report db_present=true: {health}", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(timeline.get("events"), list):
    print(f"[candidate] /api/timeline did not return events: {timeline}", file=sys.stderr)
    raise SystemExit(1)

print(
    "[candidate] local timeline API ok: "
    f"events={len(timeline['events'])} setup_blocker={health.get('setup_blocker')}"
)
PY
  local_web_proof="PASS"
else
  step "skipping local timeline API smoke"
  local_web_proof="UNRUN (--skip-local-web)"
fi

if [[ "$RUN_LINUX_PRODUCT_QA" == true ]]; then
  step "running Dockerized Linux product QA"
  args=(--wheel "$wheel_path")
  if [[ -n "$PROVIDER_STATE" ]]; then
    args+=(--provider-state "$PROVIDER_STATE")
  else
    args+=(--allow-no-provider)
  fi
  bash "$SCRIPT_DIR/linux-product-qa.sh" "${args[@]}"
  linux_product_proof="PASS"
else
  step "skipping Linux product QA"
  linux_product_proof="UNRUN (use --with-linux-product-qa)"
fi

if [[ "$RUN_LINUX_MANAGED_SERVICE" == true ]]; then
  step "running Linux managed-service smoke on this host"
  bash "$SCRIPT_DIR/linux-managed-service-smoke.sh"
  linux_service_proof="PASS"
else
  step "skipping Linux managed-service smoke"
  linux_service_proof="UNRUN (use --with-linux-managed-service)"
fi

step "candidate summary"
uv run python - <<'PY'
import tomllib
from pathlib import Path

version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
print(f"[candidate] package_version={version}")
PY
echo "[candidate] git_head=$(git rev-parse --short HEAD)"
echo "[candidate] git_describe=$(git describe --tags --dirty --always)"
if [[ -n "$wheel_path" ]]; then
  echo "[candidate] wheel=$wheel_path"
fi
echo "[candidate] proof deterministic=$deterministic_proof"
echo "[candidate] proof artifacts=$artifact_proof"
echo "[candidate] proof macos_sandbox_pi_bash=$macos_sandbox_pi_bash_proof"
echo "[candidate] proof local_web=$local_web_proof"
echo "[candidate] proof linux_product=$linux_product_proof"
echo "[candidate] proof linux_service=$linux_service_proof"
echo "[candidate] proof live_provider=$live_provider_proof"
echo "[candidate] proof clean_user_or_revoked_macos_tcc=UNRUN"
echo "[candidate] proof physical_sleep_wake=UNRUN"
echo "[candidate] completed; this is not a complete system proof"
