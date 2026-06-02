# mypy: allow-untyped-defs
from __future__ import annotations

import abc
from collections import defaultdict
from collections import deque
from collections import OrderedDict
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
import dataclasses
import functools
import inspect
import os
from pathlib import Path
import sys
import types
from typing import Any
from typing import cast
from typing import Final
from typing import final
from typing import Generic
from typing import NoReturn
from typing import overload
from typing import TYPE_CHECKING
from typing import TypeVar
import warnings

import _pytest
from _pytest import nodes
from _pytest._code import getfslineno
from _pytest._code import Source
from _pytest._code.code import FormattedExcinfo
from _pytest._code.code import TerminalRepr
from _pytest._io import TerminalWriter
from _pytest.compat import assert_never
from _pytest.compat import get_real_func
from _pytest.compat import getfuncargnames
from _pytest.compat import getimfunc
from _pytest.compat import getlocation
from _pytest.compat import NOTSET
from _pytest.compat import NotSetType
from _pytest.compat import safe_getattr
from _pytest.compat import safe_isclass
from _pytest.compat import signature
from _pytest.config import _PluggyPlugin
from _pytest.config import Config
from _pytest.config import ExitCode
from _pytest.config.argparsing import Parser
from _pytest.deprecated import check_ispytest
from _pytest.deprecated import MARKED_FIXTURE
from _pytest.deprecated import YIELD_FIXTURE
from _pytest.main import Session
from _pytest.mark import Mark
from _pytest.mark import ParameterSet
from _pytest.mark.structures import MarkDecorator
from _pytest.outcomes import fail
from _pytest.outcomes import skip
from _pytest.outcomes import TEST_OUTCOME
from _pytest.pathlib import absolutepath
from _pytest.pathlib import bestrelpath
from _pytest.scope import _ScopeName
from _pytest.scope import HIGH_SCOPES
from _pytest.scope import Scope
from _pytest.warning_types import PytestRemovedIn9Warning
from _pytest.warning_types import PytestWarning


if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup


if TYPE_CHECKING:
    from _pytest.python import CallSpec2
    from _pytest.python import Function
    from _pytest.python import Metafunc


# The value of the fixture -- return/yield of the fixture function (type variable).
FixtureValue = TypeVar("FixtureValue", covariant=True)
# The type of the fixture function (type variable).
FixtureFunction = TypeVar("FixtureFunction", bound=Callable[..., object])
# The type of a fixture function (type alias generic in fixture value).
_FixtureFunc = Callable[..., FixtureValue] | Callable[..., Generator[FixtureValue]]
# The type of FixtureDef.cached_result (type alias generic in fixture value).
_FixtureCachedResult = (
    tuple[
        # The result.
        FixtureValue,
        # Cache key.
        object,
        None,
    ]
    | tuple[
        None,
        # Cache key.
        object,
        # The exception and the original traceback.
        tuple[BaseException, types.TracebackType | None],
    ]
)


def pytest_sessionstart(session: Session) -> None:
    session._fixturemanager = FixtureManager(session)


def get_scope_package(
    node: nodes.Item,
    fixturedef: FixtureDef[object],
) -> nodes.Node | None:
    from _pytest.python import Package

    for parent in node.iter_parents():
        if isinstance(parent, Package) and parent.nodeid == fixturedef.baseid:
            return parent
    return node.session


def get_scope_node(node: nodes.Node, scope: Scope) -> nodes.Node | None:
    """Get the closest parent node (including self) which matches the given
    scope.

    If there is no parent node for the scope (e.g. asking for class scope on a
    Module, or on a Function when not defined in a class), returns None.
    """
    import _pytest.python

    if scope is Scope.Function:
        # Type ignored because this is actually safe, see:
        # https://github.com/python/mypy/issues/4717
        return node.getparent(nodes.Item)  # type: ignore[type-abstract]
    elif scope is Scope.Class:
        return node.getparent(_pytest.python.Class)
    elif scope is Scope.Module:
        return node.getparent(_pytest.python.Module)
    elif scope is Scope.Package:
        return node.getparent(_pytest.python.Package)
    elif scope is Scope.Session:
        return node.getparent(_pytest.main.Session)
    else:
        assert_never(scope)


# TODO: Try to use FixtureFunctionDefinition instead of the marker
def getfixturemarker(obj: object) -> FixtureFunctionMarker | None:
    """Return fixturemarker or None if it doesn't exist"""
    if isinstance(obj, FixtureFunctionDefinition):
        return obj._fixture_function_marker
    return None


# Algorithm for sorting on a per-parametrized resource setup basis.
# It is called for Session scope first and performs sorting
# down to the lower scopes such as to minimize number of "high scope"
# setups and teardowns.


@dataclasses.dataclass(frozen=True)
class ParamArgKey:
    """A key for a high-scoped parameter used by an item.

    For use as a hashable key in `reorder_items`. The combination of fields
    is meant to uniquely identify a particular "instance" of a param,
    potentially shared by multiple items in a scope.
    """

    #: The param name.
    argname: str
    param_index: int
    #: For scopes Package, Module, Class, the path to the file (directory in
    #: Package's case) of the package/module/class where the item is defined.
    scoped_item_path: Path | None
    #: For Class scope, the class where the item is defined.
    item_cls: type | None


_V = TypeVar("_V")
OrderedSet = dict[_V, None]


def get_param_argkeys(item: nodes.Item, scope: Scope) -> Iterator[ParamArgKey]:
    """Return all ParamArgKeys for item matching the specified high scope."""
    assert scope is not Scope.Function

    try:
        callspec: CallSpec2 = item.callspec  # type: ignore[attr-defined]
    except AttributeError:
        return

    item_cls = None
    if scope is Scope.Session:
        scoped_item_path = None
    elif scope is Scope.Package:
        # Package key = module's directory.
        scoped_item_path = item.path.parent
    elif scope is Scope.Module:
        scoped_item_path = item.path
    elif scope is Scope.Class:
        scoped_item_path = item.path
        item_cls = item.cls  # type: ignore[attr-defined]
    else:
        assert_never(scope)

    for argname in callspec.indices:
        if callspec._arg2scope[argname] != scope:
            continue
        param_index = callspec.indices[argname]
        yield ParamArgKey(argname, param_index, scoped_item_path, item_cls)


def reorder_items(items: Sequence[nodes.Item]) -> list[nodes.Item]:
    argkeys_by_item: dict[Scope, dict[nodes.Item, OrderedSet[ParamArgKey]]] = {}
    items_by_argkey: dict[Scope, dict[ParamArgKey, OrderedDict[nodes.Item, None]]] = {}
    for scope in HIGH_SCOPES:
        scoped_argkeys_by_item = argkeys_by_item[scope] = {}
        scoped_items_by_argkey = items_by_argkey[scope] = defaultdict(OrderedDict)
        for item in items:
            argkeys = dict.fromkeys(get_param_argkeys(item, scope))
            if argkeys:
                scoped_argkeys_by_item[item] = argkeys
                for argkey in argkeys:
                    scoped_items_by_argkey[argkey][item] = None

    items_set = dict.fromkeys(items)
    return list(
        reorder_items_atscope(
            items_set, argkeys_by_item, items_by_argkey, Scope.Session
        )
    )


def reorder_items_atscope(
    items: OrderedSet[nodes.Item],
    argkeys_by_item: Mapping[Scope, Mapping[nodes.Item, OrderedSet[ParamArgKey]]],
    items_by_argkey: Mapping[
        Scope, Mapping[ParamArgKey, OrderedDict[nodes.Item, None]]
    ],
    scope: Scope,
) -> OrderedSet[nodes.Item]:
    if scope is Scope.Function or len(items) < 3:
        return items

    scoped_items_by_argkey = items_by_argkey[scope]
    scoped_argkeys_by_item = argkeys_by_item[scope]

    ignore: set[ParamArgKey] = set()
    items_deque = deque(items)
    items_done: OrderedSet[nodes.Item] = {}
    while items_deque:
        no_argkey_items: OrderedSet[nodes.Item] = {}
        slicing_argkey = None
        while items_deque:
            item = items_deque.popleft()
            if item in items_done or item in no_argkey_items:
                continue
            argkeys = dict.fromkeys(
                k for k in scoped_argkeys_by_item.get(item, ()) if k not in ignore
            )
            if not argkeys:
                no_argkey_items[item] = None
            else:
                slicing_argkey, _ = argkeys.popitem()
                # We don't have to remove relevant items from later in the
                # deque because they'll just be ignored.
                matching_items = [
                    i for i in scoped_items_by_argkey[slicing_argkey] if i in items
                ]
                for i in reversed(matching_items):
                    items_deque.appendleft(i)
                    # Fix items_by_argkey order.
                    for other_scope in HIGH_SCOPES:
                        other_scoped_items_by_argkey = items_by_argkey[other_scope]
                        for argkey in argkeys_by_item[other_scope].get(i, ()):
                            argkey_dict = other_scoped_items_by_argkey[argkey]
                            if not hasattr(sys, "pypy_version_info"):
                                argkey_dict[i] = None
                                argkey_dict.move_to_end(i, last=False)
                            else:
                                # Work around a bug in PyPy:
                                # https://github.com/pypy/pypy/issues/5257
                                # https://github.com/pytest-dev/pytest/issues/13312
                                bkp = argkey_dict.copy()
                                argkey_dict.clear()
                                argkey_dict[i] = None
                                argkey_dict.update(bkp)
                break
        if no_argkey_items:
            reordered_no_argkey_items = reorder_items_atscope(
                no_argkey_items, argkeys_by_item, items_by_argkey, scope.next_lower()
            )
            items_done.update(reordered_no_argkey_items)
        if slicing_argkey is not None:
            ignore.add(slicing_argkey)
    return items_done


@dataclasses.dataclass(frozen=True)
class FuncFixtureInfo:
    """Fixture-related information for a fixture-requesting item (e.g. test
    function).

    This is used to examine the fixtures which an item requests statically
    (known during collection). This includes autouse fixtures, fixtures
    requested by the `usefixtures` marker, fixtures requested in the function
    parameters, and the transitive closure of these.

    An item may also request fixtures dynamically (using `request.getfixturevalue`);
    these are not reflected here.
    """

    __slots__ = ("argnames", "initialnames", "name2fixturedefs", "names_closure")

    # Fixture names that the item requests directly by function parameters.
    argnames: tuple[str, ...]
    # Fixture names that the item immediately requires. These include
    # argnames + fixture names specified via usefixtures and via autouse=True in
    # fixture definitions.
    initialnames: tuple[str, ...]
    # The transitive closure of the fixture names that the item requires.
    # Note: can't include dynamic dependencies (`request.getfixturevalue` calls).
    names_closure: list[str]
    # A map from a fixture name in the transitive closure to the FixtureDefs
    # matching the name which are applicable to this function.
    # There may be multiple overriding fixtures with the same name. The
    # sequence is ordered from furthest to closes to the function.
    name2fixturedefs: dict[str, Sequence[FixtureDef[Any]]]

    def prune_dependency_tree(self) -> None:
        """Recompute names_closure from initialnames and name2fixturedefs.

        Can only reduce names_closure, which means that the new closure will
        always be a subset of the old one. The order is preserved.

        This method is needed because direct parametrization may shadow some
        of the fixtures that were included in the originally built dependency
        tree. In this way the dependency tree can get pruned, and the closure
        of argnames may get reduced.
        """
        closure: set[str] = set()
        working_set = set(self.initialnames)
        while working_set:
            argname = working_set.pop()
            # Argname may be something not included in the original names_closure,
            # in which case we ignore it. This currently happens with pseudo
            # FixtureDefs which wrap 'get_direct_param_fixture_func(request)'.
            # So they introduce the new dependency 'request' which might have
            # been missing in the original tree (closure).
            if argname not in closure and argname in self.names_closure:
                closure.add(argname)
                if argname in self.name2fixturedefs:
                    working_set.update(self.name2fixturedefs[argname][-1].argnames)

        self.names_closure[:] = sorted(closure, key=self.names_closure.index)


class FixtureRequest(abc.ABC):
    """The type of the ``request`` fixture.

    A request object gives access to the requesting test context and has a
    ``param`` attribute in case the fixture is parametrized.
    """

    def __init__(
        self,
        pyfuncitem: Function,
        fixturename: str | None,
        arg2fixturedefs: dict[str, Sequence[FixtureDef[Any]]],
        fixture_defs: dict[str, FixtureDef[Any]],
        *,
        _ispytest: bool = False,
    ) -> None:
        check_ispytest(_ispytest)
        #: Fixture for which this request is being performed.
        self.fixturename: Final = fixturename
        self._pyfuncitem: Final = pyfuncitem
        # The FixtureDefs for each fixture name requested by this item.
        # Starts from the statically-known fixturedefs resolved during
        # collection. Dynamically requested fixtures (using
        # `request.getfixturevalue("foo")`) are added dynamically.
        self._arg2fixturedefs: Final = arg2fixturedefs
        # The evaluated argnames so far, mapping to the FixtureDef they resolved
        # to.
        self._fixture_defs: Final = fixture_defs
        # Notes on the type of `param`:
        # -`request.param` is only defined in parametrized fixtures, and will raise
        #   AttributeError otherwise. Python typing has no notion of "undefined", so
        #   this cannot be reflected in the type.
        # - Technically `param` is only (possibly) defined on SubRequest, not
        #   FixtureRequest, but the typing of that is still in flux so this cheats.
        # - In the future we might consider using a generic for the param type, but
        #   for now just using Any.
        self.param: Any

    @property
    def _fixturemanager(self) -> FixtureManager:
        return self._pyfuncitem.session._fixturemanager

    @property
    @abc.abstractmethod
    def _scope(self) -> Scope:
        raise NotImplementedError()

    @property
    def scope(self) -> _ScopeName:
        """Scope string, one of "function", "class", "module", "package", "session"."""
        return self._scope.value

    @abc.abstractmethod
    def _check_scope(
        self,
        requested_fixturedef: FixtureDef[object],
        requested_scope: Scope,
    ) -> None:
        raise NotImplementedError()

    @property
    def fixturenames(self) -> list[str]:
        """Names of all active fixtures in this request."""
        result = list(self._pyfuncitem.fixturenames)
        result.extend(set(self._fixture_defs).difference(result))
        return result

    @property
    @abc.abstractmethod
    def node(self):
        """Underlying collection node (depends on current request scope)."""
        raise NotImplementedError()

    @property
    def config(self) -> Config:
        """The pytest config object associated with this request."""
        return self._pyfuncitem.config

    @property
    def function(self):
        """Test function object if the request has a per-function scope."""
        if self.scope != "function":
            raise AttributeError(
                f"function not available in {self.scope}-scoped context"
            )
        return self._pyfuncitem.obj

    @property
    def cls(self):
        """Class (can be None) where the test function was collected."""
        if self.scope not in ("class", "function"):
            raise AttributeError(f"cls not available in {self.scope}-scoped context")
        clscol = self._pyfuncitem.getparent(_pytest.python.Class)
        if clscol:
            return clscol.obj

    @property
    def instance(self):
        """Instance (can be None) on which test function was collected."""
        if self.scope != "function":
            return None
        return getattr(self._pyfuncitem, "instance", None)

    @property
    def module(self):
        """Python module object where the test function was collected."""
        if self.scope not in ("function", "class", "module"):
            raise AttributeError(f"module not available in {self.scope}-scoped context")
        mod = self._pyfuncitem.getparent(_pytest.python.Module)
        assert mod is not None
        return mod.obj

    @property
    def path(self) -> Path:
        """Path where the test function was collected."""
        if self.scope not in ("function", "class", "module", "package"):
            raise AttributeError(f"path not available in {self.scope}-scoped context")
        return self._pyfuncitem.path

    @property
    def keywords(self) -> MutableMapping[str, Any]:
        """Keywords/markers dictionary for the underlying node."""
        node: nodes.Node = self.node
        return node.keywords

    @property
    def session(self) -> Session:
        """Pytest session object."""
        return self._pyfuncitem.session

    @abc.abstractmethod
    def addfinalizer(self, finalizer: Callable[[], object]) -> None:
        """Add finalizer/teardown function to be called without arguments after
        the last test within the requesting test context finished execution."""
        raise NotImplementedError()

    def applymarker(self, marker: str | MarkDecorator) -> None:
        """Apply a marker to a single test function invocation.

        This method is useful if you don't want to have a keyword/marker
        on all function invocations.

        :param marker:
            An object created by a call to ``pytest.mark.NAME(...)``.
        """
        self.node.add_marker(marker)

    def raiseerror(self, msg: str | None) -> NoReturn:
        """Raise a FixtureLookupError exception.

        :param msg:
            An optional custom error message.
        """
        raise FixtureLookupError(None, self, msg)

    def getfixturevalue(self, argname: str) -> Any:
        """Dynamically run a named fixture function.

        Declaring fixtures via function argument is recommended where possible.
        But if you can only decide whether to use another fixture at test
        setup time, you may use this function to retrieve it inside a fixture
        or test function body.

        This method can be used during the test setup phase or the test run
        phase, but during the test teardown phase a fixture's value may not
        be available.

        :param argname:
            The fixture name.
        :raises pytest.FixtureLookupError:
            If the given fixture could not be found.
        """
        # Note that in addition to the use case described in the docstring,
        # getfixturevalue() is also called by pytest itself during item and fixture
        # setup to evaluate the fixtures that are requested statically
        # (using function parameters, autouse, etc).

        fixturedef = self._get_active_fixturedef(argname)
        assert fixturedef.cached_result is not None, (
            f'The fixture value for "{argname}" is not available.  '
            "This can happen when the fixture has already been torn down."
        )
        return fixturedef.cached_result[0]

    def _iter_chain(self) -> Iterator[SubRequest]:
        """Yield all SubRequests in the chain, from self up.

        Note: does *not* yield the TopRequest.
        """
        current = self
        while isinstance(current, SubRequest):
            yield current
            current = current._parent_request

    def _get_active_fixturedef(self, argname: str) -> FixtureDef[object]:
        if argname == "request":
            return RequestFixtureDef(self)

        # If we already finished computing a fixture by this name in this item,
        # return it.
        fixturedef = self._fixture_defs.get(argname)
        if fixturedef is not None:
            self._check_scope(fixturedef, fixturedef._scope)
            return fixturedef

        # Find the appropriate fixturedef.
        fixturedefs = self._arg2fixturedefs.get(argname, None)
        if fixturedefs is None:
            # We arrive here because of a dynamic call to
            # getfixturevalue(argname) which was naturally
            # not known at parsing/collection time.
            fixturedefs = self._fixturemanager.getfixturedefs(argname, self._pyfuncitem)
            if fixturedefs is not None:
                self._arg2fixturedefs[argname] = fixturedefs
        # No fixtures defined with this name.
        if fixturedefs is None:
            raise FixtureLookupError(argname, self)
        # The are no fixtures with this name applicable for the function.
        if not fixturedefs:
            raise FixtureLookupError(argname, self)

        # A fixture may override another fixture with the same name, e.g. a
        # fixture in a module can override a fixture in a conftest, a fixture in
        # a class can override a fixture in the module, and so on.
        # An overriding fixture can request its own name (possibly indirectly);
        # in this case it gets the value of the fixture it overrides, one level
        # up.
        # Check how many `argname`s deep we are, and take the next one.
        # `fixturedefs` is sorted from furthest to closest, so use negative
        # indexing to go in reverse.
        index = -1
        for request in self._iter_chain():
            if request.fixturename == argname:
                index -= 1
        # If already consumed all of the available levels, fail.
        if -index > len(fixturedefs):
            raise FixtureLookupError(argname, self)
        fixturedef = fixturedefs[index]

        # Prepare a SubRequest object for calling the fixture.
        try:
            callspec = self._pyfuncitem.callspec
        except AttributeError:
            callspec = None
        if callspec is not None and argname in callspec.params:
            param = callspec.params[argname]
            param_index = callspec.indices[argname]
            # The parametrize invocation scope overrides the fixture's scope.
            scope = callspec._arg2scope[argname]
        else:
            param = NOTSET
            param_index = 0
            scope = fixturedef._scope
            self._check_fixturedef_without_param(fixturedef)
        # The parametrize invocation scope only controls caching behavior while
        # allowing wider-scoped fixtures to keep depending on the parametrized
        # fixture. Scope control is enforced for parametrized fixtures
        # by recreating the whole fixture tree on parameter change.
        # Hence `fixturedef._scope`, not `scope`.
        self._check_scope(fixturedef, fixturedef._scope)
        subrequest = SubRequest(
            self, scope, param, param_index, fixturedef, _ispytest=True
        )

        # Make sure the fixture value is cached, running it if it isn't
        fixturedef.execute(request=subrequest)

        self._fixture_defs[argname] = fixturedef
        return fixturedef

    def _check_fixturedef_without_param(self, fixturedef: FixtureDef[object]) -> None:
        """Check that this request is allowed to execute this fixturedef without
        a param."""
        funcitem = self._pyfuncitem
        has_params = fixturedef.params is not None
        fixtures_not_supported = getattr(funcitem, "nofuncargs", False)
        if has_params and fixtures_not_supported:
            msg = (
                f"{funcitem.name} does not support fixtures, maybe unittest.TestCase subclass?\n"
                f"Node id: {funcitem.nodeid}\n"
                f"Function type: {type(funcitem).__name__}"
            )
            fail(msg, pytrace=False)
        if has_params:
            frame = inspect.stack()[3]
            frameinfo = inspect.getframeinfo(frame[0])
            source_path = absolutepath(frameinfo.filename)
            source_lineno = frameinfo.lineno
            try:
                source_path_str = str(source_path.relative_to(funcitem.config.rootpath))
            except ValueError:
                source_path_str = str(source_path)
            location = getlocation(fixturedef.func, funcitem.config.rootpath)
            msg = (
                "The requested fixture has no parameter defined for test:\n"
                f"    {funcitem.nodeid}\n\n"
                f"Requested fixture '{fixturedef.argname}' defined in:\n"
                f"{location}\n\n"
                f"Requested here:\n"
                f"{source_path_str}:{source_lineno}"
            )
            fail(msg, pytrace=False)

    def _get_fixturestack(self) -> list[FixtureDef[Any]]:
        values = [request._fixturedef for request in self._iter_chain()]
        values.reverse()
        return values


@final
class TopRequest(FixtureRequest):
    """The type of the ``request`` fixture in a test function."""

    def __init__(self, pyfuncitem: Function, *, _ispytest: bool = False) -> None:
        super().__init__(
            fixturename=None,
            pyfuncitem=pyfuncitem,
            arg2fixturedefs=pyfuncitem._fixtureinfo.name2fixturedefs.copy(),
            fixture_defs={},
            _ispytest=_ispytest,
        )

    @property
    def _scope(self) -> Scope:
        return Scope.Function

    def _check_scope(
        self,
        requested_fixturedef: FixtureDef[object],
        requested_scope: Scope,
    ) -> None:
        # TopRequest always has function scope so always valid.
        pass

    @property
    def node(self):
        return self._pyfuncitem

    def __repr__(self) -> str:
        return f"<FixtureRequest for {self.node!r}>"

    def _fillfixtures(self) -> None:
        item = self._pyfuncitem
        for argname in item.fixturenames:
            if argname not in item.funcargs:
                item.funcargs[argname] = self.getfixturevalue(argname)

    def addfinalizer(self, finalizer: Callable[[], object]) -> None:
        self.node.addfinalizer(finalizer)


@final
class SubRequest(FixtureRequest):
    """The type of the ``request`` fixture in a fixture function requested
    (transitively) by a test function."""

    def __init__(
        self,
        request: FixtureRequest,
        scope: Scope,
        param: Any,
        param_index: int,
        fixturedef: FixtureDef[object],
        *,
        _ispytest: bool = False,
    ) -> None:
        super().__init__(
            pyfuncitem=request._pyfuncitem,
            fixturename=fixturedef.argname,
            fixture_defs=request._fixture_defs,
            arg2fixturedefs=request._arg2fixturedefs,
            _ispytest=_ispytest,
        )
        self._parent_request: Final[FixtureRequest] = request
        self._scope_field: Final = scope
        self._fixturedef: Final[FixtureDef[object]] = fixturedef
        if param is not NOTSET:
            self.param = param
        self.param_index: Final = param_index

    def __repr__(self) -> str:
        return f"<SubRequest {self.fixturename!r} for {self._pyfuncitem!r}>"

    @property
    def _scope(self) -> Scope:
        return self._scope_field

    @property
    def node(self):
        scope = self._scope
        if scope is Scope.Function:
            # This might also be a non-function Item despite its attribute name.
            node: nodes.Node | None = self._pyfuncitem
        elif scope is Scope.Package:
            node = get_scope_package(self._pyfuncitem, self._fixturedef)
        else:
            node = get_scope_node(self._pyfuncitem, scope)
        if node is None and scope is Scope.Class:
            # Fallback to function item itself.
            node = self._pyfuncitem
        assert node, (
            f'Could not obtain a node for scope "{scope}" for function {self._pyfuncitem!r}'
        )
        return node

    def _check_scope(
        self,
        requested_fixturedef: FixtureDef[object],
        requested_scope: Scope,
    ) -> None:
        if self._scope > requested_scope:
            # Try to report something helpful.
            argname = requested_fixturedef.argname
            fixture_stack = "\n".join(
                self._format_fixturedef_line(fixturedef)
                for fixturedef in self._get_fixturestack()
            )
            requested_fixture = self._format_fixturedef_line(requested_fixturedef)
            fail(
                f"ScopeMismatch: You tried to access the {requested_scope.value} scoped "
                f"fixture {argname} with a {self._scope.value} scoped request object. "
                f"Requesting fixture stack:\n{fixture_stack}\n"
                f"Requested fixture:\n{requested_fixture}",
                pytrace=False,
            )

    def _format_fixturedef_line(self, fixturedef: FixtureDef[object]) -> str:
        factory = fixturedef.func
        path, lineno = getfslineno(factory)
        if isinstance(path, Path):
            path = bestrelpath(self._pyfuncitem.session.path, path)
        sig = signature(factory)
        return f"{path}:{lineno + 1}:  def {factory.__name__}{sig}"

    def addfinalizer(self, finalizer: Callable[[], object]) -> None:
        self._fixturedef.addfinalizer(finalizer)


@final
class FixtureLookupError(LookupError):
    """Could not return a requested fixture (missing or invalid)."""

    def __init__(
        self, argname: str | None, request: FixtureRequest, msg: str | None = None
    ) -> None:
        self.argname = argname
        self.request = request
        self.fixturestack = request._get_fixturestack()
        self.msg = msg

    def formatrepr(self) -> FixtureLookupErrorRepr:
        tblines: list[str] = []
        addline = tblines.append
        stack = [self.request._pyfuncitem.obj]
        stack.extend(map(lambda x: x.func, self.fixturestack))
        msg = self.msg
        # This function currently makes an assumption that a non-None msg means we
        # have a non-empty `self.fixturestack`. This is currently true, but if
        # somebody at some point want to extend the use of FixtureLookupError to
        # new cases it might break.
        # Add the assert to make it clearer to developer that this will fail, otherwise
        # it crashes because `fspath` does not get set due to `stack` being empty.
        assert self.msg is None or self.fixturestack, (
            "formatrepr assumptions broken, rewrite it to handle it"
        )
        if msg is not None:
            # The last fixture raise an error, let's present
            # it at the requesting side.
            stack = stack[:-1]
        for function in stack:
            fspath, lineno = getfslineno(function)
            try:
                lines, _ = inspect.getsourcelines(get_real_func(function))
            except (OSError, IndexError, TypeError):
                error_msg = "file %s, line %s: source code not available"
                addline(error_msg % (fspath, lineno + 1))
            else:
                addline(f"file {fspath}, line {lineno + 1}")
                for i, line in enumerate(lines):
                    line = line.rstrip()
                    addline("  " + line)
                    if line.lstrip().startswith("def"):
                        break

        if msg is None:
            fm = self.request._fixturemanager
            available = set()
            parent = self.request._pyfuncitem.parent
            assert parent is not None
            for name, fixturedefs in fm._arg2fixturedefs.items():
                faclist = list(fm._matchfactories(fixturedefs, parent))
                if faclist:
                    available.add(name)
            if self.argname in available:
                msg = (
                    f" recursive dependency involving fixture '{self.argname}' detected"
                )
            else:
                msg = f"fixture '{self.argname}' not found"
            msg += "\n available fixtures: {}".format(", ".join(sorted(available)))
            msg += "\n use 'pytest --fixtures [testpath]' for help on them."

        return FixtureLookupErrorRepr(fspath, lineno, tblines, msg, self.argname)


class FixtureLookupErrorRepr(TerminalRepr):
    def __init__(
        self,
        filename: str | os.PathLike[str],
        firstlineno: int,
        tblines: Sequence[str],
        errorstring: str,
        argname: str | None,
    ) -> None:
        self.tblines = tblines
        self.errorstring = errorstring
        self.filename = filename
        self.firstlineno = firstlineno
        self.argname = argname

    def toterminal(self, tw: TerminalWriter) -> None:
        # tw.line("FixtureLookupError: %s" %(self.argname), red=True)
        for tbline in self.tblines:
            tw.line(tbline.rstrip())
        lines = self.errorstring.split("\n")
        if lines:
            tw.line(
                f"{FormattedExcinfo.fail_marker}       {lines[0].strip()}",
                red=True,
            )
            for line in lines[1:]:
                tw.line(
                    f"{FormattedExcinfo.flow_marker}       {line.strip()}",
                    red=True,
                )
        tw.line()
        tw.line(f"{os.fspath(self.filename)}:{self.firstlineno + 1}")


def call_fixture_func(
    fixturefunc: _FixtureFunc[FixtureValue], request: FixtureRequest, kwargs
) -> FixtureValue:
    if inspect.isgeneratorfunction(fixturefunc):
        fixturefunc = cast(Callable[..., Generator[FixtureValue]], fixturefunc)
        generator = fixturefunc(**kwargs)
        try:
            fixture_result = next(generator)
        except StopIteration:
            raise ValueError(f"{request.fixturename} did not yield a value") from None
        finalizer = functools.partial(_teardown_yield_fixture, fixturefunc, generator)
        request.addfinalizer(finalizer)
    else:
        fixturefunc = cast(Callable[..., FixtureValue], fixturefunc)
        fixture_result = fixturefunc(**kwargs)
    return fixture_result


def _teardown_yield_fixture(fixturefunc, it) -> None:
    """Execute the teardown of a fixture function by advancing the iterator
    after the yield and ensure the iteration ends (if not it means there is
    more than one yield in the function)."""
    try:
        next(it)
    except StopIteration:
        pass
    else:
        fs, lineno = getfslineno(fixturefunc)
        fail(
            f"fixture function has more than one 'yield':\n\n"
            f"{Source(fixturefunc).indent()}\n"
            f"{fs}:{lineno + 1}",
            pytrace=False,
        )


def _eval_scope_callable(
    scope_callable: Callable[[str, Config], _ScopeName],
    fixture_name: str,
    config: Config,
) -> _ScopeName:
    try:
        # Type ignored because there is no typing mechanism to specify
        # keyword arguments, currently.
        result = scope_callable(fixture_name=fixture_name, config=config)  # type: ignore[call-arg]
    except Exception as e:
        raise TypeError(
            f"Error evaluating {scope_callable} while defining fixture '{fixture_name}'.\n"
            "Expected a function with the signature (*, fixture_name, config)"
        ) from e
    if not isinstance(result, str):
        fail(
            f"Expected {scope_callable} to return a 'str' while defining fixture '{fixture_name}', but it returned:\n"
            f"{result!r}",
            pytrace=False,
        )
    return result


class FixtureDef(Generic[FixtureValue]):
    """A container for a fixture definition.

    Note: At this time, only explicitly documented fields and methods are
    considered public stable API.
    """

    def __init__(
        self,
        config: Config,
        baseid: str | None,
        argname: str,
        func: _FixtureFunc[FixtureValue],
        scope: Scope | _ScopeName | Callable[[str, Config], _ScopeName] | None,
        params: Sequence[object] | None,
        ids: tuple[object | None, ...] | Callable[[Any], object | None] | None = None,
        *,
        _ispytest: bool = False,
        # only used in a deprecationwarning msg, can be removed in pytest9
        _autouse: bool = False,
    ) -> None:
        check_ispytest(_ispytest)
        # The "base" node ID for the fixture.
        #
        # This is a node ID prefix. A fixture is only available to a node (e.g.
        # a `Function` item) if the fixture's baseid is a nodeid of a parent of
        # node.
        #
        # For a fixture found in a Collector's object (e.g. a `Module`s module,
        # a `Class`'s class), the baseid is the Collector's nodeid.
        #
        # For a fixture found in a conftest plugin, the baseid is the conftest's
        # directory path relative to the rootdir.
        #
        # For other plugins, the baseid is the empty string (always matches).
        self.baseid: Final = baseid or ""
        # Whether the fixture was found from a node or a conftest in the
        # collection tree. Will be false for fixtures defined in non-conftest
        # plugins.
        self.has_location: Final = baseid is not None
        # The fixture factory function.
