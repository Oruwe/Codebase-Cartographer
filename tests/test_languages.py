"""The language contract: every declared grammar compiles and actually captures.

These would fail loudly if a tree-sitter upgrade renamed a node type, which is
the failure mode that silently empties a graph.
"""

import pytest

from orgono.app.core.config import SUPPORTED_LANGUAGES
from orgono.app.core.languages import (
    LANGUAGES,
    get_parser,
    get_query,
    language_for_path,
    run_query,
)

SAMPLES = {
    "python": (b"import os\nclass Foo:\n    def bar(self):\n        return helper(1)\ndef helper(n): return n\n",
               {"Foo", "bar", "helper"}, {"helper"}, {"os"}),
    "javascript": (b'import fs from "fs";\nclass Foo { bar(){ return helper(1); } }\nfunction helper(n){ return n; }\n',
                   {"Foo", "bar", "helper"}, {"helper"}, {"fs"}),
    "typescript": (b'import {T} from "./t";\ninterface I { m(): void }\nfunction helper(n: number){ return n; }\nclass Foo { go(){ helper(1); } }\n',
                   {"I", "Foo", "helper", "go"}, {"helper"}, {"./t"}),
    "tsx": (b'import React from "react";\nfunction App(){ return helper(1); }\nfunction helper(n: number){ return n; }\n',
            {"App", "helper"}, {"helper"}, {"react"}),
    "go": (b'package main\nimport "fmt"\ntype Foo struct{}\nfunc (f Foo) Bar() { helper() }\nfunc helper() { fmt.Println(1) }\n',
           {"Foo", "Bar", "helper"}, {"helper", "Println"}, {'"fmt"'}),
    "rust": (b"use std::fmt;\nstruct Foo;\nfn helper(n: i32) -> i32 { n }\nfn go() -> i32 { helper(1) }\n",
             {"Foo", "helper", "go"}, {"helper"}, {"std::fmt"}),
    "java": (b"package a;\nimport java.util.List;\nclass Foo { int bar(){ return helper(); } int helper(){ return 1; } }\n",
             {"Foo", "bar", "helper"}, {"helper"}, {"java.util.List"}),
}


def test_supported_languages_matches_registry():
    assert set(SUPPORTED_LANGUAGES) == set(LANGUAGES)


@pytest.mark.parametrize("name", sorted(LANGUAGES))
def test_query_compiles(name):
    assert get_query(name) is not None


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_query_captures_expected_symbols(name):
    source, want_defs, want_calls, want_imports = SAMPLES[name]
    tree = get_parser(name).parse(source)
    caps = run_query(name, tree.root_node)
    defs = {n.text.decode() for n in caps.get("def.name", [])}
    calls = {n.text.decode() for n in caps.get("call.name", [])}
    imports = {n.text.decode() for n in caps.get("import.name", [])}
    assert want_defs <= defs, f"{name}: missing defs {want_defs - defs}"
    assert want_calls <= calls, f"{name}: missing calls {want_calls - calls}"
    assert want_imports <= imports, f"{name}: missing imports {want_imports - imports}"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("a/b.py", "python"), ("x.pyi", "python"), ("a.mjs", "javascript"),
        ("a.jsx", "javascript"), ("a.ts", "typescript"), ("a.tsx", "tsx"),
        ("main.go", "go"), ("lib.rs", "rust"), ("A.java", "java"),
        ("notes.md", None), ("Makefile", None), ("a.rb", None),
    ],
)
def test_language_for_path(path, expected):
    assert language_for_path(path) == expected


def test_tsx_and_typescript_use_different_grammars():
    from orgono.app.core.languages import get_language
    assert get_language("tsx") is not get_language("typescript")
