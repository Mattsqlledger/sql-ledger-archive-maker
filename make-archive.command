#!/usr/bin/env bash
# SQL-Ledger Archive Maker — double-click this file (Mac).
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed yet."
  echo "macOS usually offers to install it automatically; otherwise: https://www.python.org/downloads/"
  read -r -p "Press Enter to close..."; exit 1
fi
python3 make_archive.py
read -r -p "Press Enter to close..."
