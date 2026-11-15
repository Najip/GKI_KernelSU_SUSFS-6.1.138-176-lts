#!/usr/bin/env bash
# GKI_SOURCE_CACHE: keep local git mirrors of build dependencies on self-hosted runners.
# Refreshes a mirror only when the remote refs changed, then redirects git to it. Never fails a job.
set -uo pipefail
[[ "${RUNNER_ENVIRONMENT:-}" == self-hosted ]] || { echo '[mirror] not a self-hosted runner; skipping'; exit 0; }
root="${GKI_MIRROR_ROOT:-${XDG_CACHE_HOME:-$HOME/.cache}/gki-kernel/git-mirrors}"
list="${GKI_MIRROR_LIST:-$root/repos.txt}"
conf="$root/mirrors.gitconfig"
mkdir -p "$root"
exec 8>"$root/.lock"
flock 8
export GIT_TERMINAL_PROMPT=0
real_global="${GKI_REAL_GITCONFIG:-$HOME/.gitconfig}"
g() { GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git "$@"; }
if [[ ! -f "$list" ]]; then
  cat > "$list" <<'LIST'
# One upstream URL per line. Add anything the build clones repeatedly.
https://github.com/tiann/KernelSU.git
https://github.com/KernelSU-Next/KernelSU-Next.git
https://github.com/Baka-SU/BakaSU.git
https://github.com/SukiSU-Ultra/SukiSU-Ultra.git
https://github.com/KOWX712/KernelSU.git
https://github.com/LyraVoid/FinaSU.git
https://github.com/WildKernels/kernel_patches.git
https://github.com/WildKernels/AnyKernel3.git
https://github.com/maxsteeel/nomount.git
https://gitlab.com/simonpunk/susfs4ksu.git
LIST
fi
: > "$conf.new"
if [[ "${GKI_MIRROR_DISABLE:-0}" != 1 ]]; then
  while IFS= read -r url; do
    [[ -n "$url" && "$url" != \#* ]] || continue
    name=$(printf '%s' "$url" | sha256sum | cut -c1-16)
    m="$root/$name.git"
    if ! out=$(g ls-remote --symref "$url" HEAD 'refs/heads/*' 'refs/tags/*' 2>/dev/null) || [[ -z "$out" ]]; then
      echo "[mirror] $url: unreachable, using network"
      continue
    fi
    sig=$(printf '%s\n' "$out" | sort | sha256sum | cut -d' ' -f1)
    default=$(printf '%s\n' "$out" | awk '/^ref: /{print $2; exit}')
    state=unchanged
    if [[ ! -d "$m" ]]; then
      state=cloned
      g init --bare -q "$m" || continue
      g -C "$m" config remote.origin.url "$url"
      g -C "$m" config --replace-all remote.origin.fetch '+refs/heads/*:refs/heads/*'
      g -C "$m" config --add remote.origin.fetch '+refs/tags/*:refs/tags/*'
      g -C "$m" config uploadpack.allowAnySHA1InWant true
      g -C "$m" config gc.pruneExpire never
      g -C "$m" config gc.auto 0
    elif [[ "$(cat "$m/.sig" 2>/dev/null)" != "$sig" ]]; then
      state=updated
    fi
    if [[ "$state" != unchanged ]]; then
      if ! g -C "$m" fetch --prune --quiet origin; then
        echo "[mirror] $url: fetch failed, using network"
        [[ "$state" == cloned ]] && rm -rf "$m"
        continue
      fi
    fi
    [[ -z "$default" ]] || g -C "$m" symbolic-ref HEAD "$default"
    printf '%s\n' "$sig" > "$m/.sig"
    echo "[mirror] $url: $state"
    base=${url%.git}
    printf '[url "file://%s"]\n\tinsteadOf = %s\n\tinsteadOf = %s\n' "$m" "$url" "$base" >> "$conf.new"
  done < "$list"
fi
mv "$conf.new" "$conf"
if ! GIT_CONFIG_GLOBAL="$real_global" git config --global --get-all include.path 2>/dev/null | grep -qxF "$conf"; then
  GIT_CONFIG_GLOBAL="$real_global" git config --global --add include.path "$conf"
fi
exit 0
