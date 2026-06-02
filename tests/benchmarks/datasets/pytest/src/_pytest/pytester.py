# mypy: allow-untyped-defs
"""(Disabled by default) support for testing pytest and pytest plugins.

PYTEST_DONT_REWRITE
"""

from __future__ import annotations

import collections.abc
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Sequence
import contextlib
from fnmatch import fnmatch
import gc
import importlib
from io import StringIO
import locale
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import traceback
from typing import Any
from typing import Final
from typing import final
from typing import IO
from typing import Literal
from typing import overload
from typing import TextIO
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from iniconfig import IniConfig
from iniconfig import SectionWrapper

from _pytest import timing
from _pytest._code import Source
from _pytest.capture import _get_multicapture
from _pytest.compat import NOTSET
from _pytest.compat import NotSetType
from _pytest.config import _PluggyPlugin
from _pytest.config import Config
from _pytest.config import ExitCode
from _pytest.config import hookimpl
from _pytest.config import main
from _pytest.config import PytestPluginManager
from _pytest.config.argparsing import Parser
from _pytest.deprecated import check_ispytest
from _pytest.fixtures import fixture
from _pytest.fixtures import FixtureRequest
from _pytest.main import Session
from _pytest.monkeypatch import MonkeyPatch
from _pytest.nodes import Collector
from _pytest.nodes import Item
from _pytest.outcomes import fail
from _pytest.outcomes import importorskip
from _pytest.outcomes import skip
from _pytest.pathlib import bestrelpath
from _pytest.pathlib import make_numbered_dir
from _pytest.reports import CollectReport
from _pytest.reports import TestReport
from _pytest.tmpdir import TempPathFactory
from _pytest.warning_types import PytestFDWarning


if TYPE_CHECKING:
    import pexpect


pytest_plugins = ["pytester_assertions"]


IGNORE_PAM = [  # filenames added when obtaining details about the current user
    "/var/lib/sss/mc/passwd"
]


def pytest_addoption(parser: Parser) -> None:
    parser.addoption(
        "--lsof",
        action="store_true",
        dest="lsof",
        default=False,
        help="Run FD checks if lsof is available",
    )

    parser.addoption(
        "--runpytest",
        default="inprocess",
        dest="runpytest",
        choices=("inprocess", "subprocess"),
        help=(
            "Run pytest sub runs in tests using an 'inprocess' "
            "or 'subprocess' (python -m main) method"
        ),
    )

    parser.addini(
        "pytester_example_dir", help="Directory to take the pytester example files from"
    )


def pytest_configure(config: Config) -> None:
    if config.getvalue("lsof"):
        checker = LsofFdLeakChecker()
        if checker.matching_platform():
            config.pluginmanager.register(checker)

    config.addinivalue_line(
        "markers",
        "pytester_example_path(*path_segments): join the given path "
        "segments to `pytester_example_dir` for this test.",
    )


class LsofFdLeakChecker:
    def get_open_files(self) -> list[tuple[str, str]]:
        if sys.version_info >= (3, 11):
            # New in Python 3.11, ignores utf-8 mode
            encoding = locale.getencoding()
        else:
            encoding = locale.getpreferredencoding(False)
        out = subprocess.run(
            ("lsof", "-Ffn0", "-p", str(os.getpid())),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True,
            encoding=encoding,
        ).stdout

        def isopen(line: str) -> bool:
            return line.startswith("f") and (
                "deleted" not in line
                and "mem" not in line
                and "txt" not in line
                and "cwd" not in line
            )

        open_files = []

        for line in out.split("\n"):
            if isopen(line):
                fields = line.split("\0")
                fd = fields[0][1:]
                filename = fields[1][1:]
                if filename in IGNORE_PAM:
                    continue
                if filename.startswith("/"):
                    open_files.append((fd, filename))

        return open_files

    def matching_platform(self) -> bool:
        try:
            subprocess.run(("lsof", "-v"), check=True)
        except (OSError, subprocess.CalledProcessError):
            return False
        else:
            return True

    @hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: Item) -> Generator[None, object, object]:
        lines1 = self.get_open_files()
        try:
            return (yield)
        finally:
            if hasattr(sys, "pypy_version_info"):
                gc.collect()
            lines2 = self.get_open_files()

            new_fds = {t[0] for t in lines2} - {t[0] for t in lines1}
            leaked_files = [t for t in lines2 if t[0] in new_fds]
            if leaked_files:
                error = [
                    f"***** {len(leaked_files)} FD leakage detected",
                    *(str(f) for f in leaked_files),
                    "*** Before:",
                    *(str(f) for f in lines1),
                    "*** After:",
                    *(str(f) for f in lines2),
                    f"***** {len(leaked_files)} FD leakage detected",
                    "*** function {}:{}: {} ".format(*item.location),
                    "See issue #2366",
                ]
                item.warn(PytestFDWarning("\n".join(error)))


# used at least by pytest-xdist plugin


@fixture
def _pytest(request: FixtureRequest) -> PytestArg:
    """Return a helper which offers a gethookrecorder(hook) method which
    returns a HookRecorder instance which helps to make assertions about called
    hooks."""
    return PytestArg(request)


class PytestArg:
    def __init__(self, request: FixtureRequest) -> None:
        self._request = request

    def gethookrecorder(self, hook) -> HookRecorder:
        hookrecorder = HookRecorder(hook._pm)
        self._request.addfinalizer(hookrecorder.finish_recording)
        return hookrecorder


def get_public_names(values: Iterable[str]) -> list[str]:
    """Only return names from iterator values without a leading underscore."""
    return [x for x in values if x[0] != "_"]


@final
class RecordedHookCall:
    """A recorded call to a hook.

    The arguments to the hook call are set as attributes.
    For example:

    .. code-block:: python

        calls = hook_recorder.getcalls("pytest_runtest_setup")
        # Suppose pytest_runtest_setup was called once with `item=an_item`.
        assert calls[0].item is an_item
    """

    def __init__(self, name: str, kwargs) -> None:
        self.__dict__.update(kwargs)
        self._name = name

    def __repr__(self) -> str:
        d = self.__dict__.copy()
        del d["_name"]
        return f"<RecordedHookCall {self._name!r}(**{d!r})>"

    if TYPE_CHECKING:
        # The class has undetermined attributes, this tells mypy about it.
        def __getattr__(self, key: str): ...


@final
class HookRecorder:
    """Record all hooks called in a plugin manager.

    Hook recorders are created by :class:`Pytester`.

    This wraps all the hook calls in the plugin manager, recording each call
    before propagating the normal calls.
    """

    def __init__(
        self, pluginmanager: PytestPluginManager, *, _ispytest: bool = False
    ) -> None:
        check_ispytest(_ispytest)

        self._pluginmanager = pluginmanager
        self.calls: list[RecordedHookCall] = []
        self.ret: int | ExitCode | None = None

        def before(hook_name: str, hook_impls, kwargs) -> None:
            self.calls.append(RecordedHookCall(hook_name, kwargs))

        def after(outcome, hook_name: str, hook_impls, kwargs) -> None:
            pass

        self._undo_wrapping = pluginmanager.add_hookcall_monitoring(before, after)

    def finish_recording(self) -> None:
        self._undo_wrapping()

    def getcalls(self, names: str | Iterable[str]) -> list[RecordedHookCall]:
        """Get all recorded calls to hooks with the given names (or name)."""
        if isinstance(names, str):
            names = names.split()
        return [call for call in self.calls if call._name in names]

    def assert_contains(self, entries: Sequence[tuple[str, str]]) -> None:
        __tracebackhide__ = True
        i = 0
        entries = list(entries)
        # Since Python 3.13, f_locals is not a dict, but eval requires a dict.
        backlocals = dict(sys._getframe(1).f_locals)
        while entries:
            name, check = entries.pop(0)
            for ind, call in enumerate(self.calls[i:]):
                if call._name == name:
                    print("NAMEMATCH", name, call)
                    if eval(check, backlocals, call.__dict__):
                        print("CHECKERMATCH", repr(check), "->", call)
                    else:
                        print("NOCHECKERMATCH", repr(check), "-", call)
                        continue
                    i += ind + 1
                    break
                print("NONAMEMATCH", name, "with", call)
            else:
                fail(f"could not find {name!r} check {check!r}")

    def popcall(self, name: str) -> RecordedHookCall:
        __tracebackhide__ = True
        for i, call in enumerate(self.calls):
            if call._name == name:
                del self.calls[i]
                return call
        lines = [f"could not find call {name!r}, in:"]
        lines.extend([f"  {x}" for x in self.calls])
        fail("\n".join(lines))

    def getcall(self, name: str) -> RecordedHookCall:
        values = self.getcalls(name)
        assert len(values) == 1, (name, values)
        return values[0]

    # functionality for test reports

    @overload
    def getreports(
        self,
        names: Literal["pytest_collectreport"],
    ) -> Sequence[CollectReport]: ...

    @overload
    def getreports(
        self,
        names: Literal["pytest_runtest_logreport"],
    ) -> Sequence[TestReport]: ...

    @overload
    def getreports(
        self,
        names: str | Iterable[str] = (
            "pytest_collectreport",
            "pytest_runtest_logreport",
        ),
    ) -> Sequence[CollectReport | TestReport]: ...

    def getreports(
        self,
        names: str | Iterable[str] = (
            "pytest_collectreport",
            "pytest_runtest_logreport",
        ),
    ) -> Sequence[CollectReport | TestReport]:
        return [x.report for x in self.getcalls(names)]

    def matchreport(
        self,
        inamepart: str = "",
        names: str | Iterable[str] = (
            "pytest_runtest_logreport",
            "pytest_collectreport",
        ),
        when: str | None = None,
    ) -> CollectReport | TestReport:
        """Return a testreport whose dotted import path matches."""
        values = []
        for rep in self.getreports(names=names):
            if not when and rep.when != "call" and rep.passed:
                # setup/teardown passing reports - let's ignore those
                continue
            if when and rep.when != when:
                continue
            if not inamepart or inamepart in rep.nodeid.split("::"):
                values.append(rep)
        if not values:
            raise ValueError(
                f"could not find test report matching {inamepart!r}: "
                "no test reports at all!"
            )
        if len(values) > 1:
            raise ValueError(
                f"found 2 or more testreports matching {inamepart!r}: {values}"
            )
        return values[0]

    @overload
    def getfailures(
        self,
        names: Literal["pytest_collectreport"],
    ) -> Sequence[CollectReport]: ...

    @overload
    def getfailures(
        self,
        names: Literal["pytest_runtest_logreport"],
    ) -> Sequence[TestReport]: ...

    @overload
    def getfailures(
        self,
        names: str | Iterable[str] = (
            "pytest_collectreport",
            "pytest_runtest_logreport",
        ),
    ) -> Sequence[CollectReport | TestReport]: ...

    def getfailures(
        self,
        names: str | Iterable[str] = (
            "pytest_collectreport",
            "pytest_runtest_logreport",
        ),
    ) -> Sequence[CollectReport | TestReport]:
        return [rep for rep in self.getreports(names) if rep.failed]

    def getfailedcollections(self) -> Sequence[CollectReport]:
        return self.getfailures("pytest_collectreport")

    def listoutcomes(
        self,
    ) -> tuple[
        Sequence[TestReport],
        Sequence[CollectReport | TestReport],
        Sequence[CollectReport | TestReport],
    ]:
        passed = []
        skipped = []
        failed = []
        for rep in self.getreports(
            ("pytest_collectreport", "pytest_runtest_logreport")
        ):
            if rep.passed:
                if rep.when == "call":
                    assert isinstance(rep, TestReport)
                    passed.append(rep)
            elif rep.skipped:
                skipped.append(rep)
            else:
                assert rep.failed, f"Unexpected outcome: {rep!r}"
                failed.append(rep)
        return passed, skipped, failed

    def countoutcomes(self) -> list[int]:
        return [len(x) for x in self.listoutcomes()]

    def assertoutcome(self, passed: int = 0, skipped: int = 0, failed: int = 0) -> None:
        __tracebackhide__ = True
        from _pytest.pytester_assertions import assertoutcome

        outcomes = self.listoutcomes()
        assertoutcome(
            outcomes,
            passed=passed,
            skipped=skipped,
            failed=failed,
        )

    def clear(self) -> None:
        self.calls[:] = []


@fixture
def linecomp() -> LineComp:
    """A :class: `LineComp` instance for checking that an input linearly
    contains a sequence of strings."""
    return LineComp()


@fixture(name="LineMatcher")
def LineMatcher_fixture(request: FixtureRequest) -> type[LineMatcher]:
    """A reference to the :class: `LineMatcher`.

    This is instantiable with a list of lines (without their trailing newlines).
    This is useful for testing large texts, such as the output of commands.
    """
    return LineMatcher


@fixture
def pytester(
    request: FixtureRequest, tmp_path_factory: TempPathFactory, monkeypatch: MonkeyPatch
) -> Pytester:
    """
    Facilities to write tests/configuration files, execute pytest in isolation, and match
    against expected output, perfect for black-box testing of pytest plugins.

    It attempts to isolate the test run from external factors as much as possible, modifying
    the current working directory to ``path`` and environment variables during initialization.

    It is particularly useful for testing plugins. It is similar to the :fixture:`tmp_path`
    fixture but provides methods which aid in testing pytest itself.
    """
    return Pytester(request, tmp_path_factory, monkeypatch, _ispytest=True)


@fixture
def _sys_snapshot() -> Generator[None]:
    snappaths = SysPathsSnapshot()
    snapmods = SysModulesSnapshot()
    yield
    snapmods.restore()
    snappaths.restore()


@fixture
def _config_for_test() -> Generator[Config]:
    from _pytest.config import get_config

    config = get_config()
    yield config
    config._ensure_unconfigure()  # cleanup, e.g. capman closing tmpfiles.


# Regex to match the session duration string in the summary: "74.34s".
rex_session_duration = re.compile(r"\d+\.\d\ds")
# Regex to match all the counts and phrases in the summary line: "34 passed, 111 skipped".
rex_outcome = re.compile(r"(\d+) (\w+)")


@final
class RunResult:
    """The result of running a command from :class:`~pytest.Pytester`."""

    def __init__(
        self,
        ret: int | ExitCode,
        outlines: list[str],
        errlines: list[str],
        duration: float,
    ) -> None:
        try:
            self.ret: int | ExitCode = ExitCode(ret)
            """The return value."""
        except ValueError:
            self.ret = ret
        self.outlines = outlines
        """List of lines captured from stdout."""
        self.errlines = errlines
        """List of lines captured from stderr."""
        self.stdout = LineMatcher(outlines)
        """:class:`~pytest.LineMatcher` of stdout.

        Use e.g. :func:`str(stdout) <pytest.LineMatcher.__str__()>` to reconstruct stdout, or the commonly used
        :func:`stdout.fnmatch_lines() <pytest.LineMatcher.fnmatch_lines()>` method.
        """
        self.stderr = LineMatcher(errlines)
        """:class:`~pytest.LineMatcher` of stderr."""
        self.duration = duration
        """Duration in seconds."""

    def __repr__(self) -> str:
        return (
            f"<RunResult ret={self.ret!s} "
            f"len(stdout.lines)={len(self.stdout.lines)} "
            f"len(stderr.lines)={len(self.stderr.lines)} "
            f"duration={self.duration:.2f}s>"
        )

    def parseoutcomes(self) -> dict[str, int]:
        """Return a dictionary of outcome noun -> count from parsing the terminal
        output that the test process produced.

        The returned nouns will always be in plural form::

            ======= 1 failed, 1 passed, 1 warning, 1 error in 0.13s ====

        Will return ``{"failed": 1, "passed": 1, "warnings": 1, "errors": 1}``.
        """
        return self.parse_summary_nouns(self.outlines)

    @classmethod
    def parse_summary_nouns(cls, lines) -> dict[str, int]:
        """Extract the nouns from a pytest terminal summary line.

        It always returns the plural noun for consistency::

            ======= 1 failed, 1 passed, 1 warning, 1 error in 0.13s ====

        Will return ``{"failed": 1, "passed": 1, "warnings": 1, "errors": 1}``.
        """
        for line in reversed(lines):
            if rex_session_duration.search(line):
                outcomes = rex_outcome.findall(line)
                ret = {noun: int(count) for (count, noun) in outcomes}
                break
        else:
            raise ValueError("Pytest terminal summary report not found")

        to_plural = {
            "warning": "warnings",
            "error": "errors",
        }
        return {to_plural.get(k, k): v for k, v in ret.items()}

    def assert_outcomes(
        self,
        passed: int = 0,
        skipped: int = 0,
        failed: int = 0,
        errors: int = 0,
        xpassed: int = 0,
        xfailed: int = 0,
        warnings: int | None = None,
        deselected: int | None = None,
    ) -> None:
        """
        Assert that the specified outcomes appear with the respective
        numbers (0 means it didn't occur) in the text output from a test run.

        ``warnings`` and ``deselected`` are only checked if not None.
        """
        __tracebackhide__ = True
        from _pytest.pytester_assertions import assert_outcomes

        outcomes = self.parseoutcomes()
        assert_outcomes(
            outcomes,
            passed=passed,
            skipped=skipped,
            failed=failed,
            errors=errors,
            xpassed=xpassed,
            xfailed=xfailed,
            warnings=warnings,
            deselected=deselected,
        )


class SysModulesSnapshot:
    def __init__(self, preserve: Callable[[str], bool] | None = None) -> None:
        self.__preserve = preserve
        self.__saved = dict(sys.modules)

    def restore(self) -> None:
        if self.__preserve:
            self.__saved.update(
                (k, m) for k, m in sys.modules.items() if self.__preserve(k)
            )
        sys.modules.clear()
        sys.modules.update(self.__saved)


class SysPathsSnapshot:
    def __init__(self) -> None:
        self.__saved = list(sys.path), list(sys.meta_path)

    def restore(self) -> None:
        sys.path[:], sys.meta_path[:] = self.__saved


@final
class Pytester:
    """
    Facilities to write tests/configuration files, execute pytest in isolation, and match
    against expected output, perfect for black-box testing of pytest plugins.

    It attempts to isolate the test run from external factors as much as possible, modifying
    the current working directory to :attr:`path` and environment variables during initialization.
    """

    __test__ = False

    CLOSE_STDIN: Final = NOTSET

    class TimeoutExpired(Exception):
        pass

    def __init__(
        self,
        request: FixtureRequest,
        tmp_path_factory: TempPathFactory,
        monkeypatch: MonkeyPatch,
        *,
        _ispytest: bool = False,
    ) -> None:
        check_ispytest(_ispytest)
        self._request = request
        self._mod_collections: WeakKeyDictionary[Collector, list[Item | Collector]] = (
            WeakKeyDictionary()
        )
        if request.function:
            name: str = request.function.__name__
        else:
            name = request.node.name
        self._name = name
        self._path: Path = tmp_path_factory.mktemp(name, numbered=True)
        #: A list of plugins to use with :py:meth:`parseconfig` and
        #: :py:meth:`runpytest`. Initially this is an empty list but plugins can
        #: be added to the list.
        #:
        #: When running in subprocess mode, specify plugins by name (str) - adding
        #: plugin objects directly is not supported.
        self.plugins: list[str | _PluggyPlugin] = []
        self._sys_path_snapshot = SysPathsSnapshot()
        self._sys_modules_snapshot = self.__take_sys_modules_snapshot()
        self._request.addfinalizer(self._finalize)
        self._method = self._request.config.getoption("--runpytest")
        self._test_tmproot = tmp_path_factory.mktemp(f"tmp-{name}", numbered=True)

        self._monkeypatch = mp = monkeypatch
        self.chdir()
        mp.setenv("PYTEST_DEBUG_TEMPROOT", str(self._test_tmproot))
        # Ensure no unexpected caching via tox.
        mp.delenv("TOX_ENV_DIR", raising=False)
        # Discard outer pytest options.
        mp.delenv("PYTEST_ADDOPTS", raising=False)
        # Ensure no user config is used.
        tmphome = str(self.path)
        mp.setenv("HOME", tmphome)
        mp.setenv("USERPROFILE", tmphome)
        # Do not use colors for inner runs by default.
        mp.setenv("PY_COLORS", "0")

    @property
    def path(self) -> Path:
        """Temporary directory path used to create files/run tests from, etc."""
        return self._path

    def __repr__(self) -> str:
        return f"<Pytester {self.path!r}>"

    def _finalize(self) -> None:
        """
        Clean up global state artifacts.

        Some methods modify the global interpreter state and this tries to
        clean this up. It does not remove the temporary directory however so
        it can be looked at after the test run has finished.
        """
        self._sys_modules_snapshot.restore()
        self._sys_path_snapshot.restore()

    def __take_sys_modules_snapshot(self) -> SysModulesSnapshot:
        # Some zope modules used by twisted-related tests keep internal state
        # and can't be deleted; we had some trouble in the past with
        # `zope.interface` for example.
        #
        # Preserve readline due to https://bugs.python.org/issue41033.
        # pexpect issues a SIGWINCH.
        def preserve_module(name):
            return name.startswith(("zope", "readline"))

        return SysModulesSnapshot(preserve=preserve_module)

    def make_hook_recorder(self, pluginmanager: PytestPluginManager) -> HookRecorder:
        """Create a new :class:`HookRecorder` for a :class:`PytestPluginManager`."""
        pluginmanager.reprec = reprec = HookRecorder(pluginmanager, _ispytest=True)  # type: ignore[attr-defined]
        self._request.addfinalizer(reprec.finish_recording)
        return reprec

    def chdir(self) -> None:
        """Cd into the temporary directory.

        This is done automatically upon instantiation.
        """
        self._monkeypatch.chdir(self.path)

    def _makefile(
        self,
        ext: str,
        lines: Sequence[Any | bytes],
        files: dict[str, str],
        encoding: str = "utf-8",
    ) -> Path:
        items = list(files.items())

        if ext is None:
            raise TypeError("ext must not be None")

        if ext and not ext.startswith("."):
            raise ValueError(
                f"pytester.makefile expects a file extension, try .{ext} instead of {ext}"
            )

        def to_text(s: Any | bytes) -> str:
            return s.decode(encoding) if isinstance(s, bytes) else str(s)

        if lines:
            source = "\n".join(to_text(x) for x in lines)
            basename = self._name
            items.insert(0, (basename, source))

        ret = None
        for basename, value in items:
            p = self.path.joinpath(basename).with_suffix(ext)
            p.parent.mkdir(parents=True, exist_ok=True)
            source_ = Source(value)
            source = "\n".join(to_text(line) for line in source_.lines)
            p.write_text(source.strip(), encoding=encoding)
            if ret is None:
                ret = p
        assert ret is not None
        return ret

    def makefile(self, ext: str, *args: str, **kwargs: str) -> Path:
        r"""Create new text file(s) in the test directory.

        :param ext:
            The extension the file(s) should use, including the dot, e.g. `.py`.
        :param args:
            All args are treated as strings and joined using newlines.
            The result is written as contents to the file.  The name of the
            file is based on the test function requesting this fixture.
        :param kwargs:
            Each keyword is the name of a file, while the value of it will
            be written as contents of the file.
        :returns:
            The first created file.

        Examples:

        .. code-block:: python

            pytester.makefile(".txt", "line1", "line2")

            pytester.makefile(".ini", pytest="[pytest]\naddopts=-rs\n")

        To create binary files, use :meth:`pathlib.Path.write_bytes` directly:

        .. code-block:: python

            filename = pytester.path.joinpath("foo.bin")
            filename.write_bytes(b"...")
        """
        return self._makefile(ext, args, kwargs)

    def makeconftest(self, source: str) -> Path:
        """Write a conftest.py file.

        :param source: The contents.
        :returns: The conftest.py file.
        """
        return self.makepyfile(conftest=source)

    def makeini(self, source: str) -> Path:
        """Write a tox.ini file.

        :param source: The contents.
        :returns: The tox.ini file.
        """
        return self.makefile(".ini", tox=source)

    def maketoml(self, source: str) -> Path:
        """Write a pytest.toml file.

        :param source: The contents.
        :returns: The pytest.toml file.

        .. versionadded:: 9.0
        """
        return self.makefile(".toml", pytest=source)

    def getinicfg(self, source: str) -> SectionWrapper:
        """Return the pytest section from the tox.ini config file."""
        p = self.makeini(source)
        return IniConfig(str(p))["pytest"]

    def makepyprojecttoml(self, source: str) -> Path:
        """Write a pyproject.toml file.

        :param source: The contents.
        :returns: The pyproject.ini file.

        .. versionadded:: 6.0
        """
        return self.makefile(".toml", pyproject=source)

    def makepyfile(self, *args, **kwargs) -> Path:
        r"""Shortcut for .makefile() with a .py extension.

        Defaults to the test name with a '.py' extension, e.g test_foobar.py, overwriting
        existing files.

        Examples:

        .. code-block:: python

            def test_something(pytester):
                # Initial file is created test_something.py.
                pytester.makepyfile("foobar")
                # To create multiple files, pass kwargs accordingly.
                pytester.makepyfile(custom="foobar")
                # At this point, both 'test_something.py' & 'custom.py' exist in the test directory.

        """
        return self._makefile(".py", args, kwargs)

    def maketxtfile(self, *args, **kwargs) -> Path:
        r"""Shortcut for .makefile() with a .txt extension.

        Defaults to the test name with a '.txt' extension, e.g test_foobar.txt, overwriting
        existing files.

        Examples:

        .. code-block:: python

            def test_something(pytester):
                # Initial file is created test_something.txt.
                pytester.maketxtfile("foobar")
                # To create multiple files, pass kwargs accordingly.
                pytester.maketxtfile(custom="foobar")
                # At this point, both 'test_something.txt' & 'custom.txt' exist in the test directory.

        """
        return self._makefile(".txt", args, kwargs)

    def syspathinsert(self, path: str | os.PathLike[str] | None = None) -> None:
        """Prepend a directory to sys.path, defaults to :attr:`path`.

        This is undone automatically when this object dies at the end of each
        test.

        :param path:
            The path.
        """
        if path is None:
            path = self.path

        self._monkeypatch.syspath_prepend(str(path))

    def mkdir(self, name: str | os.PathLike[str]) -> Path:
        """Create a new (sub)directory.

        :param name:
            The name of the directory, relative to the pytester path.
        :returns:
            The created directory.
        :rtype: pathlib.Path
        """
        p = self.path / name
        p.mkdir()
        return p

    def mkpydir(self, name: str | os.PathLike[str]) -> Path:
        """Create a new python package.

        This creates a (sub)directory with an empty ``__init__.py`` file so it
        gets recognised as a Python package.
        """
        p = self.path / name
        p.mkdir()
        p.joinpath("__init__.py").touch()
        return p

    def copy_example(self, name: str | None = None) -> Path:
        """Copy file from project's directory into the testdir.

        :param name:
            The name of the file to copy.
        :return:
            Path to the copied directory (inside ``self.path``).
        :rtype: pathlib.Path
        """
        example_dir_ = self._request.config.getini("pytester_example_dir")
        if example_dir_ is None:
            raise ValueError("pytester_example_dir is unset, can't copy examples")
        example_dir: Path = self._request.config.rootpath / example_dir_

        for extra_element in self._request.node.iter_markers("pytester_example_path"):
            assert extra_element.args
            example_dir = example_dir.joinpath(*extra_element.args)

        if name is None:
            func_name = self._name
            maybe_dir = example_dir / func_name
            maybe_file = example_dir / (func_name + ".py")

            if maybe_dir.is_dir():
                example_path = maybe_dir
            elif maybe_file.is_file():
                example_path = maybe_file
            else:
                raise LookupError(
                    f"{func_name} can't be found as module or package in {example_dir}"
                )
        else:
            example_path = example_dir.joinpath(name)

        if example_path.is_dir() and not example_path.joinpath("__init__.py").is_file():
            shutil.copytree(example_path, self.path, symlinks=True, dirs_exist_ok=True)
            return self.path
        elif example_path.is_file():
            result = self.path.joinpath(example_path.name)
            shutil.copy(example_path, result)
            return result
        else:
            raise LookupError(
                f'example "{example_path}" is not found as a file or directory'
            )

    def getnode(self, config: Config, arg: str | os.PathLike[str]) -> Collector | Item:
        """Get the collection node of a file.

        :param config:
           A pytest config.
           See :py:meth:`parseconfig` and :py:meth:`parseconfigure` for creating it.
        :param arg:
            Path to the file.
        :returns:
            The node.
        """
        session = Session.from_config(config)
