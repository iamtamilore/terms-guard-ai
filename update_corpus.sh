#!/usr/bin/env bash
# Run the full TermsGuard corpus pipeline and copy the rendered page to
# the portfolio site.
#
# Usage:
#   bash update_corpus.sh          # all companies, all defaults
#   bash update_corpus.sh --slug google  # one company
#
# Requires:
#   - the .kbvenv venv (created by `uv venv && uv pip install -r requirements.txt`)
#   - write access to /home/newtazer/site-build/corpus/

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SITE_ROOT="${SITE_ROOT:-/home/newtazer/site-build}"

# Activate venv if present
if [ -d "$HERE/.kbvenv" ]; then
  source "$HERE/.kbvenv/bin/activate"
fi

echo "[1/3] Snapshot (fetch + score)..."
python "$HERE/corpus/snapshot.py" "$@"

echo
echo "[2/3] Build static page..."
python "$HERE/corpus/build.py"

echo
echo "[3/3] Copy rendered page to portfolio site..."
mkdir -p "$SITE_ROOT/corpus"
cp "$SITE_ROOT/corpus/index.html" "$SITE_ROOT/corpus/index.html.bak" 2>/dev/null || true
cp "$HERE/corpus/snapshots/_latest.json" "$SITE_ROOT/corpus/_latest_run.json"

echo
echo "Done. Output:"
echo "  $SITE_ROOT/corpus/index.html"
echo
echo "Next: cd $SITE_ROOT && git add corpus/ && git commit -m 'corpus: weekly snapshot'"
