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

_C_Q = """
(function_definition declarator: (function_declarator declarator: (identifier) @def.name)) @def.function
(struct_specifier name: (type_identifier) @def.name) @def.class
(enum_specifier name: (type_identifier) @def.name) @def.class
(type_definition declarator: (type_identifier) @def.name) @def.class
(call_expression function: (identifier) @call.name) @call.node
(preproc_include path: (system_lib_string) @import.name) @import.node
(preproc_include path: (string_literal) @import.name) @import.node
"""

_CPP_Q = """
(function_definition declarator: (function_declarator declarator: (identifier) @def.name)) @def.function
(function_definition declarator: (function_declarator declarator: (field_identifier) @def.name)) @def.method
(class_specifier name: (type_identifier) @def.name) @def.class
(struct_specifier name: (type_identifier) @def.name) @def.class
(namespace_definition name: (namespace_identifier) @def.name) @def.class
(call_expression function: (identifier) @call.name) @call.node
(call_expression function: (field_expression field: (field_identifier) @call.name)) @call.node
(preproc_include path: (system_lib_string) @import.name) @import.node
(preproc_include path: (string_literal) @import.name) @import.node
"""

_CSHARP_Q = """
(class_declaration name: (identifier) @def.name) @def.class
(interface_declaration name: (identifier) @def.name) @def.interface
(struct_declaration name: (identifier) @def.name) @def.class
(enum_declaration name: (identifier) @def.name) @def.class
(record_declaration name: (identifier) @def.name) @def.class
(method_declaration name: (identifier) @def.name) @def.method
(invocation_expression function: (identifier) @call.name) @call.node
(invocation_expression function: (member_access_expression name: (identifier) @call.name)) @call.node
(using_directive (identifier) @import.name) @import.node
(using_directive (qualified_name) @import.name) @import.node
"""

_RUBY_Q = """
(class name: (constant) @def.name) @def.class
(module name: (constant) @def.name) @def.class
(method name: (identifier) @def.name) @def.function
(singleton_method name: (identifier) @def.name) @def.method
(call method: (identifier) @call.name) @call.node
"""

_PHP_Q = """
(class_declaration name: (name) @def.name) @def.class
(interface_declaration name: (name) @def.name) @def.interface
(trait_declaration name: (name) @def.name) @def.class
(function_definition name: (name) @def.name) @def.function
(method_declaration name: (name) @def.name) @def.method
(function_call_expression function: (name) @call.name) @call.node
(member_call_expression name: (name) @call.name) @call.node
(namespace_use_clause (qualified_name) @import.name) @import.node
"""

_BASH_Q = """
(function_definition name: (word) @def.name) @def.function
(command name: (command_name) @call.name) @call.node
"""

_KOTLIN_Q = """
(class_declaration (identifier) @def.name) @def.class
(function_declaration (identifier) @def.name) @def.function
(call_expression (identifier) @call.name) @call.node
(import (qualified_identifier) @import.name) @import.node
"""

_SWIFT_Q = """
(class_declaration name: (type_identifier) @def.name) @def.class
(protocol_declaration name: (type_identifier) @def.name) @def.interface
(function_declaration name: (simple_identifier) @def.name) @def.function
(call_expression (simple_identifier) @call.name) @call.node
(import_declaration (identifier) @import.name) @import.node
"""

_SCALA_Q = """
(class_definition name: (identifier) @def.name) @def.class
(object_definition name: (identifier) @def.name) @def.class
(trait_definition name: (identifier) @def.name) @def.interface
(function_definition name: (identifier) @def.name) @def.function
(call_expression function: (identifier) @call.name) @call.node
(import_declaration (identifier) @import.name) @import.node
"""

_LUA_Q = """
(function_declaration name: (identifier) @def.name) @def.function
(function_call name: (identifier) @call.name) @call.node
"""

# SQL is the one language where a "definition" is a schema object. A table
# created here and selected from elsewhere is the closest orgono gets to
# connecting application code to a database table.
_SQL_Q = """
(create_table (object_reference name: (identifier) @def.name)) @def.class
(create_view (object_reference name: (identifier) @def.name)) @def.class
(relation (object_reference name: (identifier) @call.name)) @call.node
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
    "c": LanguageSpec(
        name="c", extensions=(".c", ".h"), module="tree_sitter_c", query=_C_Q,
        scope_nodes=("function_definition", "compound_statement"),
    ),
    "cpp": LanguageSpec(
        name="cpp",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        module="tree_sitter_cpp", query=_CPP_Q,
        scope_nodes=("function_definition", "class_specifier", "compound_statement"),
    ),
    "c_sharp": LanguageSpec(
        name="c_sharp", extensions=(".cs",), module="tree_sitter_c_sharp", query=_CSHARP_Q,
        scope_nodes=("class_declaration", "method_declaration", "block"),
    ),
    "ruby": LanguageSpec(
        name="ruby", extensions=(".rb", ".rake", ".gemspec"), module="tree_sitter_ruby",
        query=_RUBY_Q, scope_nodes=("class", "method", "block"),
    ),
    "php": LanguageSpec(
        name="php", extensions=(".php", ".phtml"), module="tree_sitter_php",
        language_attr="language_php", query=_PHP_Q,
        scope_nodes=("class_declaration", "function_definition", "compound_statement"),
    ),
    "bash": LanguageSpec(
        name="bash", extensions=(".sh", ".bash", ".zsh"), module="tree_sitter_bash",
        query=_BASH_Q, scope_nodes=("function_definition", "compound_statement"),
    ),
    "kotlin": LanguageSpec(
        name="kotlin", extensions=(".kt", ".kts"), module="tree_sitter_kotlin",
        query=_KOTLIN_Q, scope_nodes=("class_declaration", "function_declaration"),
    ),
    "swift": LanguageSpec(
        name="swift", extensions=(".swift",), module="tree_sitter_swift", query=_SWIFT_Q,
        scope_nodes=("class_declaration", "function_declaration"),
    ),
    "scala": LanguageSpec(
        name="scala", extensions=(".scala", ".sc"), module="tree_sitter_scala",
        query=_SCALA_Q, scope_nodes=("class_definition", "function_definition"),
    ),
    "lua": LanguageSpec(
        name="lua", extensions=(".lua",), module="tree_sitter_lua", query=_LUA_Q,
        scope_nodes=("function_declaration", "block"),
    ),
    "sql": LanguageSpec(
        name="sql", extensions=(".sql",), module="tree_sitter_sql", query=_SQL_Q,
        scope_nodes=(),
    ),
}


EXTENSION_MAP: dict[str, str] = {}
for _spec in LANGUAGES.values():
    for _ext in _spec.extensions:
        EXTENSION_MAP[_ext] = _spec.name


class UnsupportedLanguage(LookupError):
    pass


class GrammarNotInstalled(UnsupportedLanguage):
    """The language is known, but its grammar package is not importable here.

    Raised instead of crashing so a missing or broken grammar degrades to an
    `unsupported` file report naming the exact pip package that fixes it.
    """

    def __init__(self, language: str, module: str, cause: str = "") -> None:
        self.language = language
        self.module = module
        pip_name = module.replace("_", "-")
        detail = f": {cause}" if cause else ""
        super().__init__(
            f"grammar for '{language}' is not installed{detail} "
            f"(pip install {pip_name})"
        )


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
    try:
        module = __import__(spec.module)
        ptr = getattr(module, spec.language_attr)()
        return Language(ptr)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise GrammarNotInstalled(name, spec.module, str(exc)) from exc


def grammar_available(name: str) -> bool:
    """True if this language's grammar can actually be loaded on this machine."""
    try:
        get_language(name)
    except UnsupportedLanguage:
        return False
    return True


def available_languages() -> list[str]:
    """Languages whose grammars are importable here, sorted."""
    return sorted(n for n in LANGUAGES if grammar_available(n))


def missing_languages() -> dict[str, str]:
    """Known languages whose grammar is not importable, mapped to the pip name."""
    return {
        name: LANGUAGES[name].module.replace("_", "-")
        for name in sorted(LANGUAGES)
        if not grammar_available(name)
    }


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
