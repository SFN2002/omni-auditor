import io
import json
import platform
import re
import sys
import tokenize
import traceback
from collections.abc import (
    Collection,
    Generator,
    MutableMapping,
    Sequence,
)
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
from json.decoder import JSONDecodeError
from pathlib import Path
from re import Pattern
from typing import Any

import click
from click.core import ParameterSource
from mypy_extensions import mypyc_attr
from pathspec import GitIgnoreSpec
from pathspec.patterns.gitignore import GitIgnorePatternError

from _black_version import version as __version__
from black.cache import Cache
from black.comments import normalize_fmt_off
from black.const import (
    DEFAULT_EXCLUDES,
    DEFAULT_INCLUDES,
    DEFAULT_LINE_LENGTH,
    STDIN_PLACEHOLDER,
)
from black.files import (
    best_effort_relative_path,
    find_project_root,
    find_pyproject_toml,
    find_user_pyproject_toml,
    gen_python_files,
    get_gitignore,
    parse_pyproject_toml,
    path_is_excluded,
    resolves_outside_root_or_cannot_stat,
    wrap_stream_for_windows,
)
from black.handle_ipynb_magics import (
    PYTHON_CELL_MAGICS,
    jupyter_dependencies_are_installed,
    mask_cell,
    put_trailing_semicolon_back,
    remove_trailing_semicolon,
    unmask_cell,
    validate_cell,
)
from black.linegen import LN, LineGenerator, transform_line
from black.lines import EmptyLineTracker, LinesBlock
from black.mode import FUTURE_FLAG_TO_FEATURE, VERSION_TO_FEATURES, Feature
from black.mode import Mode as Mode  # re-exported
from black.mode import Preview, TargetVersion, supports_feature
from black.nodes import STARS, is_number_token, is_simple_decorator_expression, syms
from black.output import color_diff, diff, dump_to_file, err, ipynb_diff, out
from black.parsing import (  # noqa F401
    ASTSafetyError,
    InvalidInput,
    lib2to3_parse,
    parse_ast,
    stringify_ast,
)
from black.ranges import (
    adjusted_lines,
    convert_unchanged_lines,
    parse_line_ranges,
    sanitized_lines,
)
from black.report import Changed, NothingChanged, Report
from blib2to3.pgen2 import token
from blib2to3.pytree import Leaf, Node

COMPILED = Path(__file__).suffix in (".pyd", ".so")

# types
FileContent = str
Encoding = str
NewLine = str


class WriteBack(Enum):
    NO = 0
    YES = 1
    DIFF = 2
    CHECK = 3
    COLOR_DIFF = 4

    @classmethod
    def from_configuration(
        cls, *, check: bool, diff: bool, color: bool = False
    ) -> "WriteBack":
        if check and not diff:
            return cls.CHECK

        if diff and color:
            return cls.COLOR_DIFF

        return cls.DIFF if diff else cls.YES


# Legacy name, left for integrations.
FileMode = Mode


def read_pyproject_toml(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Inject Black configuration from "pyproject.toml" into defaults in `ctx`.

    Returns the path to a successfully found and read configuration file, None
    otherwise.
    """
    if not value:
        value = find_pyproject_toml(
            ctx.params.get("src", ()), ctx.params.get("stdin_filename", None)
        )
        if value is None:
            return None

    try:
        config = parse_pyproject_toml(value)
    except (OSError, ValueError) as e:
        raise click.FileError(
            filename=value, hint=f"Error reading configuration file: {e}"
        ) from None

    if not config:
        return None
    else:
        spellcheck_pyproject_toml_keys(ctx, list(config), value)
        # Sanitize the values to be Click friendly. For more information please see:
        # https://github.com/psf/black/issues/1458
        # https://github.com/pallets/click/issues/1567
        config = {
            k: str(v) if not isinstance(v, (list, dict)) else v
            for k, v in config.items()
        }

    target_version = config.get("target_version")
    if target_version is not None and not isinstance(target_version, list):
        raise click.BadOptionUsage(
            "target-version", "Config key target-version must be a list"
        )

    exclude = config.get("exclude")
    if exclude is not None and not isinstance(exclude, str):
        raise click.BadOptionUsage("exclude", "Config key exclude must be a string")

    extend_exclude = config.get("extend_exclude")
    if extend_exclude is not None and not isinstance(extend_exclude, str):
        raise click.BadOptionUsage(
            "extend-exclude", "Config key extend-exclude must be a string"
        )

    line_ranges = config.get("line_ranges")
    if line_ranges is not None:
        raise click.BadOptionUsage(
            "line-ranges", "Cannot use line-ranges in the pyproject.toml file."
        )

    default_map: dict[str, Any] = {}
    if ctx.default_map:
        default_map.update(ctx.default_map)
    default_map.update(config)

    ctx.default_map = default_map
    return value


def spellcheck_pyproject_toml_keys(
    ctx: click.Context, config_keys: list[str], config_file_path: str
) -> None:
    invalid_keys: list[str] = []
    available_config_options = {param.name for param in ctx.command.params}
    invalid_keys = [key for key in config_keys if key not in available_config_options]
    if invalid_keys:
        keys_str = ", ".join(map(repr, invalid_keys))
        out(
            f"Invalid config keys detected: {keys_str} (in {config_file_path})",
            fg="red",
        )


def target_version_option_callback(
    c: click.Context, p: click.Option | click.Parameter, v: tuple[str, ...]
) -> list[TargetVersion]:
    """Compute the target versions from a --target-version flag.

    This is its own function because mypy couldn't infer the type correctly
    when it was a lambda, causing mypyc trouble.
    """
    return [TargetVersion[val.upper()] for val in v]


def _target_versions_exceed_runtime(
    target_versions: set[TargetVersion],
) -> bool:
    if not target_versions:
        return False
    max_target_minor = max(tv.value for tv in target_versions)
    return max_target_minor > sys.version_info[1]


def _version_mismatch_message(target_versions: set[TargetVersion]) -> str:
    max_target = max(target_versions, key=lambda tv: tv.value)
    runtime = f"{sys.version_info[0]}.{sys.version_info[1]}"
    return (
        f"Python {runtime} cannot parse code formatted for"
        f" {max_target.pretty()}. To fix this: run Black with"
        f" {max_target.pretty()}, set --target-version to"
        f" py3{sys.version_info[1]}, or use --fast to skip the safety"
        " check."
    )


def enable_unstable_feature_callback(
    c: click.Context, p: click.Option | click.Parameter, v: tuple[str, ...]
) -> list[Preview]:
    """Compute the features from an --enable-unstable-feature flag."""
    return [Preview[val] for val in v]


def re_compile_maybe_verbose(regex: str) -> Pattern[str]:
    """Compile a regular expression string in `regex`.

    If it contains newlines, use verbose mode.
    """
    if "\n" in regex:
        regex = "(?x)" + regex
    compiled: Pattern[str] = re.compile(regex)
    return compiled


def validate_regex(
    ctx: click.Context,
    param: click.Parameter,
    value: str | None,
) -> Pattern[str] | None:
    try:
        return re_compile_maybe_verbose(value) if value is not None else None
    except re.error as e:
        raise click.BadParameter(f"Not a valid regular expression: {e}") from None


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    # While Click does set this field automatically using the docstring, mypyc
    # (annoyingly) strips 'em so we need to set it here too.
    help="The uncompromising code formatter.",
)
@click.option("-c", "--code", type=str, help="Format the code passed in as a string.")
@click.option(
    "-l",
    "--line-length",
    type=int,
    default=DEFAULT_LINE_LENGTH,
    help="How many characters per line to allow.",
    show_default=True,
)
@click.option(
    "-t",
    "--target-version",
    type=click.Choice([v.name.lower() for v in TargetVersion]),
    callback=target_version_option_callback,
    multiple=True,
    help=(
        "Python versions that should be supported by Black's output. You should"
        " include all versions that your code supports. By default, Black will infer"
        " target versions from the project metadata in pyproject.toml. If this does"
        " not yield conclusive results, Black will use per-file auto-detection."
    ),
)
@click.option(
    "--pyi",
    is_flag=True,
    help=(
        "Format all input files like typing stubs regardless of file extension. This"
        " is useful when piping source on standard input."
    ),
)
@click.option(
    "--ipynb",
    is_flag=True,
    help=(
        "Format all input files like Jupyter Notebooks regardless of file extension."
        " This is useful when piping source on standard input."
    ),
)
@click.option(
    "--python-cell-magics",
    multiple=True,
    help=(
        "When processing Jupyter Notebooks, add the given magic to the list"
        f" of known python-magics ({', '.join(sorted(PYTHON_CELL_MAGICS))})."
        " Useful for formatting cells with custom python magics."
    ),
    default=[],
)
@click.option(
    "-x",
    "--skip-source-first-line",
    is_flag=True,
    help="Skip the first line of the source code.",
)
@click.option(
    "-S",
    "--skip-string-normalization",
    is_flag=True,
    help="Don't normalize string quotes or prefixes.",
)
@click.option(
    "-C",
    "--skip-magic-trailing-comma",
    is_flag=True,
    help="Don't use trailing commas as a reason to split lines.",
)
@click.option(
    "--preview",
    is_flag=True,
    help=(
        "Enable potentially disruptive style changes that may be added to Black's main"
        " functionality in the next major release."
    ),
)
@click.option(
    "--unstable",
    is_flag=True,
    help=(
        "Enable potentially disruptive style changes that have known bugs or are not"
        " currently expected to make it into the stable style Black's next major"
        " release. Implies --preview."
    ),
)
@click.option(
    "--enable-unstable-feature",
    type=click.Choice([v.name for v in Preview]),
    callback=enable_unstable_feature_callback,
    multiple=True,
    help=(
        "Enable specific features included in the `--unstable` style. Requires"
        " `--preview`. No compatibility guarantees are provided on the behavior"
        " or existence of any unstable features."
    ),
)
@click.option(
    "--check",
    is_flag=True,
    help=(
        "Don't write the files back, just return the status. Return code 0 means"
        " nothing would change. Return code 1 means some files would be reformatted."
        " Return code 123 means there was an internal error."
    ),
)
@click.option(
    "--diff",
    is_flag=True,
    help=(
        "Don't write the files back, just output a diff to indicate what changes"
        " Black would've made. They are printed to stdout so capturing them is simple."
    ),
)
@click.option(
    "--color/--no-color",
    is_flag=True,
    help="Show (or do not show) colored diff. Only applies when --diff is given.",
)
@click.option(
    "--line-ranges",
    multiple=True,
    metavar="START-END",
    help=(
        "When specified, Black will try its best to only format these lines. This"
        " option can be specified multiple times, and a union of the lines will be"
        " formatted. Each range must be specified as two integers connected by a `-`:"
        " `<START>-<END>`. The `<START>` and `<END>` integer indices are 1-based and"
        " inclusive on both ends."
    ),
    default=(),
)
@click.option(
    "--fast/--safe",
    is_flag=True,
    help=(
        "By default, Black performs an AST safety check after formatting your code."
        " The --fast flag turns off this check and the --safe flag explicitly enables"
        " it. [default: --safe]"
    ),
)
@click.option(
    "--required-version",
    type=str,
    help=(
        "Require a specific version of Black to be running. This is useful for"
        " ensuring that all contributors to your project are using the same"
        " version, because different versions of Black may format code a little"
        " differently. This option can be set in a configuration file for consistent"
        " results across environments."
    ),
)
@click.option(
    "--exclude",
    type=str,
    callback=validate_regex,
    help=(
        "A regular expression that matches files and directories that should be"
        " excluded on recursive searches. An empty value means no paths are excluded."
        " Use forward slashes for directories on all platforms (Windows, too)."
        " By default, Black also ignores all paths listed in .gitignore. Changing this"
        f" value will override all default exclusions. [default: {DEFAULT_EXCLUDES}]"
    ),
    show_default=False,
)
@click.option(
    "--extend-exclude",
    type=str,
    callback=validate_regex,
    help=(
        "Like --exclude, but adds additional files and directories on top of the"
        " default values instead of overriding them."
    ),
)
@click.option(
    "--force-exclude",
    type=str,
    callback=validate_regex,
    help=(
        "Like --exclude, but files and directories matching this regex will be excluded"
        " even when they are passed explicitly as arguments. This is useful when"
        " invoking Black programmatically on changed files, such as in a pre-commit"
        " hook or editor plugin."
    ),
)
@click.option(
    "--stdin-filename",
    type=str,
    is_eager=True,
    help=(
        "The name of the file when passing it through stdin. Useful to make sure Black"
        " will respect the --force-exclude option on some editors that rely on using"
        " stdin."
    ),
)
@click.option(
    "--include",
    type=str,
    default=DEFAULT_INCLUDES,
    callback=validate_regex,
    help=(
        "A regular expression that matches files and directories that should be"
        " included on recursive searches. An empty value means all files are included"
        " regardless of the name. Use forward slashes for directories on all platforms"
        " (Windows, too). Overrides all exclusions, including from .gitignore and"
        " command line options."
    ),
    show_default=True,
)
@click.option(
    "-W",
    "--workers",
    type=click.IntRange(min=1),
    default=None,
    help=(
        "When Black formats multiple files, it may use a process pool to speed up"
        " formatting. This option controls the number of parallel workers. This can"
        " also be specified via the BLACK_NUM_WORKERS environment variable. Defaults"
        " to the number of CPUs in the system."
    ),
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help=(
        "Stop emitting all non-critical output. Error messages will still be emitted"
        " (which can silenced by 2>/dev/null)."
    ),
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help=(
        "Emit messages about files that were not changed or were ignored due to"
        " exclusion patterns. If Black is using a configuration file, a message"
        " detailing which one it is using will be emitted."
    ),
)
@click.version_option(
    version=__version__,
    message=(
        f"%(prog)s, %(version)s (compiled: {'yes' if COMPILED else 'no'})\n"
        f"Python ({platform.python_implementation()}) {platform.python_version()}"
    ),
)
@click.argument(
    "src",
    nargs=-1,
    type=click.Path(
        exists=True, file_okay=True, dir_okay=True, readable=True, allow_dash=True
    ),
    is_eager=True,
    metavar="SRC ...",
)
@click.option(
    "--config",
    type=click.Path(
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        allow_dash=False,
        path_type=str,
    ),
    is_eager=True,
    callback=read_pyproject_toml,
    help="Read configuration options from a configuration file.",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help=(
        "Skip reading and writing the cache, forcing Black to reformat all"
        " included files."
    ),
)
@click.pass_context
def main(
    ctx: click.Context,
    code: str | None,
    line_length: int,
    target_version: list[TargetVersion],
    check: bool,
    diff: bool,
    line_ranges: Sequence[str],
    color: bool,
    fast: bool,
    pyi: bool,
    ipynb: bool,
    python_cell_magics: Sequence[str],
    skip_source_first_line: bool,
    skip_string_normalization: bool,
    skip_magic_trailing_comma: bool,
    preview: bool,
    unstable: bool,
    enable_unstable_feature: list[Preview],
    quiet: bool,
    verbose: bool,
    required_version: str | None,
    include: Pattern[str],
    exclude: Pattern[str] | None,
    extend_exclude: Pattern[str] | None,
    force_exclude: Pattern[str] | None,
    stdin_filename: str | None,
    workers: int | None,
    src: tuple[str, ...],
    config: str | None,
    no_cache: bool,
) -> None:
    """The uncompromising code formatter."""
    ctx.ensure_object(dict)

    assert sys.version_info >= (3, 10), "Black requires Python 3.10+"
    if sys.version_info[:3] == (3, 12, 5):
        out(
            "Python 3.12.5 has a memory safety issue that can cause Black's "
            "AST safety checks to fail. "
            "Please upgrade to Python 3.12.6 or downgrade to Python 3.12.4"
        )
        ctx.exit(1)

    if src and code is not None:
        out(
            main.get_usage(ctx)
            + "\n\n'SRC' and 'code' cannot be passed simultaneously."
        )
        ctx.exit(1)
    if not src and code is None:
        out(main.get_usage(ctx) + "\n\nOne of 'SRC' or 'code' is required.")
        ctx.exit(1)

    # It doesn't do anything if --unstable is also passed, so just allow it.
    if enable_unstable_feature and not (preview or unstable):
        out(
            main.get_usage(ctx)
            + "\n\n'--enable-unstable-feature' requires '--preview'."
        )
        ctx.exit(1)

    root, method = (
        find_project_root(src, stdin_filename) if code is None else (None, None)
    )
    ctx.obj["root"] = root

    if verbose:
        if root:
            out(
                f"Identified `{root}` as project root containing a {method}.",
                fg="blue",
            )

        if config:
            config_source = ctx.get_parameter_source("config")
            user_level_config = str(find_user_pyproject_toml())
            if config == user_level_config:
                out(
                    "Using configuration from user-level config at "
                    f"'{user_level_config}'.",
                    fg="blue",
                )
            elif config_source in (
                ParameterSource.DEFAULT,
                ParameterSource.DEFAULT_MAP,
            ):
                out("Using configuration from project root.", fg="blue")
            else:
                out(f"Using configuration in '{config}'.", fg="blue")
            if ctx.default_map:
                for param, value in ctx.default_map.items():
                    out(f"{param}: {value}")

    error_msg = "Oh no! 💥 💔 💥"
    if (
        required_version
        and required_version != __version__
        and required_version != __version__.split(".")[0]
    ):
        err(
            f"{error_msg} The required version `{required_version}` does not match"
            f" the running version `{__version__}`!"
        )
        ctx.exit(1)
    if ipynb and pyi:
        err("Cannot pass both `pyi` and `ipynb` flags!")
        ctx.exit(1)

    write_back = WriteBack.from_configuration(check=check, diff=diff, color=color)
    if target_version:
        versions = set(target_version)
    else:
        # We'll autodetect later.
        versions = set()
    mode = Mode(
        target_versions=versions,
        line_length=line_length,
        is_pyi=pyi,
        is_ipynb=ipynb,
        skip_source_first_line=skip_source_first_line,
        string_normalization=not skip_string_normalization,
        magic_trailing_comma=not skip_magic_trailing_comma,
        preview=preview,
        unstable=unstable,
        python_cell_magics=set(python_cell_magics),
        enabled_features=set(enable_unstable_feature),
    )

    if not fast and _target_versions_exceed_runtime(versions):
        err(
            f"Warning: {_version_mismatch_message(versions)} Black's safety"
            " check verifies equivalence by parsing the AST, which fails"
            " when the running Python is older than the target version.",
            fg="yellow",
        )

    lines: list[tuple[int, int]] = []
    if line_ranges:
        if ipynb:
            err("Cannot use --line-ranges with ipynb files.")
            ctx.exit(1)

        try:
            lines = parse_line_ranges(line_ranges)
        except ValueError as e:
            err(str(e))
            ctx.exit(1)

    if code is not None:
        # Run in quiet mode by default with -c; the extra output isn't useful.
        # You can still pass -v to get verbose output.
        quiet = True

    report = Report(check=check, diff=diff, quiet=quiet, verbose=verbose)

    if code is not None:
        reformat_code(
            content=code,
            fast=fast,
            write_back=write_back,
            mode=mode,
            report=report,
            lines=lines,
        )
    else:
        assert root is not None  # root is only None if code is not None
        try:
            sources = get_sources(
                root=root,
                src=src,
                quiet=quiet,
                verbose=verbose,
                include=include,
                exclude=exclude,
                extend_exclude=extend_exclude,
                force_exclude=force_exclude,
                report=report,
                stdin_filename=stdin_filename,
            )
        except GitIgnorePatternError:
            ctx.exit(1)

        if not sources:
            if verbose or not quiet:
                out("No Python files are present to be formatted. Nothing to do 😴")
            if "-" in src:
                sys.stdout.write(sys.stdin.read())
            ctx.exit(0)

        if len(sources) == 1:
            reformat_one(
                src=sources.pop(),
                fast=fast,
                write_back=write_back,
                mode=mode,
                report=report,
                lines=lines,
                no_cache=no_cache,
            )
        else:
            from black.concurrency import reformat_many

            if lines:
                err("Cannot use --line-ranges to format multiple files.")
                ctx.exit(1)
            reformat_many(
                sources=sources,
                fast=fast,
                write_back=write_back,
                mode=mode,
                report=report,
                workers=workers,
                no_cache=no_cache,
            )

    if verbose or not quiet:
        if code is None and (verbose or report.change_count or report.failure_count):
            out()
        out(error_msg if report.return_code else "All done! ✨ 🍰 ✨")
        if code is None:
            click.echo(str(report), err=True)
    ctx.exit(report.return_code)


def get_sources(
    *,
    root: Path,
    src: tuple[str, ...],
    quiet: bool,
    verbose: bool,
    include: Pattern[str],
    exclude: Pattern[str] | None,
    extend_exclude: Pattern[str] | None,
    force_exclude: Pattern[str] | None,
    report: "Report",
    stdin_filename: str | None,
) -> set[Path]:
    """Compute the set of files to be formatted."""
    sources: set[Path] = set()

    assert root.is_absolute(), f"INTERNAL ERROR: `root` must be absolute but is {root}"
    using_default_exclude = exclude is None
    exclude = re_compile_maybe_verbose(DEFAULT_EXCLUDES) if exclude is None else exclude
    gitignore: dict[Path, GitIgnoreSpec] | None = None
    root_gitignore = get_gitignore(root)

    for s in src:
        if s == "-" and stdin_filename:
            path = Path(stdin_filename)
            if path_is_excluded(stdin_filename, force_exclude):
                report.path_ignored(
                    path,
                    "--stdin-filename matches the --force-exclude regular expression",
                )
                continue
            is_stdin = True
        else:
            path = Path(s)
            is_stdin = False

        # Compare the logic here to the logic in `gen_python_files`.
        if is_stdin or path.is_file():
            if resolves_outside_root_or_cannot_stat(path, root, report):
                if verbose:
                    out(f'Skipping invalid source: "{path}"', fg="red")
                continue

            root_relative_path = best_effort_relative_path(path, root).as_posix()
            root_relative_path = "/" + root_relative_path

            # Hard-exclude any files that matches the `--force-exclude` regex.
            if path_is_excluded(root_relative_path, force_exclude):
                report.path_ignored(
                    path, "matches the --force-exclude regular expression"
                )
                continue

            if is_stdin:
                path = Path(f"{STDIN_PLACEHOLDER}{path}")

            if path.suffix == ".ipynb" and not jupyter_dependencies_are_installed(
                warn=verbose or not quiet
            ):
                continue

            if verbose:
                out(f'Found input source: "{path}"', fg="blue")
            sources.add(path)
        elif path.is_dir():
            path = root / (path.resolve().relative_to(root))
            if verbose:
                out(f'Found input source directory: "{path}"', fg="blue")

            if using_default_exclude:
                gitignore = {
                    root: root_gitignore,
                    path: get_gitignore(path),
                }
            sources.update(
                gen_python_files(
                    path.iterdir(),
                    root,
                    include,
                    exclude,
                    extend_exclude,
                    force_exclude,
                    report,
                    gitignore,
                    verbose=verbose,
                    quiet=quiet,
                )
            )
        elif s == "-":
            if verbose:
                out("Found input source stdin", fg="blue")
            sources.add(path)
        else:
            err(f"invalid path: {s}")

    return sources


def reformat_code(
    content: str,
    fast: bool,
    write_back: WriteBack,
    mode: Mode,
    report: Report,
    *,
    lines: Collection[tuple[int, int]] = (),
) -> None:
    """
    Reformat and print out `content` without spawning child processes.
    Similar to `reformat_one`, but for string content.

    `fast`, `write_back`, and `mode` options are passed to
    :func:`format_file_in_place` or :func:`format_stdin_to_stdout`.
    """
    path = Path("<string>")
    try:
        changed = Changed.NO
        if format_stdin_to_stdout(
            content=content, fast=fast, write_back=write_back, mode=mode, lines=lines
        ):
            changed = Changed.YES
        report.done(path, changed)
    except Exception as exc:
        if report.verbose:
            traceback.print_exc()
        report.failed(path, str(exc))


# diff-shades depends on being to monkeypatch this function to operate. I know it's
# not ideal, but this shouldn't cause any issues ... hopefully. ~ichard26
@mypyc_attr(patchable=True)
def reformat_one(
    src: Path,
    fast: bool,
    write_back: WriteBack,
    mode: Mode,
    report: "Report",
    *,
    lines: Collection[tuple[int, int]] = (),
    no_cache: bool = False,
) -> None:
    """Reformat a single file under `src` without spawning child processes.

    `fast`, `write_back`, and `mode` options are passed to
    :func:`format_file_in_place` or :func:`format_stdin_to_stdout`.
    """
    try:
        changed = Changed.NO

        if str(src) == "-":
            is_stdin = True
        elif str(src).startswith(STDIN_PLACEHOLDER):
            is_stdin = True
            # Use the original name again in case we want to print something
            # to the user
            src = Path(str(src)[len(STDIN_PLACEHOLDER) :])
        else:
            is_stdin = False

        if is_stdin:
            if src.suffix == ".pyi":
                mode = replace(mode, is_pyi=True)
            elif src.suffix == ".ipynb":
                mode = replace(mode, is_ipynb=True)
            if format_stdin_to_stdout(
                fast=fast, write_back=write_back, mode=mode, lines=lines
            ):
                changed = Changed.YES
        else:
            cache = None if no_cache else Cache.read(mode)
            if cache is not None and write_back not in (
                WriteBack.DIFF,
                WriteBack.COLOR_DIFF,
            ):
                if not cache.is_changed(src):
                    changed = Changed.CACHED
            if changed is not Changed.CACHED and format_file_in_place(
                src, fast=fast, write_back=write_back, mode=mode, lines=lines
            ):
                changed = Changed.YES
            if cache is not None and (
                (write_back is WriteBack.YES and changed is not Changed.CACHED)
                or (write_back is WriteBack.CHECK and changed is Changed.NO)
            ):
                cache.write([src])
        report.done(src, changed)
    except Exception as exc:
        if report.verbose:
            traceback.print_exc()
        report.failed(src, str(exc))


def format_file_in_place(
    src: Path,
    fast: bool,
    mode: Mode,
    write_back: WriteBack = WriteBack.NO,
    lock: Any = None,  # multiprocessing.Manager().Lock() is some crazy proxy
    *,
    lines: Collection[tuple[int, int]] = (),
) -> bool:
    """Format file under `src` path. Return True if changed.

    If `write_back` is DIFF, write a diff to stdout. If it is YES, write reformatted
    code to the file.
    `mode` and `fast` options are passed to :func:`format_file_contents`.
    """
    if src.suffix == ".pyi":
        mode = replace(mode, is_pyi=True)
    elif src.suffix == ".ipynb":
        mode = replace(mode, is_ipynb=True)

    then = datetime.fromtimestamp(src.stat().st_mtime, timezone.utc)
    header = b""
    with open(src, "rb") as buf:
        if mode.skip_source_first_line:
            header = buf.readline()
        src_contents, encoding, newline = decode_bytes(buf.read(), mode)
    try:
        dst_contents = format_file_contents(
            src_contents, fast=fast, mode=mode, lines=lines
        )
    except NothingChanged:
        return False
    except JSONDecodeError:
        raise ValueError(
            f"File '{src}' cannot be parsed as valid Jupyter notebook."
        ) from None
    src_contents = header.decode(encoding) + src_contents
    dst_contents = header.decode(encoding) + dst_contents

    if write_back == WriteBack.YES:
        with open(src, "w", encoding=encoding, newline=newline) as f:
            f.write(dst_contents)
    elif write_back in (WriteBack.DIFF, WriteBack.COLOR_DIFF):
        now = datetime.now(timezone.utc)
        src_name = f"{src}\t{then}"
        dst_name = f"{src}\t{now}"
        if mode.is_ipynb:
