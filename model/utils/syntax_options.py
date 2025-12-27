"""
Centralized syntax options for static analysis.
Defines all valid syntax types for Python (130+) and C/C++ (66+).

This module serves as the single source of truth for syntax validation
across Backend and provides data for the /syntax-options API.
"""
import ast
from typing import Dict, List, Set

# =============================================================================
# PYTHON SYNTAX OPTIONS
# =============================================================================


def _get_all_python_ast_types() -> List[str]:
    """
    Dynamically get all Python AST node types as lowercase names.
    This ensures compatibility with future Python versions.
    """
    types = []
    for name in dir(ast):
        obj = getattr(ast, name, None)
        if (isinstance(obj, type) and issubclass(obj, ast.AST)
                and name[0].isupper() and obj is not ast.AST):
            types.append(name.lower())
    return sorted(set(types))


# Common syntax types shown as buttons in the UI
PYTHON_COMMON_SYNTAX = [
    # Control flow
    "for",
    "while",
    "if",
    "recursive",
    # Declarations
    "functiondef",
    "classdef",
    "lambda",
    # Exceptions
    "try",
    "raise",
    # Imports
    "import",
    "importfrom",
    # Comprehensions
    "listcomp",
    "dictcomp",
    "generatorexp",
    # Other common
    "with",
    "assert",
    "return",
    "yield"
]

# Categorized syntax for better UI organization
PYTHON_SYNTAX_CATEGORIES = {
    "control_flow": [
        "for", "while", "if", "ifexp", "match", "match_case", "break",
        "continue", "pass", "return", "yield", "yieldfrom"
    ],
    "declarations": [
        "functiondef", "asyncfunctiondef", "classdef", "lambda", "assign",
        "annassign", "augassign", "namedexpr", "global", "nonlocal"
    ],
    "expressions": [
        "call", "attribute", "subscript", "starred", "name", "constant",
        "formattedvalue", "joinedstr", "list", "tuple", "set", "dict"
    ],
    "operators": ["binop", "unaryop", "boolop", "compare"],
    "comprehensions":
    ["listcomp", "setcomp", "dictcomp", "generatorexp", "comprehension"],
    "imports": ["import", "importfrom", "alias"],
    "exceptions": ["try", "trystar", "excepthandler", "raise", "assert"],
    "async": ["await", "asyncfor", "asyncwith", "asyncfunctiondef"],
    "context": ["with", "withitem"],
    "special": [
        "recursive"  # Not an AST type but supported for recursion detection
    ]
}


def get_python_syntax_options() -> Dict:
    """
    Return Python syntax options with common list, full list, and categories.
    """
    all_types = _get_all_python_ast_types()
    # Add 'recursive' as special case (not an AST type but supported)
    all_with_recursive = sorted(set(all_types) | {"recursive"})

    return {
        "common": PYTHON_COMMON_SYNTAX,
        "all": all_with_recursive,
        "categories": PYTHON_SYNTAX_CATEGORIES
    }


# =============================================================================
# C/C++ SYNTAX OPTIONS
# =============================================================================

# Map libclang CursorKind names to user-friendly snake_case names
# This mapping is used in both Backend (validation) and Sandbox (detection)
CPP_CURSOR_KIND_NAME_MAP = {
    # Control Flow (13 types)
    "FOR_STMT": "for",
    "CXX_FOR_RANGE_STMT": "range_for",
    "WHILE_STMT": "while",
    "DO_STMT": "do_while",
    "IF_STMT": "if",
    "SWITCH_STMT": "switch",
    "CASE_STMT": "case",
    "DEFAULT_STMT": "default",
    "BREAK_STMT": "break",
    "CONTINUE_STMT": "continue",
    "RETURN_STMT": "return",
    "GOTO_STMT": "goto",
    "LABEL_STMT": "label",

    # Declarations (15 types)
    "VAR_DECL": "var_decl",
    "PARM_DECL": "param_decl",
    "FUNCTION_DECL": "function_decl",
    "FIELD_DECL": "field_decl",
    "ENUM_DECL": "enum_decl",
    "ENUM_CONSTANT_DECL": "enum_constant",
    "STRUCT_DECL": "struct_decl",
    "UNION_DECL": "union_decl",
    "CLASS_DECL": "class_decl",
    "TYPEDEF_DECL": "typedef",
    "NAMESPACE": "namespace",
    "USING_DECLARATION": "using",
    "USING_DIRECTIVE": "using_directive",
    "TYPE_ALIAS_DECL": "type_alias",
    "STATIC_ASSERT": "static_assert",

    # Expressions (7 types)
    "CALL_EXPR": "call",
    "BINARY_OPERATOR": "binary_op",
    "UNARY_OPERATOR": "unary_op",
    "COMPOUND_ASSIGNMENT_OPERATOR": "compound_assign",
    "CONDITIONAL_OPERATOR": "ternary",
    "ARRAY_SUBSCRIPT_EXPR": "array_subscript",
    "MEMBER_REF_EXPR": "member_access",

    # Literals (6 types)
    "INTEGER_LITERAL": "integer_literal",
    "FLOATING_LITERAL": "float_literal",
    "STRING_LITERAL": "string_literal",
    "CHARACTER_LITERAL": "char_literal",
    "CXX_BOOL_LITERAL_EXPR": "bool_literal",
    "CXX_NULL_PTR_LITERAL_EXPR": "nullptr",

    # Casts (6 types)
    "CSTYLE_CAST_EXPR": "c_cast",
    "CXX_STATIC_CAST_EXPR": "static_cast",
    "CXX_DYNAMIC_CAST_EXPR": "dynamic_cast",
    "CXX_CONST_CAST_EXPR": "const_cast",
    "CXX_REINTERPRET_CAST_EXPR": "reinterpret_cast",
    "CXX_FUNCTIONAL_CAST_EXPR": "functional_cast",

    # Memory Management (2 types)
    "CXX_NEW_EXPR": "new",
    "CXX_DELETE_EXPR": "delete",

    # Classes/OOP (8 types)
    "CONSTRUCTOR": "constructor",
    "DESTRUCTOR": "destructor",
    "CXX_METHOD": "method",
    "CONVERSION_FUNCTION": "conversion_func",
    "CXX_THIS_EXPR": "this",
    "CXX_BASE_SPECIFIER": "base_specifier",
    "CXX_ACCESS_SPEC_DECL": "access_specifier",
    "FRIEND_DECL": "friend",

    # Exceptions (3 types)
    "CXX_TRY_STMT": "try",
    "CXX_CATCH_STMT": "catch",
    "CXX_THROW_EXPR": "throw",

    # Templates (5 types)
    "CLASS_TEMPLATE": "class_template",
    "FUNCTION_TEMPLATE": "function_template",
    "TEMPLATE_TYPE_PARAMETER": "template_type_param",
    "TEMPLATE_NON_TYPE_PARAMETER": "template_nontype_param",
    "TEMPLATE_TEMPLATE_PARAMETER": "template_template_param",

    # Lambda (1 type)
    "LAMBDA_EXPR": "lambda",
}
# Total: 66 C/C++ syntax types

# Common syntax types shown as buttons in the UI
CPP_COMMON_SYNTAX = [
    # Loops
    "for",
    "while",
    "do_while",
    "recursive",
    # Conditionals
    "if",
    "switch",
    # Declarations
    "function_decl",
    "class_decl",
    "struct_decl",
    # Expressions
    "call",
    "binary_op",
    # Memory
    "new",
    "delete",
    # Exceptions
    "try",
    "throw",
    # Modern C++
    "lambda",
    "static_cast"
]

# Categorized syntax for better UI organization
CPP_SYNTAX_CATEGORIES = {
    "control_flow": [
        "for", "range_for", "while", "do_while", "if", "switch", "case",
        "default", "break", "continue", "return", "goto", "label"
    ],
    "declarations": [
        "var_decl", "param_decl", "function_decl", "field_decl", "enum_decl",
        "enum_constant", "struct_decl", "union_decl", "class_decl", "typedef",
        "namespace", "using", "using_directive", "type_alias", "static_assert"
    ],
    "expressions": [
        "call", "binary_op", "unary_op", "compound_assign", "ternary",
        "array_subscript", "member_access"
    ],
    "literals": [
        "integer_literal", "float_literal", "string_literal", "char_literal",
        "bool_literal", "nullptr"
    ],
    "casts": [
        "c_cast", "static_cast", "dynamic_cast", "const_cast",
        "reinterpret_cast", "functional_cast"
    ],
    "memory": ["new", "delete"],
    "classes": [
        "constructor", "destructor", "method", "conversion_func", "this",
        "base_specifier", "access_specifier", "friend"
    ],
    "exceptions": ["try", "catch", "throw"],
    "templates": [
        "class_template", "function_template", "template_type_param",
        "template_nontype_param", "template_template_param"
    ],
    "lambda": ["lambda"],
    "special": [
        "recursive"  # Not a CursorKind but supported for recursion detection
    ]
}


def get_cpp_syntax_options() -> Dict:
    """
    Return C/C++ syntax options with common list, full list, and categories.
    """
    all_types = sorted(set(CPP_CURSOR_KIND_NAME_MAP.values()) | {"recursive"})

    return {
        "common": CPP_COMMON_SYNTAX,
        "all": all_types,
        "categories": CPP_SYNTAX_CATEGORIES
    }


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================


def get_valid_syntax_set(language: str) -> Set[str]:
    """
    Return the set of all valid syntax names for a language.

    Args:
        language: 'python', 'py', 'c', 'cpp', or 'c++'

    Returns:
        Set of valid lowercase syntax names
    """
    lang_lower = language.lower()
    if lang_lower in ("python", "py"):
        return set(get_python_syntax_options()["all"])
    elif lang_lower in ("c", "cpp", "c++"):
        return set(get_cpp_syntax_options()["all"])
    return set()


def validate_syntax_values(syntax_list: List[str], language: str) -> List[str]:
    """
    Validate syntax values and return list of invalid ones.

    Args:
        syntax_list: List of syntax values to validate
        language: 'python', 'py', 'c', 'cpp', or 'c++'

    Returns:
        List of invalid syntax values (empty if all valid)
    """
    valid_set = get_valid_syntax_set(language)
    if not valid_set:
        return []  # Unknown language, skip validation
    return [s for s in syntax_list if s.lower() not in valid_set]


def filter_invalid_syntax(syntax_list: List[str], language: str) -> List[str]:
    """
    Filter out invalid syntax values and return only valid ones.

    Args:
        syntax_list: List of syntax values to filter
        language: 'python', 'py', 'c', 'cpp', or 'c++'

    Returns:
        List of valid syntax values only
    """
    valid_set = get_valid_syntax_set(language)
    if not valid_set:
        return syntax_list  # Unknown language, return as-is
    return [s for s in syntax_list if s.lower() in valid_set]
