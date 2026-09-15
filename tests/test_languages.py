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
    "c": (b"#include <stdio.h>\nint helper(int n){return n;}\nint main(){return helper(1);}\nstruct S{int a;};\n",
          {"helper", "main", "S"}, {"helper"}, {"<stdio.h>"}),
    "cpp": (b"#include <vector>\nnamespace n { class C { public: int m(){ return h(); } }; }\nint h(){return 1;}\n",
            {"C", "h", "n"}, {"h"}, {"<vector>"}),
    "c_sharp": (b"using System;\nnamespace N { class C { int M(){ return H(); } int H(){ return 1; } } }\n",
                {"C", "M", "H"}, {"H"}, {"System"}),
    "ruby": (b'require "json"\nclass Foo\n  def bar\n    helper(1)\n  end\nend\ndef helper(n); n; end\n',
             {"Foo", "bar", "helper"}, {"helper"}, set()),
    "php": (b"<?php\nuse A\\B;\nclass Foo { function bar(){ return helper(1); } }\nfunction helper($n){ return $n; }\n",
            {"Foo", "bar", "helper"}, {"helper"}, {"A\\B"}),
    "bash": (b"source ./lib.sh\nhelper() { echo 1; }\nmain() { helper; }\n",
             {"helper", "main"}, {"helper"}, set()),
    "kotlin": (b"import kotlin.io.x\nclass Foo { fun bar(): Int = helper(1) }\nfun helper(n: Int): Int = n\n",
               {"Foo", "bar", "helper"}, {"helper"}, {"kotlin.io.x"}),
    "swift": (b"import Foundation\nclass Foo { func bar() -> Int { return helper(1) } }\nfunc helper(_ n: Int) -> Int { return n }\n",
              {"Foo", "bar", "helper"}, {"helper"}, {"Foundation"}),
    "scala": (b"import scala.io\nclass Foo { def bar(): Int = helper(1) }\ndef helper(n: Int): Int = n\n",
              {"Foo", "bar", "helper"}, {"helper"}, {"scala"}),
    "lua": (b'local m = require("m")\nfunction helper(n) return n end\nfunction bar() return helper(1) end\n',
            {"helper", "bar"}, {"helper"}, set()),
    "sql": (b"CREATE TABLE users (id INT, email TEXT);\nSELECT * FROM users WHERE id=1;\n",
            {"users"}, {"users"}, set()),
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
        ("a.rb", "ruby"), ("a.c", "c"), ("a.h", "c"), ("a.cpp", "cpp"),
        ("a.hpp", "cpp"), ("a.cs", "c_sharp"), ("a.php", "php"), ("a.sh", "bash"),
        ("a.kt", "kotlin"), ("a.swift", "swift"), ("a.scala", "scala"),
        ("a.lua", "lua"), ("schema.sql", "sql"),
        ("notes.md", None), ("Makefile", None), ("a.bin", None),
    ],
)
def test_language_for_path(path, expected):
    assert language_for_path(path) == expected


def test_tsx_and_typescript_use_different_grammars():
    from orgono.app.core.languages import get_language
    assert get_language("tsx") is not get_language("typescript")


# --- graceful degradation when a grammar is unavailable ---------------------

def test_all_declared_grammars_are_importable_here():
    from orgono.app.core.languages import missing_languages
    assert missing_languages() == {}, "a declared dependency failed to import"


def test_available_languages_matches_the_registry():
    from orgono.app.core.languages import LANGUAGES, available_languages
    assert set(available_languages()) == set(LANGUAGES)


def test_a_missing_grammar_degrades_instead_of_crashing(tmp_path, monkeypatch):
    """If a grammar cannot load, that file is reported as unsupported with the
    pip command that fixes it, and the rest of the run still succeeds."""
    import builtins
    import io

    from orgono.app.core.config import Config
    from orgono.app.core.extract import extract_repo
    from orgono.app.core.languages import get_language, get_parser
    from orgono.app.core.obs import Logger

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "tree_sitter_ruby":
            raise ImportError("simulated: grammar not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    get_language.cache_clear()
    get_parser.cache_clear()
    try:
        (tmp_path / "a.rb").write_text("class Foo\n  def bar; 1; end\nend\n")
        (tmp_path / "b.py").write_text("def ok(): return 1\n")
        graph = extract_repo(tmp_path, Config(), Logger(stream=io.StringIO()))
        rb = graph.files["a.rb"]
        assert rb.status == "unsupported"
        assert "pip install tree-sitter-ruby" in rb.reason
        assert any(n.name == "ok" for n in graph.nodes.values()), "the rest of the run must continue"
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        get_language.cache_clear()
        get_parser.cache_clear()


def test_grammar_not_installed_names_the_pip_package():
    from orgono.app.core.languages import GrammarNotInstalled
    exc = GrammarNotInstalled("ruby", "tree_sitter_ruby")
    assert "pip install tree-sitter-ruby" in str(exc)
