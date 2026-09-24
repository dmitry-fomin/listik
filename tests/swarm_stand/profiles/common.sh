# Общие функции профилей стенда роя. POSIX sh, без bash-измов.
# Подключать как: . "$(dirname "$0")/common.sh"

stand_gate() {
    kind="$1"
    if [ "$kind" = "start" ]; then
        gate_file="$SWARM_STAND_GATE_START"
    else
        gate_file="$SWARM_STAND_GATE_FINISH"
    fi

    if [ -z "$gate_file" ]; then
        return 0
    fi

    timeout="${SWARM_STAND_GATE_TIMEOUT:-30}"
    max_iterations=$(awk "BEGIN { n = $timeout / 0.05; print (n == int(n)) ? n : int(n) + 1 }")
    i=0
    while [ ! -f "$gate_file" ]; do
        if [ "$i" -ge "$max_iterations" ]; then
            exit 4
        fi
        sleep 0.05
        i=$((i + 1))
    done
    return 0
}

stand_commit() {
    message="$1"
    git add -A && git commit -q -m "$message"
}

stand_report() {
    status="$1"
    head_sha=$(git rev-parse HEAD)
    printf '{"task_id": "%s", "dispatch_id": "%s", "generation": %s, "status": "%s", "head": "%s"}\n' \
        "$SWARM_STAND_TASK_ID" "$SWARM_STAND_DISPATCH_ID" "$SWARM_STAND_GENERATION" \
        "$status" "$head_sha" > "$SWARM_STAND_REPORT_PATH"
}
