#!/usr/bin/env bash
set -euo pipefail

echo "==> Setting up Syke"

syke setup --yes

syke auth status
syke doctor
syke status

echo "==> Done. Graph: ~/.syke/workspace/syke.db"
echo "==> Protected sessions: ~/.syke/control/sessions/"
echo "==> Protected receipts: ~/.syke/control/receipts/"
echo "==> Protected records: ~/.syke/control/records/"
echo "==> Check the memex with: syke memex"
echo "==> Memex artifact lives at: ~/.syke/workspace/MEMEX.md"
