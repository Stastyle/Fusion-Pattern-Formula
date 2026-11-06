#!/usr/bin/env bash
# Installs the Formula Pattern add-in into Fusion 360 (macOS).
#
#   ./install.sh          symlinks this folder's FormulaPattern directory into Fusion's AddIns folder
#   ./install.sh --copy   copies it instead of linking
#
# Afterwards, in Fusion: UTILITIES > ADD-INS > Add-Ins tab > FormulaPattern > Run
# (tick "Run on Startup" to keep it). The button appears in SOLID > CREATE.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/FormulaPattern"
ADDINS="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns"
DST="$ADDINS/FormulaPattern"

[ -d "$SRC" ] || { echo "Cannot find $SRC" >&2; exit 1; }
mkdir -p "$ADDINS"
if [ -L "$DST" ]; then
    rm "$DST"                      # a symlink: remove only the link
elif [ -e "$DST" ]; then
    rm -rf "$DST"
fi

if [ "${1:-}" = "--copy" ]; then
    cp -R "$SRC" "$DST"
    echo "Copied the add-in to $DST"
else
    ln -s "$SRC" "$DST"
    echo "Linked $DST -> $SRC"
fi
echo 'In Fusion 360: UTILITIES > ADD-INS > Add-Ins tab > FormulaPattern > Run (tick Run on Startup).'
echo 'The Formula Pattern button is in SOLID > CREATE.'
