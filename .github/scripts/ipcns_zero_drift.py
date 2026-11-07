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
extern struct ipc_ns_state init_ipc_ns_state;

extern struct ns_common *ipc_ns_to_common(struct ipc_namespace *ns);

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

        # 2. the accessor, after the extern it references (init_ipc_ns), which is
        #    what the whole thing keys on
        anchor = "extern struct ipc_namespace init_ipc_ns;\n"
        if anchor not in src:
            die("ipc_namespace.h: extern init_ipc_ns not found")
        src = src.replace(anchor, anchor + "\n" + ACCESSOR, 1)

        # 2b. the embedded ns_common now lives in the state, so container_of has to
        #     name the state type
        src, k = re.subn(r"container_of\(ns, struct ipc_namespace, ns\)",
                         "(struct ipc_namespace *)container_of(ns, struct ipc_ns_state, ns)", src)
        if not k:
            die("ipc_namespace.h: to_ipc_ns container_of not found")
        print("  ipc_namespace.h: to_ipc_ns() now resolves into ipc_ns_state")
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

    # 3b. the id-table macros spell the access the other way round, and
    #     to_ipc_ns() hands back an empty struct.
    for rel, macro in (("ipc/shm.c", "shm_ids"), ("ipc/sem.c", "sem_ids"),
                       ("ipc/msg.c", "msg_ids")):
        fp = os.path.join(root, rel)
        if not os.path.exists(fp):
            continue
        s = read(fp)
        s2, n = re.subn(r"#define %s\(ns\)\s*\(\(ns\)->ids\[" % macro,
                        "#define %s(ns)\t(ipc_ns_state(ns)->ids[" % macro, s)
        if n:
            write(fp, s2)
            print(f"  {rel}: {macro}() macro rewired")
    ns_c2 = os.path.join(root, "ipc/namespace.c")
    if os.path.exists(ns_c2):
        s = read(ns_c2)
        s2, n = re.subn(r"to_ipc_ns\(ns\)->user_ns", "ipc_ns_state(to_ipc_ns(ns))->user_ns", s)
        if n:
            write(ns_c2, s2)
            print(f"  ipc/namespace.c: to_ipc_ns()->user_ns rewired")

    # 4. allocation and namespace-core wiring in ipc/namespace.c. kzalloc of the
    #    empty struct would return ZERO_SIZE_PTR, so every namespace would alias
    #    the same address; and ns_common_init() reaches the embedded ns_common via
    #    to_ns_common(), which has nothing to point at once the struct is empty.
    ns_c = os.path.join(root, "ipc/namespace.c")
    if os.path.exists(ns_c):
        s = read(ns_c)
        n = 0
        if "sizeof(struct ipc_ns_state)" not in s:
            s, k = re.subn(r"kzalloc\(sizeof\(struct ipc_namespace\)",
                           "kzalloc(sizeof(struct ipc_ns_state)", s)
            n += k
        if "ns_common_init(&ipc_ns_state(ns)->ns)" not in s:
            s, k = re.subn(r"\bns_common_init\(ns\)",
                           "ns_common_init(&ipc_ns_state(ns)->ns)", s)
            n += k
        if n:
            write(ns_c, s)
            print(f"  ipc/namespace.c: {n} allocation/init site(s) rewired")

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
    # 4b. ns_common.h resolves ns_common through _Generic(&ns->ns), which cannot work
    #     on an empty struct - and it has to land on init_ipc_ns_state for the static
    #     namespace, so a plain cast is no good either. Route IPC through a helper.
    nc = os.path.join(root, "include/linux/ns_common.h")
    if os.path.exists(nc):
        s2 = read(nc)
        s3, k1 = re.subn(r"struct ipc_namespace \*:          &\(__ns\)->ns,",
                         "struct ipc_namespace *:          ipc_ns_to_common((struct ipc_namespace *)(__ns)),", s2)
        s3, k2 = re.subn(r"const struct ipc_namespace \*:    &\(__ns\)->ns,",
                         "const struct ipc_namespace *:    ipc_ns_to_common((struct ipc_namespace *)(__ns)),", s3)
        if not (k1 and k2):
            die("ns_common.h: ipc _Generic branches not found")
        if "struct ipc_namespace;" not in s3 and "struct ipc_namespace {" not in s3:
            s3 = s3.replace("#define to_ns_common(__ns)",
                            "struct ipc_namespace;\n\n#define to_ns_common(__ns)", 1)
        write(nc, s3)
        print("  include/linux/ns_common.h: IPC routed through ipc_ns_to_common()")

    # 5. the static namespace: its initialiser has to fill the state, and the
    #    object itself stays empty
    mu = os.path.join(root, "ipc/msgutil.c")
    if os.path.exists(mu):
        s = read(mu)
        m = re.search(r"struct ipc_namespace init_ipc_ns = \{\n(.*?)\n\};\n", s, re.S)
        if m:
            init = m.group(1).replace("&init_ipc_ns", "&init_ipc_ns_state")
            repl = ("struct ipc_namespace init_ipc_ns;\n"
                    "struct ipc_ns_state init_ipc_ns_state = {\n" + init + "\n};\n")
            s = s[: m.start()] + repl + s[m.end():]
            write(mu, s)
            print("  ipc/msgutil.c: init_ipc_ns initialiser moved to init_ipc_ns_state")
        else:
            die("ipc/msgutil.c: init_ipc_ns initialiser not found")

    ns_c3 = os.path.join(root, "ipc/namespace.c")
    if os.path.exists(ns_c3):
        s = read(ns_c3)
        n = 0
        s, k = re.subn(r"ns_common_free\(ns\)", "ns_common_free(&ipc_ns_state(ns)->ns)", s)
        n += k
        s, k = re.subn(r"\b(n|ns)->mq_mnt\b", r"ipc_ns_state(\1)->mq_mnt", s)
        n += k
        helper = ("\nstruct ns_common *ipc_ns_to_common(struct ipc_namespace *ns)\n"
                  "{\n\treturn &ipc_ns_state(ns)->ns;\n}\n")
        if "ipc_ns_to_common(struct ipc_namespace *ns)\n{" not in s:
            s = s.rstrip("\n") + "\n" + helper
        n += k
        if n:
            write(ns_c3, s)
            print(f"  ipc/namespace.c: {n} teardown site(s) rewired")

    print("[+] done (header + ipc/ + init/teardown rewritten)")


if __name__ == "__main__":
    main()
