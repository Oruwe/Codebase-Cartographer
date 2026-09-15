#!/usr/bin/env python3
"""Dump the REAL node names of each supported grammar.

This is the tool that produced the queries in orgono/app/core/languages.py.
Run it after any tree-sitter upgrade: if a grammar renames a node type, the
queries must be rewritten against what the grammar actually emits, not against
what anyone remembers it emitting.

    python tools/probe_grammars.py            # node-type trees
    python tools/probe_grammars.py --captures # what the shipped queries capture
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orgono.app.core.languages import LANGUAGES, get_parser, run_query  # noqa: E402

SAMPLES: dict[str, bytes] = {
    "python": b"import os\nfrom a.b import c as d\nclass Foo(Base):\n    def bar(self, x):\n        return c.baz(x) + helper(1)\ndef helper(n): return n\n",
    "javascript": b'import fs from "fs";\nconst r = require("x");\nclass Foo extends Base { bar(x){ return helper(x); } }\nfunction helper(n){ return n; }\nconst arrow = (a) => helper(a);\n',
    "typescript": b'import type {T} from "./t";\ninterface I { m(): void }\nclass Foo implements I { m(): void { helper(1); } }\nfunction helper(n: number): number { return n; }\n',
    "tsx": b'import React from "react";\nfunction App(){ return <div onClick={()=>helper(1)}/>; }\nfunction helper(n: number){ return n; }\n',
    "go": b'package main\nimport ("fmt"; f "os")\ntype Foo struct{ A int }\nfunc (x Foo) Bar(n int) int { return helper(n) }\nfunc helper(n int) int { fmt.Println(n); return n }\n',
    "rust": b"use std::collections::HashMap;\nmod m;\nstruct Foo { a: i32 }\nimpl Foo { fn bar(&self, n: i32) -> i32 { helper(n) } }\nfn helper(n: i32) -> i32 { n }\n",
    "java": b"package com.x;\nimport java.util.List;\nclass Foo extends Base { int bar(int n){ return helper(n); } }\n",
}


def walk(node, depth: int, out: list[str], max_depth: int) -> None:
    if depth > max_depth:
        return
    if node.is_named:
        out.append("  " * depth + node.type)
    for child in node.children:
        walk(child, depth + 1, out, max_depth)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--captures", action="store_true",
                    help="show what the shipped queries capture instead of raw node types")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--language", help="probe only this language")
    args = ap.parse_args()

    names = [args.language] if args.language else sorted(LANGUAGES)
    for name in names:
        source = SAMPLES.get(name)
        if source is None:
            print(f"(no sample for {name})")
            continue
        tree = get_parser(name).parse(source)
        print("=" * 72)
        print(f"LANGUAGE: {name}")
        if args.captures:
            caps = run_query(name, tree.root_node)
            for capture in sorted(caps):
                values = sorted({n.text.decode("utf-8", "replace") for n in caps[capture]})
                print(f"  {capture:<14} {values}")
        else:
            out: list[str] = []
            walk(tree.root_node, 0, out, args.depth)
            print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
