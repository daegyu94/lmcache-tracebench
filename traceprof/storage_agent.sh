#!/usr/bin/env bash

# Remote profiler agent. It uses bash, awk, and Linux sysfs only.

set -u
export LC_ALL=C

COMMAND=
if [ "$#" -gt 0 ]; then
    COMMAND=$1
    shift
fi
ROOT=
RUN_DIR=
NODE_NAME=
ROLE=storage
RUN_ID=
SAMPLE_INTERVAL=5
REPORT_INTERVAL=5
DEVICES=
INTERFACES=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root) ROOT=$2; shift 2 ;;
        --run-dir) RUN_DIR=$2; shift 2 ;;
        --device) DEVICES="$DEVICES $2"; shift 2 ;;
        --interface) INTERFACES="$INTERFACES $2"; shift 2 ;;
        --node-name) NODE_NAME=$2; shift 2 ;;
        --role) ROLE=$2; shift 2 ;;
        --run-id) RUN_ID=$2; shift 2 ;;
        --sample-interval) SAMPLE_INTERVAL=$2; shift 2 ;;
        --report-interval) REPORT_INTERVAL=$2; shift 2 ;;
        *) printf 'unknown profiler argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

validate_scope() {
    if [ -z "$ROOT" ] || [ -z "$RUN_DIR" ]; then
        printf 'profiler root and run directory are required\n' >&2
        return 1
    fi
    case "$ROOT" in
        /|/tmp|"") printf 'profiler root is too broad: %s\n' "$ROOT" >&2; return 1 ;;
    esac
    case "$RUN_DIR" in
        "$ROOT"/*) ;;
        *) printf 'profiler run directory is outside root: %s\n' "$RUN_DIR" >&2; return 1 ;;
    esac
}

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

preflight() {
    validate_scope || return 2
    errors=
    command_name=
    device=
    interface=
    name=
    counter=
    stat_path=

    mkdir -p "$RUN_DIR" 2>/dev/null || errors="$errors;run directory is not writable"
    if [ -d "$RUN_DIR" ] && ! : > "$RUN_DIR/.write-test" 2>/dev/null; then
        errors="$errors;run directory is not writable"
    fi
    rm -f "$RUN_DIR/.write-test"
    for command_name in awk cat date sleep readlink sed basename hostname mkdir rm grep cp tee; do
        command -v "$command_name" >/dev/null 2>&1 || \
            errors="$errors;missing command: $command_name"
    done
    case "$(date +%s%N 2>/dev/null)" in
        *N*) errors="$errors;date does not provide nanosecond timestamps" ;;
    esac
    for device in $DEVICES; do
        name=$(basename "$device")
        stat_path="/sys/class/block/$name/stat"
        [ -b "$device" ] || errors="$errors;$device is not a block device"
        [ -r "$stat_path" ] || errors="$errors;$stat_path is not readable"
    done
    for interface in $INTERFACES; do
        if [ ! -d "/sys/class/net/$interface" ]; then
            errors="$errors;network interface does not exist: $interface"
            continue
        fi
        for counter in rx_bytes tx_bytes rx_packets tx_packets rx_errors tx_errors \
            rx_dropped tx_dropped; do
            [ -r "/sys/class/net/$interface/statistics/$counter" ] || \
                errors="$errors;network counter is not readable: $interface/$counter"
        done
    done
    if [ -n "$errors" ]; then
        ok=false
    else
        ok=true
    fi
    printf '{"ok":%s,"node":"%s","errors":"%s"}\n' "$ok" \
        "$(json_escape "$(hostname)")" "$(json_escape "$errors")" \
        | tee "$RUN_DIR/preflight.json"
    [ "$ok" = true ]
}

now_ns() { date +%s%N; }
now_timestamp() { date -u +%Y-%m-%dT%H:%M:%S.%NZ; }

write_snapshot() {
    destination=$1
    : > "$destination"
    for device in $DEVICES; do
        name=$(basename "$device")
        printf 'D\t%s\t%s\t%s\t%s\t%s\t%s\n' "$device" \
            "$(awk '{print $1}' "/sys/class/block/$name/stat")" \
            "$(awk '{print $3}' "/sys/class/block/$name/stat")" \
            "$(awk '{print $5}' "/sys/class/block/$name/stat")" \
            "$(awk '{print $7}' "/sys/class/block/$name/stat")" \
            "$(awk '{print $10}' "/sys/class/block/$name/stat")" >> "$destination"
    done
    for interface in $INTERFACES; do
        printf 'N\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$interface" \
            "$(cat "/sys/class/net/$interface/statistics/rx_bytes")" \
            "$(cat "/sys/class/net/$interface/statistics/tx_bytes")" \
            "$(cat "/sys/class/net/$interface/statistics/rx_packets")" \
            "$(cat "/sys/class/net/$interface/statistics/tx_packets")" \
            "$(cat "/sys/class/net/$interface/statistics/rx_errors")" \
            "$(cat "/sys/class/net/$interface/statistics/tx_errors")" \
            "$(cat "/sys/class/net/$interface/statistics/rx_dropped")" \
            "$(cat "/sys/class/net/$interface/statistics/tx_dropped")" >> "$destination"
    done
}

field() {
    awk -v key="$2" -v column="$3" -v type="$4" \
        '$1 == type && $2 == key { print $(column); exit }' "$1"
}

delta() {
    awk -v start="$1" -v end="$2" \
        'BEGIN { value = end - start; if (value < 0) value = 0; printf "%.0f", value }'
}

seconds() {
    awk -v start="$1" -v end="$2" \
        'BEGIN { printf "%.3f", (end - start) / 1000000000 }'
}

rate() {
    awk -v value="$1" -v interval="$2" \
        'BEGIN { if (interval <= 0) interval = 1; printf "%.3f", value / interval }'
}

mibps() {
    awk -v bytes="$1" -v interval="$2" \
        'BEGIN { if (interval <= 0) interval = 1; printf "%.3f", bytes / interval / 1048576 }'
}

write_interval() {
    start_snapshot=$1
    end_snapshot=$2
    start_ns=$3
    end_ns=$4
    end_timestamp=$5
    interval=$(seconds "$start_ns" "$end_ns")
    elapsed=$(seconds "$START_NS" "$end_ns")
    for device in $DEVICES; do
        read_sectors=$(delta "$(field "$start_snapshot" "$device" 4 D)" "$(field "$end_snapshot" "$device" 4 D)")
        write_sectors=$(delta "$(field "$start_snapshot" "$device" 6 D)" "$(field "$end_snapshot" "$device" 6 D)")
        read_bytes=$(awk -v value="$read_sectors" 'BEGIN { printf "%.0f", value * 512 }')
        write_bytes=$(awk -v value="$write_sectors" 'BEGIN { printf "%.0f", value * 512 }')
        read_ios=$(delta "$(field "$start_snapshot" "$device" 3 D)" "$(field "$end_snapshot" "$device" 3 D)")
        write_ios=$(delta "$(field "$start_snapshot" "$device" 5 D)" "$(field "$end_snapshot" "$device" 5 D)")
        io_ms=$(delta "$(field "$start_snapshot" "$device" 7 D)" "$(field "$end_snapshot" "$device" 7 D)")
        io_util=$(awk -v value="$io_ms" -v interval="$interval" \
            'BEGIN { if (interval <= 0) interval = 1; value = value / interval / 10; if (value > 100) value = 100; printf "%.3f", value }')
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$end_timestamp" "$elapsed" "$interval" "$device" "$read_bytes" "$write_bytes" \
            "$(rate "$read_ios" "$interval")" "$(rate "$write_ios" "$interval")" \
            "$(mibps "$read_bytes" "$interval")" "$(mibps "$write_bytes" "$interval")" "$io_util" >> "$DISK_TSV"
    done
    for interface in $INTERFACES; do
        rx_bytes=$(delta "$(field "$start_snapshot" "$interface" 3 N)" "$(field "$end_snapshot" "$interface" 3 N)")
        tx_bytes=$(delta "$(field "$start_snapshot" "$interface" 4 N)" "$(field "$end_snapshot" "$interface" 4 N)")
        rx_packets=$(delta "$(field "$start_snapshot" "$interface" 5 N)" "$(field "$end_snapshot" "$interface" 5 N)")
        tx_packets=$(delta "$(field "$start_snapshot" "$interface" 6 N)" "$(field "$end_snapshot" "$interface" 6 N)")
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$end_timestamp" "$elapsed" "$interval" "$interface" "$rx_bytes" "$tx_bytes" \
            "$rx_packets" "$tx_packets" "$(mibps "$rx_bytes" "$interval")" \
            "$(mibps "$tx_bytes" "$interval")" "$(rate "$rx_packets" "$interval")" \
            "$(rate "$tx_packets" "$interval")" "$(field "$end_snapshot" "$interface" 7 N)" \
            "$(field "$end_snapshot" "$interface" 8 N)" "$(field "$end_snapshot" "$interface" 9 N)" \
            "$(field "$end_snapshot" "$interface" 10 N)" >> "$NETWORK_TSV"
    done
}

write_summary() {
    final_snapshot=$1
    final_ns=$2
    final_timestamp=$3
    duration=$(seconds "$START_NS" "$final_ns")
    first=true
    {
        printf '{\n  "schema_version": 1,\n  "run_id": "%s",\n  "node": "%s",\n  "role": "%s",\n  "hostname": "%s",\n  "started_at": "%s",\n  "finished_at": "%s",\n  "duration_seconds": %s,\n  "devices": {\n' \
            "$(json_escape "$RUN_ID")" "$(json_escape "$NODE_NAME")" "$(json_escape "$ROLE")" \
            "$(json_escape "$(hostname)")" "$START_TIMESTAMP" "$final_timestamp" "$duration"
        for device in $DEVICES; do
            read_bytes=$(delta "$(field "$START_SNAPSHOT" "$device" 4 D)" "$(field "$final_snapshot" "$device" 4 D)")
            write_bytes=$(delta "$(field "$START_SNAPSHOT" "$device" 6 D)" "$(field "$final_snapshot" "$device" 6 D)")
            read_bytes=$(awk -v value="$read_bytes" 'BEGIN { printf "%.0f", value * 512 }')
            write_bytes=$(awk -v value="$write_bytes" 'BEGIN { printf "%.0f", value * 512 }')
            [ "$first" = true ] || printf ',\n'
            first=false
            printf '    "%s": {"read_bytes": %s, "write_bytes": %s, "read_mibps_avg": %s, "write_mibps_avg": %s}' \
                "$(json_escape "$device")" "$read_bytes" "$write_bytes" \
                "$(mibps "$read_bytes" "$duration")" "$(mibps "$write_bytes" "$duration")"
        done
        printf '\n  },\n  "interfaces": {\n'
        first=true
        for interface in $INTERFACES; do
            rx_bytes=$(delta "$(field "$START_SNAPSHOT" "$interface" 3 N)" "$(field "$final_snapshot" "$interface" 3 N)")
            tx_bytes=$(delta "$(field "$START_SNAPSHOT" "$interface" 4 N)" "$(field "$final_snapshot" "$interface" 4 N)")
            [ "$first" = true ] || printf ',\n'
            first=false
            printf '    "%s": {"rx_bytes": %s, "tx_bytes": %s, "rx_mibps_avg": %s, "tx_mibps_avg": %s}' \
                "$(json_escape "$interface")" "$rx_bytes" "$tx_bytes" \
                "$(mibps "$rx_bytes" "$duration")" "$(mibps "$tx_bytes" "$duration")"
        done
        printf '\n  }\n}\n'
    } > "$RUN_DIR/summary.json"
}

run_agent() {
    validate_scope || return 2
    if [ ! -f "$RUN_DIR/preflight.json" ]; then
        preflight || return $?
    elif ! grep -q '"ok":true' "$RUN_DIR/preflight.json"; then
        cat "$RUN_DIR/preflight.json" >&2
        return 2
    fi
    snapshot_current="$RUN_DIR/snapshot.current"
    snapshot_last="$RUN_DIR/snapshot.last"
    snapshot_final="$RUN_DIR/snapshot.final"
    pid_file="$RUN_DIR/agent.pid"
    log_file="$RUN_DIR/agent.log"
    samples_file="$RUN_DIR/samples.jsonl"
    DISK_TSV="$RUN_DIR/disk.tsv"
    NETWORK_TSV="$RUN_DIR/network.tsv"
    printf '%s\n' "$$" > "$pid_file"
    printf 'timestamp\telapsed_s\tinterval_s\tdevice\tread_bytes\twrite_bytes\tread_iops\twrite_iops\tread_mibps\twrite_mibps\tio_util_percent\n' > "$DISK_TSV"
    printf 'timestamp\telapsed_s\tinterval_s\tinterface\trx_bytes\ttx_bytes\trx_packets\ttx_packets\trx_mibps\ttx_mibps\trx_pps\ttx_pps\trx_errors\ttx_errors\trx_drops\ttx_drops\n' > "$NETWORK_TSV"
    : > "$samples_file"
    : > "$log_file"
    START_NS=$(now_ns)
    START_TIMESTAMP=$(now_timestamp)
    START_SNAPSHOT="$snapshot_last"
    write_snapshot "$START_SNAPSHOT"
    last_report_ns=$START_NS
    printf '%s\tprofiler started\n' "$START_TIMESTAMP" >> "$log_file"
    printf 'READY\t%s\n' "$RUN_DIR"
    stop_requested=0
    trap 'stop_requested=1' TERM INT HUP
    while [ "$stop_requested" -eq 0 ]; do
        sleep "$SAMPLE_INTERVAL"
        [ "$stop_requested" -eq 0 ] || break
        end_ns=$(now_ns)
        end_timestamp=$(now_timestamp)
        write_snapshot "$snapshot_current"
        printf '{"timestamp":"%s","monotonic_ns":%s}\n' "$end_timestamp" "$end_ns" >> "$samples_file"
        elapsed=$(seconds "$last_report_ns" "$end_ns")
        if awk -v elapsed="$elapsed" -v report="$REPORT_INTERVAL" 'BEGIN { exit !(elapsed >= report) }'; then
            write_interval "$snapshot_last" "$snapshot_current" "$last_report_ns" "$end_ns" "$end_timestamp"
            cp "$snapshot_current" "$snapshot_last"
            last_report_ns=$end_ns
        fi
    done
    end_ns=$(now_ns)
    end_timestamp=$(now_timestamp)
    write_snapshot "$snapshot_final"
    printf '{"timestamp":"%s","monotonic_ns":%s}\n' "$end_timestamp" "$end_ns" >> "$samples_file"
    [ "$end_ns" -gt "$last_report_ns" ] && \
        write_interval "$snapshot_last" "$snapshot_final" "$last_report_ns" "$end_ns" "$end_timestamp"
    write_summary "$snapshot_final" "$end_ns" "$end_timestamp"
    printf '%s\tprofiler stopped\n' "$end_timestamp" >> "$log_file"
    rm -f "$pid_file" "$snapshot_current" "$snapshot_last" "$snapshot_final"
    printf 'DONE\t%s\n' "$RUN_DIR"
}

stop_agent() {
    validate_scope || return 2
    pid_file="$RUN_DIR/agent.pid"
    [ -f "$pid_file" ] && kill -TERM "$(cat "$pid_file")" 2>/dev/null || true
}

cleanup_agent() {
    validate_scope || return 2
    rm -rf -- "$RUN_DIR"
}

case "$COMMAND" in
    preflight) preflight ;;
    run) run_agent ;;
    stop) stop_agent ;;
    cleanup) cleanup_agent ;;
    *) printf 'usage: storage_agent.sh {preflight|run|stop|cleanup} ...\n' >&2; exit 2 ;;
esac
