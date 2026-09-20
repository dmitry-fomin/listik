. "$(dirname "$0")/common.sh"

stand_gate start

printf '# %s line 1\n# %s line 2\n' "$SWARM_STAND_TASK_ID" "$SWARM_STAND_TASK_ID" >> "$SWARM_STAND_TARGET"

if [ -n "$SWARM_STAND_GATE_FINISH" ]; then
    stand_gate finish
    stand_commit "$SWARM_STAND_TASK_ID: hang"
    stand_report done
else
    sleep 3600
    exit 5
fi
