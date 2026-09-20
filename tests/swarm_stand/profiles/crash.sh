. "$(dirname "$0")/common.sh"

stand_gate start

printf '# %s partial\n' "$SWARM_STAND_TASK_ID" >> "$SWARM_STAND_TARGET"

exit 3
