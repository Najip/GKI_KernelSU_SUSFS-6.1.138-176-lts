#!/usr/bin/env bash
# GKI_SOURCE_CACHE: persist pristine repo sources outside the Actions workspace.
set -euo pipefail
cd "${GITHUB_WORKSPACE:?}/kernel"
branch="${SOURCE_VERSION:?}-${SOURCE_PATCH_LEVEL:?}"
[[ "$branch" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo 'Invalid source branch' >&2; exit 1; }
cache_enabled=false
if [[ "${RUNNER_ENVIRONMENT:-}" == self-hosted ]]; then
  cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/gki-kernel/sources-v1"
  mkdir -p "$cache_root"
  cache_root=$(cd "$cache_root" && pwd -P)
  workspace=$(cd "$GITHUB_WORKSPACE" && pwd -P)
  case "$cache_root/" in "$workspace/"*) echo 'Source cache must be outside workspace' >&2; exit 1;; esac
  key=$(printf '%s' "$branch" | sha256sum | cut -d' ' -f1)
  cache="$cache_root/$key"
  exec 9>"$cache_root/$key.lock"
  flock 9
  cache_enabled=true
  if [[ -f "$cache/complete" && -d "$cache/tree/.repo" ]]; then
    echo "Source cache: HIT ($branch)"
    rsync -a --delete "$cache/tree/" "$PWD/"
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
# Snapshot before any KernelSU/SUSFS/build modifications. Never publish partial syncs.
if [[ "$cache_enabled" == true ]]; then
  staging=$(mktemp -d "$cache_root/$key.tmp.XXXXXX")
  trap 'rm -rf "$staging"' EXIT
  mkdir "$staging/tree"
  rsync -a "$PWD/" "$staging/tree/"
  printf '%s\n' "$branch" > "$staging/complete"
  rm -rf "$cache.old"
  if [[ -d "$cache" ]]; then mv "$cache" "$cache.old"; fi
  if mv "$staging" "$cache"; then
    rm -rf "$cache.old"
  else
    [[ ! -d "$cache.old" ]] || mv "$cache.old" "$cache"
    exit 1
  fi
  echo "Source cache: SAVED ($branch; pristine checkout)"
fi
if git -C common rev-parse --git-dir >/dev/null 2>&1; then
  sha=$(git -C common rev-parse HEAD)
  echo "KERNEL_SOURCE_COMMIT=$sha" >> "${GITHUB_ENV:?}"
  echo "Kernel common commit SHA: $sha"
  git -C common log -1 --format='Kernel common commit date: %cI%nKernel common commit msg: %s'
fi
