. "$(dirname "$0")/common.sh"

stand_gate start

tmp_file="${SWARM_STAND_TARGET}.stand-tmp"
printf '# %s header\n' "$SWARM_STAND_TASK_ID" > "$tmp_file"
cat "$SWARM_STAND_TARGET" >> "$tmp_file"
mv "$tmp_file" "$SWARM_STAND_TARGET"

stand_gate finish
stand_commit "$SWARM_STAND_TASK_ID: prepend"
stand_report done
