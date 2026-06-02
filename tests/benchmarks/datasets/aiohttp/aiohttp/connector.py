import asyncio
import functools
import random
import socket
import sys
import traceback
import warnings
from collections import OrderedDict, defaultdict, deque
from contextlib import suppress
from http import HTTPStatus
from itertools import chain, cycle, islice
from time import monotonic
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    DefaultDict,
    Deque,
    Dict,
    Iterator,
    List,
    Literal,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)

import aiohappyeyeballs
from aiohappyeyeballs import AddrInfoType, SocketFactoryType

from . import hdrs, helpers
from .abc import AbstractResolver, ResolveResult
from .client_exceptions import (
    ClientConnectionError,
    ClientConnectorCertificateError,
    ClientConnectorDNSError,
    ClientConnectorError,
    ClientConnectorSSLError,
    ClientHttpProxyError,
    ClientProxyConnectionError,
    ServerFingerprintMismatch,
    UnixClientConnectorError,
    cert_errors,
    ssl_errors,
)
from .client_proto import ResponseHandler
from .client_reqrep import ClientRequest, Fingerprint, _merge_ssl_params
from .helpers import (
    _SENTINEL,
    ceil_timeout,
    is_ip_address,
    noop,
    sentinel,
    set_exception,
    set_result,
)
from .log import client_logger
from .resolver import DefaultResolver

if sys.version_info >= (3, 12):
    from collections.abc import Buffer
else:
    Buffer = Union[bytes, bytearray, "memoryview[int]", "memoryview[bytes]"]

if TYPE_CHECKING:
    import ssl

    SSLContext = ssl.SSLContext
else:
    try:
        import ssl

        SSLContext = ssl.SSLContext
    except ImportError:  # pragma: no cover
        ssl = None  # type: ignore[assignment]
        SSLContext = object  # type: ignore[misc,assignment]

EMPTY_SCHEMA_SET = frozenset({""})
HTTP_SCHEMA_SET = frozenset({"http", "https"})
WS_SCHEMA_SET = frozenset({"ws", "wss"})

HTTP_AND_EMPTY_SCHEMA_SET = HTTP_SCHEMA_SET | EMPTY_SCHEMA_SET
HIGH_LEVEL_SCHEMA_SET = HTTP_AND_EMPTY_SCHEMA_SET | WS_SCHEMA_SET

NEEDS_CLEANUP_CLOSED = (3, 13, 0) <= sys.version_info < (
    3,
    13,
    1,
) or sys.version_info < (3, 12, 8)
# Cleanup closed is no longer needed after https://github.com/python/cpython/pull/118960
# which first appeared in Python 3.12.8 and 3.13.1


__all__ = (
    "BaseConnector",
    "TCPConnector",
    "UnixConnector",
    "NamedPipeConnector",
    "AddrInfoType",
    "SocketFactoryType",
)


if TYPE_CHECKING:
    from .client import ClientTimeout
    from .client_reqrep import ConnectionKey
    from .tracing import Trace


class _DeprecationWaiter:
    __slots__ = ("_awaitable", "_awaited")

    def __init__(self, awaitable: Awaitable[Any]) -> None:
        self._awaitable = awaitable
        self._awaited = False

    def __await__(self) -> Any:
        self._awaited = True
        return self._awaitable.__await__()

    def __del__(self) -> None:
        if not self._awaited:
            warnings.warn(
                "Connector.close() is a coroutine, "
                "please use await connector.close()",
                DeprecationWarning,
            )


async def _wait_for_close(waiters: List[Awaitable[object]]) -> None:
    """Wait for all waiters to finish closing."""
    results = await asyncio.gather(*waiters, return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            client_logger.debug("Error while closing connector: %r", res)


class Connection:

    _source_traceback = None

    def __init__(
        self,
        connector: "BaseConnector",
        key: "ConnectionKey",
        protocol: ResponseHandler,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._key = key
        self._connector = connector
        self._loop = loop
        self._protocol: Optional[ResponseHandler] = protocol
        self._callbacks: List[Callable[[], None]] = []

        if loop.get_debug():
            self._source_traceback = traceback.extract_stack(sys._getframe(1))

    def __repr__(self) -> str:
        return f"Connection<{self._key}>"

    def __del__(self, _warnings: Any = warnings) -> None:
        if self._protocol is not None:
            kwargs = {"source": self}
            _warnings.warn(f"Unclosed connection {self!r}", ResourceWarning, **kwargs)
            if self._loop.is_closed():
                return

            self._connector._release(self._key, self._protocol, should_close=True)

            context = {"client_connection": self, "message": "Unclosed connection"}
            if self._source_traceback is not None:
                context["source_traceback"] = self._source_traceback
            self._loop.call_exception_handler(context)

    def __bool__(self) -> Literal[True]:
        """Force subclasses to not be falsy, to make checks simpler."""
        return True

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        warnings.warn(
            "connector.loop property is deprecated", DeprecationWarning, stacklevel=2
        )
        return self._loop

    @property
    def transport(self) -> Optional[asyncio.Transport]:
        if self._protocol is None:
            return None
        return self._protocol.transport

    @property
    def protocol(self) -> Optional[ResponseHandler]:
        return self._protocol

    def add_callback(self, callback: Callable[[], None]) -> None:
        if callback is not None:
            self._callbacks.append(callback)

    def _notify_release(self) -> None:
        callbacks, self._callbacks = self._callbacks[:], []

        for cb in callbacks:
            with suppress(Exception):
                cb()

    def close(self) -> None:
        self._notify_release()

        if self._protocol is not None:
            self._connector._release(self._key, self._protocol, should_close=True)
            self._protocol = None

    def release(self) -> None:
        self._notify_release()

        if self._protocol is not None:
            self._connector._release(self._key, self._protocol)
            self._protocol = None

    @property
    def closed(self) -> bool:
        return self._protocol is None or not self._protocol.is_connected()


class _ConnectTunnelConnection(Connection):
    """Special connection wrapper for CONNECT tunnels that must never be pooled.

    This connection wraps the proxy connection that will be upgraded with TLS.
    It must never be released to the pool because:
    1. Its 'closed' future will never complete, causing session.close() to hang
    2. It represents an intermediate state, not a reusable connection
    3. The real connection (with TLS) will be created separately
    """

    def release(self) -> None:
        """Do nothing - don't pool or close the connection.

        These connections are an intermediate state during the CONNECT tunnel
        setup and will be cleaned up naturally after the TLS upgrade. If they
        were to be pooled, they would never be properly closed, causing
        session.close() to wait forever for their 'closed' future.
        """


class _TransportPlaceholder:
    """placeholder for BaseConnector.connect function"""

    __slots__ = ("closed", "transport")

    def __init__(self, closed_future: asyncio.Future[Optional[Exception]]) -> None:
        """Initialize a placeholder for a transport."""
        self.closed = closed_future
        self.transport = None

    def close(self) -> None:
        """Close the placeholder."""

    def abort(self) -> None:
        """Abort the placeholder (does nothing)."""


class BaseConnector:
    """Base connector class.

    keepalive_timeout - (optional) Keep-alive timeout.
    force_close - Set to True to force close and do reconnect
        after each request (and between redirects).
    limit - The total number of simultaneous connections.
    limit_per_host - Number of simultaneous connections to one host.
    enable_cleanup_closed - Enables clean-up closed ssl transports.
                            Disabled by default.
    timeout_ceil_threshold - Trigger ceiling of timeout values when
                             it's above timeout_ceil_threshold.
    loop - Optional event loop.
    """

    _closed = True  # prevent AttributeError in __del__ if ctor was failed
    _source_traceback = None

    # abort transport after 2 seconds (cleanup broken connections)
    _cleanup_closed_period = 2.0

    allowed_protocol_schema_set = HIGH_LEVEL_SCHEMA_SET

    def __init__(
        self,
        *,
        keepalive_timeout: Union[object, None, float] = sentinel,
        force_close: bool = False,
        limit: int = 100,
        limit_per_host: int = 0,
        enable_cleanup_closed: bool = False,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        timeout_ceil_threshold: float = 5,
    ) -> None:

        if force_close:
            if keepalive_timeout is not None and keepalive_timeout is not sentinel:
                raise ValueError(
                    "keepalive_timeout cannot be set if force_close is True"
                )
        else:
            if keepalive_timeout is sentinel:
                keepalive_timeout = 15.0

        loop = loop or asyncio.get_running_loop()
        self._timeout_ceil_threshold = timeout_ceil_threshold

        self._closed = False
        if loop.get_debug():
            self._source_traceback = traceback.extract_stack(sys._getframe(1))

        # Connection pool of reusable connections.
        # We use a deque to store connections because it has O(1) popleft()
        # and O(1) append() operations to implement a FIFO queue.
        self._conns: DefaultDict[
            ConnectionKey, Deque[Tuple[ResponseHandler, float]]
        ] = defaultdict(deque)
        self._limit = limit
        self._limit_per_host = limit_per_host
        self._acquired: Set[ResponseHandler] = set()
        self._acquired_per_host: DefaultDict[ConnectionKey, Set[ResponseHandler]] = (
            defaultdict(set)
        )
        self._keepalive_timeout = cast(float, keepalive_timeout)
        self._force_close = force_close

        # {host_key: FIFO list of waiters}
        # The FIFO is implemented with an OrderedDict with None keys because
        # python does not have an ordered set.
        self._waiters: DefaultDict[
            ConnectionKey, OrderedDict[asyncio.Future[None], None]
        ] = defaultdict(OrderedDict)

        self._loop = loop
        self._factory = functools.partial(ResponseHandler, loop=loop)

        # start keep-alive connection cleanup task
        self._cleanup_handle: Optional[asyncio.TimerHandle] = None

        # start cleanup closed transports task
        self._cleanup_closed_handle: Optional[asyncio.TimerHandle] = None

        if enable_cleanup_closed and not NEEDS_CLEANUP_CLOSED:
            warnings.warn(
                "enable_cleanup_closed ignored because "
                "https://github.com/python/cpython/pull/118960 is fixed "
                f"in Python version {sys.version_info}",
                DeprecationWarning,
                stacklevel=2,
            )
            enable_cleanup_closed = False

        self._cleanup_closed_disabled = not enable_cleanup_closed
        self._cleanup_closed_transports: List[Optional[asyncio.Transport]] = []
        self._placeholder_future: asyncio.Future[Optional[Exception]] = (
            loop.create_future()
        )
        self._placeholder_future.set_result(None)
        self._cleanup_closed()

    def __del__(self, _warnings: Any = warnings) -> None:
        if self._closed:
            return
        if not self._conns:
            return

        conns = [repr(c) for c in self._conns.values()]

        self._close()

        kwargs = {"source": self}
        _warnings.warn(f"Unclosed connector {self!r}", ResourceWarning, **kwargs)
        context = {
            "connector": self,
            "connections": conns,
            "message": "Unclosed connector",
        }
        if self._source_traceback is not None:
            context["source_traceback"] = self._source_traceback
        self._loop.call_exception_handler(context)

    def __enter__(self) -> "BaseConnector":
        warnings.warn(
            '"with Connector():" is deprecated, '
            'use "async with Connector():" instead',
            DeprecationWarning,
        )
        return self

    def __exit__(self, *exc: Any) -> None:
        self._close()

    async def __aenter__(self) -> "BaseConnector":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]] = None,
        exc_value: Optional[BaseException] = None,
        exc_traceback: Optional[TracebackType] = None,
    ) -> None:
        await self.close()

    @property
    def force_close(self) -> bool:
        """Ultimately close connection on releasing if True."""
        return self._force_close

    @property
    def limit(self) -> int:
        """The total number for simultaneous connections.

        If limit is 0 the connector has no limit.
        The default limit size is 100.
        """
        return self._limit

    @property
    def limit_per_host(self) -> int:
        """The limit for simultaneous connections to the same endpoint.

        Endpoints are the same if they are have equal
        (host, port, is_ssl) triple.
        """
        return self._limit_per_host

    def _cleanup(self) -> None:
        """Cleanup unused transports."""
        if self._cleanup_handle:
            self._cleanup_handle.cancel()
            # _cleanup_handle should be unset, otherwise _release() will not
            # recreate it ever!
            self._cleanup_handle = None

        now = monotonic()
        timeout = self._keepalive_timeout

        if self._conns:
            connections = defaultdict(deque)
            deadline = now - timeout
            for key, conns in self._conns.items():
                alive: Deque[Tuple[ResponseHandler, float]] = deque()
                for proto, use_time in conns:
                    if proto.is_connected() and use_time - deadline >= 0:
                        alive.append((proto, use_time))
                        continue
                    transport = proto.transport
                    proto.close()
                    if not self._cleanup_closed_disabled and key.is_ssl:
                        self._cleanup_closed_transports.append(transport)

                if alive:
                    connections[key] = alive

            self._conns = connections

        if self._conns:
            self._cleanup_handle = helpers.weakref_handle(
                self,
                "_cleanup",
                timeout,
                self._loop,
                timeout_ceil_threshold=self._timeout_ceil_threshold,
            )

    def _cleanup_closed(self) -> None:
        """Double confirmation for transport close.

        Some broken ssl servers may leave socket open without proper close.
        """
        if self._cleanup_closed_handle:
            self._cleanup_closed_handle.cancel()

        for transport in self._cleanup_closed_transports:
            if transport is not None:
                transport.abort()

        self._cleanup_closed_transports = []

        if not self._cleanup_closed_disabled:
            self._cleanup_closed_handle = helpers.weakref_handle(
                self,
                "_cleanup_closed",
                self._cleanup_closed_period,
                self._loop,
                timeout_ceil_threshold=self._timeout_ceil_threshold,
            )

    def close(self, *, abort_ssl: bool = False) -> Awaitable[None]:
        """Close all opened transports.

        :param abort_ssl: If True, SSL connections will be aborted immediately
                         without performing the shutdown handshake. This provides
                         faster cleanup at the cost of less graceful disconnection.
        """
        if not (waiters := self._close(abort_ssl=abort_ssl)):
            # If there are no connections to close, we can return a noop
            # awaitable to avoid scheduling a task on the event loop.
            return _DeprecationWaiter(noop())
        coro = _wait_for_close(waiters)
        if sys.version_info >= (3, 12):
            # Optimization for Python 3.12, try to close connections
            # immediately to avoid having to schedule the task on the event loop.
            task = asyncio.Task(coro, loop=self._loop, eager_start=True)
        else:
            task = self._loop.create_task(coro)
        return _DeprecationWaiter(task)

    def _close(self, *, abort_ssl: bool = False) -> List[Awaitable[object]]:
        waiters: List[Awaitable[object]] = []

        if self._closed:
            return waiters

        self._closed = True

        try:
            if self._loop.is_closed():
                return waiters

            # cancel cleanup task
            if self._cleanup_handle:
                self._cleanup_handle.cancel()

            # cancel cleanup close task
            if self._cleanup_closed_handle:
                self._cleanup_closed_handle.cancel()

            for data in self._conns.values():
                for proto, _ in data:
                    if (
                        abort_ssl
                        and proto.transport
                        and proto.transport.get_extra_info("sslcontext") is not None
                    ):
                        proto.abort()
                    else:
                        proto.close()
                    if closed := proto.closed:
                        waiters.append(closed)

            for proto in self._acquired:
                if (
                    abort_ssl
                    and proto.transport
                    and proto.transport.get_extra_info("sslcontext") is not None
                ):
                    proto.abort()
                else:
                    proto.close()
                if closed := proto.closed:
                    waiters.append(closed)

            for transport in self._cleanup_closed_transports:
                if transport is not None:
                    transport.abort()

            return waiters

        finally:
            self._conns.clear()
            self._acquired.clear()
            for keyed_waiters in self._waiters.values():
                for keyed_waiter in keyed_waiters:
                    keyed_waiter.cancel()
            self._waiters.clear()
            self._cleanup_handle = None
            self._cleanup_closed_transports.clear()
            self._cleanup_closed_handle = None

    @property
    def closed(self) -> bool:
        """Is connector closed.

        A readonly property.
        """
        return self._closed

    def _available_connections(self, key: "ConnectionKey") -> int:
        """
        Return number of available connections.

        The limit, limit_per_host and the connection key are taken into account.

        If it returns less than 1 means that there are no connections
        available.
        """
        # check total available connections
        # If there are no limits, this will always return 1
        total_remain = 1

        if self._limit and (total_remain := self._limit - len(self._acquired)) <= 0:
            return total_remain

        # check limit per host
        if host_remain := self._limit_per_host:
            if acquired := self._acquired_per_host.get(key):
                host_remain -= len(acquired)
            if total_remain > host_remain:
                return host_remain

        return total_remain

    def _update_proxy_auth_header_and_build_proxy_req(
        self, req: ClientRequest
    ) -> ClientRequest:
        """Set Proxy-Authorization header for non-SSL proxy requests and builds the proxy request for SSL proxy requests."""
        url = req.proxy
        assert url is not None
        headers: Dict[str, str] = {}
        if req.proxy_headers is not None:
            headers = req.proxy_headers  # type: ignore[assignment]
        headers[hdrs.HOST] = req.headers[hdrs.HOST]
        proxy_req = ClientRequest(
            hdrs.METH_GET,
            url,
            headers=headers,
            auth=req.proxy_auth,
            loop=self._loop,
            ssl=req.ssl,
        )
        auth = proxy_req.headers.pop(hdrs.AUTHORIZATION, None)
        if auth is not None:
            if not req.is_ssl():
                req.headers[hdrs.PROXY_AUTHORIZATION] = auth
            else:
                proxy_req.headers[hdrs.PROXY_AUTHORIZATION] = auth
        return proxy_req

    async def connect(
        self, req: ClientRequest, traces: List["Trace"], timeout: "ClientTimeout"
    ) -> Connection:
        """Get from pool or create new connection."""
        key = req.connection_key
        if (conn := await self._get(key, traces)) is not None:
            # If we do not have to wait and we can get a connection from the pool
            # we can avoid the timeout ceil logic and directly return the connection
            if req.proxy:
                self._update_proxy_auth_header_and_build_proxy_req(req)
            return conn

        async with ceil_timeout(timeout.connect, timeout.ceil_threshold):
            if self._available_connections(key) <= 0:
                await self._wait_for_available_connection(key, traces)
                if (conn := await self._get(key, traces)) is not None:
                    if req.proxy:
                        self._update_proxy_auth_header_and_build_proxy_req(req)
                    return conn

            placeholder = cast(
                ResponseHandler, _TransportPlaceholder(self._placeholder_future)
            )
            self._acquired.add(placeholder)
            if self._limit_per_host:
                self._acquired_per_host[key].add(placeholder)

            try:
                # Traces are done inside the try block to ensure that the
                # that the placeholder is still cleaned up if an exception
                # is raised.
                if traces:
                    for trace in traces:
                        await trace.send_connection_create_start()
                proto = await self._create_connection(req, traces, timeout)
                if traces:
                    for trace in traces:
                        await trace.send_connection_create_end()
            except BaseException:
                self._release_acquired(key, placeholder)
                raise
            else:
                if self._closed:
                    proto.close()
                    raise ClientConnectionError("Connector is closed.")

        # The connection was successfully created, drop the placeholder
        # and add the real connection to the acquired set. There should
        # be no awaits after the proto is added to the acquired set
        # to ensure that the connection is not left in the acquired set
        # on cancellation.
        self._acquired.remove(placeholder)
        self._acquired.add(proto)
        if self._limit_per_host:
            acquired_per_host = self._acquired_per_host[key]
            acquired_per_host.remove(placeholder)
            acquired_per_host.add(proto)
        return Connection(self, key, proto, self._loop)

    async def _wait_for_available_connection(
        self, key: "ConnectionKey", traces: List["Trace"]
    ) -> None:
        """Wait for an available connection slot."""
        # We loop here because there is a race between
        # the connection limit check and the connection
        # being acquired. If the connection is acquired
        # between the check and the await statement, we
        # need to loop again to check if the connection
        # slot is still available.
        attempts = 0
        while True:
            fut: asyncio.Future[None] = self._loop.create_future()
            keyed_waiters = self._waiters[key]
            keyed_waiters[fut] = None
            if attempts:
                # If we have waited before, we need to move the waiter
                # to the front of the queue as otherwise we might get
                # starved and hit the timeout.
                keyed_waiters.move_to_end(fut, last=False)

            try:
                # Traces happen in the try block to ensure that the
                # the waiter is still cleaned up if an exception is raised.
                if traces:
                    for trace in traces:
                        await trace.send_connection_queued_start()
                await fut
                if traces:
                    for trace in traces:
                        await trace.send_connection_queued_end()
            finally:
                # pop the waiter from the queue if its still
                # there and not already removed by _release_waiter
                keyed_waiters.pop(fut, None)
                if not self._waiters.get(key, True):
                    del self._waiters[key]

            if self._available_connections(key) > 0:
                break
            attempts += 1

    async def _get(
        self, key: "ConnectionKey", traces: List["Trace"]
    ) -> Optional[Connection]:
        """Get next reusable connection for the key or None.

        The connection will be marked as acquired.
        """
        if (conns := self._conns.get(key)) is None:
            return None

        t1 = monotonic()
        while conns:
            proto, t0 = conns.popleft()
            # We will we reuse the connection if its connected and
            # the keepalive timeout has not been exceeded
            if proto.is_connected() and t1 - t0 <= self._keepalive_timeout:
                if not conns:
                    # The very last connection was reclaimed: drop the key
                    del self._conns[key]
                self._acquired.add(proto)
                if self._limit_per_host:
                    self._acquired_per_host[key].add(proto)
                if traces:
                    for trace in traces:
                        try:
                            await trace.send_connection_reuseconn()
                        except BaseException:
                            self._release_acquired(key, proto)
                            raise
                return Connection(self, key, proto, self._loop)

            # Connection cannot be reused, close it
            transport = proto.transport
            proto.close()
            # only for SSL transports
            if not self._cleanup_closed_disabled and key.is_ssl:
                self._cleanup_closed_transports.append(transport)

        # No more connections: drop the key
        del self._conns[key]
        return None

    def _release_waiter(self) -> None:
        """
        Iterates over all waiters until one to be released is found.

        The one to be released is not finished and
        belongs to a host that has available connections.
        """
        if not self._waiters:
            return

        # Having the dict keys ordered this avoids to iterate
        # at the same order at each call.
        queues = list(self._waiters)
        random.shuffle(queues)

        for key in queues:
            if self._available_connections(key) < 1:
                continue

            waiters = self._waiters[key]
            while waiters:
                waiter, _ = waiters.popitem(last=False)
                if not waiter.done():
                    waiter.set_result(None)
                    return

    def _release_acquired(self, key: "ConnectionKey", proto: ResponseHandler) -> None:
        """Release acquired connection."""
        if self._closed:
            # acquired connection is already released on connector closing
            return

        self._acquired.discard(proto)
        if self._limit_per_host and (conns := self._acquired_per_host.get(key)):
            conns.discard(proto)
            if not conns:
                del self._acquired_per_host[key]
        self._release_waiter()

    def _release(
        self,
        key: "ConnectionKey",
        protocol: ResponseHandler,
        *,
        should_close: bool = False,
    ) -> None:
        if self._closed:
            # acquired connection is already released on connector closing
            return

        self._release_acquired(key, protocol)

        if self._force_close or should_close or protocol.should_close:
            transport = protocol.transport
            protocol.close()

            if key.is_ssl and not self._cleanup_closed_disabled:
                self._cleanup_closed_transports.append(transport)
            return

        self._conns[key].append((protocol, monotonic()))

        if self._cleanup_handle is None:
            self._cleanup_handle = helpers.weakref_handle(
                self,
                "_cleanup",
                self._keepalive_timeout,
                self._loop,
                timeout_ceil_threshold=self._timeout_ceil_threshold,
            )

    async def _create_connection(
        self, req: ClientRequest, traces: List["Trace"], timeout: "ClientTimeout"
    ) -> ResponseHandler:
        raise NotImplementedError()


class _DNSCacheTable:
    def __init__(self, ttl: Optional[float] = None, max_size: int = 1000) -> None:
        self._addrs_rr: OrderedDict[
            Tuple[str, int], Tuple[Iterator[ResolveResult], int]
        ] = OrderedDict()
        self._timestamps: Dict[Tuple[str, int], float] = {}
        self._ttl = ttl
        self._max_size = max_size

    def __contains__(self, host: object) -> bool:
        return host in self._addrs_rr

    def add(self, key: Tuple[str, int], addrs: List[ResolveResult]) -> None:
        if key in self._addrs_rr:
            self._addrs_rr.move_to_end(key)

        self._addrs_rr[key] = (cycle(addrs), len(addrs))

        if self._ttl is not None:
            self._timestamps[key] = monotonic()

        if len(self._addrs_rr) > self._max_size:
            oldest_key, _ = self._addrs_rr.popitem(last=False)
            self._timestamps.pop(oldest_key, None)

    def remove(self, key: Tuple[str, int]) -> None:
        self._addrs_rr.pop(key, None)
        self._timestamps.pop(key, None)

    def clear(self) -> None:
        self._addrs_rr.clear()
        self._timestamps.clear()

    def next_addrs(self, key: Tuple[str, int]) -> List[ResolveResult]:
        loop, length = self._addrs_rr[key]
        addrs = list(islice(loop, length))
        # Consume one more element to shift internal state of `cycle`
        next(loop)
        self._addrs_rr.move_to_end(key)
        return addrs

    def expired(self, key: Tuple[str, int]) -> bool:
        if self._ttl is None:
            return False

        return self._timestamps[key] + self._ttl < monotonic()


def _make_ssl_context(verified: bool) -> SSLContext:
    """Create SSL context.

    This method is not async-friendly and should be called from a thread
    because it will load certificates from disk and do other blocking I/O.
    """
    if ssl is None:
        # No ssl support
        return None
    if verified:
        sslcontext = ssl.create_default_context()
    else:
        sslcontext = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        sslcontext.options |= ssl.OP_NO_SSLv2
        sslcontext.options |= ssl.OP_NO_SSLv3
        sslcontext.check_hostname = False
        sslcontext.verify_mode = ssl.CERT_NONE
        sslcontext.options |= ssl.OP_NO_COMPRESSION
        sslcontext.set_default_verify_paths()
    sslcontext.set_alpn_protocols(("http/1.1",))
    return sslcontext


# The default SSLContext objects are created at import time
# since they do blocking I/O to load certificates from disk,
# and imports should always be done before the event loop starts
# or in a thread.
_SSL_CONTEXT_VERIFIED = _make_ssl_context(True)
_SSL_CONTEXT_UNVERIFIED = _make_ssl_context(False)


class TCPConnector(BaseConnector):
    """TCP connector.

    verify_ssl - Set to True to check ssl certifications.
    fingerprint - Pass the binary sha256
        digest of the expected certificate in DER format to verify
        that the certificate the server presents matches. See also
        https://en.wikipedia.org/wiki/HTTP_Public_Key_Pinning
    resolver - Enable DNS lookups and use this
        resolver
    use_dns_cache - Use memory cache for DNS lookups.
    ttl_dns_cache - Max seconds having cached a DNS entry, None forever.
    family - socket address family
    local_addr - local tuple of (host, port) to bind socket to

    keepalive_timeout - (optional) Keep-alive timeout.
    force_close - Set to True to force close and do reconnect
        after each request (and between redirects).
    limit - The total number of simultaneous connections.
    limit_per_host - Number of simultaneous connections to one host.
    enable_cleanup_closed - Enables clean-up closed ssl transports.
                            Disabled by default.
    happy_eyeballs_delay - This is the “Connection Attempt Delay”
                           as defined in RFC 8305. To disable
                           the happy eyeballs algorithm, set to None.
    interleave - “First Address Family Count” as defined in RFC 8305
    loop - Optional event loop.
    socket_factory - A SocketFactoryType function that, if supplied,
                     will be used to create sockets given an
                     AddrInfoType.
    ssl_shutdown_timeout - DEPRECATED. Will be removed in aiohttp 4.0.
                           Grace period for SSL shutdown handshake on TLS
                           connections. Default is 0 seconds (immediate abort).
                           This parameter allowed for a clean SSL shutdown by
                           notifying the remote peer of connection closure,
                           while avoiding excessive delays during connector cleanup.
                           Note: Only takes effect on Python 3.11+.
    """

    allowed_protocol_schema_set = HIGH_LEVEL_SCHEMA_SET | frozenset({"tcp"})

    def __init__(
        self,
        *,
        verify_ssl: bool = True,
        fingerprint: Optional[bytes] = None,
        use_dns_cache: bool = True,
        ttl_dns_cache: Optional[int] = 10,
        dns_cache_max_size: int = 1000,
        family: socket.AddressFamily = socket.AddressFamily.AF_UNSPEC,
        ssl_context: Optional[SSLContext] = None,
        ssl: Union[bool, Fingerprint, SSLContext] = True,
        local_addr: Optional[Tuple[str, int]] = None,
        resolver: Optional[AbstractResolver] = None,
        keepalive_timeout: Union[None, float, object] = sentinel,
        force_close: bool = False,
        limit: int = 100,
        limit_per_host: int = 0,
        enable_cleanup_closed: bool = False,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        timeout_ceil_threshold: float = 5,
        happy_eyeballs_delay: Optional[float] = 0.25,
        interleave: Optional[int] = None,
        socket_factory: Optional[SocketFactoryType] = None,
