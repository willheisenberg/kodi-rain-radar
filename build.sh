#!/usr/bin/env bash
# Baut ein installierbares Zip (Kodi: Add-ons > Aus ZIP-Datei installieren).
set -euo pipefail
cd "$(dirname "$0")"
ID=script.dwd.rainradar
VERSION=$(grep -oP '<addon[^>]*version="\K[^"]+' "$ID/addon.xml")
mkdir -p dist
OUT="dist/$ID-$VERSION.zip"
rm -f "$OUT"
zip -qr "$OUT" "$ID" -x '*/__pycache__/*' '*.pyc'
echo "$OUT"
