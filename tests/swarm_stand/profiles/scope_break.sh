. "$(dirname "$0")/common.sh"

stand_gate start

printf '# %s line 1\n# %s line 2\n' "$SWARM_STAND_TASK_ID" "$SWARM_STAND_TASK_ID" >> "$SWARM_STAND_TARGET"
printf '# %s foreign\n' "$SWARM_STAND_TASK_ID" >> "$SWARM_STAND_FOREIGN"

stand_gate finish
stand_commit "$SWARM_STAND_TASK_ID: scope_break"
stand_report done
