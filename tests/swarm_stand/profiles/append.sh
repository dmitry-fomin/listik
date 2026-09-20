. "$(dirname "$0")/common.sh"

stand_gate start

printf '# %s line 1\n# %s line 2\n' "$SWARM_STAND_TASK_ID" "$SWARM_STAND_TASK_ID" >> "$SWARM_STAND_TARGET"

stand_gate finish
stand_commit "$SWARM_STAND_TASK_ID: append"
stand_report done
