#!/usr/bin/env python3
"""Keep CONFIG_SYSVIPC ABI-neutral by hosting its task_struct fields in KABI reserves.

Stock GKI builds `struct task_struct` with CONFIG_SYSVIPC off, so the struct has no
`sysvsem`/`sysvshm` members. Turning the config on adds them:

    #ifdef CONFIG_SYSVIPC
        struct sysv_sem   sysvsem;   /*  8 bytes: one pointer */
        struct sysv_shm   sysvshm;   /* 16 bytes: a list_head  */
    #endif

24 bytes, which shifts every later member and re-CRCs every exported symbol taking
a task pointer - 6,329 of 9,741 on 6.18.

`struct task_struct` already carries ANDROID_KABI_RESERVE(2..8), i.e. seven u64
slots that exist in the stock build too. Three of them are exactly 24 bytes, so
hosting the SYSVIPC state there keeps the layout byte-identical whether the config
is on or off.

The upstream DroidSpaces "fix_sysvipc_kabi" patch does something different - it
wraps each field in a packed union with a single char, which removes padding but
keeps the fields (32 -> 24 measured), so it does not restore the stock ABI.

Usage: sysvipc_kabi_reserve.py <kernel/common>
"""

import os
import re
import sys
from typing import NoReturn

MARKER = "/* sysvipc KABI-reserve port (GKI_KernelSU_SUSFS) */"

UNION = f"""\
{MARKER}
	/*
	 * CONFIG_SYSVIPC state lives in KABI reserve slots. Those three u64 reserves
	 * exist in every GKI build, and struct sysv_sem (8B) + struct sysv_shm (16B)
	 * fit them exactly, so enabling SYSVIPC costs zero bytes of ABI and stock
	 * module CRCs keep matching.
	 */
	union {{
		struct {{
			ANDROID_KABI_RESERVE(6);
			ANDROID_KABI_RESERVE(7);
			ANDROID_KABI_RESERVE(8);
		}};
		struct {{
			struct sysv_sem	sysvsem;
			struct sysv_shm	sysvshm;
		}};
	}};
"""


def die(msg: str) -> NoReturn:
    sys.exit(f"ERROR: {msg}")


def read(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def write(p: str, t: str) -> None:
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(t)


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: sysvipc_kabi_reserve.py <kernel/common>")
    root = sys.argv[1].rstrip("/")
    sched = os.path.join(root, "include/linux/sched.h")
    if not os.path.exists(sched):
        die(f"{sched}: not found")

    src = read(sched)
    print(f"[+] sysvipc KABI-reserve port: {root}")

    if MARKER in src:
        print("  already ported - verifying only")
    else:
        # 1. the original fields must go. Two shapes exist by the time this runs:
        #    the stock block, or DroidSpaces' own kABI patch, which replaces it with
        #    packed unions (that patch strips padding but keeps the fields, so it is
        #    superseded here).
        m = re.search(r"#ifdef CONFIG_SYSVIPC\n"
                      r"\tstruct sysv_sem\t+sysvsem;\n"
                      r"\tstruct sysv_shm\t+sysvshm;\n"
                      r"#endif\n", src)
        if not m:
            # DroidSpaces-patched shape
            m = re.search(r"#ifdef CONFIG_SYSVIPC\n"
                          r"(?:[^\n]*\n){0,20}?#endif\n", src)
            if m and "sysvsem" in m.group(0) and "sysvshm" in m.group(0):
                print("  sched.h: removing DroidSpaces' packed-union block")
            else:
                die("sched.h: neither the stock nor the patched CONFIG_SYSVIPC "
                    "task_struct block was found")
        src = src[:m.start()] + "/* SYSVIPC state moved into the KABI reserves below */\n" + src[m.end():]
        print("  sched.h: removed the sysvsem/sysvshm members")

        # 2. host them in three consecutive reserve slots. Which numbers exist
        #    depends on the sublevel (6.18.38 differs from 6.18.32), so find
        #    struct task_struct by brace matching and take the highest run of three.
        start = src.index("struct task_struct {")
        depth, i = 0, src.index("{", start)
        while i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = src[start:i]
        nums = [int(n) for n in re.findall(r"ANDROID_KABI_RESERVE\((\d+)\);", body)]
        if not nums:
            die("sched.h: task_struct has no ANDROID_KABI_RESERVE slots")
        runs = []
        for n in nums:
            if n + 1 in nums and n + 2 in nums:
                runs.append(n)
        if not runs:
            die(f"sched.h: no three consecutive reserves in task_struct (have {nums})")
        pick = max(runs)
        print(f"  sched.h: reserves present {nums}, using {pick}/{pick+1}/{pick+2}")
        block = re.compile(r"[ \t]*ANDROID_KABI_RESERVE\(%d\);[ \t]*\n"
                           r"[ \t]*ANDROID_KABI_RESERVE\(%d\);[ \t]*\n"
                           r"[ \t]*ANDROID_KABI_RESERVE\(%d\);[ \t]*\n" % (pick, pick+1, pick+2))
        if len(block.findall(body)) != 1:
            die("sched.h: the chosen reserve run is not unique inside task_struct")
        union = UNION.replace("ANDROID_KABI_RESERVE(6)", f"ANDROID_KABI_RESERVE({pick})") \
                     .replace("ANDROID_KABI_RESERVE(7)", f"ANDROID_KABI_RESERVE({pick+1})") \
                     .replace("ANDROID_KABI_RESERVE(8)", f"ANDROID_KABI_RESERVE({pick+2})")
        body = block.sub(union, body, count=1)
        src = src[:start] + body + src[i:]
        write(sched, src)
        print("  sched.h: sysv fields hosted in ANDROID_KABI_RESERVE(6..8)")

    # verify
    now = read(sched)
    problems = []
    if MARKER not in now:
        problems.append("union/marker missing")
    if re.search(r"#ifdef CONFIG_SYSVIPC\n\tstruct sysv_sem", now):
        problems.append("original members still present")
    if "struct sysv_sem\tsysvsem;" not in now:
        problems.append("sysvsem missing")
    if "struct sysv_shm\tsysvshm;" not in now:
        problems.append("sysvshm missing")
    # the reserve numbers are sublevel-dependent; only require that the union
    # still carries three of them
    if len(re.findall(r"ANDROID_KABI_RESERVE\(\d+\);\n\t\t\};", now)) != 1:
        problems.append("the reserve union is not intact")
    if problems:
        die("verification failed:\n  - " + "\n  - ".join(problems))
    print("  verified: fields live in the reserve union, size unchanged")
    print("[+] done")


if __name__ == "__main__":
    main()
