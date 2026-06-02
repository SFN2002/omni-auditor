# mypy: allow-untyped-defs
"""Command line options, config-file and conftest.py processing."""

from __future__ import annotations

import argparse
import builtins
import collections.abc
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
import contextlib
import copy
import dataclasses
import enum
from functools import lru_cache
import glob
import importlib.metadata
import inspect
import os
import pathlib
import re
import shlex
import sys
from textwrap import dedent
import types
from types import FunctionType
from typing import Any
from typing import cast
from typing import Final
from typing import final
from typing import IO
from typing import TextIO
from typing import TYPE_CHECKING
import warnings

import pluggy
from pluggy import HookimplMarker
from pluggy import HookimplOpts
from pluggy import HookspecMarker
from pluggy import HookspecOpts
from pluggy import PluginManager

from .compat import PathAwareHookProxy
from .exceptions import PrintHelp as PrintHelp
from .exceptions import UsageError as UsageError
from .findpaths import ConfigValue
from .findpaths import determine_setup
from _pytest import __version__
import _pytest._code
from _pytest._code import ExceptionInfo
from _pytest._code import filter_traceback
from _pytest._code.code import TracebackStyle
from _pytest._io import TerminalWriter
from _pytest.compat import assert_never
from _pytest.config.argparsing import Argument
from _pytest.config.argparsing import FILE_OR_DIR
from _pytest.config.argparsing import Parser
import _pytest.deprecated
import _pytest.hookspec
from _pytest.outcomes import fail
from _pytest.outcomes import Skipped
from _pytest.pathlib import absolutepath
from _pytest.pathlib import bestrelpath
from _pytest.pathlib import import_path
from _pytest.pathlib import ImportMode
from _pytest.pathlib import resolve_package_path
from _pytest.pathlib import safe_exists
from _pytest.stash import Stash
from _pytest.warning_types import PytestConfigWarning
from _pytest.warning_types import warn_explicit_for


if TYPE_CHECKING:
    from _pytest.assertion.rewrite import AssertionRewritingHook
    from _pytest.cacheprovider import Cache
    from _pytest.terminal import TerminalReporter

_PluggyPlugin = object
"""A type to represent plugin objects.

Plugins can be any namespace, so we can't narrow it down much, but we use an
alias to make the intent clear.

Ideally this type would be provided by pluggy itself.
"""


hookimpl = HookimplMarker("pytest")
hookspec = HookspecMarker("pytest")


@final
class ExitCode(enum.IntEnum):
    """Encodes the valid exit codes by pytest.

    Currently users and plugins may supply other exit codes as well.

    .. versionadded:: 5.0
    """

    #: Tests passed.
    OK = 0
    #: Tests failed.
    TESTS_FAILED = 1
    #: pytest was interrupted.
    INTERRUPTED = 2
    #: An internal error got in the way.
    INTERNAL_ERROR = 3
    #: pytest was misused.
    USAGE_ERROR = 4
    #: pytest couldn't find tests.
    NO_TESTS_COLLECTED = 5

    __module__ = "pytest"


class ConftestImportFailure(Exception):
    def __init__(
        self,
        path: pathlib.Path,
        *,
        cause: Exception,
    ) -> None:
        self.path = path
        self.cause = cause

    def __str__(self) -> str:
        return f"{type(self.cause).__name__}: {self.cause} (from {self.path})"


def filter_traceback_for_conftest_import_failure(
    entry: _pytest._code.TracebackEntry,
) -> bool:
    """Filter tracebacks entries which point to pytest internals or importlib.

    Make a special case for importlib because we use it to import test modules and conftest files
    in _pytest.pathlib.import_path.
    """
    return filter_traceback(entry) and "importlib" not in str(entry.path).split(os.sep)


def print_conftest_import_error(e: ConftestImportFailure, file: TextIO) -> None:
    exc_info = ExceptionInfo.from_exception(e.cause)
    tw = TerminalWriter(file)
    tw.line(f"ImportError while loading conftest '{e.path}'.", red=True)
    exc_info.traceback = exc_info.traceback.filter(
        filter_traceback_for_conftest_import_failure
    )
    exc_repr = (
        exc_info.getrepr(style="short", chain=False)
        if exc_info.traceback
        else exc_info.exconly()
    )
    formatted_tb = str(exc_repr)
    for line in formatted_tb.splitlines():
        tw.line(line.rstrip(), red=True)


def print_usage_error(e: UsageError, file: TextIO) -> None:
    tw = TerminalWriter(file)
    for msg in e.args:
        tw.line(f"ERROR: {msg}\n", red=True)


def main(
    args: list[str] | os.PathLike[str] | None = None,
    plugins: Sequence[str | _PluggyPlugin] | None = None,
) -> int | ExitCode:
    """Perform an in-process test run.

    :param args:
        List of command line arguments. If `None` or not given, defaults to reading
        arguments directly from the process command line (:data:`sys.argv`).
    :param plugins: List of plugin objects to be auto-registered during initialization.

    :returns: An exit code.
    """
    # Handle a single `--version` argument early to avoid starting up the entire pytest infrastructure.
    new_args = sys.argv[1:] if args is None else args
    if isinstance(new_args, Sequence) and new_args.count("--version") == 1:
        sys.stdout.write(f"pytest {__version__}\n")
        return ExitCode.OK

    old_pytest_version = os.environ.get("PYTEST_VERSION")
    try:
        os.environ["PYTEST_VERSION"] = __version__
        try:
            config = _prepareconfig(new_args, plugins)
        except ConftestImportFailure as e:
            print_conftest_import_error(e, file=sys.stderr)
            return ExitCode.USAGE_ERROR

        try:
            ret: ExitCode | int = config.hook.pytest_cmdline_main(config=config)
            try:
                return ExitCode(ret)
            except ValueError:
                return ret
        finally:
            config._ensure_unconfigure()
    except UsageError as e:
        print_usage_error(e, file=sys.stderr)
        return ExitCode.USAGE_ERROR
    finally:
        if old_pytest_version is None:
            os.environ.pop("PYTEST_VERSION", None)
        else:
            os.environ["PYTEST_VERSION"] = old_pytest_version


def console_main() -> int:
    """The CLI entry point of pytest.

    This function is not meant for programmable use; use `main()` instead.
    """
    # https://docs.python.org/3/library/signal.html#note-on-sigpipe
    try:
        code = main()
        sys.stdout.flush()
        return code
    except BrokenPipeError:
        # Python flushes standard streams on exit; redirect remaining output
        # to devnull to avoid another BrokenPipeError at shutdown
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 1  # Python exits with error code 1 on EPIPE


class cmdline:  # compatibility namespace
    main = staticmethod(main)


def filename_arg(path: str, optname: str) -> str:
    """Argparse type validator for filename arguments.

    :path: Path of filename.
    :optname: Name of the option.
    """
    if os.path.isdir(path):
        raise UsageError(f"{optname} must be a filename, given: {path}")
    return path


def directory_arg(path: str, optname: str) -> str:
    """Argparse type validator for directory arguments.

    :path: Path of directory.
    :optname: Name of the option.
    """
    if not os.path.isdir(path):
        raise UsageError(f"{optname} must be a directory, given: {path}")
    return path


# Plugins that cannot be disabled via "-p no:X" currently.
essential_plugins = (
    "mark",
    "main",
    "runner",
    "fixtures",
    "helpconfig",  # Provides -p.
)

default_plugins = (
    *essential_plugins,
    "python",
    "terminal",
    "debugging",
    "unittest",
    "capture",
    "skipping",
    "legacypath",
    "tmpdir",
    "monkeypatch",
    "recwarn",
    "pastebin",
    "assertion",
    "junitxml",
    "doctest",
    "cacheprovider",
    "setuponly",
    "setupplan",
    "stepwise",
    "unraisableexception",
    "threadexception",
    "warnings",
    "logging",
    "reports",
    "faulthandler",
    "subtests",
)

builtin_plugins = {
    *default_plugins,
    "pytester",
    "pytester_assertions",
    "terminalprogress",
}


def get_config(
    args: Iterable[str] | None = None,
    plugins: Sequence[str | _PluggyPlugin] | None = None,
) -> Config:
    # Subsequent calls to main will create a fresh instance.
    pluginmanager = PytestPluginManager()
    invocation_params = Config.InvocationParams(
        args=args or (),
        plugins=plugins,
        dir=pathlib.Path.cwd(),
    )
    config = Config(pluginmanager, invocation_params=invocation_params)

    if invocation_params.args:
        # Handle any "-p no:plugin" args.
        pluginmanager.consider_preparse(invocation_params.args, exclude_only=True)

    for spec in default_plugins:
        pluginmanager.import_plugin(spec)

    return config


def get_plugin_manager() -> PytestPluginManager:
    """Obtain a new instance of the
    :py:class:`pytest.PytestPluginManager`, with default plugins
    already loaded.

    This function can be used by integration with other tools, like hooking
    into pytest to run tests into an IDE.
    """
    return get_config().pluginmanager


def _prepareconfig(
    args: list[str] | os.PathLike[str],
    plugins: Sequence[str | _PluggyPlugin] | None = None,
) -> Config:
    if isinstance(args, os.PathLike):
        args = [os.fspath(args)]
    elif not isinstance(args, list):
        msg = (  # type:ignore[unreachable]
            "`args` parameter expected to be a list of strings, got: {!r} (type: {})"
        )
        raise TypeError(msg.format(args, type(args)))

    initial_config = get_config(args, plugins)
    pluginmanager = initial_config.pluginmanager
    try:
        if plugins:
            for plugin in plugins:
                if isinstance(plugin, str):
                    pluginmanager.consider_pluginarg(plugin)
                else:
                    pluginmanager.register(plugin)
        config: Config = pluginmanager.hook.pytest_cmdline_parse(
            pluginmanager=pluginmanager, args=args
        )
        return config
    except BaseException:
        initial_config._ensure_unconfigure()
        raise


def _get_directory(path: pathlib.Path) -> pathlib.Path:
    """Get the directory of a path - itself if already a directory."""
    if path.is_file():
        return path.parent
    else:
        return path


def _get_legacy_hook_marks(
    method: Any,
    hook_type: str,
    opt_names: tuple[str, ...],
) -> dict[str, bool]:
    if TYPE_CHECKING:
        # abuse typeguard from importlib to avoid massive method type union that's lacking an alias
        assert inspect.isroutine(method)
    known_marks: set[str] = {m.name for m in getattr(method, "pytestmark", [])}
    must_warn: list[str] = []
    opts: dict[str, bool] = {}
    for opt_name in opt_names:
        opt_attr = getattr(method, opt_name, AttributeError)
        if opt_attr is not AttributeError:
            must_warn.append(f"{opt_name}={opt_attr}")
            opts[opt_name] = True
        elif opt_name in known_marks:
            must_warn.append(f"{opt_name}=True")
            opts[opt_name] = True
        else:
            opts[opt_name] = False
    if must_warn:
        hook_opts = ", ".join(must_warn)
        message = _pytest.deprecated.HOOK_LEGACY_MARKING.format(
            type=hook_type,
            fullname=method.__qualname__,
            hook_opts=hook_opts,
        )
        warn_explicit_for(cast(FunctionType, method), message)
    return opts


@final
class PytestPluginManager(PluginManager):
    """A :py:class:`pluggy.PluginManager <pluggy.PluginManager>` with
    additional pytest-specific functionality:

    * Loading plugins from the command line, ``PYTEST_PLUGINS`` env variable and
      ``pytest_plugins`` global variables found in plugins being loaded.
    * ``conftest.py`` loading during start-up.
    """

    def __init__(self) -> None:
        from _pytest.assertion import DummyRewriteHook
        from _pytest.assertion import RewriteHook

        super().__init__("pytest")

        # -- State related to local conftest plugins.
        # All loaded conftest modules.
        self._conftest_plugins: set[types.ModuleType] = set()
        # All conftest modules applicable for a directory.
        # This includes the directory's own conftest modules as well
        # as those of its parent directories.
        self._dirpath2confmods: dict[pathlib.Path, list[types.ModuleType]] = {}
        # Cutoff directory above which conftests are no longer discovered.
        self._confcutdir: pathlib.Path | None = None
        # If set, conftest loading is skipped.
        self._noconftest = False

        # _getconftestmodules()'s call to _get_directory() causes a stat
        # storm when it's called potentially thousands of times in a test
        # session (#9478), often with the same path, so cache it.
        self._get_directory = lru_cache(256)(_get_directory)

        # plugins that were explicitly skipped with pytest.skip
        # list of (module name, skip reason)
        # previously we would issue a warning when a plugin was skipped, but
        # since we refactored warnings as first citizens of Config, they are
        # just stored here to be used later.
        self.skipped_plugins: list[tuple[str, str]] = []

        self.add_hookspecs(_pytest.hookspec)
        self.register(self)
        if os.environ.get("PYTEST_DEBUG"):
            err: IO[str] = sys.stderr
            encoding: str = getattr(err, "encoding", "utf8")
            try:
                err = open(
                    os.dup(err.fileno()),
                    mode=err.mode,
                    buffering=1,
                    encoding=encoding,
                )
            except Exception:
                pass
            self.trace.root.setwriter(err.write)
            self.enable_tracing()

        # Config._consider_importhook will set a real object if required.
        self.rewrite_hook: RewriteHook = DummyRewriteHook()
        # Used to know when we are importing conftests after the pytest_configure stage.
        self._configured = False

    def parse_hookimpl_opts(
        self, plugin: _PluggyPlugin, name: str
    ) -> HookimplOpts | None:
        """:meta private:"""
        # pytest hooks are always prefixed with "pytest_",
        # so we avoid accessing possibly non-readable attributes
        # (see issue #1073).
        if not name.startswith("pytest_"):
            return None
        # Ignore names which cannot be hooks.
        if name == "pytest_plugins":
            return None

        opts = super().parse_hookimpl_opts(plugin, name)
        if opts is not None:
            return opts

        method = getattr(plugin, name)
        # Consider only actual functions for hooks (#3775).
        if not inspect.isroutine(method):
            return None
        # Collect unmarked hooks as long as they have the `pytest_' prefix.
        legacy = _get_legacy_hook_marks(
            method, "impl", ("tryfirst", "trylast", "optionalhook", "hookwrapper")
        )
        return cast(HookimplOpts, legacy)

    def parse_hookspec_opts(self, module_or_class, name: str) -> HookspecOpts | None:
        """:meta private:"""
        opts = super().parse_hookspec_opts(module_or_class, name)
        if opts is None:
            method = getattr(module_or_class, name)
            if name.startswith("pytest_"):
                legacy = _get_legacy_hook_marks(
                    method, "spec", ("firstresult", "historic")
                )
                opts = cast(HookspecOpts, legacy)
        return opts

    def register(self, plugin: _PluggyPlugin, name: str | None = None) -> str | None:
        if name in _pytest.deprecated.DEPRECATED_EXTERNAL_PLUGINS:
            warnings.warn(
                PytestConfigWarning(
                    "{} plugin has been merged into the core, "
                    "please remove it from your requirements.".format(
                        name.replace("_", "-")
                    )
                )
            )
            return None
        plugin_name = super().register(plugin, name)
        if plugin_name is not None:
            self.hook.pytest_plugin_registered.call_historic(
                kwargs=dict(
                    plugin=plugin,
                    plugin_name=plugin_name,
                    manager=self,
                )
            )

            if isinstance(plugin, types.ModuleType):
                self.consider_module(plugin)
        return plugin_name

    def getplugin(self, name: str):
        # Support deprecated naming because plugins (xdist e.g.) use it.
        plugin: _PluggyPlugin | None = self.get_plugin(name)
        return plugin

    def hasplugin(self, name: str) -> bool:
        """Return whether a plugin with the given name is registered."""
        return bool(self.get_plugin(name))

    def pytest_configure(self, config: Config) -> None:
        """:meta private:"""
        # XXX now that the pluginmanager exposes hookimpl(tryfirst...)
        # we should remove tryfirst/trylast as markers.
        config.addinivalue_line(
            "markers",
            "tryfirst: mark a hook implementation function such that the "
            "plugin machinery will try to call it first/as early as possible. "
            "DEPRECATED, use @pytest.hookimpl(tryfirst=True) instead.",
        )
        config.addinivalue_line(
            "markers",
            "trylast: mark a hook implementation function such that the "
            "plugin machinery will try to call it last/as late as possible. "
            "DEPRECATED, use @pytest.hookimpl(trylast=True) instead.",
        )
        self._configured = True

    #
    # Internal API for local conftest plugin handling.
    #
    def _set_initial_conftests(
        self,
        args: Sequence[str | pathlib.Path],
        pyargs: bool,
        noconftest: bool,
        rootpath: pathlib.Path,
        confcutdir: pathlib.Path | None,
        invocation_dir: pathlib.Path,
        importmode: ImportMode | str,
        *,
        consider_namespace_packages: bool,
    ) -> None:
        """Load initial conftest files given a preparsed "namespace".

        As conftest files may add their own command line options which have
        arguments ('--my-opt somepath') we might get some false positives.
        All builtin and 3rd party plugins will have been loaded, however, so
        common options will not confuse our logic here.
        """
        self._confcutdir = (
            absolutepath(invocation_dir / confcutdir) if confcutdir else None
        )
        self._noconftest = noconftest
        self._using_pyargs = pyargs
        foundanchor = False
        for initial_path in args:
            path = str(initial_path)
            # remove node-id syntax
            i = path.find("::")
            if i != -1:
                path = path[:i]
            anchor = absolutepath(invocation_dir / path)

            # Ensure we do not break if what appears to be an anchor
            # is in fact a very long option (#10169, #11394).
            if safe_exists(anchor):
                self._try_load_conftest(
                    anchor,
                    importmode,
                    rootpath,
                    consider_namespace_packages=consider_namespace_packages,
                )
                foundanchor = True
        if not foundanchor:
            self._try_load_conftest(
                invocation_dir,
                importmode,
                rootpath,
                consider_namespace_packages=consider_namespace_packages,
            )

    def _is_in_confcutdir(self, path: pathlib.Path) -> bool:
        """Whether to consider the given path to load conftests from."""
        if self._confcutdir is None:
            return True
        # The semantics here are literally:
        #   Do not load a conftest if it is found upwards from confcut dir.
        # But this is *not* the same as:
        #   Load only conftests from confcutdir or below.
        # At first glance they might seem the same thing, however we do support use cases where
        # we want to load conftests that are not found in confcutdir or below, but are found
        # in completely different directory hierarchies like packages installed
        # in out-of-source trees.
        # (see #9767 for a regression where the logic was inverted).
        return path not in self._confcutdir.parents

    def _try_load_conftest(
        self,
        anchor: pathlib.Path,
        importmode: str | ImportMode,
        rootpath: pathlib.Path,
        *,
        consider_namespace_packages: bool,
    ) -> None:
        self._loadconftestmodules(
            anchor,
            importmode,
            rootpath,
            consider_namespace_packages=consider_namespace_packages,
        )
        # let's also consider test* subdirs
        if anchor.is_dir():
            for x in anchor.glob("test*"):
                if x.is_dir():
                    self._loadconftestmodules(
                        x,
                        importmode,
                        rootpath,
                        consider_namespace_packages=consider_namespace_packages,
                    )

    def _loadconftestmodules(
        self,
        path: pathlib.Path,
        importmode: str | ImportMode,
        rootpath: pathlib.Path,
        *,
        consider_namespace_packages: bool,
    ) -> None:
        if self._noconftest:
            return

        directory = self._get_directory(path)

        # Optimization: avoid repeated searches in the same directory.
        # Assumes always called with same importmode and rootpath.
        if directory in self._dirpath2confmods:
            return

        clist = []
        for parent in reversed((directory, *directory.parents)):
            if self._is_in_confcutdir(parent):
                conftestpath = parent / "conftest.py"
                if conftestpath.is_file():
                    mod = self._importconftest(
                        conftestpath,
                        importmode,
                        rootpath,
                        consider_namespace_packages=consider_namespace_packages,
                    )
                    clist.append(mod)
        self._dirpath2confmods[directory] = clist

    def _getconftestmodules(self, path: pathlib.Path) -> Sequence[types.ModuleType]:
        directory = self._get_directory(path)
        return self._dirpath2confmods.get(directory, ())

    def _rget_with_confmod(
        self,
        name: str,
        path: pathlib.Path,
    ) -> tuple[types.ModuleType, Any]:
        modules = self._getconftestmodules(path)
        for mod in reversed(modules):
            try:
                return mod, getattr(mod, name)
            except AttributeError:
                continue
        raise KeyError(name)

    def _importconftest(
        self,
        conftestpath: pathlib.Path,
        importmode: str | ImportMode,
        rootpath: pathlib.Path,
        *,
        consider_namespace_packages: bool,
    ) -> types.ModuleType:
        conftestpath_plugin_name = str(conftestpath)
        existing = self.get_plugin(conftestpath_plugin_name)
        if existing is not None:
            return cast(types.ModuleType, existing)

        # conftest.py files there are not in a Python package all have module
        # name "conftest", and thus conflict with each other. Clear the existing
        # before loading the new one, otherwise the existing one will be
        # returned from the module cache.
        pkgpath = resolve_package_path(conftestpath)
        if pkgpath is None:
            try:
                del sys.modules[conftestpath.stem]
            except KeyError:
                pass

        try:
            mod = import_path(
                conftestpath,
                mode=importmode,
                root=rootpath,
                consider_namespace_packages=consider_namespace_packages,
            )
        except Exception as e:
            assert e.__traceback__ is not None
            raise ConftestImportFailure(conftestpath, cause=e) from e

        self._check_non_top_pytest_plugins(mod, conftestpath)

        self._conftest_plugins.add(mod)
        dirpath = conftestpath.parent
        if dirpath in self._dirpath2confmods:
            for path, mods in self._dirpath2confmods.items():
                if dirpath in path.parents or path == dirpath:
                    if mod in mods:
                        raise AssertionError(
                            f"While trying to load conftest path {conftestpath!s}, "
                            f"found that the module {mod} is already loaded with path {mod.__file__}. "
                            "This is not supposed to happen. Please report this issue to pytest."
                        )
                    mods.append(mod)
        self.trace(f"loading conftestmodule {mod!r}")
        self.consider_conftest(mod, registration_name=conftestpath_plugin_name)
        return mod

    def _check_non_top_pytest_plugins(
        self,
        mod: types.ModuleType,
        conftestpath: pathlib.Path,
    ) -> None:
        if (
            hasattr(mod, "pytest_plugins")
            and self._configured
            and not self._using_pyargs
        ):
            msg = (
                "Defining 'pytest_plugins' in a non-top-level conftest is no longer supported:\n"
                "It affects the entire test suite instead of just below the conftest as expected.\n"
                "  {}\n"
                "Please move it to a top level conftest file at the rootdir:\n"
                "  {}\n"
                "For more information, visit:\n"
                "  https://docs.pytest.org/en/stable/deprecations.html#pytest-plugins-in-non-top-level-conftest-files"
            )
            fail(msg.format(conftestpath, self._confcutdir), pytrace=False)

    #
    # API for bootstrapping plugin loading
    #
    #

    def consider_preparse(
        self, args: Sequence[str], *, exclude_only: bool = False
    ) -> None:
        """:meta private:"""
        i = 0
        n = len(args)
        while i < n:
            opt = args[i]
            i += 1
            if isinstance(opt, str):
                if opt == "-p":
                    try:
                        parg = args[i]
                    except IndexError:
                        return
                    i += 1
                elif opt.startswith("-p"):
                    parg = opt[2:]
                else:
                    continue
                parg = parg.strip()
                if exclude_only and not parg.startswith("no:"):
                    continue
                self.consider_pluginarg(parg)

    def consider_pluginarg(self, arg: str) -> None:
        """:meta private:"""
        if arg.startswith("no:"):
            name = arg[3:]
            if name in essential_plugins:
                raise UsageError(f"plugin {name} cannot be disabled")

            if name.endswith("conftest.py"):
                raise UsageError(
                    f"Blocking conftest files using -p is not supported: -p no:{name}\n"
                    "conftest.py files are not plugins and cannot be disabled via -p.\n"
                )

            # PR #4304: remove stepwise if cacheprovider is blocked.
            if name == "cacheprovider":
                self.set_blocked("stepwise")
                self.set_blocked("pytest_stepwise")

            self.set_blocked(name)
            if not name.startswith("pytest_"):
                self.set_blocked("pytest_" + name)
        else:
            name = arg
            # Unblock the plugin.
            self.unblock(name)
            if not name.startswith("pytest_"):
                self.unblock("pytest_" + name)
            self.import_plugin(arg, consider_entry_points=True)

    def consider_conftest(
        self, conftestmodule: types.ModuleType, registration_name: str
    ) -> None:
        """:meta private:"""
        self.register(conftestmodule, name=registration_name)

    def consider_env(self) -> None:
        """:meta private:"""
        self._import_plugin_specs(os.environ.get("PYTEST_PLUGINS"))

    def consider_module(self, mod: types.ModuleType) -> None:
        """:meta private:"""
        self._import_plugin_specs(getattr(mod, "pytest_plugins", []))

    def _import_plugin_specs(
        self, spec: None | types.ModuleType | str | Sequence[str]
    ) -> None:
        plugins = _get_plugin_specs_as_list(spec)
        for import_spec in plugins:
            self.import_plugin(import_spec)

    def import_plugin(self, modname: str, consider_entry_points: bool = False) -> None:
        """Import a plugin with ``modname``.

        If ``consider_entry_points`` is True, entry point names are also
        considered to find a plugin.
        """
        # Most often modname refers to builtin modules, e.g. "pytester",
        # "terminal" or "capture".  Those plugins are registered under their
        # basename for historic purposes but must be imported with the
        # _pytest prefix.
        assert isinstance(modname, str), (
            f"module name as text required, got {modname!r}"
        )
        if self.is_blocked(modname) or self.get_plugin(modname) is not None:
            return

        importspec = "_pytest." + modname if modname in builtin_plugins else modname
        self.rewrite_hook.mark_rewrite(importspec)

        if consider_entry_points:
            loaded = self.load_setuptools_entrypoints("pytest11", name=modname)
            if loaded:
                return

        try:
            __import__(importspec)
        except ImportError as e:
            raise ImportError(
                f'Error importing plugin "{modname}": {e.args[0]}'
            ).with_traceback(e.__traceback__) from e

        except Skipped as e:
            self.skipped_plugins.append((modname, e.msg or ""))
        else:
            mod = sys.modules[importspec]
            self.register(mod, modname)


def _get_plugin_specs_as_list(
    specs: None | types.ModuleType | str | Sequence[str],
) -> list[str]:
    """Parse a plugins specification into a list of plugin names."""
    # None means empty.
    if specs is None:
        return []
    # Workaround for #3899 - a submodule which happens to be called "pytest_plugins".
    if isinstance(specs, types.ModuleType):
        return []
    # Comma-separated list.
    if isinstance(specs, str):
        return specs.split(",") if specs else []
    # Direct specification.
    if isinstance(specs, collections.abc.Sequence):
        return list(specs)
    raise UsageError(
        f"Plugins may be specified as a sequence or a ','-separated string of plugin names. Got: {specs!r}"
    )


class Notset:
    def __repr__(self):
        return "<NOTSET>"


notset = Notset()


def _iter_rewritable_modules(package_files: Iterable[str]) -> Iterator[str]:
    """Given an iterable of file names in a source distribution, return the "names" that should
    be marked for assertion rewrite.

    For example the package "pytest_mock/__init__.py" should be added as "pytest_mock" in
    the assertion rewrite mechanism.

    This function has to deal with dist-info based distributions and egg based distributions
    (which are still very much in use for "editable" installs).

    Here are the file names as seen in a dist-info based distribution:

        pytest_mock/__init__.py
        pytest_mock/_version.py
        pytest_mock/plugin.py
        pytest_mock.egg-info/PKG-INFO

    Here are the file names as seen in an egg based distribution:

        src/pytest_mock/__init__.py
        src/pytest_mock/_version.py
        src/pytest_mock/plugin.py
        src/pytest_mock.egg-info/PKG-INFO
        LICENSE
        setup.py

    We have to take in account those two distribution flavors in order to determine which
    names should be considered for assertion rewriting.

    More information:
        https://github.com/pytest-dev/pytest-mock/issues/167
    """
    package_files = list(package_files)
    seen_some = False
    for fn in package_files:
        is_simple_module = "/" not in fn and fn.endswith(".py")
        is_package = fn.count("/") == 1 and fn.endswith("__init__.py")
        if is_simple_module:
            module_name, _ = os.path.splitext(fn)
            # we ignore "setup.py" at the root of the distribution
            # as well as editable installation finder modules made by setuptools
            if module_name != "setup" and not module_name.startswith("__editable__"):
                seen_some = True
                yield module_name
        elif is_package:
            package_name = os.path.dirname(fn)
            seen_some = True
            yield package_name

    if not seen_some:
        # At this point we did not find any packages or modules suitable for assertion
        # rewriting, so we try again by stripping the first path component (to account for
        # "src" based source trees for example).
        # This approach lets us have the common case continue to be fast, as egg-distributions
        # are rarer.
        new_package_files = []
        for fn in package_files:
            parts = fn.split("/")
            new_fn = "/".join(parts[1:])
            if new_fn:
                new_package_files.append(new_fn)
        if new_package_files:
            yield from _iter_rewritable_modules(new_package_files)


class _DeprecatedInicfgProxy(MutableMapping[str, Any]):
    """Compatibility proxy for the deprecated Config.inicfg."""

    __slots__ = ("_config",)

    def __init__(self, config: Config) -> None:
        self._config = config

    def __getitem__(self, key: str) -> Any:
