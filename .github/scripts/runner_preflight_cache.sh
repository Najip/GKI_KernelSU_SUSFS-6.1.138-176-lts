#!/usr/bin/env bash
# GKI_SOURCE_CACHE: inspect the self-hosted PC and tune cache limits automatically. Never fails a job.
set -uo pipefail
[[ "${RUNNER_ENVIRONMENT:-}" == self-hosted ]] || exit 0
gb() { echo $(( ${1:-0} / 1048576 )); }
fs() { df -Pk "$1" 2>/dev/null | awk -v f="$2" 'NR==2{print $f}'; }
wsl=false
if [[ "${GKI_FORCE_WSL:-0}" == 1 ]] || grep -qi microsoft /proc/version 2>/dev/null; then wsl=true; fi
host_path="${GKI_HOST_FREE_PATH:-}"
if [[ -z "$host_path" && "$wsl" == true ]]; then host_path=/mnt/c; fi
workspace="${GITHUB_WORKSPACE:-$PWD}"
cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}"
mkdir -p "$cache_dir" 2>/dev/null
rows=()
limit_avail=''
limit_size=''
limit_name=''
for entry in "workspace:$workspace" "cache:$cache_dir" "host:$host_path"; do
  name=${entry%%:*}
  p=${entry#*:}
  [[ -n "$p" && -d "$p" ]] || continue
  size=$(fs "$p" 2)
  avail=$(fs "$p" 4)
  [[ "$size" =~ ^[0-9]+$ && "$avail" =~ ^[0-9]+$ ]] || continue
  rows+=("$name|$p|$size|$avail")
  if [[ -z "$limit_avail" || "$avail" -lt "$limit_avail" ]]; then limit_avail=$avail; limit_size=$size; limit_name=$name; fi
done
reserve_note='set by the runner environment'
reserve_gb="${GKI_CACHE_RESERVE_GB:-30}"
if [[ -z "${GKI_CACHE_RESERVE_GB:-}" && -z "${GKI_CACHE_RESERVE_KB:-}" && -n "$limit_size" ]]; then
  total_gb=$(gb "$limit_size")
  reserve_gb=$(( total_gb * 12 / 100 ))
  (( reserve_gb < 8 )) && reserve_gb=8
  (( reserve_gb > 30 )) && reserve_gb=30
  reserve_note="auto: 12% of ${total_gb} GB, limited to 8-30 GB"
  [[ -z "${GITHUB_ENV:-}" ]] || echo "GKI_CACHE_RESERVE_GB=$reserve_gb" >> "$GITHUB_ENV"
fi
os=$( . /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}" )
mem_gb=$(awk '/MemTotal/{printf "%d", $2/1048576}' /proc/meminfo 2>/dev/null)
missing=()
for tool in git rsync flock jq curl python3 sha256sum; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
warn=()
[[ -z "$limit_avail" ]] || (( limit_avail >= (reserve_gb + 12) * 1048576 )) || warn+=("Free space on the $limit_name drive is $(gb "$limit_avail") GB; the source cache needs about $((reserve_gb + 12)) GB and will be skipped or pruned.")
(( ${#missing[@]} == 0 )) || warn+=("Missing tools: ${missing[*]}")
if [[ "$wsl" == true ]]; then
  for f in ${GKI_VHDX_GLOB:-/mnt/c/Users/*/AppData/Local/Packages/*/LocalState/ext4.vhdx}; do
    [[ -f "$f" ]] || continue
    vsize=$(( $(stat -c %s "$f") / 1024 ))
    used=$(fs / 3)
    [[ "$used" =~ ^[0-9]+$ ]] || continue
    if (( vsize - used > 10 * 1048576 )); then
      warn+=("A WSL disk at $f is $(gb "$vsize") GB but Linux uses $(gb "$used") GB; compact it to give about $(gb $(( vsize - used ))) GB back to Windows.")
    fi
  done
fi
[[ -z "${ACTIONS_RUNNER_HOOK_JOB_STARTED:-}" ]] || warn+=("A runner job-started hook is also set (${ACTIONS_RUNNER_HOOK_JOB_STARTED}); remove it if it is the old git-mirror hook.")
echo "[pc] host: $(hostname) | ${os} | $(nproc) CPUs | ${mem_gb:-?} GB RAM | wsl=${wsl}"
for r in "${rows[@]}"; do IFS='|' read -r n p s a <<< "$r"; echo "[pc] $n: $p | $(gb "$s") GB total | $(gb "$a") GB free"; done
echo "[pc] cache reserve: ${reserve_gb} GB (${reserve_note})"
for w in "${warn[@]}"; do echo "[pc] WARNING: $w"; done
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Runner PC check"
    echo "| Item | Value |"
    echo "|---|---|"
    echo "| Host | $(hostname), ${os}, $(nproc) CPUs, ${mem_gb:-?} GB RAM, WSL: ${wsl} |"
    for r in "${rows[@]}"; do IFS='|' read -r n p s a <<< "$r"; echo "| Free on $n | $(gb "$a") GB of $(gb "$s") GB ($p) |"; done
    echo "| Cache reserve | ${reserve_gb} GB (${reserve_note}) |"
    for w in "${warn[@]}"; do echo "| Warning | $w |"; done
  } >> "$GITHUB_STEP_SUMMARY"
fi
exit 0
