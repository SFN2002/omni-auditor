#
# This file is part of gunicorn released under the MIT license.
# See the NOTICE for more information.

"""
ASGI protocol handler for gunicorn.

Implements asyncio.Protocol to handle HTTP/1.x and HTTP/2 connections
and dispatch to ASGI applications.
"""

import asyncio
import errno
import ipaddress
import time

from gunicorn.asgi.unreader import AsyncUnreader
from gunicorn.asgi.parser import (
    PythonProtocol, CallbackRequest, ParseError,
    LimitRequestLine, LimitRequestHeaders, InvalidChunkExtension
)
from gunicorn.asgi.uwsgi import AsyncUWSGIRequest
from gunicorn.http.errors import NoMoreData
from gunicorn.uwsgi.errors import UWSGIParseException


class _RequestTime:
    """Lightweight request time container compatible with logging atoms.

    Uses time.monotonic() elapsed seconds instead of datetime.now() syscalls.
    Provides .seconds and .microseconds attributes for glogging.py compatibility.
    """

    __slots__ = ('seconds', 'microseconds')

    def __init__(self, elapsed):
        self.seconds = int(elapsed)
        self.microseconds = int((elapsed - self.seconds) * 1_000_000)


def _normalize_sockaddr(sockaddr):
    """Normalize socket address to ASGI-compatible (host, port) tuple.

    ASGI spec requires server/client to be (host, port) tuples.
    IPv6 sockets return 4-tuples (host, port, flowinfo, scope_id),
    so we extract just the first two elements.
    """
    return tuple(sockaddr[:2]) if sockaddr else None


def _check_trusted_proxy(peer_addr, allow_list, networks):
    """Check if peer address is in the trusted proxy list.

    Cached at connection start to avoid repeated IP parsing per request.
    """
    if not isinstance(peer_addr, tuple):
        return False
    if '*' in allow_list:
        return True
    try:
        ip = ipaddress.ip_address(peer_addr[0])
    except ValueError:
        return False
    for network in networks:
        if ip in network:
            return True
    return False


# Cached response bytes for common cases
_CACHED_STATUS_LINES = {}
_CACHED_SERVER_HEADER = b"Server: gunicorn/asgi\r\n"

# Date header cache (updated once per second)
_cached_date_header = b""
_cached_date_time = 0.0

# Pre-compute common chunk size prefixes to avoid repeated formatting
_CHUNK_PREFIXES = {i: f"{i:x}\r\n".encode("latin-1") for i in range(16384)}

# High water mark for write buffer backpressure (64KB)
HIGH_WATER_LIMIT = 65536


class FlowControl:
    """Manage transport-level write flow control.

    Blocks send() when transport buffer exceeds high water mark,
    preventing memory issues with large streaming responses.
    """
    __slots__ = ('_transport', 'read_paused', 'write_paused', '_is_writable_event')

    def __init__(self, transport):
        self._transport = transport
        self.read_paused = False
        self.write_paused = False
        self._is_writable_event = asyncio.Event()
        self._is_writable_event.set()

    async def drain(self):
        """Wait until transport is writable."""
        await self._is_writable_event.wait()

    def pause_reading(self):
        if not self.read_paused:
            self.read_paused = True
            self._transport.pause_reading()

    def resume_reading(self):
        if self.read_paused:
            self.read_paused = False
            self._transport.resume_reading()

    def pause_writing(self):
        if not self.write_paused:
            self.write_paused = True
            self._is_writable_event.clear()

    def resume_writing(self):
        if self.write_paused:
            self.write_paused = False
            self._is_writable_event.set()


def _get_cached_date_header():
    """Get cached Date header, updating once per second."""
    global _cached_date_header, _cached_date_time  # pylint: disable=global-statement
    now = time.time()
    if now - _cached_date_time >= 1.0:
        # Update date header
        from email.utils import formatdate
        _cached_date_header = f"Date: {formatdate(usegmt=True)}\r\n".encode("latin-1")
        _cached_date_time = now
    return _cached_date_header


def _get_cached_status_line(version, status, reason):
    """Get cached status line bytes."""
    key = (version, status)
    if key not in _CACHED_STATUS_LINES:
        line = f"HTTP/{version[0]}.{version[1]} {status} {reason}\r\n"
        _CACHED_STATUS_LINES[key] = line.encode("latin-1")
    return _CACHED_STATUS_LINES[key]


class ASGIResponseInfo:
    """Simple container for ASGI response info for access logging."""

    def __init__(self, status, headers, sent):
        self.status = status
        self.sent = sent
        # Convert headers to list of string tuples for logging
        self.headers = []
        for name, value in headers:
            if isinstance(name, bytes):
                name = name.decode("latin-1")
            if isinstance(value, bytes):
                value = value.decode("latin-1")
            self.headers.append((name, value))


class BodyReceiver:
    """Body receiver for callback-based parsers.

    Body chunks are fed directly via the feed() method from parser callbacks.
    Uses Future-based waiting for efficient async receive().
    """

    __slots__ = ('_chunks', '_complete', '_body_finished', '_closed',
                 '_body_wait_expired', '_waiter', 'request', 'protocol')

    def __init__(self, request, protocol):
        self.request = request
        self.protocol = protocol
        self._chunks = []
        self._complete = False
        self._body_finished = False  # True after returning more_body=False
        # _closed means the client transport has gone away (signal_disconnect
        # was called or the protocol detected a disconnect).  _body_wait_expired
        # means the body did not finish framing within the configured timeout
        # but the transport itself may still be open.  Both surface as
        # http.disconnect to the app, but they are distinct conditions.
        self._closed = False
        self._body_wait_expired = False
        self._waiter = None

    def feed(self, chunk):
        """Feed a body chunk directly (called by parser callback)."""
        if chunk:
            self._chunks.append(chunk)
            self._wake_waiter()

    def set_complete(self):
        """Mark body as complete (called when message ends)."""
        self._complete = True
        self._wake_waiter()

    def signal_disconnect(self):
        """Signal that the client transport has gone away."""
        self._closed = True
        self._wake_waiter()

    @property
    def _disconnected(self):
        """True when the receiver should yield http.disconnect to the app."""
        return self._closed or self._body_wait_expired

    def _wake_waiter(self):
        """Wake up any pending receive() call."""
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)

    async def receive(self):  # pylint: disable=too-many-return-statements
        """ASGI receive callable - returns body chunks or disconnect."""
        # Already disconnected (transport closed or body wait timed out)
        if self._disconnected:
            return {"type": "http.disconnect"}

        # Body finished but not disconnected - wait for actual disconnect
        # This is needed for frameworks like Django that listen for disconnect
        if self._body_finished:
            await self._wait_for_disconnect()
            return {"type": "http.disconnect"}

        # Fast path: chunk already available
        if self._chunks:
            return self._pop_chunk()

        # Body complete with no more chunks
        if self._complete:
            self._body_finished = True
            return {"type": "http.request", "body": b"", "more_body": False}

        # No body expected
        if self.request.content_length == 0 and not self.request.chunked:
            self._complete = True
            self._body_finished = True
            return {"type": "http.request", "body": b"", "more_body": False}

        # Check protocol closed state
        if self.protocol._closed:
            self._closed = True
            return {"type": "http.disconnect"}

        # Wait for body chunk to arrive via callback
        try:
            await self._wait_for_data()
            return self._build_receive_result()
        except asyncio.CancelledError:
            return {"type": "http.disconnect"}

    def _pop_chunk(self):
        """Pop a chunk and return the appropriate message."""
        chunk = self._chunks.pop(0)
        more = bool(self._chunks) or not self._complete
        if not more:
            self._body_finished = True
        return {"type": "http.request", "body": chunk, "more_body": more}

    def _build_receive_result(self):
        """Build receive result after waiting for data."""
        if self._disconnected:
            return {"type": "http.disconnect"}

        if self._chunks:
            return self._pop_chunk()

        if self._complete:
            self._body_finished = True
            return {"type": "http.request", "body": b"", "more_body": False}

        # Wait returned without data and the message was not framed complete:
        # treat as a body-wait expiry rather than synthesizing end-of-body
        # (which would desync the next pipelined request).
        self._body_wait_expired = True
        return {"type": "http.disconnect"}

    async def _wait_for_data(self):
        """Wait for body data to arrive via callback."""
        if self._chunks or self._complete or self._disconnected:
            return

        # Create a new waiter
        loop = asyncio.get_event_loop()
        self._waiter = loop.create_future()

        # Bound the wait by the configured worker timeout (default 30s).
        # The protocol-level timeout drives transport disconnect handling;
        # this only needs to escape an idle wait if data never arrives.
        cfg = getattr(self.protocol, 'cfg', None)
        timeout = getattr(cfg, 'timeout', None) if cfg is not None else None
        if not timeout or timeout <= 0:
            timeout = 30.0

        try:
            await asyncio.wait_for(self._waiter, timeout=timeout)
        except asyncio.TimeoutError:
            # No data arrived in time: mark body-wait as expired so receive()
            # yields http.disconnect rather than a fake terminal http.request
            # with more_body=False.  The transport itself may still be alive;
            # _closed stays False so any code keying on transport-disconnect
            # only is unaffected.
            self._body_wait_expired = True
        finally:
            self._waiter = None

    async def _wait_for_disconnect(self):
        """Wait for connection to close after body is finished.

        This is needed for ASGI apps (like Django) that call receive()
        to listen for client disconnect after the request body is consumed.
        """
        if self._closed:
            return

        # Check protocol closed state first
        if self.protocol._closed:
            self._closed = True
            return

        # Create a new waiter to wait for disconnect
        loop = asyncio.get_event_loop()
        self._waiter = loop.create_future()

        try:
            # Wait indefinitely for disconnect (or until cancelled)
            await self._waiter
        except asyncio.CancelledError:
            pass
        finally:
            self._waiter = None
            self._closed = True


class ASGIProtocol(asyncio.Protocol):
    """HTTP/1.1 protocol handler for ASGI applications.

    Handles connection lifecycle, request parsing, and ASGI app invocation.
    Uses callback-based parsing (H1CProtocol/PythonProtocol) for efficient
    incremental parsing in data_received().
    """

    # Class-level cache for H1CProtocol availability
    _h1c_available = None
    _h1c_protocol_class = None
    _h1c_has_limits = False  # True if >= 0.4.1 (has limit parameters)
    _h1c_limit_request_line = None  # Exception class from gunicorn_h1c >= 0.4.1
    _h1c_limit_request_headers = None  # Exception class from gunicorn_h1c >= 0.4.1
    _h1c_invalid_chunk_extension = None  # Exception class from gunicorn_h1c >= 0.6.3

    def __init__(self, worker):
        self.worker = worker
        self.cfg = worker.cfg
        self.log = worker.log
        self.app = worker.asgi

        self.transport = None
        self.reader = None  # Only used for HTTP/2
        self.writer = None
        self._task = None
        self.req_count = 0

        # Connection state
        self._closed = False
        self._body_receiver = None  # Set per-request for disconnect signaling

        # Response buffering for write batching
        self._response_buffer = None

        # Backpressure control
        self._reading_paused = False
        self._max_buffer_size = 65536 * 4  # 256KB max buffer (HTTP/2 only)

        # Keep-alive timer
        self._keepalive_handle = None

        # Callback parser state
        self._callback_parser = None
        self._request_ready = None  # Event signaling headers complete
        self._current_request = None  # Request built from parser state
        self._is_ssl = False

        # Write flow control
        self._flow_control = None

        # WebSocket protocol (set during upgrade, receives data via callbacks)
        self._websocket = None

    def connection_made(self, transport):
        """Called when a connection is established."""
        self.transport = transport
        self.worker.nr_conns += 1

        # Check if HTTP/2 was negotiated via ALPN
        ssl_object = transport.get_extra_info('ssl_object')
        if ssl_object and hasattr(ssl_object, 'selected_alpn_protocol'):
            alpn = ssl_object.selected_alpn_protocol()
            if alpn == 'h2':
                # HTTP/2 connection - uses StreamReader (complex framing)
                self.reader = asyncio.StreamReader()
                self._task = self.worker.loop.create_task(
                    self._handle_http2_connection(transport, ssl_object)
                )
                return

        # HTTP/1.x connection - always use callback parser
        self._is_ssl = ssl_object is not None
        self.writer = transport

        # Setup flow control for HTTP/1.x
        self._flow_control = FlowControl(transport)
        transport.set_write_buffer_limits(high=HIGH_WATER_LIMIT)

        # Setup callback parser with request ready event
        self._request_ready = asyncio.Event()
        self._setup_callback_parser()
        self._task = self.worker.loop.create_task(self._handle_connection())

    @classmethod
    def _check_h1c_protocol_available(cls):
        """Check if H1CProtocol is available (cached at class level)."""
        if cls._h1c_available is None:
            try:
                import gunicorn_h1c
                from gunicorn_h1c import H1CProtocol
                cls._h1c_available = True
                cls._h1c_protocol_class = H1CProtocol
                # Require >= 0.4.1 for limit enforcement
                cls._h1c_has_limits = hasattr(gunicorn_h1c, 'LimitRequestLine')
                # Store h1c exception classes for handling (>= 0.4.1)
                cls._h1c_limit_request_line = getattr(
                    gunicorn_h1c, 'LimitRequestLine', None
                )
                cls._h1c_limit_request_headers = getattr(
                    gunicorn_h1c, 'LimitRequestHeaders', None
                )
                # Check for InvalidChunkExtension (>= 0.6.3)
                cls._h1c_invalid_chunk_extension = getattr(
                    gunicorn_h1c, 'InvalidChunkExtension', None
                )
            except ImportError:
                cls._h1c_available = False
                cls._h1c_has_limits = False
        return cls._h1c_available

    # Compatibility flags not supported by the fast parser
    _FAST_PARSER_INCOMPATIBLE_FLAGS = (
        'permit_obsolete_folding',
        'strip_header_spaces',
    )

    def _setup_callback_parser(self):
        """Create callback parser based on http_parser setting.

        Parser selection:
        - auto: Use H1CProtocol if available (>= 0.4.1) and no incompatible flags, else PythonProtocol
        - fast: Require H1CProtocol >= 0.4.1 (error if unavailable or incompatible flags)
        - python: Use PythonProtocol only
        """
        parser_setting = getattr(self.cfg, 'http_parser', 'auto')

        # Check for incompatible compatibility flags
        incompatible = []
        for flag in self._FAST_PARSER_INCOMPATIBLE_FLAGS:
            if getattr(self.cfg, flag, False):
                incompatible.append(flag)
        # PROXY protocol framing is implemented only in PythonProtocol; the C parser
        # has no proxy_protocol kwarg and would silently drop the framing.
        if getattr(self.cfg, 'proxy_protocol', 'off') != 'off':
            incompatible.append('proxy_protocol')

        if parser_setting == 'python':
            parser_class = PythonProtocol
        elif parser_setting == 'fast':
            if not self._check_h1c_protocol_available():
                raise RuntimeError("gunicorn_h1c required for http_parser='fast'")
            if not ASGIProtocol._h1c_has_limits:
                raise RuntimeError(
                    "gunicorn_h1c >= 0.4.1 required for http_parser='fast'. "
                    "Please upgrade: pip install --upgrade gunicorn_h1c"
                )
            if incompatible:
                raise RuntimeError(
                    "http_parser='fast' is incompatible with compatibility flags: %s. "
                    "Use http_parser='python' or disable these flags."
                    % ', '.join(incompatible)
                )
            parser_class = ASGIProtocol._h1c_protocol_class
        else:  # auto
            if (self._check_h1c_protocol_available() and
                    ASGIProtocol._h1c_has_limits and not incompatible):
                parser_class = ASGIProtocol._h1c_protocol_class
            else:
                parser_class = PythonProtocol

        # Handle limit_request_line=0 (unlimited per documentation)
        # PythonProtocol handles 0 correctly, but C parser needs a large value
        limit_request_line = self.cfg.limit_request_line
        if limit_request_line == 0 and parser_class != PythonProtocol:
            limit_request_line = 1024 * 1024  # 1MB for C parser

        # Create parser with callbacks and limit parameters (both parsers support them).
        # Only the Python parser implements PROXY protocol framing; pass the option there.
        parser_kwargs = {
            'on_headers_complete': self._on_headers_complete,
            'on_body': self._on_body,
            'on_message_complete': self._on_message_complete,
            'limit_request_line': limit_request_line,
            'limit_request_fields': self.cfg.limit_request_fields,
            'limit_request_field_size': self.cfg.limit_request_field_size,
            'permit_unconventional_http_method': self.cfg.permit_unconventional_http_method,
            'permit_unconventional_http_version': self.cfg.permit_unconventional_http_version,
        }
        if parser_class is PythonProtocol:
            # PROXY framing is only honored when the peer is in
            # ``proxy_allow_ips`` (the WSGI parser enforces the same gate at
            # gunicorn/http/message.py:proxy_protocol_access_check).  Untrusted
            # peers get proxy_protocol='off', so any framing they send is
            # interpreted as malformed HTTP and rejected with a 400.
            cfg_proxy = getattr(self.cfg, 'proxy_protocol', 'off')
            if cfg_proxy != 'off':
                peername = self.transport.get_extra_info('peername')
                normalized = _normalize_sockaddr(peername)
                trusted = _check_trusted_proxy(
                    normalized,
                    self.cfg.proxy_allow_ips,
                    self.cfg.proxy_allow_networks(),
                )
                parser_kwargs['proxy_protocol'] = cfg_proxy if trusted else 'off'
            else:
                parser_kwargs['proxy_protocol'] = 'off'
        self._callback_parser = parser_class(**parser_kwargs)

    def _on_headers_complete(self):
        """Callback: request headers are complete."""
        # Build request from parser state
        self._current_request = CallbackRequest.from_parser(
            self._callback_parser, is_ssl=self._is_ssl
        )

        # Create body receiver for this request
        self._body_receiver = BodyReceiver(self._current_request, self)

        # Signal that request is ready for processing
        if self._request_ready:
            self._request_ready.set()

        # Return True for HEAD to skip body parsing
        return self._callback_parser.method == b'HEAD'

    def _on_body(self, chunk):
        """Callback: received body data chunk."""
        if self._body_receiver:
            self._body_receiver.feed(chunk)

    def _on_message_complete(self):
        """Callback: request is fully received."""
        if self._body_receiver:
            self._body_receiver.set_complete()

    def _handle_h1c_exception(self, exc):
        """Handle gunicorn_h1c exceptions with appropriate HTTP status codes.

        Returns True if the exception was handled, False otherwise.
        """
        # pylint: disable=isinstance-second-argument-not-valid-type
        h1c_limit_line = ASGIProtocol._h1c_limit_request_line
        if h1c_limit_line is not None and isinstance(exc, h1c_limit_line):
            self._send_error_response(414, str(exc))  # URI Too Long
            self._close_transport()
            return True
        h1c_limit_headers = ASGIProtocol._h1c_limit_request_headers
        if h1c_limit_headers is not None and isinstance(exc, h1c_limit_headers):
            self._send_error_response(431, str(exc))  # Request Header Fields Too Large
            self._close_transport()
            return True
        h1c_chunk_ext = ASGIProtocol._h1c_invalid_chunk_extension
        if h1c_chunk_ext is not None and isinstance(exc, h1c_chunk_ext):
            self._send_error_response(400, str(exc))
            self._close_transport()
            return True
        return False

    def data_received(self, data):
        """Called when data is received on the connection."""
        if self._websocket:
            # WebSocket path - forward to WebSocket protocol
            self._websocket.feed_data(data)
            return
        if self.reader:
            # HTTP/2 path - use StreamReader
            self.reader.feed_data(data)
        elif self._callback_parser:
            # HTTP/1.x path - feed directly to callback parser
            if not self._feed_callback_parser(data):
                return

        # Backpressure: pause reading if buffer is too large
        if not self._reading_paused and self._is_buffer_full():
            self._pause_reading()

    def _feed_callback_parser(self, data):
        """Feed data to callback parser, handling parse errors.

        Returns True if parsing should continue, False if connection was closed.
        """
        try:
            self._callback_parser.feed(data)
            return True
        except LimitRequestLine as e:
            self._send_error_response(414, str(e))  # URI Too Long
            self._close_transport()
            return False
        except LimitRequestHeaders as e:
            self._send_error_response(431, str(e))  # Request Header Fields Too Large
            self._close_transport()
            return False
        except (InvalidChunkExtension, ParseError) as e:
            self._send_error_response(400, str(e))
            self._close_transport()
            return False
        except Exception as e:
            # Handle gunicorn_h1c exceptions (different class hierarchy)
            if self._handle_h1c_exception(e):
                return False
            raise

    def _is_buffer_full(self):
        """Check if internal buffer is full (HTTP/2 only)."""
        if self.reader and hasattr(self.reader, '_buffer'):
            return len(self.reader._buffer) > self._max_buffer_size
        return False

    def _pause_reading(self):
        """Pause reading from transport due to backpressure."""
        if not self._reading_paused and self.transport:
            self._reading_paused = True
            try:
                self.transport.pause_reading()
            except (AttributeError, RuntimeError):
                pass

    def _resume_reading(self):
        """Resume reading from transport."""
        if self._reading_paused and self.transport:
            self._reading_paused = False
            try:
                self.transport.resume_reading()
            except (AttributeError, RuntimeError):
                pass

    def _arm_keepalive_timer(self):
        """Arm keepalive timeout timer after response completion."""
        if self._keepalive_handle:
            self._keepalive_handle.cancel()
        keepalive_timeout = self.cfg.keepalive
        if keepalive_timeout > 0:
            self._keepalive_handle = self.worker.loop.call_later(
                keepalive_timeout, self._keepalive_timeout
            )

    def _cancel_keepalive_timer(self):
        """Cancel keepalive timer when new request arrives."""
        if self._keepalive_handle:
            self._keepalive_handle.cancel()
            self._keepalive_handle = None

    def _keepalive_timeout(self):
        """Called when keepalive timeout expires."""
        self._close_transport()

    def connection_lost(self, exc):
        """Called when the connection is lost or closed.

        Instead of immediately cancelling the task, we signal a disconnect
        event and send an http.disconnect message to the receive queue.
        This allows the ASGI app to clean up resources (like database
        connections) gracefully before the task is cancelled.

        See: https://github.com/benoitc/gunicorn/issues/3484
        """
        # Guard against multiple calls (idempotent)
        if self._closed:
            return

        self._closed = True
        self.worker.nr_conns -= 1

        # Cancel keepalive timer
        self._cancel_keepalive_timer()

        if self.reader:
            self.reader.feed_eof()

        # Signal EOF to WebSocket if active
        if self._websocket:
            self._websocket.feed_eof()

        # Signal disconnect to the app via the body receiver
        if self._body_receiver is not None:
            self._body_receiver.signal_disconnect()

        # Schedule task cancellation after grace period if task doesn't complete
        if self._task and not self._task.done():
            grace_period = getattr(self.cfg, 'asgi_disconnect_grace_period', 3)
            if grace_period > 0:
                self.worker.loop.call_later(
                    grace_period,
                    self._cancel_task_if_pending
                )
            else:
                # Grace period of 0 means cancel immediately
                self._task.cancel()

    def _cancel_task_if_pending(self):
        """Cancel the task if it's still pending after grace period."""
        if self._task and not self._task.done():
            self._task.cancel()

    def pause_writing(self):
        """Called by transport when write buffer exceeds high water mark."""
        if self._flow_control:
            self._flow_control.pause_writing()

    def resume_writing(self):
        """Called by transport when write buffer drains below low water mark."""
        if self._flow_control:
            self._flow_control.resume_writing()

    def _safe_write(self, data):
        """Write data to transport, handling connection errors gracefully.

        Catches exceptions that occur when the client has disconnected:
        - OSError with errno EPIPE, ECONNRESET, ENOTCONN
        - RuntimeError when transport is closing/closed
        - AttributeError when transport is None

        These are silently ignored since the client is already gone.
        """
        try:
            self.transport.write(data)
        except OSError as e:
            if e.errno not in (errno.EPIPE, errno.ECONNRESET, errno.ENOTCONN):
                self.log.exception("Socket error writing response.")
        except (RuntimeError, AttributeError):
            # Transport is closing/closed or None
            pass

    async def _handle_connection(self):
        """Main request handling loop using callback-based parser.

        Uses synchronous parsing in data_received(), avoiding the async
        overhead of pull-based parsing. The parser fires callbacks when
        headers and body data are available, and this loop waits on
        events rather than actively parsing.
        """
        try:
            peername = self.transport.get_extra_info('peername')
            sockname = self.transport.get_extra_info('sockname')

            # Check protocol type - use separate path for uWSGI
            protocol_type = getattr(self.cfg, 'protocol', 'http')
            if protocol_type == 'uwsgi':
                await self._handle_connection_uwsgi(peername, sockname)
                return

            while not self._closed:
                self.req_count += 1
                self._cancel_keepalive_timer()

                # Wait for headers to be parsed (callback sets the event and _current_request)
                # Don't clear if request already arrived (data_received ran before us)
                if not self._request_ready.is_set():
                    try:
                        await self._request_ready.wait()
                    except asyncio.CancelledError:
                        break

                if self._closed or self._current_request is None:
                    break

                request = self._current_request

                # If PROXY protocol provided a real client address, use it.
                effective_peer = self._effective_peername(peername)

                # Check for WebSocket upgrade
                if self._is_websocket_upgrade(request):
                    await self._handle_websocket(request, sockname, effective_peer)
                    break  # WebSocket takes over the connection

                # Handle HTTP request
                keepalive = await self._handle_http_request(
                    request, sockname, effective_peer
                )

                # Increment worker request count
                self.worker.nr += 1

                # Check max_requests
                if self.worker.nr >= self.worker.max_requests:
                    self.log.info("Autorestarting worker after current request.")
                    self.worker.alive = False
                    keepalive = False

                if not keepalive or not self.worker.alive:
                    break

                # Check connection limits for keepalive
                if not self.cfg.keepalive:
                    break

                # Refuse keepalive if the previous request body was not fully
                # framed: residual bytes in the transport stream would be parsed
                # as the start of the next request (smuggling).  Only _complete
                # signals a cleanly framed message; _closed is set on transport
                # disconnect *and* on receive timeout, neither of which means
                # the body finished framing.
                receiver = self._body_receiver
                if receiver is not None and not receiver._complete:
                    break

                # Resume reading if paused during body consumption
                self._resume_reading()

                # Reset parser for next request
                if self._callback_parser:
                    self._callback_parser.reset()

                # Clear request state for next iteration
                self._current_request = None
                self._body_receiver = None
                self._request_ready.clear()

                # Arm keepalive timer between requests
                self._arm_keepalive_timer()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log.exception("Error handling connection: %s", e)
        finally:
            self._close_transport()

    async def _handle_connection_uwsgi(self, peername, sockname):
        """Handle uWSGI protocol connections (legacy path)."""
        unreader = AsyncUnreader(self.reader)

        while not self._closed:
            self.req_count += 1

            try:
                request = await AsyncUWSGIRequest.parse(
                    self.cfg,
                    unreader,
                    peername,
                    self.req_count
                )
            except NoMoreData:
                break
            except UWSGIParseException as e:
                self.log.debug("uWSGI parse error: %s", e)
                break

            # Check for WebSocket upgrade
            if self._is_websocket_upgrade(request):
                await self._handle_websocket(request, sockname, peername)
                break

            # Handle HTTP request
            keepalive = await self._handle_http_request(
                request, sockname, peername
            )

            # Increment worker request count
            self.worker.nr += 1

            # Check max_requests
            if self.worker.nr >= self.worker.max_requests:
                self.log.info("Autorestarting worker after current request.")
                self.worker.alive = False
                keepalive = False

            if not keepalive or not self.worker.alive:
                break

            if not self.cfg.keepalive:
                break

            await request.drain_body()

    def _is_websocket_upgrade(self, request):
        """Check if request is a WebSocket upgrade.

        Per RFC 6455 Section 4.1, the opening handshake requires:
        - HTTP method MUST be GET
        - Upgrade header MUST be "websocket" (case-insensitive)
        - Connection header MUST contain "Upgrade"
        """
        # RFC 6455: The method of the request MUST be GET
        if request.method != "GET":
            return False

        upgrade = None
        connection = None
        for name, value in request.headers:
            if name == "UPGRADE":
                upgrade = value.lower()
            elif name == "CONNECTION":
                connection = value.lower()
        return upgrade == "websocket" and connection and "upgrade" in connection

    async def _handle_websocket(self, request, sockname, peername):
        """Handle WebSocket upgrade request."""
        from gunicorn.asgi.websocket import WebSocketProtocol

        # Stop callback parser - WebSocket uses its own data handling
        self._callback_parser = None

        scope = self._build_websocket_scope(request, sockname, peername)
        ws_protocol = WebSocketProtocol(
            self.transport, scope, self.app, self.log
        )

        # Store reference so data_received() forwards to WebSocket
        self._websocket = ws_protocol

        await ws_protocol.run()

    async def _handle_http_request(self, request, sockname, peername):
        """Handle a single HTTP request."""
        scope = self._build_http_scope(request, sockname, peername)
        response_started = False
        response_complete = False
        exc_to_raise = None
        use_chunked = False
        omits_body = False
        omits_body_warned = False

        # Reset response buffer for write batching
        self._response_buffer = None

        # Response tracking for access logging
        response_status = 500
        response_headers = []
        response_sent = 0

        # Use body receiver created in _on_headers_complete (receives data via callbacks)
        body_receiver = self._body_receiver

        async def send(message):
            nonlocal response_started, response_complete, exc_to_raise
            nonlocal response_status, response_headers, response_sent, use_chunked, omits_body
            nonlocal omits_body_warned

            # If client disconnected, silently ignore send attempts
            # This allows apps to finish cleanup without errors
            if self._closed:
                return

            msg_type = message["type"]

            if msg_type == "http.response.informational":
                # Handle informational responses (1xx) like 103 Early Hints
                info_status = message.get("status")
                info_headers = message.get("headers", [])
                self._send_informational(info_status, info_headers, request)
                return

            if msg_type == "http.response.start":
                if response_started:
                    exc_to_raise = RuntimeError("Response already started")
                    return
                response_started = True
                response_status = message["status"]
                response_headers = message.get("headers", [])

                # Check if Content-Length or Transfer-Encoding is present
                has_content_length = False
                has_transfer_encoding = False
                for name, _ in response_headers:
                    name_lower = name.lower() if isinstance(name, str) else name.lower()
                    if name_lower in (b"content-length", "content-length"):
                        has_content_length = True
                    elif name_lower in (b"transfer-encoding", "transfer-encoding"):
                        has_transfer_encoding = True
                        use_chunked = True  # Framework already set chunked encoding

                # No-body responses (HEAD/1xx/204/304) must not carry a body.
                # Always drop Transfer-Encoding (no chunked terminator without
                # a body); Content-Length is dropped only for statuses that
                # forbid it per RFC 9110 §6.4.2 (1xx, 204).  HEAD and 304 keep
                # an app-supplied Content-Length.
                omits_body = self._response_omits_body(request.method, response_status)
                if omits_body and (has_content_length or has_transfer_encoding):
                    response_headers = self._strip_body_framing_headers(
                        response_headers, response_status
                    )
                    if self._response_forbids_content_length(response_status):
                        has_content_length = False
