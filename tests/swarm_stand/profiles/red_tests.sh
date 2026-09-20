. "$(dirname "$0")/common.sh"

stand_gate start

base=$(basename "$SWARM_STAND_TARGET")
name=${base%.py}
tmp_file="${SWARM_STAND_TARGET}.stand-tmp"

awk -v prefix="    return \"${name}-" '
    index($0, prefix) == 1 { print "    return 42"; next }
    { print }
' "$SWARM_STAND_TARGET" > "$tmp_file"
mv "$tmp_file" "$SWARM_STAND_TARGET"

stand_gate finish
stand_commit "$SWARM_STAND_TASK_ID: red_tests"
stand_report done
