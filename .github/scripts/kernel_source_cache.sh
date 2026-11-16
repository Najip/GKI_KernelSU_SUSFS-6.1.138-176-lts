#!/usr/bin/env bash
# GKI_SOURCE_CACHE: space-aware pristine repo source cache for self-hosted runners.
set -euo pipefail
cd "${GITHUB_WORKSPACE:?}/kernel"
branch="${SOURCE_VERSION:?}-${SOURCE_PATCH_LEVEL:?}"
[[ "$branch" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo 'Invalid source branch' >&2; exit 1; }
reserve_kb="${GKI_CACHE_RESERVE_KB:-$(( ${GKI_CACHE_RESERVE_GB:-30} * 1048576 ))}"
max_kb="${GKI_CACHE_MAX_KB:-$(( ${GKI_CACHE_MAX_GB:-15} * 1048576 ))}"
gb() { echo $(( $1 / 1048576 )); }
free_kb() {
  local min='' p v
  for p in "$@"; do
    [[ -n "$p" && -d "$p" ]] || continue
    v=$(df -Pk "$p" | awk 'NR==2{print $4}')
    [[ "$v" =~ ^[0-9]+$ ]] || continue
    if [[ -z "$min" || "$v" -lt "$min" ]]; then min="$v"; fi
  done
  echo "${min:-0}"
}
signature() {
  printf '%s:%s' "$(git -C common rev-parse HEAD 2>/dev/null || echo none)" "$(git -C .repo/manifests rev-parse HEAD 2>/dev/null || echo none)"
}
cache_enabled=false
hit=false
sig_before=''
if [[ "${RUNNER_ENVIRONMENT:-}" == self-hosted ]]; then
  cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/gki-kernel/sources-v1"
  mkdir -p "$cache_root"
  cache_root=$(cd "$cache_root" && pwd -P)
  workspace=$(cd "$GITHUB_WORKSPACE" && pwd -P)
  case "$cache_root/" in "$workspace/"*) echo 'Source cache must be outside workspace' >&2; exit 1;; esac
  host_path="${GKI_HOST_FREE_PATH:-}"
  if [[ -z "$host_path" ]] && grep -qi microsoft /proc/version 2>/dev/null && [[ -d /mnt/c ]]; then host_path=/mnt/c; fi
  probe=("$cache_root" "$workspace" "$host_path")
  exec 9>"$cache_root/.lock"
  flock 9
  rm -rf "$cache_root"/*.tmp.* "$cache_root"/*.old
  key=$(printf '%s' "$branch" | sha256sum | cut -d' ' -f1)
  cache="$cache_root/$key"
  cache_enabled=true
  if [[ -f "$cache/complete" && -d "$cache/tree/.repo" ]]; then
    size=$(cat "$cache/size_kb" 2>/dev/null || du -sk "$cache/tree" | cut -f1)
    free=$(free_kb "${probe[@]}")
    if (( free - size < reserve_kb )); then
      echo "Source cache: PRUNED ($branch; free $(gb "$free") GB, restore would leave $(gb $(( free - size > 0 ? free - size : 0 ))) GB < reserve $(gb "$reserve_kb") GB)"
      rm -rf "$cache"
    else
      echo "Source cache: HIT ($branch)"
      rsync -a --delete "$cache/tree/" "$PWD/"
      sig_before=$(cat "$cache/sig" 2>/dev/null || true)
      hit=true
    fi
  else
    echo "Source cache: MISS ($branch)"
  fi
else
  echo 'Source cache: DISABLED (not self-hosted)'
fi
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0=http.lowSpeedLimit GIT_CONFIG_VALUE_0=1024
export GIT_CONFIG_KEY_1=http.lowSpeedTime GIT_CONFIG_VALUE_1=60
init_repo() {
  repo init -u https://android.googlesource.com/kernel/manifest -b "common-$branch" --depth=1 || return $?
  local remote latest
  remote=$(git ls-remote https://android.googlesource.com/kernel/common "$branch") || return $?
  deprecated=false
  tag_fallback=''
  if grep -q deprecated <<< "$remote"; then
    sed -i "s|\"$branch\"|\"deprecated/$branch\"|g" .repo/manifests/default.xml
    deprecated=true
  elif [[ -z "$remote" ]]; then
    latest=$(git ls-remote https://android.googlesource.com/kernel/common "refs/tags/${branch}_r*" | awk '{print $2}' | grep -v '\^{}$' | sed 's|refs/tags/||' | awk -F'_r' '{print $NF+0, $0}' | sort -n | tail -n 1 | cut -d' ' -f2- || true)
    if [[ -n "$latest" ]]; then
      sed -i "/path=\"common\"/ s|revision=\"$branch\"|revision=\"refs/tags/$latest\"|" .repo/manifests/default.xml
      tag_fallback="$latest"
      echo "Using release tag $latest"
    else
      echo "WARNING: no upstream head or release tag for $branch" >&2
    fi
  fi
}
synced=false
delays=(15 30 60 120)
for attempt in 1 2 3 4 5; do
  echo "Attempt $attempt/5: initialize and sync $branch"
  rc=1
  if init_repo; then
    flags=(--no-tags)
    [[ -z "$tag_fallback" ]] || flags=()
    if timeout 15m repo sync -c --current-branch --no-clone-bundle "${flags[@]}" --jobs-checkout="${REPO_CHECKOUT_JOBS:-$(nproc)}" -j"${REPO_SYNC_JOBS:-16}"; then
      synced=true
      break
    else
      rc=$?
    fi
  else
    rc=$?
  fi
  if ((attempt == 5)); then
    echo "Kernel sync failed (exit $rc); existing source cache left unchanged." >&2
    exit "$rc"
  fi
  echo "Sync failed (exit $rc); cleaning working copy before retry."
  find "$PWD" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  sleep "${delays[$((attempt-1))]}"
done
[[ "$synced" == true ]]
printf 'deprecated=%s\ntag_fallback=%s\n' "$deprecated" "$tag_fallback" >> "${GITHUB_OUTPUT:?}"
# Snapshot before KernelSU/SUSFS/build modifications; skip when space is short.
if [[ "$cache_enabled" == true ]]; then
  sig_after=$(signature)
  if [[ "$hit" == true && "$sig_before" == "$sig_after" ]]; then
    touch -c "$cache/complete"
    echo "Source cache: UNCHANGED ($branch; upstream matches cache)"
  else
    tree_kb=$(du -sk "$PWD" | cut -f1)
    old_kb=0
    [[ ! -f "$cache/size_kb" ]] || old_kb=$(cat "$cache/size_kb")
    free=$(free_kb "${probe[@]}")
    if (( tree_kb > max_kb )); then
      echo "Source cache: SKIPPED ($branch; tree $(gb "$tree_kb") GB exceeds cap $(gb "$max_kb") GB)"
    elif (( free + old_kb - tree_kb < reserve_kb )); then
      echo "Source cache: SKIPPED ($branch; saving would leave $(gb $(( free + old_kb - tree_kb > 0 ? free + old_kb - tree_kb : 0 ))) GB < reserve $(gb "$reserve_kb") GB)"
    else
      rm -rf "$cache"
      others=0
      lines=()
      for f in "$cache_root"/*/complete; do
        [[ -f "$f" ]] || continue
        d=${f%/complete}
        s=$(cat "$d/size_kb" 2>/dev/null || echo 0)
        lines+=("$(stat -c %Y "$f") $s $d")
        others=$(( others + s ))
      done
      if (( ${#lines[@]} )); then
        while read -r _ s d; do
          (( others + tree_kb > max_kb )) || break
          echo "Source cache: PRUNED older entry ($(gb "$s") GB, over cap $(gb "$max_kb") GB)"
          rm -rf "$d"
          others=$(( others - s ))
        done < <(printf '%s\n' "${lines[@]}" | sort -n)
      fi
      staging=$(mktemp -d "$cache_root/$key.tmp.XXXXXX")
      trap 'rm -rf "$staging"' EXIT
      mkdir "$staging/tree"
      rsync -a "$PWD/" "$staging/tree/"
      printf '%s\n' "$branch" > "$staging/complete"
      printf '%s\n' "$tree_kb" > "$staging/size_kb"
      printf '%s\n' "$sig_after" > "$staging/sig"
      mv "$staging" "$cache"
      echo "Source cache: SAVED ($branch; $(gb "$tree_kb") GB pristine checkout)"
    fi
  fi
fi
if git -C common rev-parse --git-dir >/dev/null 2>&1; then
  sha=$(git -C common rev-parse HEAD)
  echo "KERNEL_SOURCE_COMMIT=$sha" >> "${GITHUB_ENV:?}"
  echo "Kernel common commit SHA: $sha"
  git -C common log -1 --format='Kernel common commit date: %cI%nKernel common commit msg: %s'
fi
