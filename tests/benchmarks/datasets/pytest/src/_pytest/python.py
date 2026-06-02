# mypy: allow-untyped-defs
"""Python test discovery, setup and run of test functions."""

from __future__ import annotations

import abc
from collections import Counter
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
import dataclasses
import enum
import fnmatch
from functools import partial
import inspect
import itertools
import os
from pathlib import Path
import re
import textwrap
import types
from typing import Any
from typing import cast
from typing import final
from typing import Literal
from typing import NoReturn
from typing import TYPE_CHECKING
import warnings

import _pytest
from _pytest import fixtures
from _pytest import nodes
from _pytest._code import filter_traceback
from _pytest._code import getfslineno
from _pytest._code.code import ExceptionInfo
from _pytest._code.code import TerminalRepr
from _pytest._code.code import Traceback
from _pytest._io.saferepr import saferepr
from _pytest.compat import ascii_escaped
from _pytest.compat import get_default_arg_names
from _pytest.compat import get_real_func
from _pytest.compat import getimfunc
from _pytest.compat import is_async_function
from _pytest.compat import LEGACY_PATH
from _pytest.compat import NOTSET
from _pytest.compat import safe_getattr
from _pytest.compat import safe_isclass
from _pytest.config import Config
from _pytest.config import hookimpl
from _pytest.config.argparsing import Parser
from _pytest.deprecated import check_ispytest
from _pytest.fixtures import FixtureDef
from _pytest.fixtures import FixtureRequest
from _pytest.fixtures import FuncFixtureInfo
from _pytest.fixtures import get_scope_node
from _pytest.main import Session
from _pytest.mark import ParameterSet
from _pytest.mark.structures import _HiddenParam
from _pytest.mark.structures import get_unpacked_marks
from _pytest.mark.structures import HIDDEN_PARAM
from _pytest.mark.structures import Mark
from _pytest.mark.structures import MarkDecorator
from _pytest.mark.structures import normalize_mark_list
from _pytest.outcomes import fail
from _pytest.outcomes import skip
from _pytest.pathlib import fnmatch_ex
from _pytest.pathlib import import_path
from _pytest.pathlib import ImportPathMismatchError
from _pytest.pathlib import scandir
from _pytest.scope import _ScopeName
from _pytest.scope import Scope
from _pytest.stash import StashKey
from _pytest.warning_types import PytestCollectionWarning
from _pytest.warning_types import PytestReturnNotNoneWarning


if TYPE_CHECKING:
    from typing_extensions import Self


def pytest_addoption(parser: Parser) -> None:
    parser.addini(
        "python_files",
        type="args",
        # NOTE: default is also used in AssertionRewritingHook.
        default=["test_*.py", "*_test.py"],
        help="Glob-style file patterns for Python test module discovery",
    )
    parser.addini(
        "python_classes",
        type="args",
        default=["Test"],
        help="Prefixes or glob names for Python test class discovery",
    )
    parser.addini(
        "python_functions",
        type="args",
        default=["test"],
        help="Prefixes or glob names for Python test function and method discovery",
    )
    parser.addini(
        "disable_test_id_escaping_and_forfeit_all_rights_to_community_support",
        type="bool",
        default=False,
        help="Disable string escape non-ASCII characters, might cause unwanted "
        "side effects(use at your own risk)",
    )
    parser.addini(
        "strict_parametrization_ids",
        type="bool",
        # None => fallback to `strict`.
        default=None,
        help="Emit an error if non-unique parameter set IDs are detected",
    )


def pytest_generate_tests(metafunc: Metafunc) -> None:
    for marker in metafunc.definition.iter_markers(name="parametrize"):
        metafunc.parametrize(*marker.args, **marker.kwargs, _param_mark=marker)


def pytest_configure(config: Config) -> None:
    config.addinivalue_line(
        "markers",
        "parametrize(argnames, argvalues): call a test function multiple "
        "times passing in different arguments in turn. argvalues generally "
        "needs to be a list of values if argnames specifies only one name "
        "or a list of tuples of values if argnames specifies multiple names. "
        "Example: @parametrize('arg1', [1,2]) would lead to two calls of the "
        "decorated test function, one with arg1=1 and another with arg1=2."
        "see https://docs.pytest.org/en/stable/how-to/parametrize.html for more info "
        "and examples.",
    )
    config.addinivalue_line(
        "markers",
        "usefixtures(fixturename1, fixturename2, ...): mark tests as needing "
        "all of the specified fixtures. see "
        "https://docs.pytest.org/en/stable/explanation/fixtures.html#usefixtures ",
    )


def async_fail(nodeid: str) -> None:
    msg = (
        "async def functions are not natively supported.\n"
        "You need to install a suitable plugin for your async framework, for example:\n"
        "  - anyio\n"
        "  - pytest-asyncio\n"
        "  - pytest-tornasync\n"
        "  - pytest-trio\n"
        "  - pytest-twisted"
    )
    fail(msg, pytrace=False)


@hookimpl(trylast=True)
def pytest_pyfunc_call(pyfuncitem: Function) -> object | None:
    testfunction = pyfuncitem.obj
    if is_async_function(testfunction):
        async_fail(pyfuncitem.nodeid)
    funcargs = pyfuncitem.funcargs
    testargs = {arg: funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames}
    result = testfunction(**testargs)
    if hasattr(result, "__await__") or hasattr(result, "__aiter__"):
        async_fail(pyfuncitem.nodeid)
    elif result is not None:
        warnings.warn(
            PytestReturnNotNoneWarning(
                f"Test functions should return None, but {pyfuncitem.nodeid} returned {type(result)!r}.\n"
                "Did you mean to use `assert` instead of `return`?\n"
                "See https://docs.pytest.org/en/stable/how-to/assert.html#return-not-none for more information."
            )
        )
    return True


def pytest_collect_directory(
    path: Path, parent: nodes.Collector
) -> nodes.Collector | None:
    pkginit = path / "__init__.py"
    try:
        has_pkginit = pkginit.is_file()
    except PermissionError:
        # See https://github.com/pytest-dev/pytest/issues/12120#issuecomment-2106349096.
        return None
    if has_pkginit:
        return Package.from_parent(parent, path=path)
    return None


def pytest_collect_file(file_path: Path, parent: nodes.Collector) -> Module | None:
    if file_path.suffix == ".py":
        if not parent.session.isinitpath(file_path):
            if not path_matches_patterns(
                file_path, parent.config.getini("python_files")
            ):
                return None
        ihook = parent.session.gethookproxy(file_path)
        module: Module = ihook.pytest_pycollect_makemodule(
            module_path=file_path, parent=parent
        )
        return module
    return None


def path_matches_patterns(path: Path, patterns: Iterable[str]) -> bool:
    """Return whether path matches any of the patterns in the list of globs given."""
    return any(fnmatch_ex(pattern, path) for pattern in patterns)


def pytest_pycollect_makemodule(module_path: Path, parent) -> Module:
    return Module.from_parent(parent, path=module_path)


@hookimpl(trylast=True)
def pytest_pycollect_makeitem(
    collector: Module | Class, name: str, obj: object
) -> None | nodes.Item | nodes.Collector | list[nodes.Item | nodes.Collector]:
    assert isinstance(collector, Class | Module), type(collector)
    # Nothing was collected elsewhere, let's do it here.
    if safe_isclass(obj):
        if collector.istestclass(obj, name):
            return Class.from_parent(collector, name=name, obj=obj)
    elif collector.istestfunction(obj, name):
        # mock seems to store unbound methods (issue473), normalize it.
        obj = getattr(obj, "__func__", obj)
        # We need to try and unwrap the function if it's a functools.partial
        # or a functools.wrapped.
        # We mustn't if it's been wrapped with mock.patch (python 2 only).
        if not (inspect.isfunction(obj) or inspect.isfunction(get_real_func(obj))):
            filename, lineno = getfslineno(obj)
            warnings.warn_explicit(
                message=PytestCollectionWarning(
                    f"cannot collect {name!r} because it is not a function."
                ),
                category=None,
                filename=str(filename),
                lineno=lineno + 1,
            )
        elif getattr(obj, "__test__", True):
            if inspect.isgeneratorfunction(obj):
                fail(
                    f"'yield' keyword is allowed in fixtures, but not in tests ({name})",
                    pytrace=False,
                )
            return list(collector._genfunctions(name, obj))
        return None
    return None


class PyobjMixin(nodes.Node):
    """this mix-in inherits from Node to carry over the typing information

    as its intended to always mix in before a node
    its position in the mro is unaffected"""

    _ALLOW_MARKERS = True

    @property
    def module(self):
        """Python module object this node was collected from (can be None)."""
        node = self.getparent(Module)
        return node.obj if node is not None else None

    @property
    def cls(self):
        """Python class object this node was collected from (can be None)."""
        node = self.getparent(Class)
        return node.obj if node is not None else None

    @property
    def instance(self):
        """Python instance object the function is bound to.

        Returns None if not a test method, e.g. for a standalone test function,
        a class or a module.
        """
        # Overridden by Function.
        return None

    @property
    def obj(self):
        """Underlying Python object."""
        obj = getattr(self, "_obj", None)
        if obj is None:
            self._obj = obj = self._getobj()
            # XXX evil hack
            # used to avoid Function marker duplication
            if self._ALLOW_MARKERS:
                self.own_markers.extend(get_unpacked_marks(self.obj))
                # This assumes that `obj` is called before there is a chance
                # to add custom keys to `self.keywords`, so no fear of overriding.
                self.keywords.update((mark.name, mark) for mark in self.own_markers)
        return obj

    @obj.setter
    def obj(self, value):
        self._obj = value

    def _getobj(self):
        """Get the underlying Python object. May be overwritten by subclasses."""
        # TODO: Improve the type of `parent` such that assert/ignore aren't needed.
        assert self.parent is not None
        obj = self.parent.obj  # type: ignore[attr-defined]
        return getattr(obj, self.name)

    def getmodpath(self, stopatmodule: bool = True, includemodule: bool = False) -> str:
        """Return Python path relative to the containing module."""
        parts = []
        for node in self.iter_parents():
            name = node.name
            if isinstance(node, Module):
                name = os.path.splitext(name)[0]
                if stopatmodule:
                    if includemodule:
                        parts.append(name)
                    break
            parts.append(name)
        parts.reverse()
        return ".".join(parts)

    def reportinfo(self) -> tuple[os.PathLike[str] | str, int | None, str]:
        # XXX caching?
        path, lineno = getfslineno(self.obj)
        modpath = self.getmodpath()
        return path, lineno, modpath


# As an optimization, these builtin attribute names are pre-ignored when
# iterating over an object during collection -- the pytest_pycollect_makeitem
# hook is not called for them.
# fmt: off
class _EmptyClass: pass  # noqa: E701
IGNORED_ATTRIBUTES = frozenset.union(
    frozenset(),
    # Module.
    dir(types.ModuleType("empty_module")),
    # Some extra module attributes the above doesn't catch.
    {"__builtins__", "__file__", "__cached__"},
    # Class.
    dir(_EmptyClass),
    # Instance.
    dir(_EmptyClass()),
)
del _EmptyClass
# fmt: on


class PyCollector(PyobjMixin, nodes.Collector, abc.ABC):
    def funcnamefilter(self, name: str) -> bool:
        return self._matches_prefix_or_glob_option("python_functions", name)

    def isnosetest(self, obj: object) -> bool:
        """Look for the __test__ attribute, which is applied by the
        @nose.tools.istest decorator.
        """
        # We explicitly check for "is True" here to not mistakenly treat
        # classes with a custom __getattr__ returning something truthy (like a
        # function) as test classes.
        return safe_getattr(obj, "__test__", False) is True

    def classnamefilter(self, name: str) -> bool:
        return self._matches_prefix_or_glob_option("python_classes", name)

    def istestfunction(self, obj: object, name: str) -> bool:
        if self.funcnamefilter(name) or self.isnosetest(obj):
            if isinstance(obj, staticmethod | classmethod):
                # staticmethods and classmethods need to be unwrapped.
                obj = safe_getattr(obj, "__func__", False)
            return callable(obj) and fixtures.getfixturemarker(obj) is None
        else:
            return False

    def istestclass(self, obj: object, name: str) -> bool:
        if not (self.classnamefilter(name) or self.isnosetest(obj)):
            return False
        if inspect.isabstract(obj):
            return False
        return True

    def _matches_prefix_or_glob_option(self, option_name: str, name: str) -> bool:
        """Check if the given name matches the prefix or glob-pattern defined
        in configuration."""
        for option in self.config.getini(option_name):
            if name.startswith(option):
                return True
            # Check that name looks like a glob-string before calling fnmatch
            # because this is called for every name in each collected module,
            # and fnmatch is somewhat expensive to call.
            elif ("*" in option or "?" in option or "[" in option) and fnmatch.fnmatch(
                name, option
            ):
                return True
        return False

    def collect(self) -> Iterable[nodes.Item | nodes.Collector]:
        if not getattr(self.obj, "__test__", True):
            return []

        # Avoid random getattrs and peek in the __dict__ instead.
        dicts = [getattr(self.obj, "__dict__", {})]
        if isinstance(self.obj, type):
            for basecls in self.obj.__mro__:
                dicts.append(basecls.__dict__)

        # In each class, nodes should be definition ordered.
        # __dict__ is definition ordered.
        seen: set[str] = set()
        dict_values: list[list[nodes.Item | nodes.Collector]] = []
        collect_imported_tests = self.session.config.getini("collect_imported_tests")
        ihook = self.ihook
        for dic in dicts:
            values: list[nodes.Item | nodes.Collector] = []
            # Note: seems like the dict can change during iteration -
            # be careful not to remove the list() without consideration.
            for name, obj in list(dic.items()):
                if name in IGNORED_ATTRIBUTES:
                    continue
                if name in seen:
                    continue
                seen.add(name)

                if not collect_imported_tests and isinstance(self, Module):
                    # Do not collect functions and classes from other modules.
                    if inspect.isfunction(obj) or inspect.isclass(obj):
                        if obj.__module__ != self._getobj().__name__:
                            continue

                res = ihook.pytest_pycollect_makeitem(
                    collector=self, name=name, obj=obj
                )
                if res is None:
                    continue
                elif isinstance(res, list):
                    values.extend(res)
                else:
                    values.append(res)
            dict_values.append(values)

        # Between classes in the class hierarchy, reverse-MRO order -- nodes
        # inherited from base classes should come before subclasses.
        result = []
        for values in reversed(dict_values):
            result.extend(values)
        return result

    def _genfunctions(self, name: str, funcobj) -> Iterator[Function]:
        modulecol = self.getparent(Module)
        assert modulecol is not None
        module = modulecol.obj
        clscol = self.getparent(Class)
        cls = (clscol and clscol.obj) or None

        definition = FunctionDefinition.from_parent(self, name=name, callobj=funcobj)
        fixtureinfo = definition._fixtureinfo

        # pytest_generate_tests impls call metafunc.parametrize() which fills
        # metafunc._calls, the outcome of the hook.
        metafunc = Metafunc(
            definition=definition,
            fixtureinfo=fixtureinfo,
            config=self.config,
            cls=cls,
            module=module,
            _ispytest=True,
        )
        methods = []
        if hasattr(module, "pytest_generate_tests"):
            methods.append(module.pytest_generate_tests)
        if cls is not None and hasattr(cls, "pytest_generate_tests"):
            methods.append(cls().pytest_generate_tests)
        self.ihook.pytest_generate_tests.call_extra(methods, dict(metafunc=metafunc))

        if not metafunc._calls:
            yield Function.from_parent(self, name=name, fixtureinfo=fixtureinfo)
        else:
            metafunc._recompute_direct_params_indices()
            # Direct parametrizations taking place in module/class-specific
            # `metafunc.parametrize` calls may have shadowed some fixtures, so make sure
            # we update what the function really needs a.k.a its fixture closure. Note that
            # direct parametrizations using `@pytest.mark.parametrize` have already been considered
            # into making the closure using `ignore_args` arg to `getfixtureclosure`.
            fixtureinfo.prune_dependency_tree()

            for callspec in metafunc._calls:
                subname = f"{name}[{callspec.id}]" if callspec._idlist else name
                yield Function.from_parent(
                    self,
                    name=subname,
                    callspec=callspec,
                    fixtureinfo=fixtureinfo,
                    keywords={callspec.id: True},
                    originalname=name,
                )


def importtestmodule(
    path: Path,
    config: Config,
):
    # We assume we are only called once per module.
    importmode = config.getoption("--import-mode")
    try:
        mod = import_path(
            path,
            mode=importmode,
            root=config.rootpath,
            consider_namespace_packages=config.getini("consider_namespace_packages"),
        )
    except SyntaxError as e:
        raise nodes.Collector.CollectError(
            ExceptionInfo.from_current().getrepr(style="short")
        ) from e
    except ImportPathMismatchError as e:
        raise nodes.Collector.CollectError(
            "import file mismatch:\n"
            "imported module {!r} has this __file__ attribute:\n"
            "  {}\n"
            "which is not the same as the test file we want to collect:\n"
            "  {}\n"
            "HINT: remove __pycache__ / .pyc files and/or use a "
            "unique basename for your test file modules".format(*e.args)
        ) from e
    except ImportError as e:
        exc_info = ExceptionInfo.from_current()
        if config.get_verbosity() < 2:
            exc_info.traceback = exc_info.traceback.filter(filter_traceback)
        exc_repr = (
            exc_info.getrepr(style="short")
            if exc_info.traceback
            else exc_info.exconly()
        )
        formatted_tb = str(exc_repr)
        raise nodes.Collector.CollectError(
            f"ImportError while importing test module '{path}'.\n"
            "Hint: make sure your test modules/packages have valid Python names.\n"
            "Traceback:\n"
            f"{formatted_tb}"
        ) from e
    except skip.Exception as e:
        if e.allow_module_level:
            raise
        raise nodes.Collector.CollectError(
            "Using pytest.skip outside of a test will skip the entire module. "
            "If that's your intention, pass `allow_module_level=True`. "
            "If you want to skip a specific test or an entire class, "
            "use the @pytest.mark.skip or @pytest.mark.skipif decorators."
        ) from e
    config.pluginmanager.consider_module(mod)
    return mod


class Module(nodes.File, PyCollector):
    """Collector for test classes and functions in a Python module."""

    def _getobj(self):
        return importtestmodule(self.path, self.config)

    def collect(self) -> Iterable[nodes.Item | nodes.Collector]:
        self._register_setup_module_fixture()
        self._register_setup_function_fixture()
        self.session._fixturemanager.parsefactories(self)
        return super().collect()

    def _register_setup_module_fixture(self) -> None:
        """Register an autouse, module-scoped fixture for the collected module object
        that invokes setUpModule/tearDownModule if either or both are available.

        Using a fixture to invoke this methods ensures we play nicely and unsurprisingly with
        other fixtures (#517).
        """
        setup_module = _get_first_non_fixture_func(
            self.obj, ("setUpModule", "setup_module")
        )
        teardown_module = _get_first_non_fixture_func(
            self.obj, ("tearDownModule", "teardown_module")
        )

        if setup_module is None and teardown_module is None:
            return

        def xunit_setup_module_fixture(request) -> Generator[None]:
            module = request.module
            if setup_module is not None:
                _call_with_optional_argument(setup_module, module)
            yield
            if teardown_module is not None:
                _call_with_optional_argument(teardown_module, module)

        self.session._fixturemanager._register_fixture(
            # Use a unique name to speed up lookup.
            name=f"_xunit_setup_module_fixture_{self.obj.__name__}",
            func=xunit_setup_module_fixture,
            nodeid=self.nodeid,
            scope="module",
            autouse=True,
        )

    def _register_setup_function_fixture(self) -> None:
        """Register an autouse, function-scoped fixture for the collected module object
        that invokes setup_function/teardown_function if either or both are available.

        Using a fixture to invoke this methods ensures we play nicely and unsurprisingly with
        other fixtures (#517).
        """
        setup_function = _get_first_non_fixture_func(self.obj, ("setup_function",))
        teardown_function = _get_first_non_fixture_func(
            self.obj, ("teardown_function",)
        )
        if setup_function is None and teardown_function is None:
            return

        def xunit_setup_function_fixture(request) -> Generator[None]:
            if request.instance is not None:
                # in this case we are bound to an instance, so we need to let
                # setup_method handle this
                yield
                return
            function = request.function
            if setup_function is not None:
                _call_with_optional_argument(setup_function, function)
            yield
            if teardown_function is not None:
                _call_with_optional_argument(teardown_function, function)

        self.session._fixturemanager._register_fixture(
            # Use a unique name to speed up lookup.
            name=f"_xunit_setup_function_fixture_{self.obj.__name__}",
            func=xunit_setup_function_fixture,
            nodeid=self.nodeid,
            scope="function",
            autouse=True,
        )


class Package(nodes.Directory):
    """Collector for files and directories in a Python packages -- directories
    with an `__init__.py` file.

    .. note::

        Directories without an `__init__.py` file are instead collected by
        :class:`~pytest.Dir` by default. Both are :class:`~pytest.Directory`
        collectors.

    .. versionchanged:: 8.0

        Now inherits from :class:`~pytest.Directory`.
    """

    def __init__(
        self,
        fspath: LEGACY_PATH | None,
        parent: nodes.Collector,
        # NOTE: following args are unused:
        config=None,
        session=None,
        nodeid=None,
        path: Path | None = None,
    ) -> None:
        # NOTE: Could be just the following, but kept as-is for compat.
        # super().__init__(self, fspath, parent=parent)
        session = parent.session
        super().__init__(
            fspath=fspath,
            path=path,
            parent=parent,
            config=config,
            session=session,
            nodeid=nodeid,
        )

    def setup(self) -> None:
        init_mod = importtestmodule(self.path / "__init__.py", self.config)

        # Not using fixtures to call setup_module here because autouse fixtures
        # from packages are not called automatically (#4085).
        setup_module = _get_first_non_fixture_func(
            init_mod, ("setUpModule", "setup_module")
        )
        if setup_module is not None:
            _call_with_optional_argument(setup_module, init_mod)

        teardown_module = _get_first_non_fixture_func(
            init_mod, ("tearDownModule", "teardown_module")
        )
        if teardown_module is not None:
            func = partial(_call_with_optional_argument, teardown_module, init_mod)
            self.addfinalizer(func)

    def collect(self) -> Iterable[nodes.Item | nodes.Collector]:
        # Always collect __init__.py first.
        def sort_key(entry: os.DirEntry[str]) -> object:
            return (entry.name != "__init__.py", entry.name)

        config = self.config
        col: nodes.Collector | None
        cols: Sequence[nodes.Collector]
        ihook = self.ihook
        for direntry in scandir(self.path, sort_key):
            if direntry.is_dir():
                path = Path(direntry.path)
                if not self.session.isinitpath(path, with_parents=True):
                    if ihook.pytest_ignore_collect(collection_path=path, config=config):
                        continue
                col = ihook.pytest_collect_directory(path=path, parent=self)
                if col is not None:
                    yield col

            elif direntry.is_file():
                path = Path(direntry.path)
                if not self.session.isinitpath(path):
                    if ihook.pytest_ignore_collect(collection_path=path, config=config):
                        continue
                cols = ihook.pytest_collect_file(file_path=path, parent=self)
                yield from cols


def _call_with_optional_argument(func, arg) -> None:
    """Call the given function with the given argument if func accepts one argument, otherwise
    calls func without arguments."""
    arg_count = func.__code__.co_argcount
    if inspect.ismethod(func):
        arg_count -= 1
    if arg_count:
        func(arg)
    else:
        func()


def _get_first_non_fixture_func(obj: object, names: Iterable[str]) -> object | None:
    """Return the attribute from the given object to be used as a setup/teardown
    xunit-style function, but only if not marked as a fixture to avoid calling it twice.
    """
    for name in names:
        meth: object | None = getattr(obj, name, None)
        if meth is not None and fixtures.getfixturemarker(meth) is None:
            return meth
    return None


class Class(PyCollector):
    """Collector for test methods (and nested classes) in a Python class."""

    @classmethod
    def from_parent(cls, parent, *, name, obj=None, **kw) -> Self:  # type: ignore[override]
        """The public constructor."""
        return super().from_parent(name=name, parent=parent, **kw)

    def newinstance(self):
        return self.obj()

    def collect(self) -> Iterable[nodes.Item | nodes.Collector]:
        if not safe_getattr(self.obj, "__test__", True):
            return []
        if hasinit(self.obj):
            assert self.parent is not None
            self.warn(
                PytestCollectionWarning(
                    f"cannot collect test class {self.obj.__name__!r} because it has a "
                    f"__init__ constructor (from: {self.parent.nodeid})"
                )
            )
            return []
        elif hasnew(self.obj):
            assert self.parent is not None
            self.warn(
                PytestCollectionWarning(
                    f"cannot collect test class {self.obj.__name__!r} because it has a "
                    f"__new__ constructor (from: {self.parent.nodeid})"
                )
            )
            return []

        self._register_setup_class_fixture()
        self._register_setup_method_fixture()

        self.session._fixturemanager.parsefactories(self.newinstance(), self.nodeid)

        return super().collect()

    def _register_setup_class_fixture(self) -> None:
        """Register an autouse, class scoped fixture into the collected class object
        that invokes setup_class/teardown_class if either or both are available.

        Using a fixture to invoke this methods ensures we play nicely and unsurprisingly with
        other fixtures (#517).
        """
        setup_class = _get_first_non_fixture_func(self.obj, ("setup_class",))
        teardown_class = _get_first_non_fixture_func(self.obj, ("teardown_class",))
        if setup_class is None and teardown_class is None:
            return

        def xunit_setup_class_fixture(request) -> Generator[None]:
            cls = request.cls
            if setup_class is not None:
                func = getimfunc(setup_class)
                _call_with_optional_argument(func, cls)
            yield
            if teardown_class is not None:
                func = getimfunc(teardown_class)
                _call_with_optional_argument(func, cls)

        self.session._fixturemanager._register_fixture(
            # Use a unique name to speed up lookup.
            name=f"_xunit_setup_class_fixture_{self.obj.__qualname__}",
            func=xunit_setup_class_fixture,
            nodeid=self.nodeid,
            scope="class",
            autouse=True,
        )

    def _register_setup_method_fixture(self) -> None:
        """Register an autouse, function scoped fixture into the collected class object
        that invokes setup_method/teardown_method if either or both are available.

        Using a fixture to invoke these methods ensures we play nicely and unsurprisingly with
        other fixtures (#517).
        """
        setup_name = "setup_method"
        setup_method = _get_first_non_fixture_func(self.obj, (setup_name,))
        teardown_name = "teardown_method"
        teardown_method = _get_first_non_fixture_func(self.obj, (teardown_name,))
        if setup_method is None and teardown_method is None:
            return

        def xunit_setup_method_fixture(request) -> Generator[None]:
            instance = request.instance
            method = request.function
            if setup_method is not None:
                func = getattr(instance, setup_name)
                _call_with_optional_argument(func, method)
            yield
            if teardown_method is not None:
                func = getattr(instance, teardown_name)
                _call_with_optional_argument(func, method)

        self.session._fixturemanager._register_fixture(
            # Use a unique name to speed up lookup.
            name=f"_xunit_setup_method_fixture_{self.obj.__qualname__}",
            func=xunit_setup_method_fixture,
            nodeid=self.nodeid,
            scope="function",
            autouse=True,
        )


def hasinit(obj: object) -> bool:
    init: object = getattr(obj, "__init__", None)
    if init:
        return init != object.__init__
    return False


def hasnew(obj: object) -> bool:
    new: object = getattr(obj, "__new__", None)
    if new:
        return new != object.__new__
    return False


@final
@dataclasses.dataclass(frozen=True)
class IdMaker:
    """Make IDs for a parametrization."""

    __slots__ = (
        "argnames",
        "config",
        "func_name",
        "idfn",
        "ids",
        "nodeid",
        "parametersets",
    )

    # The argnames of the parametrization.
    argnames: Sequence[str]
    # The ParameterSets of the parametrization.
    parametersets: Sequence[ParameterSet]
    # Optionally, a user-provided callable to make IDs for parameters in a
    # ParameterSet.
    idfn: Callable[[Any], object | None] | None
    # Optionally, explicit IDs for ParameterSets by index.
    ids: Sequence[object | None] | None
    # Optionally, the pytest config.
    # Used for controlling ASCII escaping, determining parametrization ID
    # strictness, and for calling the :hook:`pytest_make_parametrize_id` hook.
    config: Config | None
    # Optionally, the ID of the node being parametrized.
    # Used only for clearer error messages.
    nodeid: str | None
    # Optionally, the ID of the function being parametrized.
    # Used only for clearer error messages.
    func_name: str | None

    def make_unique_parameterset_ids(self) -> list[str | _HiddenParam]:
        """Make a unique identifier for each ParameterSet, that may be used to
        identify the parametrization in a node ID.

        If strict_parametrization_ids is enabled, and duplicates are detected,
        raises CollectError. Otherwise makes the IDs unique as follows:

        Format is <prm_1_token>-...-<prm_n_token>[counter], where prm_x_token is
        - user-provided id, if given
        - else an id derived from the value, applicable for certain types
        - else <argname><parameterset index>
        The counter suffix is appended only in case a string wouldn't be unique
        otherwise.
        """
        resolved_ids = list(self._resolve_ids())
        # All IDs must be unique!
        if len(resolved_ids) != len(set(resolved_ids)):
            # Record the number of occurrences of each ID.
            id_counts = Counter(resolved_ids)

            if self._strict_parametrization_ids_enabled():
                parameters = ", ".join(self.argnames)
                parametersets = ", ".join(
                    [saferepr(list(param.values)) for param in self.parametersets]
                )
                ids = ", ".join(
                    id if id is not HIDDEN_PARAM else "<hidden>" for id in resolved_ids
                )
                duplicates = ", ".join(
                    id if id is not HIDDEN_PARAM else "<hidden>"
                    for id, count in id_counts.items()
                    if count > 1
                )
                msg = textwrap.dedent(f"""
                    Duplicate parametrization IDs detected, but strict_parametrization_ids is set.

                    Test name:      {self.nodeid}
                    Parameters:     {parameters}
                    Parameter sets: {parametersets}
                    IDs:            {ids}
                    Duplicates:     {duplicates}

                    You can fix this problem using `@pytest.mark.parametrize(..., ids=...)` or `pytest.param(..., id=...)`.
                """).strip()  # noqa: E501
                raise nodes.Collector.CollectError(msg)

            # Map the ID to its next suffix.
            id_suffixes: dict[str, int] = defaultdict(int)
            # Suffix non-unique IDs to make them unique.
            for index, id in enumerate(resolved_ids):
                if id_counts[id] > 1:
                    if id is HIDDEN_PARAM:
                        self._complain_multiple_hidden_parameter_sets()
                    suffix = ""
                    if id and id[-1].isdigit():
                        suffix = "_"
                    new_id = f"{id}{suffix}{id_suffixes[id]}"
                    while new_id in set(resolved_ids):
                        id_suffixes[id] += 1
                        new_id = f"{id}{suffix}{id_suffixes[id]}"
                    resolved_ids[index] = new_id
                    id_suffixes[id] += 1
        assert len(resolved_ids) == len(set(resolved_ids)), (
            f"Internal error: {resolved_ids=}"
        )
        return resolved_ids

    def _strict_parametrization_ids_enabled(self) -> bool:
        if self.config is None:
            return False
        strict_parametrization_ids = self.config.getini("strict_parametrization_ids")
        if strict_parametrization_ids is None:
            strict_parametrization_ids = self.config.getini("strict")
        return cast(bool, strict_parametrization_ids)

    def _resolve_ids(self) -> Iterable[str | _HiddenParam]:
        """Resolve IDs for all ParameterSets (may contain duplicates)."""
        for idx, parameterset in enumerate(self.parametersets):
            if parameterset.id is not None:
                # ID provided directly - pytest.param(..., id="...")
                if parameterset.id is HIDDEN_PARAM:
                    yield HIDDEN_PARAM
                else:
                    yield _ascii_escaped_by_config(parameterset.id, self.config)
            elif self.ids and idx < len(self.ids) and self.ids[idx] is not None:
                # ID provided in the IDs list - parametrize(..., ids=[...]).
                if self.ids[idx] is HIDDEN_PARAM:
                    yield HIDDEN_PARAM
                else:
                    yield self._idval_from_value_required(self.ids[idx], idx)
            else:
                # ID not provided - generate it.
                yield "-".join(
                    self._idval(val, argname, idx)
                    for val, argname in zip(
                        parameterset.values, self.argnames, strict=True
                    )
                )

    def _idval(self, val: object, argname: str, idx: int) -> str:
        """Make an ID for a parameter in a ParameterSet."""
