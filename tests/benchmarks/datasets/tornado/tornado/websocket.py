"""Implementation of the WebSocket protocol.

`WebSockets <http://dev.w3.org/html5/websockets/>`_ allow for bidirectional
communication between the browser and server. WebSockets are supported in the
current versions of all major browsers.

This module implements the final version of the WebSocket protocol as
defined in `RFC 6455 <http://tools.ietf.org/html/rfc6455>`_.

.. versionchanged:: 4.0
   Removed support for the draft 76 protocol version.
"""

import abc
import asyncio
import base64
import functools
import hashlib
import logging
import os
import struct
import sys
import warnings
import zlib
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import (
    Any,
    Optional,
    Protocol,
    Type,
    Union,
    cast,
)
from urllib.parse import urlparse

import tornado
from tornado import gen, httpclient, httputil, simple_httpclient
from tornado.concurrent import Future, future_set_result_unless_cancelled
from tornado.escape import native_str, to_unicode, utf8
from tornado.ioloop import IOLoop
from tornado.iostream import IOStream, StreamClosedError
from tornado.log import app_log, gen_log
from tornado.netutil import Resolver
from tornado.queues import Queue
from tornado.tcpclient import TCPClient
from tornado.util import _websocket_mask


# The zlib compressor types aren't actually exposed anywhere
# publicly, so declare protocols for the portions we use.
class _Compressor(Protocol):
    def compress(self, data: bytes) -> bytes:
        pass

    def flush(self, mode: int) -> bytes:
        pass


class _Decompressor(Protocol):
    @property
    def unconsumed_tail(self) -> bytes:
        pass

    def decompress(self, data: bytes, max_length: int) -> bytes:
        pass


class _WebSocketDelegate(Protocol):
    # The common base interface implemented by WebSocketHandler on
    # the server side and WebSocketClientConnection on the client
    # side.
    def on_ws_connection_close(
        self, close_code: int | None = None, close_reason: str | None = None
    ) -> None:
        pass

    def on_message(self, message: str | bytes) -> Optional["Awaitable[None]"]:
        pass

    def on_ping(self, data: bytes) -> None:
        pass

    def on_pong(self, data: bytes) -> None:
        pass

    def log_exception(
        self,
        typ: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass


_default_max_message_size = 10 * 1024 * 1024

# log to "gen_log" but suppress duplicate log messages
de_dupe_gen_log = functools.lru_cache(gen_log.log)


class WebSocketError(Exception):
    pass


class WebSocketClosedError(WebSocketError):
    """Raised by operations on a closed connection.

    .. versionadded:: 3.2
    """

    pass


class _DecompressTooLargeError(Exception):
    pass


class _WebSocketParams:
    def __init__(
        self,
        ping_interval: float | None = None,
        ping_timeout: float | None = None,
        max_message_size: int = _default_max_message_size,
        compression_options: dict[str, Any] | None = None,
    ) -> None:
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.max_message_size = max_message_size
        self.compression_options = compression_options


class WebSocketHandler(tornado.web.RequestHandler):
    """Subclass this class to create a basic WebSocket handler.

    Override `on_message` to handle incoming messages, and use
    `write_message` to send messages to the client. You can also
    override `open` and `on_close` to handle opened and closed
    connections.

    Custom upgrade response headers can be sent by overriding
    `~tornado.web.RequestHandler.set_default_headers` or
    `~tornado.web.RequestHandler.prepare`.

    See http://dev.w3.org/html5/websockets/ for details on the
    JavaScript interface.  The protocol is specified at
    http://tools.ietf.org/html/rfc6455.

    Here is an example WebSocket handler that echos back all received messages
    back to the client:

    .. testcode::

      class EchoWebSocket(tornado.websocket.WebSocketHandler):
          def open(self):
              print("WebSocket opened")

          def on_message(self, message):
              self.write_message(u"You said: " + message)

          def on_close(self):
              print("WebSocket closed")

    WebSockets are not standard HTTP connections. The "handshake" is
    HTTP, but after the handshake, the protocol is
    message-based. Consequently, most of the Tornado HTTP facilities
    are not available in handlers of this type. The only communication
    methods available to you are `write_message()`, `ping()`, and
    `close()`. Likewise, your request handler class should implement
    `open()` method rather than ``get()`` or ``post()``.

    If you map the handler above to ``/websocket`` in your application, you can
    invoke it in JavaScript with::

      var ws = new WebSocket("ws://localhost:8888/websocket");
      ws.onopen = function() {
         ws.send("Hello, world");
      };
      ws.onmessage = function (evt) {
         alert(evt.data);
      };

    This script pops up an alert box that says "You said: Hello, world".

    Web browsers allow any site to open a websocket connection to any other,
    instead of using the same-origin policy that governs other network
    access from JavaScript.  This can be surprising and is a potential
    security hole, so since Tornado 4.0 `WebSocketHandler` requires
    applications that wish to receive cross-origin websockets to opt in
    by overriding the `~WebSocketHandler.check_origin` method (see that
    method's docs for details).  Failure to do so is the most likely
    cause of 403 errors when making a websocket connection.

    When using a secure websocket connection (``wss://``) with a self-signed
    certificate, the connection from a browser may fail because it wants
    to show the "accept this certificate" dialog but has nowhere to show it.
    You must first visit a regular HTML page using the same certificate
    to accept it before the websocket connection will succeed.

    If the application setting ``websocket_ping_interval`` has a non-zero
    value, a ping will be sent periodically, and the connection will be
    closed if a response is not received before the ``websocket_ping_timeout``.
    Both settings are in seconds; floating point values are allowed.
    The default timeout is equal to the interval.

    Messages larger than the ``websocket_max_message_size`` application setting
    (default 10MiB) will not be accepted.

    .. versionchanged:: 4.5
       Added ``websocket_ping_interval``, ``websocket_ping_timeout``, and
       ``websocket_max_message_size``.
    """

    def __init__(
        self,
        application: tornado.web.Application,
        request: httputil.HTTPServerRequest,
        **kwargs: Any,
    ) -> None:
        super().__init__(application, request, **kwargs)
        self.ws_connection: WebSocketProtocol | None = None
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._on_close_called = False

    async def get(self, *args: Any, **kwargs: Any) -> None:
        self.open_args = args
        self.open_kwargs = kwargs

        # Upgrade header should be present and should be equal to WebSocket
        if self.request.headers.get("Upgrade", "").lower() != "websocket":
            self.set_status(400)
            log_msg = 'Can "Upgrade" only to "WebSocket".'
            self.finish(log_msg)
            gen_log.debug(log_msg)
            return

        # Connection header should be upgrade.
        # Some proxy servers/load balancers
        # might mess with it.
        headers = self.request.headers
        connection = map(
            lambda s: s.strip().lower(), headers.get("Connection", "").split(",")
        )
        if "upgrade" not in connection:
            self.set_status(400)
            log_msg = '"Connection" must be "Upgrade".'
            self.finish(log_msg)
            gen_log.debug(log_msg)
            return

        # Handle WebSocket Origin naming convention differences
        # The difference between version 8 and 13 is that in 8 the
        # client sends a "Sec-Websocket-Origin" header and in 13 it's
        # simply "Origin".
        if "Origin" in self.request.headers:
            origin = self.request.headers.get("Origin")
        else:
            origin = self.request.headers.get("Sec-Websocket-Origin", None)

        # If there was an origin header, check to make sure it matches
        # according to check_origin. When the origin is None, we assume it
        # did not come from a browser and that it can be passed on.
        if origin is not None and not self.check_origin(origin):
            self.set_status(403)
            log_msg = "Cross origin websockets not allowed"
            self.finish(log_msg)
            gen_log.debug(log_msg)
            return

        self.ws_connection = self.get_websocket_protocol()
        if self.ws_connection:
            await self.ws_connection.accept_connection(self)
        else:
            self.set_status(426, "Upgrade Required")
            self.set_header("Sec-WebSocket-Version", "7, 8, 13")

    @property
    def ping_interval(self) -> float | None:
        """The interval for sending websocket pings.

        If this is non-zero, the websocket will send a ping every
        ping_interval seconds.
        The client will respond with a "pong". The connection can be configured
        to timeout on late pong delivery using ``websocket_ping_timeout``.

        Set ``websocket_ping_interval = 0`` to disable pings.

        Default: ``0``
        """
        return self.settings.get("websocket_ping_interval", None)

    @property
    def ping_timeout(self) -> float | None:
        """Timeout if no pong is received in this many seconds.

        To be used in combination with ``websocket_ping_interval > 0``.
        If a ping response (a "pong") is not received within
        ``websocket_ping_timeout`` seconds, then the websocket connection
        will be closed.

        This can help to clean up clients which have disconnected without
        cleanly closing the websocket connection.

        Note, the ping timeout cannot be longer than the ping interval.

        Set ``websocket_ping_timeout = 0`` to disable the ping timeout.

        Default: equal to the ``ping_interval``.

        .. versionchanged:: 6.5.0
           Default changed from the max of 3 pings or 30 seconds.
           The ping timeout can no longer be configured longer than the
           ping interval.
        """
        return self.settings.get("websocket_ping_timeout", None)

    @property
    def max_message_size(self) -> int:
        """Maximum allowed message size.

        If the remote peer sends a message larger than this, the connection
        will be closed.

        Default is 10MiB.
        """
        return self.settings.get(
            "websocket_max_message_size", _default_max_message_size
        )

    def write_message(
        self, message: bytes | str | dict[str, Any], binary: bool = False
    ) -> "Future[None]":
        """Sends the given message to the client of this Web Socket.

        The message may be either a string or a dict (which will be
        encoded as json).  If the ``binary`` argument is false, the
        message will be sent as utf8; in binary mode any byte string
        is allowed.

        If the connection is already closed, raises `WebSocketClosedError`.
        Returns a `.Future` which can be used for flow control.

        .. versionchanged:: 3.2
           `WebSocketClosedError` was added (previously a closed connection
           would raise an `AttributeError`)

        .. versionchanged:: 4.3
           Returns a `.Future` which can be used for flow control.

        .. versionchanged:: 5.0
           Consistently raises `WebSocketClosedError`. Previously could
           sometimes raise `.StreamClosedError`.
        """
        if self.ws_connection is None or self.ws_connection.is_closing():
            raise WebSocketClosedError()
        if isinstance(message, dict):
            message = tornado.escape.json_encode(message)
        return self.ws_connection.write_message(message, binary=binary)

    def select_subprotocol(self, subprotocols: list[str]) -> str | None:
        """Override to implement subprotocol negotiation.

        ``subprotocols`` is a list of strings identifying the
        subprotocols proposed by the client.  This method may be
        overridden to return one of those strings to select it, or
        ``None`` to not select a subprotocol.

        Failure to select a subprotocol does not automatically abort
        the connection, although clients may close the connection if
        none of their proposed subprotocols was selected.

        The list may be empty, in which case this method must return
        None. This method is always called exactly once even if no
        subprotocols were proposed so that the handler can be advised
        of this fact.

        .. versionchanged:: 5.1

           Previously, this method was called with a list containing
           an empty string instead of an empty list if no subprotocols
           were proposed by the client.
        """
        return None

    @property
    def selected_subprotocol(self) -> str | None:
        """The subprotocol returned by `select_subprotocol`.

        .. versionadded:: 5.1
        """
        assert self.ws_connection is not None
        return self.ws_connection.selected_subprotocol

    def get_compression_options(self) -> dict[str, Any] | None:
        """Override to return compression options for the connection.

        If this method returns None (the default), compression will
        be disabled.  If it returns a dict (even an empty one), it
        will be enabled.  The contents of the dict may be used to
        control the following compression options:

        ``compression_level`` specifies the compression level.

        ``mem_level`` specifies the amount of memory used for the internal compression state.

         These parameters are documented in detail here:
         https://docs.python.org/3.13/library/zlib.html#zlib.compressobj

        .. versionadded:: 4.1

        .. versionchanged:: 4.5

           Added ``compression_level`` and ``mem_level``.
        """
        # TODO: Add wbits option.
        return None

    def _open(self, *args: str, **kwargs: str) -> Awaitable[None] | None:
        pass

    open: Callable[..., Awaitable[None] | None] = _open
    """Invoked when a new WebSocket is opened.

    The arguments to `open` are extracted from the `tornado.web.URLSpec`
    regular expression, just like the arguments to
    `tornado.web.RequestHandler.get`.

    `open` may be a coroutine. `on_message` will not be called until
    `open` has returned.

    .. versionchanged:: 5.1

        ``open`` may be a coroutine.
    """

    def on_message(self, message: str | bytes) -> Awaitable[None] | None:
        """Handle incoming messages on the WebSocket

        This method must be overridden.

        .. versionchanged:: 4.5

           ``on_message`` can be a coroutine.
        """
        raise NotImplementedError

    def ping(self, data: str | bytes = b"") -> None:
        """Send ping frame to the remote end.

        The data argument allows a small amount of data (up to 125
        bytes) to be sent as a part of the ping message. Note that not
        all websocket implementations expose this data to
        applications.

        Consider using the ``websocket_ping_interval`` application
        setting instead of sending pings manually.

        .. versionchanged:: 5.1

           The data argument is now optional.

        """
        data = utf8(data)
        if self.ws_connection is None or self.ws_connection.is_closing():
            raise WebSocketClosedError()
        self.ws_connection.write_ping(data)

    def on_pong(self, data: bytes) -> None:
        """Invoked when the response to a ping frame is received."""
        pass

    def on_ping(self, data: bytes) -> None:
        """Invoked when the a ping frame is received."""
        pass

    def on_close(self) -> None:
        """Invoked when the WebSocket is closed.

        If the connection was closed cleanly and a status code or reason
        phrase was supplied, these values will be available as the attributes
        ``self.close_code`` and ``self.close_reason``.

        .. versionchanged:: 4.0

           Added ``close_code`` and ``close_reason`` attributes.
        """
        pass

    def close(self, code: int | None = None, reason: str | None = None) -> None:
        """Closes this Web Socket.

        Once the close handshake is successful the socket will be closed.

        ``code`` may be a numeric status code, taken from the values
        defined in `RFC 6455 section 7.4.1
        <https://tools.ietf.org/html/rfc6455#section-7.4.1>`_.
        ``reason`` may be a textual message about why the connection is
        closing.  These values are made available to the client, but are
        not otherwise interpreted by the websocket protocol.

        .. versionchanged:: 4.0

           Added the ``code`` and ``reason`` arguments.
        """
        if self.ws_connection:
            self.ws_connection.close(code, reason)
            self.ws_connection = None

    def check_origin(self, origin: str) -> bool:
        """Override to enable support for allowing alternate origins.

        The ``origin`` argument is the value of the ``Origin`` HTTP
        header, the url responsible for initiating this request.  This
        method is not called for clients that do not send this header;
        such requests are always allowed (because all browsers that
        implement WebSockets support this header, and non-browser
        clients do not have the same cross-site security concerns).

        Should return ``True`` to accept the request or ``False`` to
        reject it. By default, rejects all requests with an origin on
        a host other than this one.

        This is a security protection against cross site scripting attacks on
        browsers, since WebSockets are allowed to bypass the usual same-origin
        policies and don't use CORS headers.

        .. warning::

           This is an important security measure; don't disable it
           without understanding the security implications. In
           particular, if your authentication is cookie-based, you
           must either restrict the origins allowed by
           ``check_origin()`` or implement your own XSRF-like
           protection for websocket connections. See `these
           <https://www.christian-schneider.net/CrossSiteWebSocketHijacking.html>`_
           `articles
           <https://devcenter.heroku.com/articles/websocket-security>`_
           for more.

        To accept all cross-origin traffic (which was the default prior to
        Tornado 4.0), simply override this method to always return ``True``::

            def check_origin(self, origin):
                return True

        To allow connections from any subdomain of your site, you might
        do something like::

            def check_origin(self, origin):
                parsed_origin = urllib.parse.urlparse(origin)
                return parsed_origin.netloc.endswith(".mydomain.com")

        .. versionadded:: 4.0

        """
        parsed_origin = urlparse(origin)
        origin = parsed_origin.netloc
        origin = origin.lower()

        host = self.request.headers.get("Host")

        # Check to see that origin matches host directly, including ports
        return origin == host

    def set_nodelay(self, value: bool) -> None:
        """Set the no-delay flag for this stream.

        By default, small messages may be delayed and/or combined to minimize
        the number of packets sent.  This can sometimes cause 200-500ms delays
        due to the interaction between Nagle's algorithm and TCP delayed
        ACKs.  To reduce this delay (at the expense of possibly increasing
        bandwidth usage), call ``self.set_nodelay(True)`` once the websocket
        connection is established.

        See `.BaseIOStream.set_nodelay` for additional details.

        .. versionadded:: 3.1
        """
        assert self.ws_connection is not None
        self.ws_connection.set_nodelay(value)

    def on_connection_close(self) -> None:
        if self.ws_connection:
            self.ws_connection.on_connection_close()
            self.ws_connection = None
        if not self._on_close_called:
            self._on_close_called = True
            self.on_close()
            self._break_cycles()

    def on_ws_connection_close(
        self, close_code: int | None = None, close_reason: str | None = None
    ) -> None:
        self.close_code = close_code
        self.close_reason = close_reason
        self.on_connection_close()

    def _break_cycles(self) -> None:
        # WebSocketHandlers call finish() early, but we don't want to
        # break up reference cycles (which makes it impossible to call
        # self.render_string) until after we've really closed the
        # connection (if it was established in the first place,
        # indicated by status code 101).
        if self.get_status() != 101 or self._on_close_called:
            super()._break_cycles()

    def get_websocket_protocol(self) -> Optional["WebSocketProtocol"]:
        websocket_version = self.request.headers.get("Sec-WebSocket-Version")
        if websocket_version in ("7", "8", "13"):
            params = _WebSocketParams(
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
                max_message_size=self.max_message_size,
                compression_options=self.get_compression_options(),
            )
            return WebSocketProtocol13(self, False, params)
        return None

    def _detach_stream(self) -> IOStream:
        # disable non-WS methods
        for method in [
            "write",
            "redirect",
            "set_header",
            "set_cookie",
            "set_status",
            "flush",
            "finish",
        ]:
            setattr(self, method, _raise_not_supported_for_websockets)
        return self.detach()


def _raise_not_supported_for_websockets(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("Method not supported for Web Sockets")


class WebSocketProtocol(abc.ABC):
    """Base class for WebSocket protocol versions."""

    def __init__(self, handler: "_WebSocketDelegate") -> None:
        self.handler = handler
        self.stream: IOStream | None = None
        self.client_terminated = False
        self.server_terminated = False

    def _run_callback(
        self, callback: Callable, *args: Any, **kwargs: Any
    ) -> "Optional[Future[Any]]":
        """Runs the given callback with exception handling.

        If the callback is a coroutine, returns its Future. On error, aborts the
        websocket connection and returns None.
        """
        try:
            result = callback(*args, **kwargs)
        except Exception:
            self.handler.log_exception(*sys.exc_info())
            self._abort()
            return None
        else:
            if result is not None:
                result = gen.convert_yielded(result)
                assert self.stream is not None
                self.stream.io_loop.add_future(result, lambda f: f.result())
            return result

    def on_connection_close(self) -> None:
        self._abort()

    def _abort(self) -> None:
        """Instantly aborts the WebSocket connection by closing the socket"""
        self.client_terminated = True
        self.server_terminated = True
        if self.stream is not None:
            self.stream.close()  # forcibly tear down the connection
        self.close()  # let the subclass cleanup

    @abc.abstractmethod
    def close(self, code: int | None = None, reason: str | None = None) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def is_closing(self) -> bool:
        raise NotImplementedError()

    @abc.abstractmethod
    async def accept_connection(self, handler: WebSocketHandler) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def write_message(
        self, message: str | bytes | dict[str, Any], binary: bool = False
    ) -> "Future[None]":
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def selected_subprotocol(self) -> str | None:
        raise NotImplementedError()

    @abc.abstractmethod
    def write_ping(self, data: bytes) -> None:
        raise NotImplementedError()

    # The entry points below are used by WebSocketClientConnection,
    # which was introduced after we only supported a single version of
    # WebSocketProtocol. The WebSocketProtocol/WebSocketProtocol13
    # boundary is currently pretty ad-hoc.
    @abc.abstractmethod
    def _process_server_headers(
        self, key: str | bytes, headers: httputil.HTTPHeaders
    ) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def start_pinging(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def _receive_frame_loop(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def set_nodelay(self, x: bool) -> None:
        raise NotImplementedError()


class _PerMessageDeflateCompressor:
    def __init__(
        self,
        persistent: bool,
        max_wbits: int | None,
        compression_options: dict[str, Any] | None = None,
    ) -> None:
        if max_wbits is None:
            max_wbits = zlib.MAX_WBITS
        # There is no symbolic constant for the minimum wbits value.
        if not (8 <= max_wbits <= zlib.MAX_WBITS):
            raise ValueError(
                "Invalid max_wbits value %r; allowed range 8-%d",
                max_wbits,
                zlib.MAX_WBITS,
            )
        self._max_wbits = max_wbits

        if (
            compression_options is None
            or "compression_level" not in compression_options
        ):
            self._compression_level = tornado.web.GZipContentEncoding.GZIP_LEVEL
        else:
            self._compression_level = compression_options["compression_level"]

        if compression_options is None or "mem_level" not in compression_options:
            self._mem_level = 8
        else:
            self._mem_level = compression_options["mem_level"]

        if persistent:
            self._compressor: _Compressor | None = self._create_compressor()
        else:
            self._compressor = None

    def _create_compressor(self) -> "_Compressor":
        return zlib.compressobj(
            self._compression_level, zlib.DEFLATED, -self._max_wbits, self._mem_level
        )

    def compress(self, data: bytes) -> bytes:
        compressor = self._compressor or self._create_compressor()
        data = compressor.compress(data) + compressor.flush(zlib.Z_SYNC_FLUSH)
        assert data.endswith(b"\x00\x00\xff\xff")
        return data[:-4]


class _PerMessageDeflateDecompressor:
    def __init__(
        self,
        persistent: bool,
        max_wbits: int | None,
        max_message_size: int,
        compression_options: dict[str, Any] | None = None,
    ) -> None:
        self._max_message_size = max_message_size
        if max_wbits is None:
            max_wbits = zlib.MAX_WBITS
        if not (8 <= max_wbits <= zlib.MAX_WBITS):
            raise ValueError(
                "Invalid max_wbits value %r; allowed range 8-%d",
                max_wbits,
                zlib.MAX_WBITS,
            )
        self._max_wbits = max_wbits
        if persistent:
            self._decompressor: _Decompressor | None = self._create_decompressor()
        else:
            self._decompressor = None

    def _create_decompressor(self) -> "_Decompressor":
        return zlib.decompressobj(-self._max_wbits)

    def decompress(self, data: bytes) -> bytes:
        decompressor = self._decompressor or self._create_decompressor()
        result = decompressor.decompress(
            data + b"\x00\x00\xff\xff", self._max_message_size
        )
        if decompressor.unconsumed_tail:
            raise _DecompressTooLargeError()
        return result


class WebSocketProtocol13(WebSocketProtocol):
    """Implementation of the WebSocket protocol from RFC 6455.

    This class supports versions 7 and 8 of the protocol in addition to the
    final version 13.
    """

    # Bit masks for the first byte of a frame.
    FIN = 0x80
    RSV1 = 0x40
    RSV2 = 0x20
    RSV3 = 0x10
    RSV_MASK = RSV1 | RSV2 | RSV3
    OPCODE_MASK = 0x0F

    stream: IOStream

    def __init__(
        self,
        handler: "_WebSocketDelegate",
        mask_outgoing: bool,
        params: _WebSocketParams,
    ) -> None:
        WebSocketProtocol.__init__(self, handler)
        self.mask_outgoing = mask_outgoing
        self.params = params
        self._final_frame = False
        self._frame_opcode = None
        self._masked_frame = None
        self._frame_mask: bytes | None = None
        self._frame_length = None
        self._fragmented_message_buffer: bytearray | None = None
        self._fragmented_message_opcode = None
        self._waiting: object = None
        self._compression_options = params.compression_options
        self._decompressor: _PerMessageDeflateDecompressor | None = None
        self._compressor: _PerMessageDeflateCompressor | None = None
        self._frame_compressed: bool | None = None
        # The total uncompressed size of all messages received or sent.
        # Unicode messages are encoded to utf8.
        # Only for testing; subject to change.
        self._message_bytes_in = 0
        self._message_bytes_out = 0
        # The total size of all packets received or sent.  Includes
        # the effect of compression, frame overhead, and control frames.
        self._wire_bytes_in = 0
        self._wire_bytes_out = 0
        self._received_pong: bool = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._ping_coroutine: asyncio.Task | None = None

    # Use a property for this to satisfy the abc.
    @property
    def selected_subprotocol(self) -> str | None:
        return self._selected_subprotocol

    @selected_subprotocol.setter
    def selected_subprotocol(self, value: str | None) -> None:
        self._selected_subprotocol = value

    async def accept_connection(self, handler: WebSocketHandler) -> None:
        try:
            self._handle_websocket_headers(handler)
        except ValueError:
            handler.set_status(400)
            log_msg = "Missing/Invalid WebSocket headers"
            handler.finish(log_msg)
            gen_log.debug(log_msg)
            return

        try:
            await self._accept_connection(handler)
        except asyncio.CancelledError:
            self._abort()
            return
        except ValueError:
            gen_log.debug("Malformed WebSocket request received", exc_info=True)
            self._abort()
            return

    def _handle_websocket_headers(self, handler: WebSocketHandler) -> None:
        """Verifies all invariant- and required headers

        If a header is missing or have an incorrect value ValueError will be
        raised
        """
        fields = ("Host", "Sec-Websocket-Key", "Sec-Websocket-Version")
        if not all(map(lambda f: handler.request.headers.get(f), fields)):
            raise ValueError("Missing/Invalid WebSocket headers")

    @staticmethod
    def compute_accept_value(key: str | bytes) -> str:
        """Computes the value for the Sec-WebSocket-Accept header,
        given the value for Sec-WebSocket-Key.
        """
        sha1 = hashlib.sha1()
        sha1.update(utf8(key))
        sha1.update(b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11")  # Magic value
        return native_str(base64.b64encode(sha1.digest()))

    def _challenge_response(self, handler: WebSocketHandler) -> str:
        return WebSocketProtocol13.compute_accept_value(
            cast(str, handler.request.headers.get("Sec-Websocket-Key"))
        )

    async def _accept_connection(self, handler: WebSocketHandler) -> None:
        subprotocol_header = handler.request.headers.get("Sec-WebSocket-Protocol")
        if subprotocol_header:
            subprotocols = [s.strip() for s in subprotocol_header.split(",")]
        else:
            subprotocols = []
        self.selected_subprotocol = handler.select_subprotocol(subprotocols)
        if self.selected_subprotocol:
            assert self.selected_subprotocol in subprotocols
            handler.set_header("Sec-WebSocket-Protocol", self.selected_subprotocol)

        extensions = self._parse_extensions_header(handler.request.headers)
        for ext in extensions:
            if ext[0] == "permessage-deflate" and self._compression_options is not None:
                # TODO: negotiate parameters if compression_options
                # specifies limits.
                self._create_compressors("server", ext[1], self._compression_options)
                if (
                    "client_max_window_bits" in ext[1]
                    and ext[1]["client_max_window_bits"] is None
                ):
                    # Don't echo an offered client_max_window_bits
                    # parameter with no value.
                    del ext[1]["client_max_window_bits"]
                handler.set_header(
                    "Sec-WebSocket-Extensions",
                    httputil._encode_header("permessage-deflate", ext[1]),
                )
                break

        handler.clear_header("Content-Type")
        handler.set_status(101)
        handler.set_header("Upgrade", "websocket")
        handler.set_header("Connection", "Upgrade")
        handler.set_header("Sec-WebSocket-Accept", self._challenge_response(handler))
        handler.finish()

        self.stream = handler._detach_stream()

        self.start_pinging()
        try:
            open_result = handler.open(*handler.open_args, **handler.open_kwargs)
            if open_result is not None:
                await open_result
        except Exception:
            handler.log_exception(*sys.exc_info())
            self._abort()
            return

        await self._receive_frame_loop()

    def _parse_extensions_header(
        self, headers: httputil.HTTPHeaders
    ) -> list[tuple[str, dict[str, str]]]:
        extensions = headers.get("Sec-WebSocket-Extensions", "")
        if extensions:
            return [httputil._parse_header(e.strip()) for e in extensions.split(",")]
        return []

    def _process_server_headers(
        self, key: str | bytes, headers: httputil.HTTPHeaders
    ) -> None:
        """Process the headers sent by the server to this client connection.

        'key' is the websocket handshake challenge/response key.
        """
        assert headers["Upgrade"].lower() == "websocket"
        assert headers["Connection"].lower() == "upgrade"
        accept = self.compute_accept_value(key)
        assert headers["Sec-Websocket-Accept"] == accept

        extensions = self._parse_extensions_header(headers)
        for ext in extensions:
            if ext[0] == "permessage-deflate" and self._compression_options is not None:
                self._create_compressors("client", ext[1])
            else:
                raise ValueError("unsupported extension %r", ext)

        self.selected_subprotocol = headers.get("Sec-WebSocket-Protocol", None)

    def _get_compressor_options(
