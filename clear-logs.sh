#!/bin/bash
#
# Bloodhound — Clear logs only (dev convenience script).
#
# Wipes ingested log data (graylog_0, graylog_1) and client history
# snapshots (bloodhound_client_history) from OpenSearch.
#
# Does NOT touch: Ruckus One / Unleashed / SmartZone configs, Redis cache,
# active platform selection, login credentials, network settings, hostname,
# or system accounts. Use factory-reset.sh instead if you need a full wipe
# before building the OVA template.
#
set -euo pipefail

OPENSEARCH_URL="http://localhost:9200"

echo "=========================================="
echo " Bloodhound — Clear Logs"
echo "=========================================="
echo "This will permanently erase:"
echo "  - All ingested logs (graylog_0, graylog_1)"
echo "  - Client history snapshots (bloodhound_client_history)"
echo
echo "Configs, credentials, network, and accounts are NOT touched."
echo "=========================================="
read -rp "Type CLEAR to confirm: " CONFIRM
if [[ "$CONFIRM" != "CLEAR" ]]; then
    echo "Aborted. Nothing was changed."
    exit 0
fi

echo
echo "--- Wiping log indices ---"
# Discover all graylog_* indices dynamically (Graylog rotates indices over
# time: graylog_0, graylog_1, graylog_2, ...). Rotated/closed indices are
# locked read-only by Graylog (index.blocks.write) — lift the lock just
# long enough to delete, then restore it to match Graylog's own bookkeeping.
INDICES=$(curl -s "$OPENSEARCH_URL/_cat/indices/graylog_*?h=index" | tr -d ' ')
for idx in $INDICES; do
    WAS_LOCKED=$(curl -s "$OPENSEARCH_URL/$idx/_settings?include_defaults=true" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$idx',{}).get('settings',{}).get('index',{}).get('blocks',{}).get('write','false'))" 2>/dev/null || echo "false")

    if [[ "$WAS_LOCKED" == "true" ]]; then
        curl -s -X PUT "$OPENSEARCH_URL/$idx/_settings" \
            -H "Content-Type: application/json" \
            -d '{"index.blocks.write": false}' -o /dev/null
    fi

    curl -s -X POST "$OPENSEARCH_URL/$idx/_delete_by_query?conflicts=proceed&refresh=true&wait_for_completion=true" \
        -H "Content-Type: application/json" \
        -d '{"query": {"match_all": {}}}' \
        -o /dev/null -w "  $idx: HTTP %{http_code}\n"

    if [[ "$WAS_LOCKED" == "true" ]]; then
        curl -s -X PUT "$OPENSEARCH_URL/$idx/_settings" \
            -H "Content-Type: application/json" \
            -d '{"index.blocks.write": true}' -o /dev/null
    fi
done

echo
echo "--- Wiping client history snapshots (bloodhound_client_history) ---"
curl -s -X POST "$OPENSEARCH_URL/bloodhound_client_history/_delete_by_query?conflicts=proceed&refresh=true" \
    -H "Content-Type: application/json" \
    -d '{"query": {"match_all": {}}}' \
    -o /dev/null -w "  bloodhound_client_history: HTTP %{http_code}\n" || true

echo
echo "=========================================="
echo " Logs cleared. Configs and accounts untouched."
echo "=========================================="
