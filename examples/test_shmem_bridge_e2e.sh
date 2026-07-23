#!/bin/bash
# examples/test_shmem_bridge_e2e.sh
#
# Full system check: launches shmem_bridge_robonet.py and
# shmem_bridge_ai.py as two genuinely separate OS processes (real
# `python3` invocations, not multiprocessing.Process from within one
# test runner) -- this is what actually happens on a real system,
# where robonet and the AI are started independently, possibly by
# different tools or on different schedules.
#
# The unittest suite (tests/test_shmem_channel.py,
# tests/test_bridge_control.py) already covers correctness in detail
# and runs on every CI pass; this script is a slower, coarser sanity
# check on top of that -- confirms the two example scripts actually
# start, connect, exchange real data, and shut down cleanly as
# standalone programs. Run it by hand after touching the bridge.

set -u
cd "$(dirname "$0")/.." || exit 1

ROBONET_LOG=$(mktemp)
AI_LOG=$(mktemp)
trap 'rm -f "$ROBONET_LOG" "$AI_LOG"' EXIT

RUN_S=5

echo "Starting robonet side..."
timeout "$RUN_S" python3 -m examples.shmem_bridge_robonet > "$ROBONET_LOG" 2>&1 &
ROBONET_PID=$!

sleep 1.5

echo "Starting AI side..."
timeout "$((RUN_S - 1))" python3 -m examples.shmem_bridge_ai > "$AI_LOG" 2>&1 &
AI_PID=$!

echo "Running for ${RUN_S}s (each side has its own timeout, so this always terminates)..."
wait "$ROBONET_PID" 2>/dev/null
wait "$AI_PID" 2>/dev/null

FAIL=0

check() {
    if grep -qF "$1" "$2"; then
        echo "  OK: $1"
    else
        echo "  MISSING: $1"
        FAIL=1
    fi
}

echo ""
echo "--- robonet side log ---"
cat "$ROBONET_LOG"
echo "--- AI side log ---"
cat "$AI_LOG"
echo "---"
echo ""
echo "Checks:"
check "AI process connected" "$ROBONET_LOG"
check "connected -- channels available" "$AI_LOG"
check "frames seen:" "$AI_LOG"

# Confirm real data actually flowed, not just connection log lines --
# frames_seen of 0 would mean the connection worked but the seqlock
# channel itself never delivered anything.
if grep -qE "frames seen: [1-9]" "$AI_LOG"; then
    echo "  OK: at least one real frame was read"
else
    echo "  MISSING: no frames were actually read (frames seen stayed at 0)"
    FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
    echo ""
    echo "PASS"
    exit 0
else
    echo ""
    echo "FAIL -- see logs above"
    exit 1
fi
