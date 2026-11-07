#!/usr/bin/env python3
"""Rebuild the customised workflow from upstream main.yml.

usage: apply_custom_main.py <upstream main.yml> <output yml>
Every edit is anchored on text and idempotent; if upstream changes an
anchor the script exits non-zero instead of writing a broken workflow.

If the upstream workflow would have more than 25 workflow_dispatch inputs
after adding the two custom pinned-commit inputs, this script removes the
`use_ds` (DroidSpaces) and `test_release_notes` (preview release) inputs
to stay within GitHub's 25-input limit. If the total is 25 or fewer, those
inputs are left untouched.
"""
import re
import sys

NAME = "Build Kernels 6.1.x Keep KMI, KernelSU, No Droidspaces"
MAX_INPUTS = 25
DEFAULTS = {
    "kernel_build_version": "6.1.x-android14",
    "os_patch_level": "176",
    "brand_name": "Najip",
    "keep_kmi": "true",
    "commit_mode": "latest",
    "root_flavor": "KernelSU",
    "use_ds": "false",
}

PINNED_KSU_INPUT = """      pinned_ksu_commit:
        description: "Optional KernelSU commit SHA. If filled, overrides commit_mode for KernelSU only."
        required: false
        type: string
        default: ""
"""
PINNED_SUSFS_INPUT = """      pinned_susfs_commit:
        description: "Optional SUSFS commit SHA. Overrides the SUSFS commit for the selected Kernel Version."
        required: false
        type: string
        default: ""
"""
KSU_OVERRIDE = """          # Manual KernelSU override: if provided, it wins regardless of commit_mode.
          if [ -n "${{ inputs.pinned_ksu_commit }}" ]; then
            KERNELSU_COMMIT="${{ inputs.pinned_ksu_commit }}"
          fi
"""
CASES = [
    ("5.10.x-android12", "android12_5_10"), ("5.10.x-android13", "android13_5_10"),
    ("5.15.x-android13", "android13_5_15"), ("5.15.x-android14", "android14_5_15"),
    ("6.1.x-android14", "android14_6_1"), ("6.6.x-android15", "android15_6_6"),
    ("6.12.x-android16", "android16_6_12"),
]
SUSFS_OVERRIDE = (
    "          # Manual SUSFS override for the selected kernel family.\n"
    '          PINNED_SUSFS="${{ inputs.pinned_susfs_commit }}"\n'
    '          if [ -n "$PINNED_SUSFS" ]; then\n'
    "            KVER=\"${{ inputs.kernel_build_version || 'All' }}\"\n"
    '            if [ "$KVER" = "All" ]; then\n'
    '              echo "ERROR: pinned_susfs_commit requires selecting one Kernel Version, not All." >&2\n'
    "              exit 1\n"
    "            fi\n"
    '            case "$KVER" in\n'
    + "".join(
        f'              {k})\n                emit susfs_commit_{v} "$PINNED_SUSFS"\n                ;;\n'
        for k, v in CASES
    )
    + "              *)\n"
    "                echo \"ERROR: no SUSFS override mapping for kernel version '$KVER'.\" >&2\n"
    "                exit 1\n"
    "                ;;\n"
    "            esac\n"
    "          fi\n"
)


def die(msg):
    sys.exit(f"ERROR: upstream main.yml changed, anchor not found: {msg}")


def insert_before(text, anchor, block, marker):
    if marker in text:
        return text
    if text.count(anchor) < 1:
        die(anchor.strip())
    return text.replace(anchor, block + anchor, 1)


def set_default(lines, key, value):
    start = next((i for i, l in enumerate(lines) if l == f"      {key}:"), None)
    if start is None:
        die(f"input {key}")
    for i in range(start + 1, len(lines)):
        if re.match(r"^      \S", lines[i]):
            break
        if lines[i].startswith("        default:"):
            lines[i] = f"        default: {value}"
            return
    die(f"default of {key}")


def count_inputs(text):
    """Count workflow_dispatch inputs only (keys directly under inputs:)."""
    m = re.search(r"  workflow_dispatch:\n    inputs:\n(.*?)(?=\n  [a-z]|\njobs:)", text, flags=re.S)
    if not m:
        return 0
    return len(re.findall(r"^      [a-z_]+:\n", m.group(1), flags=re.M))


def remove_input(lines, key):
    """Remove an input block (key + its indented properties) from lines."""
    start = next((i for i, l in enumerate(lines) if l == f"      {key}:"), None)
    if start is None:
        return lines
    end = start + 1
    while end < len(lines) and not re.match(r"^      \S", lines[end]):
        end += 1
    return lines[:start] + lines[end:]


def main(src, dst):
    text = open(src, encoding="utf-8").read()
    text, n = re.subn(r"^name:.*$", f"name: {NAME}", text, count=1, flags=re.M)
    if n != 1:
        die("name:")
    lines = text.split("\n")
    for k, v in DEFAULTS.items():
        set_default(lines, k, v)
    text = "\n".join(lines)
    text = insert_before(text, "      use_susfs:\n", PINNED_KSU_INPUT, "pinned_ksu_commit:\n        description")
    text = insert_before(text, "      use_nomount:\n", PINNED_SUSFS_INPUT, "pinned_susfs_commit:\n        description")
    text = insert_before(text, '          emit ksu_commit "$KERNELSU_COMMIT"\n', KSU_OVERRIDE, "Manual KernelSU override")
    text = insert_before(text, "          # Tracked build helpers (resolved automatically, before the run)\n", SUSFS_OVERRIDE, "Manual SUSFS override")

    # Input-count guard: if total inputs exceed GitHub's 25 limit, drop
    # DroidSpaces (use_ds) and preview release (test_release_notes) inputs.
    lines = text.split("\n")
    total = count_inputs(text)
    if total > MAX_INPUTS:
        lines = remove_input(lines, "use_ds")
        lines = remove_input(lines, "test_release_notes")
        text = "\n".join(lines)
    open(dst, "w", encoding="utf-8").write(text)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
