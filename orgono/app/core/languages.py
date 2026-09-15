"""Language support: explicitly named, verified grammars and queries.

Every query in this file was written by dumping the real grammar's node names
(see tools/probe_grammars.py) and every one is compile-checked by
tests/test_languages.py. Nothing here was written from a remembered API.

Anything not listed here is reported as `unsupported`, never half-parsed.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

from tree_sitter import Language, Parser, Query, QueryCursor


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    module: str
    # attribute on the grammar module that returns the language pointer
    language_attr: str = "language"
    query: str = ""
    # node types that introduce a new lexical scope, used for depth bounding
    scope_nodes: tuple[str, ...] = field(default_factory=tuple)


# --- Queries -----------------------------------------------------------------
# Capture names are a fixed vocabulary consumed by extract.py:
#   @def.function @def.class @def.method @def.interface  -> `defines`
#   @def.name                                            -> name of the enclosing def
#   @call.name   @call.node                              -> `calls`
#   @import.name @import.node                            -> `imports`

_PYTHON_Q = """
(function_definition name: (identifier) @def.name) @def.function
(class_definition name: (identifier) @def.name) @def.class
(call function: (identifier) @call.name) @call.node
(call function: (attribute attribute: (identifier) @call.name)) @call.node
(import_statement name: (dotted_name) @import.name) @import.node
(import_statement name: (aliased_import name: (dotted_name) @import.name)) @import.node
(import_from_statement module_name: (dotted_name) @import.name) @import.node
(import_from_statement module_name: (relative_import) @import.name) @import.node
(module (expression_statement (assignment left: (identifier) @def.name)) @def.constant)
"""

_JAVASCRIPT_Q = """
(function_declaration name: (identifier) @def.name) @def.function
(generator_function_declaration name: (identifier) @def.name) @def.function
(class_declaration name: (identifier) @def.name) @def.class
(method_definition name: (property_identifier) @def.name) @def.method
(variable_declarator name: (identifier) @def.name value: (arrow_function)) @def.function
(variable_declarator name: (identifier) @def.name value: (function_expression)) @def.function
(call_expression function: (identifier) @call.name) @call.node
(call_expression function: (member_expression property: (property_identifier) @call.name)) @call.node
(import_statement source: (string (string_fragment) @import.name)) @import.node
(call_expression function: (identifier) @_req arguments: (arguments (string (string_fragment) @import.name))) @import.node
(program (lexical_declaration (variable_declarator name: (identifier) @def.name)) @def.constant)
"""

_TYPESCRIPT_Q = """
(function_declaration name: (identifier) @def.name) @def.function
(generator_function_declaration name: (identifier) @def.name) @def.function
(class_declaration name: (type_identifier) @def.name) @def.class
(interface_declaration name: (type_identifier) @def.name) @def.interface
(method_definition name: (property_identifier) @def.name) @def.method
(method_signature name: (property_identifier) @def.name) @def.method
(variable_declarator name: (identifier) @def.name value: (arrow_function)) @def.function
(call_expression function: (identifier) @call.name) @call.node
(call_expression function: (member_expression property: (property_identifier) @call.name)) @call.node
(import_statement source: (string (string_fragment) @import.name)) @import.node
(program (lexical_declaration (variable_declarator name: (identifier) @def.name)) @def.constant)
(enum_declaration name: (identifier) @def.name) @def.class
(type_alias_declaration name: (type_identifier) @def.name) @def.class
"""

_GO_Q = """
(function_declaration name: (identifier) @def.name) @def.function
(method_declaration name: (field_identifier) @def.name) @def.method
(type_spec name: (type_identifier) @def.name) @def.class
(call_expression function: (identifier) @call.name) @call.node
(call_expression function: (selector_expression field: (field_identifier) @call.name)) @call.node
(import_spec path: (interpreted_string_literal) @import.name) @import.node
(const_spec name: (identifier) @def.name) @def.constant
"""

_RUST_Q = """
(function_item name: (identifier) @def.name) @def.function
(struct_item name: (type_identifier) @def.name) @def.class
(enum_item name: (type_identifier) @def.name) @def.class
(trait_item name: (type_identifier) @def.name) @def.interface
(mod_item name: (identifier) @def.name) @def.class
(call_expression function: (identifier) @call.name) @call.node
(call_expression function: (field_expression field: (field_identifier) @call.name)) @call.node
(call_expression function: (scoped_identifier name: (identifier) @call.name)) @call.node
(use_declaration argument: (scoped_identifier) @import.name) @import.node
(use_declaration argument: (identifier) @import.name) @import.node
(use_declaration argument: (scoped_use_list path: (_) @import.name)) @import.node
(const_item name: (identifier) @def.name) @def.constant
(static_item name: (identifier) @def.name) @def.constant
(type_item name: (type_identifier) @def.name) @def.class
(union_item name: (type_identifier) @def.name) @def.class
"""

_JAVA_Q = """
(class_declaration name: (identifier) @def.name) @def.class
(interface_declaration name: (identifier) @def.name) @def.interface
(method_declaration name: (identifier) @def.name) @def.method
(constructor_declaration name: (identifier) @def.name) @def.method
(method_invocation name: (identifier) @call.name) @call.node
(object_creation_expression type: (type_identifier) @call.name) @call.node
(import_declaration (scoped_identifier) @import.name) @import.node
(enum_declaration name: (identifier) @def.name) @def.class
"""

LANGUAGES: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        name="python",
        extensions=(".py", ".pyi"),
        module="tree_sitter_python",
        query=_PYTHON_Q,
        scope_nodes=("function_definition", "class_definition", "block"),
    ),
    "javascript": LanguageSpec(
        name="javascript",
        extensions=(".js", ".mjs", ".cjs", ".jsx"),
        module="tree_sitter_javascript",
        query=_JAVASCRIPT_Q,
        scope_nodes=("function_declaration", "class_declaration", "statement_block"),
    ),
    "typescript": LanguageSpec(
        name="typescript",
        extensions=(".ts", ".mts", ".cts"),
        module="tree_sitter_typescript",
        language_attr="language_typescript",
        query=_TYPESCRIPT_Q,
        scope_nodes=("function_declaration", "class_declaration", "statement_block"),
    ),
    "tsx": LanguageSpec(
        name="tsx",
        extensions=(".tsx",),
        module="tree_sitter_typescript",
        language_attr="language_tsx",
        query=_TYPESCRIPT_Q,
        scope_nodes=("function_declaration", "class_declaration", "statement_block"),
    ),
    "go": LanguageSpec(
        name="go",
        extensions=(".go",),
        module="tree_sitter_go",
        query=_GO_Q,
        scope_nodes=("function_declaration", "method_declaration", "block"),
    ),
    "rust": LanguageSpec(
        name="rust",
        extensions=(".rs",),
        module="tree_sitter_rust",
        query=_RUST_Q,
        scope_nodes=("function_item", "impl_item", "block"),
    ),
    "java": LanguageSpec(
        name="java",
        extensions=(".java",),
        module="tree_sitter_java",
        query=_JAVA_Q,
        scope_nodes=("class_declaration", "method_declaration", "block"),
    ),
}

EXTENSION_MAP: dict[str, str] = {}
for _spec in LANGUAGES.values():
    for _ext in _spec.extensions:
        EXTENSION_MAP[_ext] = _spec.name


class UnsupportedLanguage(LookupError):
    pass


def language_for_path(path: str) -> str | None:
    """Return the language name for a path, or None if unsupported."""
    lowered = path.lower()
    for ext, lang in sorted(EXTENSION_MAP.items(), key=lambda kv: -len(kv[0])):
        if lowered.endswith(ext):
            return lang
    return None


@functools.cache
def get_language(name: str) -> Language:
    spec = LANGUAGES.get(name)
    if spec is None:
        raise UnsupportedLanguage(name)
    module = __import__(spec.module)
    ptr = getattr(module, spec.language_attr)()
    return Language(ptr)


@functools.cache
def get_parser(name: str) -> Parser:
    return Parser(get_language(name))


@functools.cache
def get_query(name: str) -> Query:
    spec = LANGUAGES[name]
    return Query(get_language(name), spec.query)


def run_query(name: str, root) -> dict[str, list]:
    """Execute the language's query. Returns capture-name -> [nodes]."""
    cursor = QueryCursor(get_query(name))
    return cursor.captures(root)
