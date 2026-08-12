#!/usr/bin/env bash
# Shared helpers for replay benchmark launchers.

replay_trace_label() {
  local trace="$1"
  local label
  label="$(basename -- "$(dirname -- "$trace")")"
  if [[ -z "$label" || "$label" == "." || "$label" == "/" ]]; then
    label="$(basename -- "$trace")"
    label="${label%.*}"
  fi
  printf '%s\n' "$label"
}

replay_default_output_root() {
  local label="$1"
  local timestamp
  local base
  local candidate
  local suffix=1

  label="$(printf '%s' "$label" | tr -c '[:alnum:]._-' '-')"
  [[ -n "$label" ]] || label="replay"
  timestamp="$(date -u +%Y%m%d-%H%M%S)"
  base="outputs/replay-l2/${label}-${timestamp}"
  candidate="$base"
  while [[ -e "$candidate" ]]; do
    candidate="${base}-${suffix}"
    suffix=$((suffix + 1))
  done
  printf '%s\n' "$candidate"
}
