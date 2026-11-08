#!/usr/bin/env python3
"""Collapse a kernel config fragment to one entry per symbol, last one wins.

Several steps append to the same fragment, so a symbol can appear more than once.
merge_config.sh takes the last occurrence and prints a redefinition warning, which
makes the result depend on which step ran last and is easy to miss in a long log.
Doing it explicitly here keeps the fragment honest: one line per symbol, and a note
when two steps disagreed about the value.

Usage: dedupe_fragment.py <fragment>
"""

import re
import sys

SYMBOL = re.compile(r"^(?:# )?(CONFIG_[A-Za-z0-9_]+)(?:=.*| is not set)$")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: dedupe_fragment.py <fragment>")
    path = sys.argv[1]

    seen: dict[str, str] = {}
    redefined: list[str] = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        sys.exit(f"{path}: not found")

    for line in lines:
        item = line.strip()
        match = SYMBOL.match(item)
        if not match:
            continue
        key = match.group(1)
        if key in seen and seen[key] != item:
            redefined.append(f"{key}: {seen[key]} -> {item}")
        seen[key] = item

    for note in redefined:
        print(f"note: redefined by a later step - {note}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(seen.values()) + "\n")
    print(f"{path}: {len(seen)} symbol(s), {len(redefined)} redefinition(s)")


if __name__ == "__main__":
    main()
