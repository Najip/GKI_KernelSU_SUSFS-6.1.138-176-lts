#!/usr/bin/env python3
"""Keep CONFIG_IPC_NS ABI-neutral by moving struct ipc_namespace into a side block.

Stock GKI builds with CONFIG_IPC_NS off, and then:

    struct ipc_namespace { };            /* empty - it is the ABI */

With the config on, the struct becomes ids[3], sem_ctls[4], msg_ctl*, shm_ctl*,
mq_*, ctl_table_set, notifier_block and an embedded `struct ns_common ns`. nsproxy
holds `struct ipc_namespace *` unconditionally and genksyms expands nested struct
definitions through pointers, so that definition change ripples into every symbol
that reaches a task - 6,329 of 9,741 on 6.18.

CONFIG_IPC_NS cannot be disabled independently: it is a hidden symbol,
`depends on (SYSVIPC || POSIX_MQUEUE)`, `default y`. So the struct has to stay
empty while the namespace still works.

Port:
  * the real body is renamed struct ipc_ns_state, with ns_common first so
    to_ipc_ns()'s container_of still lands on it
  * `struct ipc_namespace { }` is left behind as the empty placeholder
  * ipc_ns_state(ns) maps a namespace pointer to its state, special-casing
    &init_ipc_ns because that one is a static object, not an allocation
  * create_ipc_ns() allocates the state and returns it cast as the namespace
  * every `ns->field` access becomes ipc_ns_state(ns)->field

Usage: ipcns_zero_drift.py <kernel/common> [--check]
"""

import os
import re
import sys
from typing import NoReturn

MARKER = "/* ipc_namespace side-table port (GKI_KernelSU_SUSFS) */"

FIELDS = [
    "ids", "sem_ctls", "used_sems",
    "msg_ctlmax", "msg_ctlmnb", "msg_ctlmni", "percpu_msg_bytes", "percpu_msg_hdrs",
    "shm_ctlmax", "shm_ctlall", "shm_tot", "shm_ctlmni", "shm_rmid_forced",
    "ipcns_nb", "mq_mnt", "mq_queues_count", "mq_queues_max", "mq_msg_max",
    "mq_msgsize_max", "mq_msg_default", "mq_msgsize_default", "mq_set",
    "mq_sysctls", "user_ns",
]

ACCESSOR = f"""\
{MARKER}
/*
 * struct ipc_namespace is empty in every stock GKI build, because CONFIG_IPC_NS
 * is off there, and nsproxy's pointer target is part of the ABI. The real state
 * lives here instead; ipc_ns_state() maps a namespace to it.
 *
 * init_ipc_ns is a static object rather than an allocation, so it keeps its own
 * state block and is special-cased.
 */
struct ipc_ns_state {{
	struct ns_common ns;
	struct ipc_ids ids[3];
	int sem_ctls[4];
	int used_sems;
	unsigned int msg_ctlmax, msg_ctlmnb, msg_ctlmni;
	struct percpu_counter percpu_msg_bytes, percpu_msg_hdrs;
	size_t shm_ctlmax, shm_ctlall;
	unsigned long shm_tot;
	int shm_ctlmni;
	int shm_rmid_forced;
	struct notifier_block ipcns_nb;
	struct vfsmount *mq_mnt;
	unsigned int mq_queues_count, mq_queues_max, mq_msg_max;
	unsigned int mq_msgsize_max, mq_msg_default, mq_msgsize_default;
	struct ctl_table_set mq_set;
	struct ctl_table_header *mq_sysctls;
#ifdef CONFIG_USER_NS
	struct user_namespace *user_ns;
#endif
}};

extern struct ipc_ns_state init_ipc_ns_state;

static inline struct ipc_ns_state *ipc_ns_state(struct ipc_namespace *ns)
{{
	if (ns == &init_ipc_ns)
		return &init_ipc_ns_state;

	return (struct ipc_ns_state *)ns;
}}
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check_only = "--check" in sys.argv
    if len(args) != 1:
        die("usage: ipcns_zero_drift.py <kernel/common> [--check]")
    root = args[0].rstrip("/")
    hdr = os.path.join(root, "include/linux/ipc_namespace.h")
    for p in (hdr,):
        if not os.path.exists(p):
            die(f"{p}: not found")

    print(f"[+] ipc_namespace side-table port: {root}")
    src = read(hdr)
    if MARKER in src:
        print("  header already ported")
    else:
        # 1. the real body becomes struct ipc_ns_state
        # the definition ends with an annotation: "} __randomize_layout;"
        m = re.search(r"struct ipc_namespace \{\n(.*?)\n\}[ \t]*(?:__\w+)?;\n", src, re.S)
        if not m:
            die("ipc_namespace.h: struct ipc_namespace not found")
        body = m.group(1)
        state = ("struct ipc_ns_state {\n" + body + "\n};\n")
        placeholder = ("struct ipc_namespace {\n};\n")
        src = src[: m.start()] + placeholder + "\n" + state + src[m.end():]

        # 2. the accessor, after the placeholder
        anchor = "struct ipc_namespace {\n};\n"
        src = src.replace(anchor, anchor + "\n" + ACCESSOR, 1)
        write(hdr, src)
        print("  ipc_namespace.h: body moved to ipc_ns_state, empty placeholder kept")

    # 3. rewrite the field accesses, but only on expressions that really are an
    #    ipc namespace. `->ids[` and friends also exist on other structs, so the
    #    rewrite is restricted to a known set of pointer expressions.
    pat = re.compile(r"\b(%s)->(%s)\b" % (
        r"(?:ns|ipc_ns|new|old|new_ns|old_ns|nsproxy->ipc_ns|current->nsproxy->ipc_ns)",
        "|".join(FIELDS)))
    total = 0
    for dirpath, _d, files in os.walk(os.path.join(root, "ipc")):
        for f in files:
            if not f.endswith((".c", ".h")):
                continue
            p = os.path.join(dirpath, f)
            s = read(p)
            out, n = pat.subn(lambda mm: f"ipc_ns_state({mm.group(1)})->{mm.group(2)}", s)
            if n:
                if not check_only:
                    write(p, out)
                total += n
                print(f"  {os.path.relpath(p, root)}: {n} access(es)")
    print(f"  total access sites rewritten: {total}")

    if check_only:
        return

    # 4. report what is left, so the next iteration knows where to look
    leftovers = []
    for dirpath, _d, files in os.walk(os.path.join(root, "ipc")):
        for f in files:
            if not f.endswith((".c", ".h")):
                continue
            p = os.path.join(dirpath, f)
            for i, line in enumerate(read(p).splitlines(), 1):
                if "ipc_ns_state(" in line:
                    continue
                for fld in FIELDS:
                    if re.search(r"->" + fld + r"\b", line):
                        leftovers.append(f"{os.path.relpath(p, root)}:{i}: {line.strip()[:90]}")
                        break
    if leftovers:
        print(f"  NOTE: {len(leftovers)} access(es) left for manual follow-up:")
        for l in leftovers[:10]:
            print("    " + l)
    print("[+] done (header + ipc/ rewritten; allocation/init wiring is the next stage)")


if __name__ == "__main__":
    main()
