"""
String transformers that can split and merge strings.
"""

import re
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Literal, TypeVar, Union

from mypy_extensions import trait

from black.comments import contains_pragma_comment
from black.lines import Line, append_leaves
from black.mode import Feature, Mode
from black.nodes import (
    CLOSING_BRACKETS,
    OPENING_BRACKETS,
    STANDALONE_COMMENT,
    is_empty_lpar,
    is_empty_par,
    is_empty_rpar,
    is_part_of_annotation,
    parent_type,
    replace_child,
    syms,
)
from black.rusty import Err, Ok, Result
from black.strings import (
    assert_is_leaf_string,
    count_chars_in_width,
    get_string_prefix,
    has_triple_quotes,
    normalize_string_quotes,
    str_width,
)
from blib2to3.pgen2 import token
from blib2to3.pytree import Leaf, Node


class CannotTransform(Exception):
    """Base class for errors raised by Transformers."""


# types
T = TypeVar("T")
LN = Union[Leaf, Node]
Transformer = Callable[[Line, Collection[Feature], Mode], Iterator[Line]]
Index = int
NodeType = int
ParserState = int
StringID = int
TResult = Result[T, CannotTransform]  # (T)ransform Result
TMatchResult = TResult[list[Index]]

SPLIT_SAFE_CHARS = frozenset(["\u3001", "\u3002", "\uff0c"])  # East Asian stops


def TErr(err_msg: str) -> Err[CannotTransform]:
    """(T)ransform Err

    Convenience function used when working with the TResult type.
    """
    cant_transform = CannotTransform(err_msg)
    return Err(cant_transform)


# Remove when `simplify_power_operator_hugging` becomes stable.
def hug_power_op(
    line: Line, features: Collection[Feature], mode: Mode
) -> Iterator[Line]:
    """A transformer which normalizes spacing around power operators."""

    # Performance optimization to avoid unnecessary Leaf clones and other ops.
    for leaf in line.leaves:
        if leaf.type == token.DOUBLESTAR:
            break
    else:
        raise CannotTransform("No doublestar token was found in the line.")

    def is_simple_lookup(index: int, kind: Literal[1, -1]) -> bool:
        # Brackets and parentheses indicate calls, subscripts, etc. ...
        # basically stuff that doesn't count as "simple". Only a NAME lookup
        # or dotted lookup (eg. NAME.NAME) is OK.
        if kind == -1:
            return handle_is_simple_look_up_prev(line, index, {token.RPAR, token.RSQB})
        else:
            return handle_is_simple_lookup_forward(
                line, index, {token.LPAR, token.LSQB}
            )

    def is_simple_operand(index: int, kind: Literal[1, -1]) -> bool:
        # An operand is considered "simple" if's a NAME, a numeric CONSTANT, a simple
        # lookup (see above), with or without a preceding unary operator.
        start = line.leaves[index]
        if start.type in {token.NAME, token.NUMBER}:
            return is_simple_lookup(index, kind)

        if start.type in {token.PLUS, token.MINUS, token.TILDE}:
            if line.leaves[index + 1].type in {token.NAME, token.NUMBER}:
                # kind is always one as bases with a preceding unary op will be checked
                # for simplicity starting from the next token (so it'll hit the check
                # above).
                return is_simple_lookup(index + 1, kind=1)

        return False

    new_line = line.clone()
    should_hug = False
    for idx, leaf in enumerate(line.leaves):
        new_leaf = leaf.clone()
        if should_hug:
            new_leaf.prefix = ""
            should_hug = False

        should_hug = (
            (0 < idx < len(line.leaves) - 1)
            and leaf.type == token.DOUBLESTAR
            and is_simple_operand(idx - 1, kind=-1)
            and line.leaves[idx - 1].value != "lambda"
            and is_simple_operand(idx + 1, kind=1)
        )
        if should_hug:
            new_leaf.prefix = ""

        # We have to be careful to make a new line properly:
        # - bracket related metadata must be maintained (handled by Line.append)
        # - comments need to copied over, updating the leaf IDs they're attached to
        new_line.append(new_leaf, preformatted=True)
        for comment_leaf in line.comments_after(leaf):
            new_line.append(comment_leaf, preformatted=True)

    yield new_line


# Remove when `simplify_power_operator_hugging` becomes stable.
def handle_is_simple_look_up_prev(line: Line, index: int, disallowed: set[int]) -> bool:
    """
    Handling the determination of is_simple_lookup for the lines prior to the doublestar
    token. This is required because of the need to isolate the chained expression
    to determine the bracket or parenthesis belong to the single expression.
    """
    contains_disallowed = False
    chain = []

    while 0 <= index < len(line.leaves):
        current = line.leaves[index]
        chain.append(current)
        if not contains_disallowed and current.type in disallowed:
            contains_disallowed = True
        if not is_expression_chained(chain):
            return not contains_disallowed

        index -= 1

    return True


# Remove when `simplify_power_operator_hugging` becomes stable.
def handle_is_simple_lookup_forward(
    line: Line, index: int, disallowed: set[int]
) -> bool:
    """
    Handling decision is_simple_lookup for the lines behind the doublestar token.
    This function is simplified to keep consistent with the prior logic and the forward
    case are more straightforward and do not need to care about chained expressions.
    """
    while 0 <= index < len(line.leaves):
        current = line.leaves[index]
        if current.type in disallowed:
            return False
        if current.type not in {token.NAME, token.DOT} or (
            current.type == token.NAME and current.value == "for"
        ):
            # If the current token isn't disallowed, we'll assume this is simple as
            # only the disallowed tokens are semantically attached to this lookup
            # expression we're checking. Also, stop early if we hit the 'for' bit
            # of a comprehension.
            return True

        index += 1

    return True


# Remove when `simplify_power_operator_hugging` becomes stable.
def is_expression_chained(chained_leaves: list[Leaf]) -> bool:
    """
    Function to determine if the variable is a chained call.
    (e.g., foo.lookup, foo().lookup, (foo.lookup())) will be recognized as chained call)
    """
    if len(chained_leaves) < 2:
        return True

    current_leaf = chained_leaves[-1]
    past_leaf = chained_leaves[-2]

    if past_leaf.type == token.NAME:
        return current_leaf.type in {token.DOT}
    elif past_leaf.type in {token.RPAR, token.RSQB}:
        return current_leaf.type in {token.RSQB, token.RPAR}
    elif past_leaf.type in {token.LPAR, token.LSQB}:
        return current_leaf.type in {token.NAME, token.LPAR, token.LSQB}
    else:
        return False


class StringTransformer(ABC):
    """
    An implementation of the Transformer protocol that relies on its
    subclasses overriding the template methods `do_match(...)` and
    `do_transform(...)`.

    This Transformer works exclusively on strings (for example, by merging
    or splitting them).

    The following sections can be found among the docstrings of each concrete
    StringTransformer subclass.

    Requirements:
        Which requirements must be met of the given Line for this
        StringTransformer to be applied?

    Transformations:
        If the given Line meets all of the above requirements, which string
        transformations can you expect to be applied to it by this
        StringTransformer?

    Collaborations:
        What contractual agreements does this StringTransformer have with other
        StringTransfomers? Such collaborations should be eliminated/minimized
        as much as possible.
    """

    __name__: Final = "StringTransformer"

    # Ideally this would be a dataclass, but unfortunately mypyc breaks when used with
    # `abc.ABC`.
    def __init__(self, line_length: int, normalize_strings: bool) -> None:
        self.line_length = line_length
        self.normalize_strings = normalize_strings

    @abstractmethod
    def do_match(self, line: Line) -> TMatchResult:
        """
        Returns:
            * Ok(string_indices) such that for each index, `line.leaves[index]`
              is our target string if a match was able to be made. For
              transformers that don't result in more lines (e.g. StringMerger,
              StringParenStripper), multiple matches and transforms are done at
              once to reduce the complexity.
              OR
            * Err(CannotTransform), if no match could be made.
        """

    @abstractmethod
    def do_transform(
        self, line: Line, string_indices: list[int]
    ) -> Iterator[TResult[Line]]:
        """
        Yields:
            * Ok(new_line) where new_line is the new transformed line.
              OR
            * Err(CannotTransform) if the transformation failed for some reason. The
              `do_match(...)` template method should usually be used to reject
              the form of the given Line, but in some cases it is difficult to
              know whether or not a Line meets the StringTransformer's
              requirements until the transformation is already midway.

        Side Effects:
            This method should NOT mutate @line directly, but it MAY mutate the
            Line's underlying Node structure. (WARNING: If the underlying Node
            structure IS altered, then this method should NOT be allowed to
            yield an CannotTransform after that point.)
        """

    def __call__(
        self, line: Line, _features: Collection[Feature], _mode: Mode
    ) -> Iterator[Line]:
        """
        StringTransformer instances have a call signature that mirrors that of
        the Transformer type.

        Raises:
            CannotTransform(...) if the concrete StringTransformer class is unable
            to transform @line.
        """
        # Optimization to avoid calling `self.do_match(...)` when the line does
        # not contain any string.
        if not any(leaf.type == token.STRING for leaf in line.leaves):
            raise CannotTransform("There are no strings in this line.")

        match_result = self.do_match(line)

        if isinstance(match_result, Err):
            cant_transform = match_result.err()
            raise CannotTransform(
                f"The string transformer {self.__class__.__name__} does not recognize"
                " this line as one that it can transform."
            ) from cant_transform

        string_indices = match_result.ok()

        for line_result in self.do_transform(line, string_indices):
            if isinstance(line_result, Err):
                cant_transform = line_result.err()
                raise CannotTransform(
                    "StringTransformer failed while attempting to transform string."
                ) from cant_transform
            line = line_result.ok()
            yield line


@dataclass
class CustomSplit:
    """A custom (i.e. manual) string split.

    A single CustomSplit instance represents a single substring.

    Examples:
        Consider the following string:
        ```
        "Hi there friend."
        " This is a custom"
        f" string {split}."
        ```

        This string will correspond to the following three CustomSplit instances:
        ```
        CustomSplit(False, 16)
        CustomSplit(False, 17)
        CustomSplit(True, 16)
        ```
    """

    has_prefix: bool
    break_idx: int


CustomSplitMapKey = tuple[StringID, str]


@trait
class CustomSplitMapMixin:
    """
    This mixin class is used to map merged strings to a sequence of
    CustomSplits, which will then be used to re-split the strings iff none of
    the resultant substrings go over the configured max line length.
    """

    _CUSTOM_SPLIT_MAP: ClassVar[dict[CustomSplitMapKey, tuple[CustomSplit, ...]]] = (
        defaultdict(tuple)
    )

    @staticmethod
    def _get_key(string: str) -> CustomSplitMapKey:
        """
        Returns:
            A unique identifier that is used internally to map @string to a
            group of custom splits.
        """
        return (id(string), string)

    def add_custom_splits(
        self, string: str, custom_splits: Iterable[CustomSplit]
    ) -> None:
        """Custom Split Map Setter Method

        Side Effects:
            Adds a mapping from @string to the custom splits @custom_splits.
        """
        key = self._get_key(string)
        self._CUSTOM_SPLIT_MAP[key] = tuple(custom_splits)

    def pop_custom_splits(self, string: str) -> list[CustomSplit]:
        """Custom Split Map Getter Method

        Returns:
            * A list of the custom splits that are mapped to @string, if any
              exist.
              OR
            * [], otherwise.

        Side Effects:
            Deletes the mapping between @string and its associated custom
            splits (which are returned to the caller).
        """
        key = self._get_key(string)

        custom_splits = self._CUSTOM_SPLIT_MAP[key]
        del self._CUSTOM_SPLIT_MAP[key]

        return list(custom_splits)

    def has_custom_splits(self, string: str) -> bool:
        """
        Returns:
            True iff @string is associated with a set of custom splits.
        """
        key = self._get_key(string)
        return key in self._CUSTOM_SPLIT_MAP


class StringMerger(StringTransformer, CustomSplitMapMixin):
    """StringTransformer that merges strings together.

    Requirements:
        (A) The line contains adjacent strings such that ALL of the validation checks
        listed in StringMerger._validate_msg(...)'s docstring pass.
        OR
        (B) The line contains a string which uses line continuation backslashes.

    Transformations:
        Depending on which of the two requirements above where met, either:

        (A) The string group associated with the target string is merged.
        OR
        (B) All line-continuation backslashes are removed from the target string.

    Collaborations:
        StringMerger provides custom split information to StringSplitter.
    """

    def do_match(self, line: Line) -> TMatchResult:
        LL = line.leaves

        is_valid_index = is_valid_index_factory(LL)

        string_indices = []
        idx = 0
        while is_valid_index(idx):
            leaf = LL[idx]
            if (
                leaf.type == token.STRING
                and is_valid_index(idx + 1)
                and LL[idx + 1].type == token.STRING
            ):
                # Let's check if the string group contains an inline comment
                # If we have a comment inline, we don't merge the strings
                contains_comment = False
                i = idx
                while is_valid_index(i):
                    if LL[i].type != token.STRING:
                        break
                    if line.comments_after(LL[i]):
                        contains_comment = True
                        break
                    i += 1

                if not contains_comment and not is_part_of_annotation(leaf):
                    string_indices.append(idx)

                # Advance to the next non-STRING leaf.
                idx += 2
                while is_valid_index(idx) and LL[idx].type == token.STRING:
                    idx += 1

            elif leaf.type == token.STRING and "\\\n" in leaf.value:
                string_indices.append(idx)
                # Advance to the next non-STRING leaf.
                idx += 1
                while is_valid_index(idx) and LL[idx].type == token.STRING:
                    idx += 1

            else:
                idx += 1

        if string_indices:
            return Ok(string_indices)
        else:
            return TErr("This line has no strings that need merging.")

    def do_transform(
        self, line: Line, string_indices: list[int]
    ) -> Iterator[TResult[Line]]:
        new_line = line

        rblc_result = self._remove_backslash_line_continuation_chars(
            new_line, string_indices
        )
        if isinstance(rblc_result, Ok):
            new_line = rblc_result.ok()

        msg_result = self._merge_string_group(new_line, string_indices)
        if isinstance(msg_result, Ok):
            new_line = msg_result.ok()

        if isinstance(rblc_result, Err) and isinstance(msg_result, Err):
            msg_cant_transform = msg_result.err()
            rblc_cant_transform = rblc_result.err()
            cant_transform = CannotTransform(
                "StringMerger failed to merge any strings in this line."
            )

            # Chain the errors together using `__cause__`.
            msg_cant_transform.__cause__ = rblc_cant_transform
            cant_transform.__cause__ = msg_cant_transform

            yield Err(cant_transform)
        else:
            yield Ok(new_line)

    @staticmethod
    def _remove_backslash_line_continuation_chars(
        line: Line, string_indices: list[int]
    ) -> TResult[Line]:
        """
        Merge strings that were split across multiple lines using
        line-continuation backslashes.

        Returns:
            Ok(new_line), if @line contains backslash line-continuation
            characters.
                OR
            Err(CannotTransform), otherwise.
        """
        LL = line.leaves

        indices_to_transform = []
        for string_idx in string_indices:
            string_leaf = LL[string_idx]
            if (
                string_leaf.type == token.STRING
                and "\\\n" in string_leaf.value
                and not has_triple_quotes(string_leaf.value)
            ):
                indices_to_transform.append(string_idx)

        if not indices_to_transform:
            return TErr(
                "Found no string leaves that contain backslash line continuation"
                " characters."
            )

        new_line = line.clone()
        new_line.comments = line.comments.copy()
        append_leaves(new_line, line, LL)

        for string_idx in indices_to_transform:
            new_string_leaf = new_line.leaves[string_idx]
            new_string_leaf.value = new_string_leaf.value.replace("\\\n", "")

        return Ok(new_line)

    def _merge_string_group(
        self, line: Line, string_indices: list[int]
    ) -> TResult[Line]:
        """
        Merges string groups (i.e. set of adjacent strings).

        Each index from `string_indices` designates one string group's first
        leaf in `line.leaves`.

        Returns:
            Ok(new_line), if ALL of the validation checks found in
            _validate_msg(...) pass.
                OR
            Err(CannotTransform), otherwise.
        """
        LL = line.leaves

        is_valid_index = is_valid_index_factory(LL)

        # A dict of {string_idx: tuple[num_of_strings, string_leaf]}.
        merged_string_idx_dict: dict[int, tuple[int, Leaf]] = {}
        for string_idx in string_indices:
            vresult = self._validate_msg(line, string_idx)
            if isinstance(vresult, Err):
                continue
            merged_string_idx_dict[string_idx] = self._merge_one_string_group(
                LL, string_idx, is_valid_index
            )

        if not merged_string_idx_dict:
            return TErr("No string group is merged")

        # Build the final line ('new_line') that this method will later return.
        new_line = line.clone()
        previous_merged_string_idx = -1
        previous_merged_num_of_strings = -1
        for i, leaf in enumerate(LL):
            if i in merged_string_idx_dict:
                previous_merged_string_idx = i
                previous_merged_num_of_strings, string_leaf = merged_string_idx_dict[i]
                new_line.append(string_leaf)

            if (
                previous_merged_string_idx
                <= i
                < previous_merged_string_idx + previous_merged_num_of_strings
            ):
                for comment_leaf in line.comments_after(leaf):
                    new_line.append(comment_leaf, preformatted=True)
                continue

            append_leaves(new_line, line, [leaf])

        return Ok(new_line)

    def _merge_one_string_group(
        self, LL: list[Leaf], string_idx: int, is_valid_index: Callable[[int], bool]
    ) -> tuple[int, Leaf]:
        """
        Merges one string group where the first string in the group is
        `LL[string_idx]`.

        Returns:
            A tuple of `(num_of_strings, leaf)` where `num_of_strings` is the
            number of strings merged and `leaf` is the newly merged string
            to be replaced in the new line.
        """
        # If the string group is wrapped inside an Atom node, we must make sure
        # to later replace that Atom with our new (merged) string leaf.
        atom_node = LL[string_idx].parent

        # We will place BREAK_MARK in between every two substrings that we
        # merge. We will then later go through our final result and use the
        # various instances of BREAK_MARK we find to add the right values to
        # the custom split map.
        BREAK_MARK = "@@@@@ BLACK BREAKPOINT MARKER @@@@@"

        QUOTE = LL[string_idx].value[-1]

        def make_naked(string: str, string_prefix: str) -> str:
            """Strip @string (i.e. make it a "naked" string)

            Pre-conditions:
                * assert_is_leaf_string(@string)

            Returns:
                A string that is identical to @string except that
                @string_prefix has been stripped, the surrounding QUOTE
                characters have been removed, and any remaining QUOTE
                characters have been escaped.
            """
            assert_is_leaf_string(string)
            if "f" in string_prefix:
                f_expressions = [
                    string[span[0] + 1 : span[1] - 1]  # +-1 to get rid of curly braces
                    for span in iter_fexpr_spans(string)
                ]
                debug_expressions_contain_visible_quotes = any(
                    re.search(r".*[\'\"].*(?<![!:=])={1}(?!=)(?![^\s:])", expression)
                    for expression in f_expressions
                )
                if not debug_expressions_contain_visible_quotes:
                    # We don't want to toggle visible quotes in debug f-strings, as
                    # that would modify the AST
                    string = _toggle_fexpr_quotes(string, QUOTE)
                    # After quotes toggling, quotes in expressions won't be escaped
                    # because quotes can't be reused in f-strings. So we can simply
                    # let the escaping logic below run without knowing f-string
                    # expressions.

            RE_EVEN_BACKSLASHES = r"(?:(?<!\\)(?:\\\\)*)"
            naked_string = string[len(string_prefix) + 1 : -1]
            naked_string = re.sub(
                "(" + RE_EVEN_BACKSLASHES + ")" + QUOTE, r"\1\\" + QUOTE, naked_string
            )
            return naked_string

        # Holds the CustomSplit objects that will later be added to the custom
        # split map.
        custom_splits = []

        # Temporary storage for the 'has_prefix' part of the CustomSplit objects.
        prefix_tracker = []

        # Sets the 'prefix' variable. This is the prefix that the final merged
        # string will have.
        next_str_idx = string_idx
        prefix = ""
        while (
            not prefix
            and is_valid_index(next_str_idx)
            and LL[next_str_idx].type == token.STRING
        ):
            prefix = get_string_prefix(LL[next_str_idx].value).lower()
            next_str_idx += 1

        # The next loop merges the string group. The final string will be
        # contained in 'S'.
        #
        # The following convenience variables are used:
        #
        #   S: string
        #   NS: naked string
        #   SS: next string
        #   NSS: naked next string
        S = ""
        NS = ""
        num_of_strings = 0
        next_str_idx = string_idx
        while is_valid_index(next_str_idx) and LL[next_str_idx].type == token.STRING:
            num_of_strings += 1

            SS = LL[next_str_idx].value
            next_prefix = get_string_prefix(SS).lower()

            # If this is an f-string group but this substring is not prefixed
            # with 'f'...
            if "f" in prefix and "f" not in next_prefix:
                # Then we must escape any braces contained in this substring.
                SS = re.sub(r"(\{|\})", r"\1\1", SS)

            NSS = make_naked(SS, next_prefix)

            has_prefix = bool(next_prefix)
            prefix_tracker.append(has_prefix)

            S = prefix + QUOTE + NS + NSS + BREAK_MARK + QUOTE
            NS = make_naked(S, prefix)

            next_str_idx += 1

        # Take a note on the index of the non-STRING leaf.
        non_string_idx = next_str_idx

        S_leaf = Leaf(token.STRING, S)
        if self.normalize_strings:
            S_leaf.value = normalize_string_quotes(S_leaf.value)

        # Fill the 'custom_splits' list with the appropriate CustomSplit objects.
        temp_string = S_leaf.value[len(prefix) + 1 : -1]
        for has_prefix in prefix_tracker:
            mark_idx = temp_string.find(BREAK_MARK)
            assert (
                mark_idx >= 0
            ), "Logic error while filling the custom string breakpoint cache."

            temp_string = temp_string[mark_idx + len(BREAK_MARK) :]
            breakpoint_idx = mark_idx + (len(prefix) if has_prefix else 0) + 1
            custom_splits.append(CustomSplit(has_prefix, breakpoint_idx))

        string_leaf = Leaf(token.STRING, S_leaf.value.replace(BREAK_MARK, ""))

        if atom_node is not None:
            # If not all children of the atom node are merged (this can happen
            # when there is a standalone comment in the middle) ...
            if non_string_idx - string_idx < len(atom_node.children):
                # We need to replace the old STRING leaves with the new string leaf.
                first_child_idx = LL[string_idx].remove()
                for idx in range(string_idx + 1, non_string_idx):
                    LL[idx].remove()
                if first_child_idx is not None:
                    atom_node.insert_child(first_child_idx, string_leaf)
            else:
                # Else replace the atom node with the new string leaf.
                replace_child(atom_node, string_leaf)

        self.add_custom_splits(string_leaf.value, custom_splits)
        return num_of_strings, string_leaf

    @staticmethod
    def _validate_msg(line: Line, string_idx: int) -> TResult[None]:
        """Validate (M)erge (S)tring (G)roup

        Transform-time string validation logic for _merge_string_group(...).

        Returns:
            * Ok(None), if ALL validation checks (listed below) pass.
                OR
            * Err(CannotTransform), if any of the following are true:
                - The target string group does not contain ANY stand-alone comments.
                - The target string is not in a string group (i.e. it has no
                  adjacent strings).
                - The string group has more than one inline comment.
                - The string group has an inline comment that appears to be a pragma.
                - The set of all string prefixes in the string group is of
                  length greater than one and is not equal to {"", "f"}.
                - The string group consists of raw strings.
                - The string group would merge f-strings with different quote types
                  and internal quotes.
                - The string group is stringified type annotations. We don't want to
                  process stringified type annotations since pyright doesn't support
                  them spanning multiple string values. (NOTE: mypy, pytype, pyre do
                  support them, so we can change if pyright also gains support in the
                  future. See https://github.com/microsoft/pyright/issues/4359.)
        """
        # We first check for "inner" stand-alone comments (i.e. stand-alone
        # comments that have a string leaf before them AND after them).
        for inc in [1, -1]:
            i = string_idx
            found_sa_comment = False
            is_valid_index = is_valid_index_factory(line.leaves)
            while is_valid_index(i) and line.leaves[i].type in [
                token.STRING,
                STANDALONE_COMMENT,
            ]:
                if line.leaves[i].type == STANDALONE_COMMENT:
                    found_sa_comment = True
                elif found_sa_comment:
                    return TErr(
                        "StringMerger does NOT merge string groups which contain "
                        "stand-alone comments."
                    )

                i += inc

        QUOTE = line.leaves[string_idx].value[-1]

        num_of_inline_string_comments = 0
        set_of_prefixes = set()
        num_of_strings = 0
        for leaf in line.leaves[string_idx:]:
            if leaf.type != token.STRING:
                # If the string group is trailed by a comma, we count the
                # comments trailing the comma to be one of the string group's
                # comments.
                if leaf.type == token.COMMA and id(leaf) in line.comments:
                    num_of_inline_string_comments += 1
                break

            if has_triple_quotes(leaf.value):
                return TErr("StringMerger does NOT merge multiline strings.")

            num_of_strings += 1
            prefix = get_string_prefix(leaf.value).lower()
            if "r" in prefix:
                return TErr("StringMerger does NOT merge raw strings.")

            set_of_prefixes.add(prefix)

            if (
                "f" in prefix
                and leaf.value[-1] != QUOTE
                and (
                    "'" in leaf.value[len(prefix) + 1 : -1]
                    or '"' in leaf.value[len(prefix) + 1 : -1]
                )
            ):
                return TErr(
                    "StringMerger does NOT merge f-strings with different quote types"
                    " and internal quotes."
                )

            if id(leaf) in line.comments:
                num_of_inline_string_comments += 1
                if contains_pragma_comment(line.comments[id(leaf)]):
                    return TErr("Cannot merge strings which have pragma comments.")

        if num_of_strings < 2:
            return TErr(
                f"Not enough strings to merge (num_of_strings={num_of_strings})."
            )

        if num_of_inline_string_comments > 1:
            return TErr(
                f"Too many inline string comments ({num_of_inline_string_comments})."
            )

        if len(set_of_prefixes) > 1 and set_of_prefixes != {"", "f"}:
            return TErr(f"Too many different prefixes ({set_of_prefixes}).")

        return Ok(None)


class StringParenStripper(StringTransformer):
    """StringTransformer that strips surrounding parentheses from strings.

    Requirements:
        The line contains a string which is surrounded by parentheses and:
            - The target string is NOT the only argument to a function call.
            - The target string is NOT a "pointless" string.
            - The target string is NOT a dictionary value.
            - If the target string contains a PERCENT, the brackets are not
              preceded or followed by an operator with higher precedence than
              PERCENT.

    Transformations:
        The parentheses mentioned in the 'Requirements' section are stripped.

    Collaborations:
        StringParenStripper has its own inherent usefulness, but it is also
        relied on to clean up the parentheses created by StringParenWrapper (in
        the event that they are no longer needed).
    """

    def do_match(self, line: Line) -> TMatchResult:
        LL = line.leaves

        is_valid_index = is_valid_index_factory(LL)

        string_indices = []

        idx = -1
        while True:
            idx += 1
            if idx >= len(LL):
                break
            leaf = LL[idx]

            # Should be a string...
            if leaf.type != token.STRING:
                continue

            # If this is a "pointless" string...
            if (
                leaf.parent
                and leaf.parent.parent
                and leaf.parent.parent.type == syms.simple_stmt
            ):
                continue

            # Should be preceded by a non-empty LPAR...
            if (
                not is_valid_index(idx - 1)
                or LL[idx - 1].type != token.LPAR
                or is_empty_lpar(LL[idx - 1])
            ):
                continue

            # That LPAR should NOT be preceded by a colon (which could be a
            # dictionary value), function name, or a closing bracket (which
            # could be a function returning a function or a list/dictionary
            # containing a function)...
            if is_valid_index(idx - 2) and (
                LL[idx - 2].type == token.COLON
                or LL[idx - 2].type == token.NAME
                or LL[idx - 2].type in CLOSING_BRACKETS
            ):
                continue

            string_idx = idx

            # Skip the string trailer, if one exists.
            string_parser = StringParser()
            next_idx = string_parser.parse(LL, string_idx)

            # if the leaves in the parsed string include a PERCENT, we need to
            # make sure the initial LPAR is NOT preceded by an operator with
            # higher or equal precedence to PERCENT
            if is_valid_index(idx - 2):
                # mypy can't quite follow unless we name this
                before_lpar = LL[idx - 2]
                if token.PERCENT in {leaf.type for leaf in LL[idx - 1 : next_idx]} and (
                    (
                        before_lpar.type
                        in {
                            token.STAR,
                            token.AT,
                            token.SLASH,
                            token.DOUBLESLASH,
                            token.PERCENT,
                            token.TILDE,
                            token.DOUBLESTAR,
                            token.AWAIT,
                            token.LSQB,
                            token.LPAR,
                        }
                    )
                    or (
                        # only unary PLUS/MINUS
                        before_lpar.parent
                        and before_lpar.parent.type == syms.factor
                        and (before_lpar.type in {token.PLUS, token.MINUS})
                    )
                ):
                    continue

            # Should be followed by a non-empty RPAR...
            if (
                is_valid_index(next_idx)
                and LL[next_idx].type == token.RPAR
                and not is_empty_rpar(LL[next_idx])
            ):
                # That RPAR should NOT be followed by anything with higher
                # precedence than PERCENT
                if is_valid_index(next_idx + 1) and LL[next_idx + 1].type in {
                    token.DOUBLESTAR,
                    token.LSQB,
                    token.LPAR,
                    token.DOT,
                }:
                    continue

                string_indices.append(string_idx)
                idx = string_idx
                while idx < len(LL) - 1 and LL[idx + 1].type == token.STRING:
                    idx += 1

        if string_indices:
            return Ok(string_indices)
        return TErr("This line has no strings wrapped in parens.")

    def do_transform(
        self, line: Line, string_indices: list[int]
    ) -> Iterator[TResult[Line]]:
        LL = line.leaves

        string_and_rpar_indices: list[int] = []
        for string_idx in string_indices:
            string_parser = StringParser()
            rpar_idx = string_parser.parse(LL, string_idx)

            should_transform = True
            for leaf in (LL[string_idx - 1], LL[rpar_idx]):
                if line.comments_after(leaf):
                    # Should not strip parentheses which have comments attached
