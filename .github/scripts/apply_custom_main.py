#!/usr/bin/env python3
import re
import sys
from pathlib import Path

PRIORITY = ['test_release_notes', 'use_nomount', 'use_ds']
DEFAULTS = {'kernel_build_version': '6.1.x-android14', 'os_patch_level': '"176"', 'brand_name': 'Najip', 'keep_kmi': 'true', 'commit_mode': 'latest', 'root_flavor': 'KernelSU', 'use_ds': 'false'}

def transform(text):
    text = re.sub(r'^name:.*$', 'name: Build Kernels 6.1.x Keep KMI, KernelSU, No Droidspaces', text, count=1, flags=re.M)
    trigger = re.search(r'^(?:on|"on"|\x27on\x27):\s*\n.*?(?=^\S)', text, re.M | re.S)
    if not trigger:
        raise ValueError('Cannot locate workflow triggers')
    dispatch = re.search(r'^  workflow_dispatch:.*?(?=^  [A-Za-z_]|\Z)', trigger[0], re.M | re.S)
    if not dispatch:
        raise ValueError('workflow_dispatch missing')
    section = dispatch[0]
    for name, default in DEFAULTS.items():
        pattern = r'(^      ' + name + r':\s*\n)(.*?)(?=^      \w+:|\Z)'
        def update(m):
            body, n = re.subn(r'^        default:.*$', '        default: ' + default, m[2], count=1, flags=re.M)
            if not n:
                body = body.rstrip() + '\n        default: ' + default + '\n'
            return m[1] + body
        section, n = re.subn(pattern, update, section, count=1, flags=re.M | re.S)
        if not n and name != 'use_ds':
            raise ValueError('Required preset input missing: ' + name)
    for name, desc in [('pinned_ksu_commit', 'Optional KernelSU commit SHA override'), ('pinned_susfs_commit', 'Optional SUSFS commit SHA override for selected family')]:
        if re.search(r'^      ' + name + ':', section, re.M):
            raise ValueError('Source already contains custom pin input')
        section = section.rstrip() + '\n      ' + name + ':\n        description: "' + desc + '"\n        type: string\n        required: false\n        default: ""\n'
    text = text[:trigger.start()] + 'on:\n' + section.rstrip() + '\n\n' + text[trigger.end():]
    anchor = '          emit ksu_commit "$KERNELSU_COMMIT"'
    if text.count(anchor) != 1:
        raise ValueError('KernelSU override anchor changed')
    override = '          CUSTOM_KSU=\'${{ inputs.pinned_ksu_commit }}\'\n          if [ -n "$CUSTOM_KSU" ]; then\n            [[ "$CUSTOM_KSU" =~ ^[0-9a-fA-F]{40}$ ]] || exit 1\n            KERNELSU_COMMIT="$CUSTOM_KSU"\n          fi\n'
    text = text.replace(anchor, override + anchor, 1)
    anchor = '          # Tracked build helpers (resolved automatically, before the run)'
    if text.count(anchor) != 1:
        raise ValueError('SUSFS override anchor changed')
    override = '          CUSTOM_SUSFS=\'${{ inputs.pinned_susfs_commit }}\'\n          if [ -n "$CUSTOM_SUSFS" ]; then\n            [[ "$CUSTOM_SUSFS" =~ ^[0-9a-fA-F]{40}$ ]] || exit 1\n            case "${{ inputs.kernel_build_version }}" in\n'
    for label, key in [('5.10.x-android12','android12_5_10'),('5.10.x-android13','android13_5_10'),('5.15.x-android13','android13_5_15'),('5.15.x-android14','android14_5_15'),('6.1.x-android14','android14_6_1'),('6.6.x-android15','android15_6_6'),('6.12.x-android16','android16_6_12')]:
        override += '              ' + label + ') emit susfs_commit_' + key + ' "$CUSTOM_SUSFS" ;;\n'
    override += '              *) echo "Select a supported single kernel family for SUSFS override" >&2; exit 1 ;;\n            esac\n          fi\n'
    text = text.replace(anchor, override + anchor, 1)
    head, sep, jobs = text.partition('\njobs:')
    if not sep:
        raise ValueError('jobs missing')
    def blocks(s):
        return list(re.finditer(r'^      (\w+):\s*\n.*?(?=^      \w+:|\Z)', s, re.M | re.S))
    removed = []
    for name in PRIORITY:
        if len(blocks(head)) <= 25:
            break
        match = next((m for m in blocks(head) if m[1] == name), None)
        if match:
            head = head[:match.start()] + head[match.end():]
            removed.append(name)
    if len(blocks(head)) > 25:
        raise ValueError('Still above 25 dispatch inputs after allowed removals')
    text = head + sep + jobs
    for name in removed:
        text = re.sub(r'\binputs\.' + name + r'\b', 'false', text)
    return text

if __name__ == '__main__':
    target = Path(sys.argv[1])
    target.write_text(transform(Path('.github/workflows/main.yml').read_text()))
