#!/usr/bin/env bash
# Configures Graylog for Bloodhound, through its REST API — the same setup as
# the reference appliance:
#   - input  "Ruckus APs Syslog"            Syslog UDP, port 514
#   - rule   "extract_ruckus_5tuple"         graylog/extract_ruckus_5tuple.rule
#   - pipeline "Ruckus AP Processing"        stage 0: that rule
#   - stream "Ruckus APs - 5-tuple flows"    message contains "Ruckus-AP New Flow",
#                                            default index set (graylog_*), pipeline attached
#   - message processors: Pipeline Processor after the stream rules
#
# Idempotent: objects are looked up by title and only created when missing.
#   sudo /opt/bloodhound/graylog-bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")"

PASS="$(grep '^GRAYLOG_PASSWORD=' .env | cut -d= -f2-)"
export GL_API="http://127.0.0.1:9000/api" GL_AUTH="admin:$PASS"

echo "  waiting for the Graylog API..."
for i in $(seq 1 120); do
  curl -sf -o /dev/null -u "$GL_AUTH" "$GL_API/system/inputstates" 2>/dev/null && break
  [ "$i" = 120 ] && { echo "  Graylog API not reachable after 10 minutes" >&2; exit 1; }
  sleep 5
done

python3 - "graylog/extract_ruckus_5tuple.rule" <<'PYEOF'
import base64, json, os, sys, urllib.request

API, AUTH = os.environ["GL_API"], os.environ["GL_AUTH"]
RULE_SOURCE = open(sys.argv[1]).read()
RULE_TITLE = "extract_ruckus_5tuple"
PIPELINE_TITLE = "Ruckus AP Processing"
STREAM_TITLE = "Ruckus APs - 5-tuple flows"
INPUT_TITLE = "Ruckus APs Syslog"


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", "Basic " + base64.b64encode(AUTH.encode()).decode())
    req.add_header("X-Requested-By", "bloodhound")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


# ── Input ────────────────────────────────────────────────────────────────────
inputs = call("GET", "/system/inputs")["inputs"]
if not any(i["attributes"].get("port") == 514 for i in inputs):
    call("POST", "/system/inputs", {
        "title": INPUT_TITLE,
        "type": "org.graylog2.inputs.syslog.udp.SyslogUDPInput",
        "global": True,
        "configuration": {
            "bind_address": "0.0.0.0", "port": 514, "recv_buffer_size": 262144,
            "number_worker_threads": 4, "charset_name": "UTF-8",
            "store_full_message": True, "allow_override_date": True,
            "force_rdns": False, "expand_structured_data": False,
        },
    })
    print("  input created: Syslog UDP 514")
else:
    print("  input on 514: present")

# ── Rule ─────────────────────────────────────────────────────────────────────
rules = call("GET", "/system/pipelines/rule")
rule = next((r for r in rules if r["title"] == RULE_TITLE), None)
if rule is None:
    call("POST", "/system/pipelines/rule",
         {"title": RULE_TITLE, "description": "Ruckus AP flow log → Bloodhound fields", "source": RULE_SOURCE})
    print(f"  rule created: {RULE_TITLE}")
elif rule["source"].strip() != RULE_SOURCE.strip():
    call("PUT", f"/system/pipelines/rule/{rule['id']}",
         {"title": RULE_TITLE, "description": rule.get("description", ""), "source": RULE_SOURCE})
    print(f"  rule updated: {RULE_TITLE}")
else:
    print(f"  rule: present")

# ── Pipeline ─────────────────────────────────────────────────────────────────
pipelines = call("GET", "/system/pipelines/pipeline")
pipeline = next((p for p in pipelines if p["title"] == PIPELINE_TITLE), None)
if pipeline is None:
    pipeline = call("POST", "/system/pipelines/pipeline", {
        "title": PIPELINE_TITLE, "description": "",
        "source": f'pipeline "{PIPELINE_TITLE}"\nstage 0 match either\nrule "{RULE_TITLE}"\nend',
    })
    print(f"  pipeline created: {PIPELINE_TITLE}")
else:
    print(f"  pipeline: present")

# ── Stream ───────────────────────────────────────────────────────────────────
streams = call("GET", "/streams")["streams"]
stream = next((s for s in streams if s["title"] == STREAM_TITLE), None)
if stream is None:
    index_set = next(s for s in call("GET", "/system/indices/index_sets")["index_sets"] if s["default"])
    created = call("POST", "/streams", {
        "title": STREAM_TITLE,
        "description": "Ruckus AP 5-tuple flow logs (parsed by the Ruckus AP Processing pipeline)",
        "rules": [{"field": "message", "type": 6, "value": "Ruckus-AP New Flow",
                   "inverted": False, "description": ""}],
        "matching_type": "AND",
        "remove_matches_from_default_stream": True,
        "index_set_id": index_set["id"],
    })
    stream_id = created["stream_id"]
    call("POST", f"/streams/{stream_id}/resume")
    print(f"  stream created and started: {STREAM_TITLE}")
else:
    stream_id = stream["id"]
    if stream.get("disabled"):
        call("POST", f"/streams/{stream_id}/resume")
    print(f"  stream: present")

# ── Pipeline ↔ stream ────────────────────────────────────────────────────────
connections = call("GET", "/system/pipelines/connections")
current = next((c for c in connections if c["stream_id"] == stream_id), None)
ids = set(current["pipeline_ids"]) if current else set()
if pipeline["id"] not in ids:
    call("POST", "/system/pipelines/connections/to_stream",
         {"stream_id": stream_id, "pipeline_ids": sorted(ids | {pipeline["id"]})})
    print("  pipeline connected to the stream")

# ── Processor order: pipelines see the stream assignment ─────────────────────
cfg = call("GET", "/system/messageprocessors/config")
order = cfg["processor_order"]
names = [p["class_name"] for p in order]
pipe = next((p for p in order if p["name"] == "Pipeline Processor"), None)
after = [i for i, p in enumerate(order) if p["name"] in ("Message Filter Chain", "Stream Rule Processor")]
if pipe and after and names.index(pipe["class_name"]) < max(after):
    order.remove(pipe)
    order.insert(max(i for i, p in enumerate(order) if p["name"] in ("Message Filter Chain", "Stream Rule Processor")) + 1, pipe)
    call("PUT", "/system/messageprocessors/config", {**cfg, "processor_order": order})
    print("  message processors reordered (Pipeline Processor last)")

print("  Graylog ready")
PYEOF
