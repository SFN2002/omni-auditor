"""
Generating lines of code.
"""

import re
import sys
from collections.abc import Collection, Iterator
from dataclasses import replace
from enum import Enum, auto
from functools import partial, wraps
from typing import Union, cast

from black.brackets import (
    COMMA_PRIORITY,
    DOT_PRIORITY,
    STRING_PRIORITY,
    get_leaves_inside_matching_brackets,
    max_delimiter_priority_in_atom,
)
from black.comments import (
    FMT_OFF,
    FMT_ON,
    contains_fmt_directive,
    generate_comments,
    list_comments,
)
from black.lines import (
    Line,
    RHSResult,
    append_leaves,
    can_be_split,
    can_omit_invisible_parens,
    is_line_short_enough,
    line_to_string,
)
from black.mode import Feature, Mode, Preview
from black.nodes import (
    ASSIGNMENTS,
    BRACKETS,
    CLOSING_BRACKETS,
    OPENING_BRACKETS,
    STANDALONE_COMMENT,
    STATEMENT,
    WHITESPACE,
    Visitor,
    ensure_visible,
    fstring_tstring_to_string,
    get_annotation_type,
    has_sibling_with_type,
    is_arith_like,
    is_async_stmt_or_funcdef,
    is_atom_with_invisible_parens,
    is_docstring,
    is_empty_tuple,
    is_generator,
    is_lpar_token,
    is_multiline_string,
    is_name_token,
    is_one_sequence_between,
    is_one_tuple,
    is_parent_function_or_class,
    is_part_of_annotation,
    is_rpar_token,
    is_stub_body,
    is_stub_suite,
    is_tuple,
    is_tuple_containing_star,
    is_tuple_containing_walrus,
    is_type_ignore_comment_string,
    is_vararg,
    is_walrus_assignment,
    is_yield,
    syms,
    wrap_in_parentheses,
)
from black.numerics import normalize_numeric_literal
from black.strings import (
    fix_multiline_docstring,
    get_string_prefix,
    normalize_string_prefix,
    normalize_string_quotes,
    normalize_unicode_escape_sequences,
)
from black.trans import (
    CannotTransform,
    StringMerger,
    StringParenStripper,
    StringParenWrapper,
    StringSplitter,
    Transformer,
    hug_power_op,
)
from blib2to3.pgen2 import token
from blib2to3.pytree import Leaf, Node

# types
LeafID = int
LN = Union[Leaf, Node]


class CannotSplit(CannotTransform):
    """A readable split that fits the allotted line length is impossible."""


# This isn't a dataclass because @dataclass + Generic breaks mypyc.
# See also https://github.com/mypyc/mypyc/issues/827.
class LineGenerator(Visitor[Line]):
    """Generates reformatted Line objects.  Empty lines are not emitted.

    Note: destroys the tree it's visiting by mutating prefixes of its leaves
    in ways that will no longer stringify to valid Python code on the tree.
    """

    def __init__(self, mode: Mode, features: Collection[Feature]) -> None:
        self.mode = mode
        self.features = features
        self.current_line: Line
        self.__post_init__()

    def line(self, indent: int = 0) -> Iterator[Line]:
        """Generate a line.

        If the line is empty, only emit if it makes sense.
        If the line is too long, split it first and then generate.

        If any lines were generated, set up a new current_line.
        """
        if not self.current_line:
            self.current_line.depth += indent
            return  # Line is empty, don't emit. Creating a new one unnecessary.

        if len(self.current_line.leaves) == 1 and is_async_stmt_or_funcdef(
            self.current_line.leaves[0]
        ):
            # Special case for async def/for/with statements. `visit_async_stmt`
            # adds an `ASYNC` leaf then visits the child def/for/with statement
            # nodes. Line yields from those nodes shouldn't treat the former
            # `ASYNC` leaf as a complete line.
            return

        complete_line = self.current_line
        self.current_line = Line(mode=self.mode, depth=complete_line.depth + indent)
        yield complete_line

    def visit_default(self, node: LN) -> Iterator[Line]:
        """Default `visit_*()` implementation. Recurses to children of `node`."""
        if isinstance(node, Leaf):
            any_open_brackets = self.current_line.bracket_tracker.any_open_brackets()
            for comment in generate_comments(node, mode=self.mode):
                if any_open_brackets:
                    # any comment within brackets is subject to splitting
                    self.current_line.append(comment)
                elif comment.type == token.COMMENT:
                    # regular trailing comment
                    self.current_line.append(comment)
                    yield from self.line()

                else:
                    # regular standalone comment
                    yield from self.line()

                    self.current_line.append(comment)
                    yield from self.line()

            if any_open_brackets:
                node.prefix = ""
            if node.type not in WHITESPACE:
                self.current_line.append(node)
        yield from super().visit_default(node)

    def visit_test(self, node: Node) -> Iterator[Line]:
        """Visit an `x if y else z` test"""

        already_parenthesized = (
            node.prev_sibling and node.prev_sibling.type == token.LPAR
        )

        if not already_parenthesized:
            # Similar to logic in wrap_in_parentheses
            lpar = Leaf(token.LPAR, "")
            rpar = Leaf(token.RPAR, "")
            prefix = node.prefix
            node.prefix = ""
            lpar.prefix = prefix
            node.insert_child(0, lpar)
            node.append_child(rpar)

        yield from self.visit_default(node)

    def visit_INDENT(self, node: Leaf) -> Iterator[Line]:
        """Increase indentation level, maybe yield a line."""
        # In blib2to3 INDENT never holds comments.
        yield from self.line(+1)
        yield from self.visit_default(node)

    def visit_DEDENT(self, node: Leaf) -> Iterator[Line]:
        """Decrease indentation level, maybe yield a line."""
        # The current line might still wait for trailing comments.  At DEDENT time
        # there won't be any (they would be prefixes on the preceding NEWLINE).
        # Emit the line then.
        yield from self.line()

        # While DEDENT has no value, its prefix may contain standalone comments
        # that belong to the current indentation level.  Get 'em.
        yield from self.visit_default(node)

        # Finally, emit the dedent.
        yield from self.line(-1)

    def visit_stmt(
        self, node: Node, keywords: set[str], parens: set[str]
    ) -> Iterator[Line]:
        """Visit a statement.

        This implementation is shared for `if`, `while`, `for`, `try`, `except`,
        `def`, `with`, `class`, `assert`, and assignments.

        The relevant Python language `keywords` for a given statement will be
        NAME leaves within it. This methods puts those on a separate line.

        `parens` holds a set of string leaf values immediately after which
        invisible parens should be put.
        """
        normalize_invisible_parens(
            node, parens_after=parens, mode=self.mode, features=self.features
        )
        for child in node.children:
            if is_name_token(child) and child.value in keywords:
                yield from self.line()

            yield from self.visit(child)

    def visit_typeparams(self, node: Node) -> Iterator[Line]:
        yield from self.visit_default(node)
        node.children[0].prefix = ""

    def visit_typevartuple(self, node: Node) -> Iterator[Line]:
        yield from self.visit_default(node)
        node.children[1].prefix = ""

    def visit_paramspec(self, node: Node) -> Iterator[Line]:
        yield from self.visit_default(node)
        node.children[1].prefix = ""

    def visit_dictsetmaker(self, node: Node) -> Iterator[Line]:
        if Preview.wrap_long_dict_values_in_parens in self.mode:
            for i, child in enumerate(node.children):
                if i == 0:
                    continue
                if node.children[i - 1].type == token.COLON:
                    if (
                        child.type == syms.atom
                        and child.children[0].type in OPENING_BRACKETS
                        and not is_walrus_assignment(child)
                    ):
                        maybe_make_parens_invisible_in_atom(
                            child,
                            parent=node,
                            mode=self.mode,
                            features=self.features,
                            remove_brackets_around_comma=False,
                        )
                    else:
                        wrap_in_parentheses(node, child, visible=False)
        yield from self.visit_default(node)

    def visit_funcdef(self, node: Node) -> Iterator[Line]:
        """Visit function definition."""
        yield from self.line()

        # Remove redundant brackets around return type annotation.
        is_return_annotation = False
        for child in node.children:
            if child.type == token.RARROW:
                is_return_annotation = True
            elif is_return_annotation:
                if child.type == syms.atom and child.children[0].type == token.LPAR:
                    if maybe_make_parens_invisible_in_atom(
                        child,
                        parent=node,
                        mode=self.mode,
                        features=self.features,
                        remove_brackets_around_comma=False,
                    ):
                        wrap_in_parentheses(node, child, visible=False)
                else:
                    wrap_in_parentheses(node, child, visible=False)
                is_return_annotation = False

        for child in node.children:
            yield from self.visit(child)

    def visit_match_case(self, node: Node) -> Iterator[Line]:
        """Visit either a match or case statement."""
        normalize_invisible_parens(
            node, parens_after=set(), mode=self.mode, features=self.features
        )

        yield from self.line()
        for child in node.children:
            yield from self.visit(child)

    def visit_suite(self, node: Node) -> Iterator[Line]:
        """Visit a suite."""
        if is_stub_suite(node):
            yield from self.visit(node.children[2])
        else:
            yield from self.visit_default(node)

    def visit_simple_stmt(self, node: Node) -> Iterator[Line]:
        """Visit a statement without nested statements."""
        prev_type: int | None = None
        for child in node.children:
            if (prev_type is None or prev_type == token.SEMI) and is_arith_like(child):
                wrap_in_parentheses(node, child, visible=False)
            prev_type = child.type

        if node.parent and node.parent.type in STATEMENT:
            if is_parent_function_or_class(node) and is_stub_body(node):
                yield from self.visit_default(node)
            else:
                yield from self.line(+1)
                yield from self.visit_default(node)
                yield from self.line(-1)

        else:
            if node.parent and is_stub_suite(node.parent):
                node.prefix = ""
                yield from self.visit_default(node)
                return
            yield from self.line()
            yield from self.visit_default(node)

    def visit_async_stmt(self, node: Node) -> Iterator[Line]:
        """Visit `async def`, `async for`, `async with`."""
        yield from self.line()

        children = iter(node.children)
        for child in children:
            yield from self.visit(child)

            if child.type == token.ASYNC or child.type == STANDALONE_COMMENT:
                # STANDALONE_COMMENT happens when `# fmt: skip` is applied on the async
                # line.
                break

        internal_stmt = next(children)
        yield from self.visit(internal_stmt)

    def visit_decorators(self, node: Node) -> Iterator[Line]:
        """Visit decorators."""
        for child in node.children:
            yield from self.line()
            yield from self.visit(child)

    def visit_power(self, node: Node) -> Iterator[Line]:
        for idx, leaf in enumerate(node.children[:-1]):
            next_leaf = node.children[idx + 1]

            if not isinstance(leaf, Leaf):
                continue

            value = leaf.value.lower()
            if (
                leaf.type == token.NUMBER
                and next_leaf.type == syms.trailer
                # Ensure that we are in an attribute trailer
                and next_leaf.children[0].type == token.DOT
                # It shouldn't wrap hexadecimal, binary and octal literals
                and not value.startswith(("0x", "0b", "0o"))
                # It shouldn't wrap complex literals
                and "j" not in value
            ):
                wrap_in_parentheses(node, leaf)

        remove_await_parens(node, mode=self.mode, features=self.features)

        yield from self.visit_default(node)

    def visit_SEMI(self, leaf: Leaf) -> Iterator[Line]:
        """Remove a semicolon and put the other statement on a separate line."""
        yield from self.line()

    def visit_ENDMARKER(self, leaf: Leaf) -> Iterator[Line]:
        """End of file. Process outstanding comments and end with a newline."""
        yield from self.visit_default(leaf)
        yield from self.line()

    def visit_STANDALONE_COMMENT(self, leaf: Leaf) -> Iterator[Line]:
        any_open_brackets = self.current_line.bracket_tracker.any_open_brackets()
        if not any_open_brackets:
            yield from self.line()
        # STANDALONE_COMMENT nodes created by our special handling in
        # normalize_fmt_off for comment-only blocks have fmt:off as the first
        # line and fmt:on as the last line (each directive on its own line,
        # not embedded in other text). These should be appended directly
        # without calling visit_default, which would process their prefix and
        # lose indentation. Normal STANDALONE_COMMENT nodes go through
        # visit_default.
        value = leaf.value
        lines = value.splitlines()
        is_fmt_off_block = (
            len(lines) >= 2
            and contains_fmt_directive(lines[0], FMT_OFF)
            and contains_fmt_directive(lines[-1], FMT_ON)
        )
        if is_fmt_off_block:
            # This is a fmt:off/on block from normalize_fmt_off - we still need
            # to process any prefix comments (like markdown comments) but append
            # the fmt block itself directly to preserve its formatting

            # Only process prefix comments if there actually is a prefix with comments
            if leaf.prefix and any(
                line.strip().startswith("#")
                and not contains_fmt_directive(line.strip())
                for line in leaf.prefix.split("\n")
            ):
                for comment in generate_comments(leaf, mode=self.mode):
                    yield from self.line()
                    self.current_line.append(comment)
                    yield from self.line()
                # Clear the prefix since we've processed it as comments above
                leaf.prefix = ""

            self.current_line.append(leaf)
            if not any_open_brackets:
                yield from self.line()
        else:
            # Normal standalone comment - process through visit_default
            yield from self.visit_default(leaf)

    def visit_factor(self, node: Node) -> Iterator[Line]:
        """Force parentheses between a unary op and a binary power:

        -2 ** 8 -> -(2 ** 8)
        """
        _operator, operand = node.children
        if (
            operand.type == syms.power
            and len(operand.children) == 3
            and operand.children[1].type == token.DOUBLESTAR
        ):
            lpar = Leaf(token.LPAR, "(")
            rpar = Leaf(token.RPAR, ")")
            index = operand.remove() or 0
            node.insert_child(index, Node(syms.atom, [lpar, operand, rpar]))
        yield from self.visit_default(node)

    def visit_tname(self, node: Node) -> Iterator[Line]:
        """
        Add potential parentheses around types in function parameter lists to be made
        into real parentheses in case the type hint is too long to fit on a line
        Examples:
        def foo(a: int, b: float = 7): ...

        ->

        def foo(a: (int), b: (float) = 7): ...
        """
        if len(node.children) == 3 and maybe_make_parens_invisible_in_atom(
            node.children[2], parent=node, mode=self.mode, features=self.features
        ):
            wrap_in_parentheses(node, node.children[2], visible=False)

        yield from self.visit_default(node)

    def visit_STRING(self, leaf: Leaf) -> Iterator[Line]:
        normalize_unicode_escape_sequences(leaf)

        if is_docstring(leaf) and not re.search(r"\\\s*\n", leaf.value):
            # We're ignoring docstrings with backslash newline escapes because changing
            # indentation of those changes the AST representation of the code.
            if self.mode.string_normalization:
                docstring = normalize_string_prefix(leaf.value)
                # We handle string normalization at the end of this method, but since
                # what we do right now acts differently depending on quote style (ex.
                # see padding logic below), there's a possibility for unstable
                # formatting. To avoid a situation where this function formats a
                # docstring differently on the second pass, normalize it early.
                docstring = normalize_string_quotes(docstring)
            else:
                docstring = leaf.value
            prefix = get_string_prefix(docstring)
            docstring = docstring[len(prefix) :]  # Remove the prefix
            quote_char = docstring[0]
            # A natural way to remove the outer quotes is to do:
            #   docstring = docstring.strip(quote_char)
            # but that breaks on """""x""" (which is '""x').
            # So we actually need to remove the first character and the next two
            # characters but only if they are the same as the first.
            quote_len = 1 if docstring[1] != quote_char else 3
            docstring = docstring[quote_len:-quote_len]
            docstring_started_empty = not docstring
            indent = " " * 4 * self.current_line.depth

            if is_multiline_string(leaf):
                docstring = fix_multiline_docstring(docstring, indent)
            else:
                docstring = docstring.strip()

            has_trailing_backslash = False
            if docstring:
                # Add some padding if the docstring starts / ends with a quote mark.
                if docstring[0] == quote_char:
                    docstring = " " + docstring
                if docstring[-1] == quote_char:
                    docstring += " "
                if docstring[-1] == "\\":
                    backslash_count = len(docstring) - len(docstring.rstrip("\\"))
                    if backslash_count % 2:
                        # Odd number of tailing backslashes, add some padding to
                        # avoid escaping the closing string quote.
                        docstring += " "
                        has_trailing_backslash = True
            elif not docstring_started_empty:
                docstring = " "

            # We could enforce triple quotes at this point.
            quote = quote_char * quote_len

            # It's invalid to put closing single-character quotes on a new line.
            if quote_len == 3:
                # We need to find the length of the last line of the docstring
                # to find if we can add the closing quotes to the line without
                # exceeding the maximum line length.
                # If docstring is one line, we don't put the closing quotes on a
                # separate line because it looks ugly (#3320).
                lines = docstring.splitlines()
                last_line_length = len(lines[-1]) if docstring else 0

                # If adding closing quotes would cause the last line to exceed
                # the maximum line length, and the closing quote is not
                # prefixed by a newline then put a line break before
                # the closing quotes
                if (
                    len(lines) > 1
                    and last_line_length + quote_len > self.mode.line_length
                    and len(indent) + quote_len <= self.mode.line_length
                    and not has_trailing_backslash
                ):
                    if leaf.value[-1 - quote_len] == "\n":
                        leaf.value = prefix + quote + docstring + quote
                    else:
                        leaf.value = prefix + quote + docstring + "\n" + indent + quote
                else:
                    leaf.value = prefix + quote + docstring + quote
            else:
                leaf.value = prefix + quote + docstring + quote

        if self.mode.string_normalization and leaf.type == token.STRING:
            leaf.value = normalize_string_prefix(leaf.value)
            leaf.value = normalize_string_quotes(leaf.value)
        yield from self.visit_default(leaf)

    def visit_NUMBER(self, leaf: Leaf) -> Iterator[Line]:
        normalize_numeric_literal(leaf)
        yield from self.visit_default(leaf)

    def visit_atom(self, node: Node) -> Iterator[Line]:
        """Visit any atom"""
        if len(node.children) == 3:
            first = node.children[0]
            last = node.children[-1]
            if (first.type == token.LSQB and last.type == token.RSQB) or (
                first.type == token.LBRACE and last.type == token.RBRACE
            ):
                # Lists or sets of one item
                maybe_make_parens_invisible_in_atom(
                    node.children[1],
                    parent=node,
                    mode=self.mode,
                    features=self.features,
                )

        yield from self.visit_default(node)

    def visit_fstring(self, node: Node) -> Iterator[Line]:
        # currently we don't want to format and split f-strings at all.
        string_leaf = fstring_tstring_to_string(node)
        node.replace(string_leaf)
        if "\\" in string_leaf.value and any(
            "\\" in str(child)
            for child in node.children
            if child.type == syms.fstring_replacement_field
        ):
            # string normalization doesn't account for nested quotes,
            # causing breakages. skip normalization when nested quotes exist
            yield from self.visit_default(string_leaf)
            return
        yield from self.visit_STRING(string_leaf)

    def visit_tstring(self, node: Node) -> Iterator[Line]:
        # currently we don't want to format and split t-strings at all.
        string_leaf = fstring_tstring_to_string(node)
        node.replace(string_leaf)
        if "\\" in string_leaf.value and any(
            "\\" in str(child)
            for child in node.children
            if child.type == syms.fstring_replacement_field
        ):
            # string normalization doesn't account for nested quotes,
            # causing breakages. skip normalization when nested quotes exist
            yield from self.visit_default(string_leaf)
            return
        yield from self.visit_STRING(string_leaf)

        # TODO: Uncomment Implementation to format f-string children
        # fstring_start = node.children[0]
        # fstring_end = node.children[-1]
        # assert isinstance(fstring_start, Leaf)
        # assert isinstance(fstring_end, Leaf)

        # quote_char = fstring_end.value[0]
        # quote_idx = fstring_start.value.index(quote_char)
        # prefix, quote = (
        #     fstring_start.value[:quote_idx],
        #     fstring_start.value[quote_idx:]
        # )

        # if not is_docstring(node, self.mode):
        #     prefix = normalize_string_prefix(prefix)

        # assert quote == fstring_end.value

        # is_raw_fstring = "r" in prefix or "R" in prefix
        # middles = [
        #     leaf
        #     for leaf in node.leaves()
        #     if leaf.type == token.FSTRING_MIDDLE
        # ]

        # if self.mode.string_normalization:
        #     middles, quote = normalize_fstring_quotes(quote, middles, is_raw_fstring)

        # fstring_start.value = prefix + quote
        # fstring_end.value = quote

        # yield from self.visit_default(node)

    def visit_comp_for(self, node: Node) -> Iterator[Line]:
        if Preview.wrap_comprehension_in in self.mode:
            normalize_invisible_parens(
                node, parens_after={"in"}, mode=self.mode, features=self.features
            )
        yield from self.visit_default(node)

    def visit_old_comp_for(self, node: Node) -> Iterator[Line]:
        yield from self.visit_comp_for(node)

    def __post_init__(self) -> None:
        """You are in a twisty little maze of passages."""
        self.current_line = Line(mode=self.mode)

        v = self.visit_stmt
        Ø: set[str] = set()
        self.visit_assert_stmt = partial(v, keywords={"assert"}, parens={"assert", ","})
        self.visit_if_stmt = partial(
            v, keywords={"if", "else", "elif"}, parens={"if", "elif"}
        )
        self.visit_while_stmt = partial(v, keywords={"while", "else"}, parens={"while"})
        self.visit_for_stmt = partial(v, keywords={"for", "else"}, parens={"for", "in"})
        self.visit_try_stmt = partial(
            v, keywords={"try", "except", "else", "finally"}, parens=Ø
        )
        self.visit_except_clause = partial(v, keywords={"except"}, parens={"except"})
        self.visit_with_stmt = partial(v, keywords={"with"}, parens={"with"})
        self.visit_classdef = partial(v, keywords={"class"}, parens=Ø)

        self.visit_expr_stmt = partial(v, keywords=Ø, parens=ASSIGNMENTS)
        self.visit_return_stmt = partial(v, keywords={"return"}, parens={"return"})
        self.visit_import_from = partial(v, keywords=Ø, parens={"import"})
        self.visit_del_stmt = partial(v, keywords=Ø, parens={"del"})
        self.visit_async_funcdef = self.visit_async_stmt
        self.visit_decorated = self.visit_decorators

        # PEP 634
        self.visit_match_stmt = self.visit_match_case
        self.visit_case_block = self.visit_match_case
        self.visit_guard = partial(v, keywords=Ø, parens={"if"})


# Remove when `simplify_power_operator_hugging` becomes stable.
def _hugging_power_ops_line_to_string(
    line: Line,
    features: Collection[Feature],
    mode: Mode,
) -> str | None:
    try:
        return line_to_string(next(hug_power_op(line, features, mode)))
    except CannotTransform:
        return None


def transform_line(
    line: Line, mode: Mode, features: Collection[Feature] = ()
) -> Iterator[Line]:
    """Transform a `line`, potentially splitting it into many lines.

    They should fit in the allotted `line_length` but might not be able to.

    `features` are syntactical features that may be used in the output.
    """
    if line.is_comment:
        yield line
        return

    line_str = line_to_string(line)

    if Preview.simplify_power_operator_hugging in mode:
        line_str_hugging_power_ops = line_str
    else:
        # We need the line string when power operators are hugging to determine if we
        # should split the line. Default to line_str, if no power operator are present
        # on the line.
        line_str_hugging_power_ops = (
            _hugging_power_ops_line_to_string(line, features, mode) or line_str
        )

    ll = mode.line_length
    sn = mode.string_normalization
    string_merge = StringMerger(ll, sn)
    string_paren_strip = StringParenStripper(ll, sn)
    string_split = StringSplitter(ll, sn)
    string_paren_wrap = StringParenWrapper(ll, sn)

    transformers: list[Transformer]
    if (
        not line.contains_uncollapsable_type_comments()
        and not line.should_split_rhs
        and not line.magic_trailing_comma
        and (
            is_line_short_enough(line, mode=mode, line_str=line_str_hugging_power_ops)
            or line.contains_unsplittable_type_ignore()
        )
        and not (line.inside_brackets and line.contains_standalone_comments())
        and not line.contains_implicit_multiline_string_with_comments()
    ):
        # Only apply basic string preprocessing, since lines shouldn't be split here.
        if Preview.string_processing in mode:
            transformers = [string_merge, string_paren_strip]
        else:
            transformers = []
    elif line.is_def and not should_split_funcdef_with_rhs(line, mode):
        transformers = [left_hand_split]
    else:

        def _rhs(
            self: object, line: Line, features: Collection[Feature], mode: Mode
        ) -> Iterator[Line]:
            """Wraps calls to `right_hand_split`.

            The calls increasingly `omit` right-hand trailers (bracket pairs with
            content), meaning the trailers get glued together to split on another
            bracket pair instead.
            """
            for omit in generate_trailers_to_omit(line, mode.line_length):
                lines = list(right_hand_split(line, mode, features, omit=omit))
                # Note: this check is only able to figure out if the first line of the
                # *current* transformation fits in the line length.  This is true only
                # for simple cases.  All others require running more transforms via
                # `transform_line()`.  This check doesn't know if those would succeed.
                if is_line_short_enough(lines[0], mode=mode):
                    yield from lines
                    return

            # All splits failed, best effort split with no omits.
            # This mostly happens to multiline strings that are by definition
            # reported as not fitting a single line, as well as lines that contain
            # trailing commas (those have to be exploded).
            yield from right_hand_split(line, mode, features=features)

        # HACK: nested functions (like _rhs) compiled by mypyc don't retain their
        # __name__ attribute which is needed in `run_transformer` further down.
        # Unfortunately a nested class breaks mypyc too. So a class must be created
        # via type ... https://github.com/mypyc/mypyc/issues/884
        rhs = type("rhs", (), {"__call__": _rhs})()

        if Preview.string_processing in mode:
            if line.inside_brackets:
                transformers = [
                    string_merge,
                    string_paren_strip,
                    string_split,
                    delimiter_split,
                    standalone_comment_split,
                    string_paren_wrap,
                    rhs,
                ]
            else:
                transformers = [
                    string_merge,
                    string_paren_strip,
                    string_split,
                    string_paren_wrap,
                    rhs,
                ]
        else:
            if line.inside_brackets:
                transformers = [delimiter_split, standalone_comment_split, rhs]
            else:
                transformers = [rhs]

    if Preview.simplify_power_operator_hugging not in mode:
        # It's always safe to attempt hugging of power operations and pretty much every
        # line could match.
        transformers.append(hug_power_op)

    for transform in transformers:
        # We are accumulating lines in `result` because we might want to abort
        # mission and return the original line in the end, or attempt a different
        # split altogether.
        try:
            result = run_transformer(line, transform, mode, features, line_str=line_str)
        except CannotTransform:
            continue
        else:
            yield from result
            break

    else:
        yield line


def should_split_funcdef_with_rhs(line: Line, mode: Mode) -> bool:
    """If a funcdef has a magic trailing comma in the return type, then we should first
    split the line with rhs to respect the comma.
    """
    return_type_leaves: list[Leaf] = []
    in_return_type = False

    for leaf in line.leaves:
        if leaf.type == token.COLON:
            in_return_type = False
        if in_return_type:
            return_type_leaves.append(leaf)
        if leaf.type == token.RARROW:
            in_return_type = True

    # using `bracket_split_build_line` will mess with whitespace, so we duplicate a
    # couple lines from it.
    result = Line(mode=line.mode, depth=line.depth)
    leaves_to_track = get_leaves_inside_matching_brackets(return_type_leaves)
    for leaf in return_type_leaves:
        result.append(
            leaf,
            preformatted=True,
            track_bracket=id(leaf) in leaves_to_track,
        )

    # we could also return true if the line is too long, and the return type is longer
    # than the param list. Or if `should_split_rhs` returns True.
    return result.magic_trailing_comma is not None


class _BracketSplitComponent(Enum):
    head = auto()
    body = auto()
    tail = auto()


def left_hand_split(
    line: Line, _features: Collection[Feature], mode: Mode
) -> Iterator[Line]:
    """Split line into many lines, starting with the first matching bracket pair.

    Note: this usually looks weird, only use this for function definitions.
    Prefer RHS otherwise.  This is why this function is not symmetrical with
    :func:`right_hand_split` which also handles optional parentheses.
    """
    for leaf_type in [token.LPAR, token.LSQB]:
        tail_leaves: list[Leaf] = []
        body_leaves: list[Leaf] = []
        head_leaves: list[Leaf] = []
        current_leaves = head_leaves
        matching_bracket: Leaf | None = None
        depth = 0
        for index, leaf in enumerate(line.leaves):
            if index == 2 and leaf.type == token.LSQB:
                # A [ at index 2 means this is a type param, so start
                # tracking the depth
                depth += 1
            elif depth > 0:
                if leaf.type == token.LSQB:
                    depth += 1
                elif leaf.type == token.RSQB:
                    depth -= 1
            if (
                current_leaves is body_leaves
                and leaf.type in CLOSING_BRACKETS
                and leaf.opening_bracket is matching_bracket
                and isinstance(matching_bracket, Leaf)
                # If the code is still on LPAR and we are inside a type
                # param, ignore the match since this is searching
                # for the function arguments
                and not (leaf_type == token.LPAR and depth > 0)
            ):
                ensure_visible(leaf)
                ensure_visible(matching_bracket)
                current_leaves = tail_leaves if body_leaves else head_leaves
            current_leaves.append(leaf)
            if current_leaves is head_leaves:
                if leaf.type == leaf_type and (
                    not (leaf_type == token.LPAR and depth > 0)
                ):
                    matching_bracket = leaf
                    current_leaves = body_leaves
        if matching_bracket and tail_leaves:
            break
    if not matching_bracket or not tail_leaves:
        raise CannotSplit("No brackets found")

    head = bracket_split_build_line(
        head_leaves, line, matching_bracket, component=_BracketSplitComponent.head
    )
    body = bracket_split_build_line(
        body_leaves, line, matching_bracket, component=_BracketSplitComponent.body
    )
    tail = bracket_split_build_line(
        tail_leaves, line, matching_bracket, component=_BracketSplitComponent.tail
    )
    bracket_split_succeeded_or_raise(head, body, tail)
    for result in (head, body, tail):
        if result:
            yield result


def right_hand_split(
    line: Line,
    mode: Mode,
    features: Collection[Feature] = (),
    omit: Collection[LeafID] = (),
) -> Iterator[Line]:
    """Split line into many lines, starting with the last matching bracket pair.

    If the split was by optional parentheses, attempt splitting without them, too.
    `omit` is a collection of closing bracket IDs that shouldn't be considered for
    this split.

    Note: running this function modifies `bracket_depth` on the leaves of `line`.
    """
    rhs_result = _first_right_hand_split(line, omit=omit)
    yield from _maybe_split_omitting_optional_parens(
        rhs_result, line, mode, features=features, omit=omit
    )


def _first_right_hand_split(
    line: Line,
    omit: Collection[LeafID] = (),
) -> RHSResult:
    """Split the line into head, body, tail starting with the last bracket pair.

    Note: this function should not have side effects. It's relied upon by
    _maybe_split_omitting_optional_parens to get an opinion whether to prefer
    splitting on the right side of an assignment statement.
    """
    tail_leaves: list[Leaf] = []
    body_leaves: list[Leaf] = []
    head_leaves: list[Leaf] = []
    current_leaves = tail_leaves
    opening_bracket: Leaf | None = None
    closing_bracket: Leaf | None = None
    for leaf in reversed(line.leaves):
        if current_leaves is body_leaves:
            if leaf is opening_bracket:
                current_leaves = head_leaves if body_leaves else tail_leaves
        current_leaves.append(leaf)
        if current_leaves is tail_leaves:
            if leaf.type in CLOSING_BRACKETS and id(leaf) not in omit:
                opening_bracket = leaf.opening_bracket
                closing_bracket = leaf
                current_leaves = body_leaves
    if not (opening_bracket and closing_bracket and head_leaves):
        # If there is no opening or closing_bracket that means the split failed and
        # all content is in the tail.  Otherwise, if `head_leaves` are empty, it means
        # the matching `opening_bracket` wasn't available on `line` anymore.
        raise CannotSplit("No brackets found")

    tail_leaves.reverse()
    body_leaves.reverse()
    head_leaves.reverse()

    body: Line | None = None
    if (
        Preview.hug_parens_with_braces_and_square_brackets in line.mode
        and tail_leaves[0].value
        and tail_leaves[0].opening_bracket is head_leaves[-1]
    ):
        inner_body_leaves = list(body_leaves)
        hugged_opening_leaves: list[Leaf] = []
        hugged_closing_leaves: list[Leaf] = []
        is_unpacking = body_leaves[0].type in [token.STAR, token.DOUBLESTAR]
        unpacking_offset: int = 1 if is_unpacking else 0
        while (
            len(inner_body_leaves) >= 2 + unpacking_offset
            and inner_body_leaves[-1].type in CLOSING_BRACKETS
            and inner_body_leaves[-1].opening_bracket
            is inner_body_leaves[unpacking_offset]
        ):
            if unpacking_offset:
