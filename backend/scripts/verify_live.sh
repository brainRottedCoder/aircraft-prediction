#!/usr/bin/env bash
# verify_live.sh — manual, end-to-end verification of the RUNNING service.
#
# Complements `make check` rather than replacing it: pytest asserts on code, this
# asserts on a live container over HTTP and psql. The point is to catch the failure
# mode the test suite structurally cannot -- the service answering plausibly from the
# deterministic fallback while a valid model sits on disk unused.
#
#   bash scripts/verify_live.sh                          # read-only sections
#   bash scripts/verify_live.sh --with-fallback-test     # ALSO section H (opt-in)
#
# Sections A-G and I are read-only. Section H stops the api container and moves
# data/ml/all aside, so it is behind a flag and an interactive confirmation.
#
# Exit status: number of failed checks (capped at 125), or 0 when all passed.

set -uo pipefail

BASE="${BASE:-http://localhost:8000}"
API_CONTAINER="${API_CONTAINER:-fdt-api}"
PSQL_CONTAINER="${PSQL_CONTAINER:-fdt-postgres}"
PSQL_USER="${PSQL_USER:-fdt}"
PSQL_DB="${PSQL_DB:-fdt}"
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WITH_FALLBACK_TEST=0
[[ "${1:-}" == "--with-fallback-test" ]] && WITH_FALLBACK_TEST=1

if [[ -t 1 ]]; then
  C_RED=$'\e[31m'; C_GRN=$'\e[32m'; C_YLW=$'\e[33m'; C_BLD=$'\e[1m'; C_DIM=$'\e[2m'; C_OFF=$'\e[0m'
else
  C_RED=""; C_GRN=""; C_YLW=""; C_BLD=""; C_DIM=""; C_OFF=""
fi

PASS=0
FAIL=0
section() { printf '\n%s== %s%s\n' "$C_BLD" "$1" "$C_OFF"; }
info()    { printf '   %s%s%s\n' "$C_DIM" "$1" "$C_OFF"; }
ok()      { PASS=$((PASS + 1)); printf '   %sPASS%s %s\n' "$C_GRN" "$C_OFF" "$1"; }
bad()     { FAIL=$((FAIL + 1)); printf '   %sFAIL%s %s\n' "$C_RED" "$C_OFF" "$1"
            [[ -n "${2:-}" ]] && printf '        %s%s%s\n' "$C_DIM" "$2" "$C_OFF"; return 0; }
check()   { if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1" "expected [$3], got [$2]"; fi; }
checkne() { if [[ "$2" != "$3" ]]; then ok "$1"; else bad "$1" "expected anything other than [$3]"; fi; }

need() { command -v "$1" >/dev/null 2>&1 || { printf 'missing dependency: %s\n' "$1"; exit 2; }; }
need curl
need python3
need jq
need docker

q() { curl -sS --max-time 15 -H "authorization: Bearer $1" "$BASE$2"; }

# Separate from q() on purpose: /api/v1/demo/{pause,resume,tick} are POST-only. Sending
# them as GET returns 405 and the demo is silently never controlled, which looks exactly
# like "pause does not work".
qp() { curl -sS --max-time 15 -X POST -H "authorization: Bearer $1" "$BASE$2"; }

login() { curl -sS --max-time 10 -X POST "$BASE/api/v1/auth/login" \
  -H 'content-type: application/json' \
  -d "{\"username\":\"$1\",\"password\":\"$2\"}" | jq -r '.access_token // empty'; }

healthz() { curl -sS --max-time 10 "$BASE/healthz" 2>/dev/null; }

# Poll /healthz until it answers or the budget runs out. A fixed sleep is a race:
# `docker compose up --build` recompiles and re-seeds, and how long that takes depends
# on the machine. This returned an empty body on a slow build, which then read as
# "status is not degraded" rather than "the api is not up".
wait_for_health() {
  local tries=${1:-40} body=""
  for ((i = 0; i < tries; i++)); do
    body=$(healthz)
    [[ -n "$body" ]] && { echo "$body"; return 0; }
    sleep 3
  done
  echo ""
  return 1
}

psql_at() { docker exec "$PSQL_CONTAINER" psql -U "$PSQL_USER" -d "$PSQL_DB" -At -c "$1" 2>/dev/null; }

# ── window builder ────────────────────────────────────────────────────────────
# 30 cycles of nominal FD001 telemetry. $1 = JSON merged into the request body, so a
# test can inject a subset, a regime, or extra sensors.
mkbody() {
  python3 - "$1" <<'PY'
import json, sys

extra = json.loads(sys.argv[1]) if sys.argv[1].strip() else {}
base = {"s2": 642.7, "s3": 1590.5, "s4": 1400.0, "s6": 21.6, "s7": 1525.0,
        "s8": 2538.0, "s9": 2455.0, "s11": 521.9, "s12": 1.88, "s13": 2538.0,
        "s14": 11.72, "s15": 522.2, "s17": 641.2, "s20": 542.7, "s21": 2388.0}
base.update(extra.pop("sensors", {}))
body = {"persist": False,
        "window": [{"cycle": 100 + i,
                    "settings": {"setting_1": 0.0, "setting_2": 0.0, "setting_3": 100.0},
                    "sensors": dict(base)} for i in range(30)]}
body.update(extra)
print(json.dumps(body))
PY
}

predict() {
  curl -sS --max-time 20 -X POST "$BASE/api/v1/internal/ml/predict" \
    -H "authorization: Bearer $OFFICER" -H 'content-type: application/json' -d "$1"
}

# ═══0. preconditions ════════════════════════════════════════════════════════════
section "0. preconditions"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$API_CONTAINER"; then
  ok "container $API_CONTAINER is up"
else
  bad "container $API_CONTAINER is not running" "cd backend && docker compose up -d --build"
  exit 2
fi

HZ=$(healthz)
if [[ -z "$HZ" ]]; then
  bad "GET /healthz returned nothing" "is the API listening on $BASE?"
  exit 2
fi

# ═══A. model loaded ════════════════════════════════════════════════════════════
section "A. model is loaded, not the fallback"
check "healthz status"        "$(jq -r '.status'         <<<"$HZ")" "ok"
check "model.loaded"          "$(jq -r '.model.loaded'   <<<"$HZ")" "true"
check "model.fallback"        "$(jq -r '.model.fallback' <<<"$HZ")" "false"
check "model.version"         "$(jq -r '.model.version'  <<<"$HZ")" "ALL"
check "model.dataset"         "$(jq -r '.model.dataset'  <<<"$HZ")" "FD001+FD002+FD003+FD004"
info "model.mae = $(jq -r '.model.mae' <<<"$HZ") (a CV RMSE: the refit has no holdout)"
if [[ "$(jq -r '.model.mae_is_cv' <<<"$HZ")" == "true" ]]; then
  ok "mae is flagged as cross-validated, not a holdout figure"
else
  info "mae_is_cv not exposed on /healthz (reported per-response instead)"
fi

WIDTH=$(docker logs "$API_CONTAINER" 2>&1 | grep -o 'features=[0-9]*' | tail -1)
check "booster width is the 32-column pooled contract" "${WIDTH#features=}" "32"

# ═══0b. authenticate ══════════════════════════════════════════════════════════
section "0b. authenticate"
TOKEN=$(login commander commander123)
OFFICER=$(login officer officer123)
if [[ -n "$TOKEN" ]]; then ok "commander token (audit + demo endpoints)"; else
  bad "commander login failed"; exit 2
fi
if [[ -n "$OFFICER" ]]; then ok "officer token (ml predict)"; else bad "officer login failed"; fi

# ═══B. real predictions ════════════════════════════════════════════════════════
section "B. predictions come from the booster, not rul = 125 - cycle"
RESP_B=$(predict "$(mkbody '')")
BRUL=$(jq -r '.rul' <<<"$RESP_B")
check "model.fallback"      "$(jq -r '.model.fallback'      <<<"$RESP_B")" "false"
check "model.degraded"      "$(jq -r '.model.degraded'      <<<"$RESP_B")" "false"
check "model.subset"        "$(jq -r '.model.subset'        <<<"$RESP_B")" "FD001"
check "model.regime"        "$(jq -r '.model.regime'        <<<"$RESP_B")" "0"
check "model.regime_global" "$(jq -r '.model.regime_global' <<<"$RESP_B")" "0"
check "model.requires_scaler" "$(jq -r '.model.requires_scaler' <<<"$RESP_B")" "false"
check "deviation covers 15 sensors" "$(jq -r '.deviation | length' <<<"$RESP_B")" "15"
info "last cycle is 129, so the fallback would answer 0. Model answered: $BRUL"
checkne "RUL differs from the fallback value" "$BRUL" "0"

info "persisted aircraft vs the fallback curve (delta != 0 means model output):"
docker exec "$PSQL_CONTAINER" psql -U "$PSQL_USER" -d "$PSQL_DB" -At -F'|' -c \
  "SELECT code, current_cycle, rul,
          rul - greatest(125 - current_cycle, 0) AS delta
     FROM aircraft ORDER BY code;" 2>/dev/null |
while IFS='|' read -r c cyc rul delta; do
  printf '        %-11s cycle %-5s rul %-5s delta %s\n' "$c" "$cyc" "$rul" "$delta"
done
AWAY=$(psql_at "SELECT count(*) FROM aircraft
                WHERE abs(rul - greatest(125 - current_cycle, 0)) > 3;")
if [[ "${AWAY:-0}" -gt 0 ]]; then
  ok "$AWAY aircraft differ from the fallback curve"
else
  bad "every aircraft sits on the fallback curve" "the model is not answering -- check section A"
fi

# ═══C. the 32-column contract ══════════════════════════════════════════════════
section "C. the 32-column contract is genuinely fed (s10/s16 must be inert for FD001)"
RESP_C1=$(predict "$(mkbody '')")
RESP_C2=$(predict "$(mkbody '{"sensors":{"s10":99.0,"s16":99.0}}')")
info "FD001 baselines omit s10/s16 because they are constant in FD001, so the"
info "z-scorer must zero them. Identical RUL = correct; differing = raw values are"
info "leaking into the matrix positionally (docs/15 section 3)."
if [[ "$(jq -r '.rul' <<<"$RESP_C1")" == "$(jq -r '.rul' <<<"$RESP_C2")" ]]; then
  ok "RUL identical with s10/s16 absent vs set to 99"
else
  bad "RUL changed when s10/s16 were injected" \
      "$(jq -r '.rul' <<<"$RESP_C1") -> $(jq -r '.rul' <<<"$RESP_C2")"
fi

# ═══D. subsets and regimes ═════════════════════════════════════════════════════
section "D. subset- and regime-aware baselines"
BASE_RUL=$(jq -r '.rul' <<<"$RESP_C1")
# offsets from regime_baselines_all.json: FD001=0, FD002=1, FD003=7, FD004=8
for pair in "FD002 1" "FD003 7" "FD004 8"; do
  set -- $pair
  SUB=$1; OFF=$2
  RESP_SUB=$(predict "$(mkbody "{\"subset\":\"$SUB\"}")")
  LREG=$(jq -r '.model.regime' <<<"$RESP_SUB")
  LGLOB=$(jq -r '.model.regime_global' <<<"$RESP_SUB")
  check "$SUB not falling back" "$(jq -r '.model.fallback' <<<"$RESP_SUB")" "false"
  check "$SUB regime_global == regime + $OFF" "$LGLOB" "$((LREG + OFF))"
  checkne "$SUB RUL differs from the FD001 answer" "$(jq -r '.rul' <<<"$RESP_SUB")" "$BASE_RUL"
done

NEG=$(predict "$(mkbody '{"subset":"FD003_BAD"}')")
NEGREASON=$(jq -r '.model.reason // ""' <<<"$NEG")
info "unknown-subset reason: $NEGREASON"
if [[ "$(jq -r '.model.degraded' <<<"$NEG")" == "true" ]] &&
   grep -q 'no baseline block' <<<"$NEGREASON"; then
  ok "unknown subset degrades with a baseline-specific reason"
else
  bad "unknown subset did not report a missing baseline" \
      "must never borrow another subset's medians"
fi

# ═══E. replay ══════════════════════════════════════════════════════════════════
section "E. replay is real telemetry"
# /api/v1/demo/status declares response_model=DemoStatus, which STRIPS the extra
# replay_subset/subsets keys that /healthz returns for the very same dict. So the
# staged-subset evidence is read from /healthz, not from here.
ST=$(q "$TOKEN" /api/v1/demo/status)
HR=$(healthz)
check "cmapss_loaded" "$(jq -r '.cmapss_loaded' <<<"$ST")" "true"
check "replay_subset" "$(jq -r '.replay.replay_subset' <<<"$HR")" "FD001"
check "units replayed" "$(jq -r '.units'          <<<"$ST")" "100"
for s in FD001 FD002 FD003 FD004; do
  n=$(jq -r ".replay.subsets.$s // empty" <<<"$HR")
  if [[ "$n" =~ ^[0-9]+$ ]] && [[ "$n" -gt 0 ]]; then
    ok "$s staged: $n units"
  else
    bad "$s not staged"
  fi
done

# engine_telemetry has a unique (aircraft_id, cycle) with ON CONFLICT DO UPDATE, so
# the ROW COUNT saturates once the fleet wraps and re-walks cycles it has already
# visited. Counting rows therefore stops growing while replay is demonstrably running --
# it failed here for exactly that reason. recency is the honest liveness signal.
latest_ts() { psql_at "SELECT coalesce(to_char(max(recorded_at), 'HH24:MI:SS.MS'), 'none') FROM engine_telemetry;"; }
T1=$(latest_ts)

qp "$TOKEN" /api/v1/demo/pause >/dev/null
sleep 2
P1=$(q "$TOKEN" /api/v1/demo/status | jq -r '.tick')
sleep 3
P2=$(q "$TOKEN" /api/v1/demo/status | jq -r '.tick')
check "tick frozen while paused" "$P1" "$P2"

# /demo/tick pauses the engine as a side effect, so the read-back is taken while
# paused and the delta is exactly 1.
qp "$TOKEN" /api/v1/demo/tick >/dev/null
P3=$(q "$TOKEN" /api/v1/demo/status | jq -r '.tick')
check "manual tick advances exactly 1" "$P3" "$((P2 + 1))"

qp "$TOKEN" /api/v1/demo/resume >/dev/null
sleep 5
T2=$(latest_ts)
if [[ "$T2" != "$T1" && "$T2" != "none" ]]; then
  ok "telemetry advancing: newest row $T1 -> $T2"
else
  bad "no new telemetry after resume" "$T1 -> $T2 (is FDT_DEMO_MODE=true?)"
fi
ROWS=$(psql_at "SELECT count(*) FROM engine_telemetry;")
info "$ROWS telemetry rows total (saturates: unique on aircraft_id+cycle)"

WRAP=$(psql_at "SELECT count(*) FROM aircraft WHERE current_cycle < 5;")
if [[ "${WRAP:-0}" -gt 0 ]]; then
  ok "$WRAP aircraft have wrapped to end-of-life (expected in a long-running demo)"
else
  info "no wrap yet (demo restarts at cycle 1; wraps after ~200 cycles)"
fi

# ═══F. business rules ══════════════════════════════════════════════════════════
section "F. business rules on live data"
# GET /api/v1/aircraft returns {"items": [...], "total": N}, not a bare array.
AL=$(q "$OFFICER" /api/v1/aircraft | jq '.items')
jq -r '.[] | "        \(.code)  cycle \(.current_cycle)  rul \(.rul)  health \(.engine_health)  risk \(.risk)  ready \(.mission_ready)  worst \(.worst_part // "-")"' <<<"$AL"

# mission_ready must track rul > 30 (strict) AND every part health > 0.4.
# `risk` is the aircraft-level band; engine_health is the engine part's health.
if jq -e 'all(.[]; if .rul > 30 and (.parts | to_entries | all(.value != "critical"))
                 then .mission_ready == true else .mission_ready == false end)' <<<"$AL" >/dev/null; then
  ok "mission_ready tracks rul > 30 and no critical part"
else
  bad "mission_ready disagrees with the rules" \
      "$(jq -r '[.[] | select((.mission_ready != ((.rul > 30 and (.parts|to_entries|all(.value != "critical"))))) ) | "\(.code) rul=\(.rul) ready=\(.mission_ready) parts=\(.parts)"] | join("; ")' <<<"$AL")"
fi

# The aircraft row and the engine part row are written by different statements in the
# replay loop. If they disagree, one of them is not being updated -- which reads as a
# plausible, entirely healthy fleet while the part data says otherwise.
MISMATCH=$(jq -r --argjson h "$(healthz)" '
  [.[] | select(.parts.engine != null) |
   select((if (.parts.engine == "healthy") then (.engine_health > 0.70)
           elif (.parts.engine == "watch") then (.engine_health > 0.40 and .engine_health <= 0.70)
           else (.engine_health <= 0.40) end) | not)
   | "\(.code) aircraft.risk=\(.risk) but engine part=\(.parts.engine) health=\(.engine_health)"] | join("; ")' <<<"$AL")
if [[ -z "$MISMATCH" ]]; then
  ok "aircraft risk band agrees with the engine part's health"
else
  bad "STALE COLUMN: aircraft.risk_level is not updated by the replay loop" "$MISMATCH"
fi

NULLW=$(jq -r '[.[] | select(.worst_part == null or .worst_part == "")] | length' <<<"$AL")
check "worst_part populated for every aircraft" "$NULLW" "0"

WP=$(jq -r '[.[] | select(.worst_part != null) | .worst_part] | unique | join(",")' <<<"$AL")
info "worst parts in play: ${WP:-none}"
info "tie-break order is engine -> radar -> gear -> hyd -> fuel (see app/domain/rules.py)"

# ═══G. websocket ═══════════════════════════════════════════════════════════════
section "G. websocket stream"
if ! python3 -c 'import websockets' 2>/dev/null; then
  info "SKIPPED - python 'websockets' not installed (pip install websockets)"
else
  WS=$(python3 - "$TOKEN" <<'PY'
import asyncio, json, sys
import websockets

async def main():
    url = f"ws://localhost:8000/ws/fleet?token={sys.argv[1]}"
    async with websockets.connect(url, open_timeout=10) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), 10))
        kinds, seqs = [first.get("type")], [first.get("seq")]
        # ping/pong round-trip: proves the server reads, not just writes
        await ws.send(json.dumps({"type": "ping"}))
        for _ in range(4):
            ev = json.loads(await asyncio.wait_for(ws.recv(), 10))
            kinds.append(ev.get("type"))
            seqs.append(ev.get("seq"))
            if ev.get("type") == "pong":
                break
        print(json.dumps({"first": kinds[0], "kinds": kinds, "seqs": seqs,
                          "protocol_version": first.get("payload", {}).get("protocol_version")}))

asyncio.run(main())
PY
)
  if [[ -z "$WS" ]]; then
    bad "websocket connection or first frame failed"
  else
    check "first frame is connection.ready" "$(jq -r '.first' <<<"$WS")" "connection.ready"
    check "protocol version" "$(jq -r '.protocol_version' <<<"$WS")" "1"
    # `pong` is emitted via .envelope() with no seq, so it arrives as null. Only the
    # broadcast frames carry a sequence, and those are what must be monotonic.
    if jq -e '[.seqs[] | select(. != null)] as $s
              | ($s == ($s | sort)) and (($s | length) == ($s | unique | length)) and ($s | length > 0)' <<<"$WS" >/dev/null; then
      ok "seq monotonic with no duplicates (broadcast frames)"
    else
      bad "seq not monotonic" "$(jq -c '[.seqs[] | select(. != null)]' <<<"$WS")"
    fi
    if jq -e '.kinds | index("pong")' <<<"$WS" >/dev/null; then
      ok "server answers ping with pong"
    else
      info "pong not observed within 4 frames (replay may have been paused)"
    fi
    info "frame types: $(jq -r '.kinds | join(", ")' <<<"$WS")"
  fi
fi

# ═══I. audit ═══════════════════════════════════════════════════════════════════
section "I. audit trail (read-only)"
AU=$(q "$TOKEN" "/api/v1/audit?limit=200")
N=$(jq -r '.items | length' <<<"$AU" 2>/dev/null)
info "$N audit rows"
if [[ "${N:-0}" -gt 0 ]]; then
  INCOMPLETE=$(jq '[.items[] | select((.actor == null) or (.action == null) or (.at == null))] | length' <<<"$AU")
  check "every row carries actor/action/at" "$INCOMPLETE" "0"
  jq -r '.items[0:5][] | "        \(.at)  \(.actor)  \(.entity) \(.action)"' <<<"$AU"
else
  info "no mutations recorded yet - expected on a fresh seed"
fi

# ═══H. negative test (opt-in) ══════════════════════════════════════════════════
if [[ "$WITH_FALLBACK_TEST" == "1" ]]; then
  section "H. NEGATIVE TEST - the fallback must engage when artifacts are hidden"
  printf '   %sThis stops %s and moves data/ml/all aside. Nothing is deleted.%s\n' "$C_YLW" "$API_CONTAINER" "$C_OFF"
  read -r -p "   type 'yes' to continue: " CONFIRM
  if [[ "$CONFIRM" != "yes" ]]; then
    bad "skipped by operator"
else
    cd "$BACKEND_DIR" || { bad "cannot cd to $BACKEND_DIR"; }
    # The artifacts are COPY'd into the image, so hiding the host directory does
    # nothing until the image is rebuilt. `up -d` alone silently kept the old image and
    # the "hidden" run still loaded the model -- the test passed for the wrong reason.
    docker compose stop api >/dev/null 2>&1
    HIDDEN=0
    if [[ -d data/ml/all ]]; then mv data/ml/all data/ml/all.hidden && HIDDEN=1; fi
    docker compose up -d --build api >/dev/null 2>&1
    D=$(wait_for_health)
    if [[ -z "$D" ]]; then
      bad "api never became reachable after rebuild"
    else
      check "status becomes degraded"      "$(jq -r '.status'         <<<"$D")" "degraded"
      check "model.fallback becomes true"  "$(jq -r '.model.fallback' <<<"$D")" "true"
      checkne "model.error is populated"   "$(jq -r '.model.error // ""' <<<"$D")" ""
      OFFICER=$(login officer officer123)
      RESP=$(predict "$(mkbody '')")
      check "prediction reports degraded" "$(jq -r '.model.degraded' <<<"$RESP")" "true"
      check "prediction reports fallback" "$(jq -r '.model.fallback' <<<"$RESP")" "true"
      info "reason: $(jq -r '.model.reason // "(none)"' <<<"$RESP")"
    fi

    [[ "$HIDDEN" -eq 1 ]] && mv data/ml/all.hidden data/ml/all
    docker compose up -d --build api >/dev/null 2>&1
    RESP2=$(wait_for_health)
    if [[ -z "$RESP2" ]]; then
      bad "api never recovered after restoring the artifacts"
    else
      check "recovered to ok"           "$(jq -r '.status'         <<<"$RESP2")" "ok"
      check "recovered to not-fallback" "$(jq -r '.model.fallback' <<<"$RESP2")" "false"
      check "recovered to model loaded" "$(jq -r '.model.loaded'   <<<"$RESP2")" "true"
    fi
    OFFICER=$(login officer officer123)
  fi
else
  section "H. NEGATIVE TEST - fallback when artifacts are hidden"
  info "SKIPPED (opt-in: it restarts the api). Re-run with --with-fallback-test"
fi

# ═══summary ════════════════════════════════════════════════════════════════════
section "summary"
if [[ "$FAIL" -eq 0 ]]; then
  printf '   %sall %d checks passed%s\n' "$C_GRN" "$PASS" "$C_OFF"
  exit 0
fi
printf '   %s%d passed, %d FAILED%s -- see the FAIL lines above\n' "$C_YLW" "$PASS" "$FAIL" "$C_OFF"
((FAIL > 125)) && exit 125
exit "$FAIL"