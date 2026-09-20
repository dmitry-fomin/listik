. "$(dirname "$0")/common.sh"

stand_gate start

base=$(basename "$SWARM_STAND_TARGET")
name=${base%.py}
tmp_file="${SWARM_STAND_TARGET}.stand-tmp"

awk -v prefix="    return \"${name}-" -v replacement="    return \"${name}-${SWARM_STAND_TASK_ID}\"" '
    index($0, prefix) == 1 { print replacement; next }
    { print }
' "$SWARM_STAND_TARGET" > "$tmp_file"
mv "$tmp_file" "$SWARM_STAND_TARGET"

stand_gate finish
stand_commit "$SWARM_STAND_TASK_ID: rewrite"
stand_report done
