#!/usr/bin/env bash
# Daily Paperless auto-classification with Claude (CLI). Meant to be run from
# cron by a dedicated user. Self-contained: loads .env and PATH, does not rely
# on an interactive shell.
#
# Example crontab entry:
#   0 7 * * * /path/to/paperless-organize/run.sh >> /path/to/paperless-organize/cron.log 2>&1
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$SELF_DIR/../.env}"

set -a; [ -f "$ENV_FILE" ] && . "$ENV_FILE"; set +a
export PATH="/usr/local/bin:$PATH"
export HOME="${HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
cd "${AI_WORKDIR:-$HOME}"   # working dir the AI CLI trusts

LIMIT="${1:-20}"

OUT=$(python3 "$SELF_DIR/paperless_organize.py" --limit "$LIMIT" 2>&1)
echo "$OUT"
SUMMARY=$(printf '%s' "$OUT" | grep -E '^Riepilogo:' | tail -1)
[ -z "$SUMMARY" ] && SUMMARY="paperless-organize: run without summary (see log)"

# Optional ntfy notification (skipped when NTFY_URL is unset)
if [ -n "${NTFY_URL:-}" ]; then
  curl -s --max-time 15 \
    -H "Title: Paperless - documents organized" \
    -H "Tags: open_file_folder" \
    ${PAPERLESS_UI_URL:+-H "Click: $PAPERLESS_UI_URL"} \
    --data-binary "$(printf '%s' "$SUMMARY" | head -c 800)" \
    "$NTFY_URL" >/dev/null 2>&1 || true
fi

echo "[$(date +%F_%T)] $SUMMARY"
