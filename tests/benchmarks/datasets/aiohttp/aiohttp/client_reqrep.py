import asyncio
import codecs
import contextlib
import functools
import io
import re
import sys
import traceback
import warnings
from collections.abc import Mapping
from hashlib import md5, sha1, sha256
from http.cookies import Morsel, SimpleCookie
from types import MappingProxyType, TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    NamedTuple,
    Optional,
    Tuple,
    Type,
    Union,
)

import attr
from multidict import CIMultiDict, CIMultiDictProxy, MultiDict, MultiDictProxy
from yarl import URL

from . import hdrs, helpers, http, multipart, payload
from ._cookie_helpers import (
    parse_cookie_header,
    parse_set_cookie_headers,
    preserve_morsel_with_coded_value,
)
from .abc import AbstractStreamWriter
from .client_exceptions import (
    ClientConnectionError,
    ClientOSError,
    ClientResponseError,
    ContentTypeError,
    InvalidURL,
    ServerFingerprintMismatch,
)
from .compression_utils import HAS_BROTLI, HAS_ZSTD
from .formdata import FormData
from .helpers import (
    _SENTINEL,
    BaseTimerContext,
    BasicAuth,
    HeadersMixin,
    TimerNoop,
    noop,
    reify,
    sentinel,
    set_exception,
    set_result,
)
from .http import (
    SERVER_SOFTWARE,
    HttpVersion,
    HttpVersion10,
    HttpVersion11,
    StreamWriter,
)
from .streams import StreamReader
from .typedefs import (
    DEFAULT_JSON_DECODER,
    JSONDecoder,
    LooseCookies,
    LooseHeaders,
    Query,
    RawHeaders,
)

if TYPE_CHECKING:
    import ssl
    from ssl import SSLContext
else:
    try:
        import ssl
        from ssl import SSLContext
    except ImportError:  # pragma: no cover
        ssl = None  # type: ignore[assignment]
        SSLContext = object  # type: ignore[misc,assignment]


__all__ = ("ClientRequest", "ClientResponse", "RequestInfo", "Fingerprint")


if TYPE_CHECKING:
    from .client import ClientSession
    from .connector import Connection
    from .tracing import Trace


_CONNECTION_CLOSED_EXCEPTION = ClientConnectionError("Connection closed")
_CONTAINS_CONTROL_CHAR_RE = re.compile(r"[^-!#$%&'*+.^_`|~0-9a-zA-Z]")
json_re = re.compile(r"^application/(?:[\w.+-]+?\+)?json")


def _gen_default_accept_encoding() -> str:
    encodings = [
        "gzip",
        "deflate",
    ]
    if HAS_BROTLI:
        encodings.append("br")
    if HAS_ZSTD:
        encodings.append("zstd")
    return ", ".join(encodings)


@attr.s(auto_attribs=True, frozen=True, slots=True)
class ContentDisposition:
    type: Optional[str]
    parameters: "MappingProxyType[str, str]"
    filename: Optional[str]


class _RequestInfo(NamedTuple):
    url: URL
    method: str
    headers: "CIMultiDictProxy[str]"
    real_url: URL


class RequestInfo(_RequestInfo):

    def __new__(
        cls,
        url: URL,
        method: str,
        headers: "CIMultiDictProxy[str]",
        real_url: Union[URL, _SENTINEL] = sentinel,
    ) -> "RequestInfo":
        """Create a new RequestInfo instance.

        For backwards compatibility, the real_url parameter is optional.
        """
        return tuple.__new__(
            cls, (url, method, headers, url if real_url is sentinel else real_url)
        )


class Fingerprint:
    HASHFUNC_BY_DIGESTLEN = {
        16: md5,
        20: sha1,
        32: sha256,
    }

    def __init__(self, fingerprint: bytes) -> None:
        digestlen = len(fingerprint)
        hashfunc = self.HASHFUNC_BY_DIGESTLEN.get(digestlen)
        if not hashfunc:
            raise ValueError("fingerprint has invalid length")
        elif hashfunc is md5 or hashfunc is sha1:
            raise ValueError("md5 and sha1 are insecure and not supported. Use sha256.")
        self._hashfunc = hashfunc
        self._fingerprint = fingerprint

    @property
    def fingerprint(self) -> bytes:
        return self._fingerprint

    def check(self, transport: asyncio.Transport) -> None:
        if not transport.get_extra_info("sslcontext"):
            return
        sslobj = transport.get_extra_info("ssl_object")
        cert = sslobj.getpeercert(binary_form=True)
        got = self._hashfunc(cert).digest()
        if got != self._fingerprint:
            host, port, *_ = transport.get_extra_info("peername")
            raise ServerFingerprintMismatch(self._fingerprint, got, host, port)


if ssl is not None:
    SSL_ALLOWED_TYPES = (ssl.SSLContext, bool, Fingerprint, type(None))
else:  # pragma: no cover
    SSL_ALLOWED_TYPES = (bool, type(None))


def _merge_ssl_params(
    ssl: Union["SSLContext", bool, Fingerprint],
    verify_ssl: Optional[bool],
    ssl_context: Optional["SSLContext"],
    fingerprint: Optional[bytes],
) -> Union["SSLContext", bool, Fingerprint]:
    if ssl is None:
        ssl = True  # Double check for backwards compatibility
    if verify_ssl is not None and not verify_ssl:
        warnings.warn(
            "verify_ssl is deprecated, use ssl=False instead",
            DeprecationWarning,
            stacklevel=3,
        )
        if ssl is not True:
            raise ValueError(
                "verify_ssl, ssl_context, fingerprint and ssl "
                "parameters are mutually exclusive"
            )
        else:
            ssl = False
    if ssl_context is not None:
        warnings.warn(
            "ssl_context is deprecated, use ssl=context instead",
            DeprecationWarning,
            stacklevel=3,
        )
        if ssl is not True:
            raise ValueError(
                "verify_ssl, ssl_context, fingerprint and ssl "
                "parameters are mutually exclusive"
            )
        else:
            ssl = ssl_context
    if fingerprint is not None:
        warnings.warn(
            "fingerprint is deprecated, use ssl=Fingerprint(fingerprint) instead",
            DeprecationWarning,
            stacklevel=3,
        )
        if ssl is not True:
            raise ValueError(
                "verify_ssl, ssl_context, fingerprint and ssl "
                "parameters are mutually exclusive"
            )
        else:
            ssl = Fingerprint(fingerprint)
    if not isinstance(ssl, SSL_ALLOWED_TYPES):
        raise TypeError(
            "ssl should be SSLContext, bool, Fingerprint or None, "
            "got {!r} instead.".format(ssl)
        )
    return ssl


_SSL_SCHEMES = frozenset(("https", "wss"))


# ConnectionKey is a NamedTuple because it is used as a key in a dict
# and a set in the connector. Since a NamedTuple is a tuple it uses
# the fast native tuple __hash__ and __eq__ implementation in CPython.
class ConnectionKey(NamedTuple):
    # the key should contain an information about used proxy / TLS
    # to prevent reusing wrong connections from a pool
    host: str
    port: Optional[int]
    is_ssl: bool
    ssl: Union[SSLContext, bool, Fingerprint]
    proxy: Optional[URL]
    proxy_auth: Optional[BasicAuth]
    proxy_headers_hash: Optional[int]  # hash(CIMultiDict)


def _is_expected_content_type(
    response_content_type: str, expected_content_type: str
) -> bool:
    if expected_content_type == "application/json":
        return json_re.match(response_content_type) is not None
    return expected_content_type in response_content_type


def _warn_if_unclosed_payload(payload: payload.Payload, stacklevel: int = 2) -> None:
    """Warn if the payload is not closed.

    Callers must check that the body is a Payload before calling this method.

    Args:
        payload: The payload to check
        stacklevel: Stack level for the warning (default 2 for direct callers)
    """
    if not payload.autoclose and not payload.consumed:
        warnings.warn(
            "The previous request body contains unclosed resources. "
            "Use await request.update_body() instead of setting request.body "
            "directly to properly close resources and avoid leaks.",
            ResourceWarning,
            stacklevel=stacklevel,
        )


class ClientResponse(HeadersMixin):

    # Some of these attributes are None when created,
    # but will be set by the start() method.
    # As the end user will likely never see the None values, we cheat the types below.
    # from the Status-Line of the response
    version: Optional[HttpVersion] = None  # HTTP-Version
    status: int = None  # type: ignore[assignment] # Status-Code
    reason: Optional[str] = None  # Reason-Phrase

    content: StreamReader = None  # type: ignore[assignment] # Payload stream
    _body: Optional[bytes] = None
    _headers: CIMultiDictProxy[str] = None  # type: ignore[assignment]
    _history: Tuple["ClientResponse", ...] = ()
    _raw_headers: RawHeaders = None  # type: ignore[assignment]

    _connection: Optional["Connection"] = None  # current connection
    _cookies: Optional[SimpleCookie] = None
    _raw_cookie_headers: Optional[Tuple[str, ...]] = None
    _continue: Optional["asyncio.Future[bool]"] = None
    _source_traceback: Optional[traceback.StackSummary] = None
    _session: Optional["ClientSession"] = None
    # set up by ClientRequest after ClientResponse object creation
    # post-init stage allows to not change ctor signature
    _closed = True  # to allow __del__ for non-initialized properly response
    _released = False
    _in_context = False

    _resolve_charset: Callable[["ClientResponse", bytes], str] = lambda *_: "utf-8"

    __writer: Optional["asyncio.Task[None]"] = None

    def __init__(
        self,
        method: str,
        url: URL,
        *,
        writer: "Optional[asyncio.Task[None]]",
        continue100: Optional["asyncio.Future[bool]"],
        timer: BaseTimerContext,
        request_info: RequestInfo,
        traces: List["Trace"],
        loop: asyncio.AbstractEventLoop,
        session: "ClientSession",
    ) -> None:
        # URL forbids subclasses, so a simple type check is enough.
        assert type(url) is URL

        self.method = method

        self._real_url = url
        self._url = url.with_fragment(None) if url.raw_fragment else url
        if writer is not None:
            self._writer = writer
        if continue100 is not None:
            self._continue = continue100
        self._request_info = request_info
        self._timer = timer if timer is not None else TimerNoop()
        self._cache: Dict[str, Any] = {}
        self._traces = traces
        self._loop = loop
        # Save reference to _resolve_charset, so that get_encoding() will still
        # work after the response has finished reading the body.
        # TODO: Fix session=None in tests (see ClientRequest.__init__).
        if session is not None:
            # store a reference to session #1985
            self._session = session
            self._resolve_charset = session._resolve_charset
        if loop.get_debug():
            self._source_traceback = traceback.extract_stack(sys._getframe(1))

    def __reset_writer(self, _: object = None) -> None:
        self.__writer = None

    @property
    def _writer(self) -> Optional["asyncio.Task[None]"]:
        """The writer task for streaming data.

        _writer is only provided for backwards compatibility
        for subclasses that may need to access it.
        """
        return self.__writer

    @_writer.setter
    def _writer(self, writer: Optional["asyncio.Task[None]"]) -> None:
        """Set the writer task for streaming data."""
        if self.__writer is not None:
            self.__writer.remove_done_callback(self.__reset_writer)
        self.__writer = writer
        if writer is None:
            return
        if writer.done():
            # The writer is already done, so we can clear it immediately.
            self.__writer = None
        else:
            writer.add_done_callback(self.__reset_writer)

    @property
    def cookies(self) -> SimpleCookie:
        if self._cookies is None:
            if self._raw_cookie_headers is not None:
                # Parse cookies for response.cookies (SimpleCookie for backward compatibility)
                cookies = SimpleCookie()
                # Use parse_set_cookie_headers for more lenient parsing that handles
                # malformed cookies better than SimpleCookie.load
                cookies.update(parse_set_cookie_headers(self._raw_cookie_headers))
                self._cookies = cookies
            else:
                self._cookies = SimpleCookie()
        return self._cookies

    @cookies.setter
    def cookies(self, cookies: SimpleCookie) -> None:
        self._cookies = cookies
        # Generate raw cookie headers from the SimpleCookie
        if cookies:
            self._raw_cookie_headers = tuple(
                morsel.OutputString() for morsel in cookies.values()
            )
        else:
            self._raw_cookie_headers = None

    @reify
    def url(self) -> URL:
        return self._url

    @reify
    def url_obj(self) -> URL:
        warnings.warn("Deprecated, use .url #1654", DeprecationWarning, stacklevel=2)
        return self._url

    @reify
    def real_url(self) -> URL:
        return self._real_url

    @reify
    def host(self) -> str:
        assert self._url.host is not None
        return self._url.host

    @reify
    def headers(self) -> "CIMultiDictProxy[str]":
        return self._headers

    @reify
    def raw_headers(self) -> RawHeaders:
        return self._raw_headers

    @reify
    def request_info(self) -> RequestInfo:
        return self._request_info

    @reify
    def content_disposition(self) -> Optional[ContentDisposition]:
        raw = self._headers.get(hdrs.CONTENT_DISPOSITION)
        if raw is None:
            return None
        disposition_type, params_dct = multipart.parse_content_disposition(raw)
        params = MappingProxyType(params_dct)
        filename = multipart.content_disposition_filename(params)
        return ContentDisposition(disposition_type, params, filename)

    def __del__(self, _warnings: Any = warnings) -> None:
        if self._closed:
            return

        if self._connection is not None:
            self._connection.release()
            self._cleanup_writer()

            if self._loop.get_debug():
                kwargs = {"source": self}
                _warnings.warn(f"Unclosed response {self!r}", ResourceWarning, **kwargs)
                context = {"client_response": self, "message": "Unclosed response"}
                if self._source_traceback:
                    context["source_traceback"] = self._source_traceback
                self._loop.call_exception_handler(context)

    def __repr__(self) -> str:
        out = io.StringIO()
        ascii_encodable_url = str(self.url)
        if self.reason:
            ascii_encodable_reason = self.reason.encode(
                "ascii", "backslashreplace"
            ).decode("ascii")
        else:
            ascii_encodable_reason = "None"
        print(
            "<ClientResponse({}) [{} {}]>".format(
                ascii_encodable_url, self.status, ascii_encodable_reason
            ),
            file=out,
        )
        print(self.headers, file=out)
        return out.getvalue()

    @property
    def connection(self) -> Optional["Connection"]:
        return self._connection

    @reify
    def history(self) -> Tuple["ClientResponse", ...]:
        """A sequence of of responses, if redirects occurred."""
        return self._history

    @reify
    def links(self) -> "MultiDictProxy[MultiDictProxy[Union[str, URL]]]":
        links_str = ", ".join(self.headers.getall("link", []))

        if not links_str:
            return MultiDictProxy(MultiDict())

        links: MultiDict[MultiDictProxy[Union[str, URL]]] = MultiDict()

        for val in re.split(r",(?=\s*<)", links_str):
            match = re.match(r"\s*<(.*)>(.*)", val)
            if match is None:  # pragma: no cover
                # the check exists to suppress mypy error
                continue
            url, params_str = match.groups()
            params = params_str.split(";")[1:]

            link: MultiDict[Union[str, URL]] = MultiDict()

            for param in params:
                match = re.match(r"^\s*(\S*)\s*=\s*(['\"]?)(.*?)(\2)\s*$", param, re.M)
                if match is None:  # pragma: no cover
                    # the check exists to suppress mypy error
                    continue
                key, _, value, _ = match.groups()

                link.add(key, value)

            key = link.get("rel", url)

            link.add("url", self.url.join(URL(url)))

            links.add(str(key), MultiDictProxy(link))

        return MultiDictProxy(links)

    async def start(self, connection: "Connection") -> "ClientResponse":
        """Start response processing."""
        self._closed = False
        self._protocol = connection.protocol
        self._connection = connection

        with self._timer:
            while True:
                # read response
                try:
                    protocol = self._protocol
                    message, payload = await protocol.read()  # type: ignore[union-attr]
                except http.HttpProcessingError as exc:
                    raise ClientResponseError(
                        self.request_info,
                        self.history,
                        status=exc.code,
                        message=exc.message,
                        headers=exc.headers,
                    ) from exc

                if message.code < 100 or message.code > 199 or message.code == 101:
                    break

                if self._continue is not None:
                    set_result(self._continue, True)
                    self._continue = None

        # payload eof handler
        payload.on_eof(self._response_eof)

        # response status
        self.version = message.version
        self.status = message.code
        self.reason = message.reason

        # headers
        self._headers = message.headers  # type is CIMultiDictProxy
        self._raw_headers = message.raw_headers  # type is Tuple[bytes, bytes]

        # payload
        self.content = payload

        # cookies
        if cookie_hdrs := self.headers.getall(hdrs.SET_COOKIE, ()):
            # Store raw cookie headers for CookieJar
            self._raw_cookie_headers = tuple(cookie_hdrs)
        return self

    def _response_eof(self) -> None:
        if self._closed:
            return

        # protocol could be None because connection could be detached
        protocol = self._connection and self._connection.protocol
        if protocol is not None and protocol.upgraded:
            return

        self._closed = True
        self._cleanup_writer()
        self._release_connection()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if not self._released:
            self._notify_content()

        self._closed = True
        if self._loop is None or self._loop.is_closed():
            return

        self._cleanup_writer()
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def release(self) -> Any:
        if not self._released:
            self._notify_content()

        self._closed = True

        self._cleanup_writer()
        self._release_connection()
        return noop()

    @property
    def ok(self) -> bool:
        """Returns ``True`` if ``status`` is less than ``400``, ``False`` if not.

        This is **not** a check for ``200 OK`` but a check that the response
        status is under 400.
        """
        return 400 > self.status

    def raise_for_status(self) -> None:
        if not self.ok:
            # reason should always be not None for a started response
            assert self.reason is not None

            # If we're in a context we can rely on __aexit__() to release as the
            # exception propagates.
            if not self._in_context:
                self.release()

            raise ClientResponseError(
                self.request_info,
                self.history,
                status=self.status,
                message=self.reason,
                headers=self.headers,
            )

    def _release_connection(self) -> None:
        if self._connection is not None:
            if self.__writer is None:
                self._connection.release()
                self._connection = None
            else:
                self.__writer.add_done_callback(lambda f: self._release_connection())

    async def _wait_released(self) -> None:
        if self.__writer is not None:
            try:
                await self.__writer
            except asyncio.CancelledError:
                if (
                    sys.version_info >= (3, 11)
                    and (task := asyncio.current_task())
                    and task.cancelling()
                ):
                    raise
        self._release_connection()

    def _cleanup_writer(self) -> None:
        if self.__writer is not None:
            self.__writer.cancel()
        self._session = None

    def _notify_content(self) -> None:
        content = self.content
        if content and content.exception() is None:
            set_exception(content, _CONNECTION_CLOSED_EXCEPTION)
        self._released = True

    async def wait_for_close(self) -> None:
        if self.__writer is not None:
            try:
                await self.__writer
            except asyncio.CancelledError:
                if (
                    sys.version_info >= (3, 11)
                    and (task := asyncio.current_task())
                    and task.cancelling()
                ):
                    raise
        self.release()

    async def read(self) -> bytes:
        """Read response payload."""
        if self._body is None:
            try:
                self._body = await self.content.read()
                for trace in self._traces:
                    await trace.send_response_chunk_received(
                        self.method, self.url, self._body
                    )
            except BaseException:
                self.close()
                raise
        elif self._released:  # Response explicitly released
            raise ClientConnectionError("Connection closed")

        protocol = self._connection and self._connection.protocol
        if protocol is None or not protocol.upgraded:
            await self._wait_released()  # Underlying connection released
        return self._body

    def get_encoding(self) -> str:
        ctype = self.headers.get(hdrs.CONTENT_TYPE, "").lower()
        mimetype = helpers.parse_mimetype(ctype)

        encoding = mimetype.parameters.get("charset")
        if encoding:
            with contextlib.suppress(LookupError, ValueError):
                return codecs.lookup(encoding).name

        if mimetype.type == "application" and (
            mimetype.subtype == "json" or mimetype.subtype == "rdap"
        ):
            # RFC 7159 states that the default encoding is UTF-8.
            # RFC 7483 defines application/rdap+json
            return "utf-8"

        if self._body is None:
            raise RuntimeError(
                "Cannot compute fallback encoding of a not yet read body"
            )

        return self._resolve_charset(self, self._body)

    async def text(self, encoding: Optional[str] = None, errors: str = "strict") -> str:
        """Read response payload and decode."""
        if self._body is None:
            await self.read()

        if encoding is None:
            encoding = self.get_encoding()

        return self._body.decode(encoding, errors=errors)  # type: ignore[union-attr]

    async def json(
        self,
        *,
        encoding: Optional[str] = None,
        loads: JSONDecoder = DEFAULT_JSON_DECODER,
        content_type: Optional[str] = "application/json",
    ) -> Any:
        """Read and decodes JSON response."""
        if self._body is None:
            await self.read()

        if content_type:
            ctype = self.headers.get(hdrs.CONTENT_TYPE, "").lower()
            if not _is_expected_content_type(ctype, content_type):
                raise ContentTypeError(
                    self.request_info,
                    self.history,
                    status=self.status,
                    message=(
                        "Attempt to decode JSON with unexpected mimetype: %s" % ctype
                    ),
                    headers=self.headers,
                )

        stripped = self._body.strip()  # type: ignore[union-attr]
        if not stripped:
            return None

        if encoding is None:
            encoding = self.get_encoding()

        return loads(stripped.decode(encoding))

    async def __aenter__(self) -> "ClientResponse":
        self._in_context = True
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self._in_context = False
        # similar to _RequestContextManager, we do not need to check
        # for exceptions, response object can close connection
        # if state is broken
        self.release()
        await self.wait_for_close()


class ClientRequest:
    GET_METHODS = {
        hdrs.METH_GET,
        hdrs.METH_HEAD,
        hdrs.METH_OPTIONS,
        hdrs.METH_TRACE,
    }
    POST_METHODS = {hdrs.METH_PATCH, hdrs.METH_POST, hdrs.METH_PUT}
    ALL_METHODS = GET_METHODS.union(POST_METHODS).union({hdrs.METH_DELETE})

    DEFAULT_HEADERS = {
        hdrs.ACCEPT: "*/*",
        hdrs.ACCEPT_ENCODING: _gen_default_accept_encoding(),
    }

    # Type of body depends on PAYLOAD_REGISTRY, which is dynamic.
    _body: Union[None, payload.Payload] = None
    auth = None
    response = None

    __writer: Optional["asyncio.Task[None]"] = None  # async task for streaming data

    # These class defaults help create_autospec() work correctly.
    # If autospec is improved in future, maybe these can be removed.
    url = URL()
    method = "GET"

    _continue = None  # waiter future for '100 Continue' response

    _skip_auto_headers: Optional["CIMultiDict[None]"] = None

    # N.B.
    # Adding __del__ method with self._writer closing doesn't make sense
    # because _writer is instance method, thus it keeps a reference to self.
    # Until writer has finished finalizer will not be called.

    def __init__(
        self,
        method: str,
        url: URL,
        *,
        params: Query = None,
        headers: Optional[LooseHeaders] = None,
        skip_auto_headers: Optional[Iterable[str]] = None,
        data: Any = None,
        cookies: Optional[LooseCookies] = None,
        auth: Optional[BasicAuth] = None,
        version: http.HttpVersion = http.HttpVersion11,
        compress: Union[str, bool, None] = None,
        chunked: Optional[bool] = None,
        expect100: bool = False,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        response_class: Optional[Type["ClientResponse"]] = None,
        proxy: Optional[URL] = None,
        proxy_auth: Optional[BasicAuth] = None,
        timer: Optional[BaseTimerContext] = None,
        session: Optional["ClientSession"] = None,
        ssl: Union[SSLContext, bool, Fingerprint] = True,
        proxy_headers: Optional[LooseHeaders] = None,
        traces: Optional[List["Trace"]] = None,
        trust_env: bool = False,
        server_hostname: Optional[str] = None,
    ):
        if loop is None:
            loop = asyncio.get_event_loop()
        if match := _CONTAINS_CONTROL_CHAR_RE.search(method):
            raise ValueError(
                f"Method cannot contain non-token characters {method!r} "
                f"(found at least {match.group()!r})"
            )
        # URL forbids subclasses, so a simple type check is enough.
        assert type(url) is URL, url
        if proxy is not None:
            assert type(proxy) is URL, proxy
        # FIXME: session is None in tests only, need to fix tests
        # assert session is not None
        if TYPE_CHECKING:
            assert session is not None
        self._session = session
        if params:
            url = url.extend_query(params)
        self.original_url = url
        self.url = url.with_fragment(None) if url.raw_fragment else url
        self.method = method.upper()
        self.chunked = chunked
        self.compress = compress
        self.loop = loop
        self.length = None
        if response_class is None:
            real_response_class = ClientResponse
        else:
            real_response_class = response_class
        self.response_class: Type[ClientResponse] = real_response_class
        self._timer = timer if timer is not None else TimerNoop()
        self._ssl = ssl if ssl is not None else True
        self.server_hostname = server_hostname

        if loop.get_debug():
            self._source_traceback = traceback.extract_stack(sys._getframe(1))

        self.update_version(version)
        self.update_host(url)
        self.update_headers(headers)
        self.update_auto_headers(skip_auto_headers)
        self.update_cookies(cookies)
        self.update_content_encoding(data)
        self.update_auth(auth, trust_env)
        self.update_proxy(proxy, proxy_auth, proxy_headers)

        self.update_body_from_data(data)
        if data is not None or self.method not in self.GET_METHODS:
            self.update_transfer_encoding()
        self.update_expect_continue(expect100)
        self._traces = [] if traces is None else traces

    def __reset_writer(self, _: object = None) -> None:
        self.__writer = None

    def _get_content_length(self) -> Optional[int]:
        """Extract and validate Content-Length header value.

        Returns parsed Content-Length value or None if not set.
        Raises ValueError if header exists but cannot be parsed as an integer.
        """
        if hdrs.CONTENT_LENGTH not in self.headers:
            return None

        content_length_hdr = self.headers[hdrs.CONTENT_LENGTH]
        try:
            return int(content_length_hdr)
        except ValueError:
            raise ValueError(
                f"Invalid Content-Length header: {content_length_hdr}"
            ) from None

    @property
    def skip_auto_headers(self) -> CIMultiDict[None]:
        return self._skip_auto_headers or CIMultiDict()

    @property
    def _writer(self) -> Optional["asyncio.Task[None]"]:
        return self.__writer

    @_writer.setter
    def _writer(self, writer: "asyncio.Task[None]") -> None:
        if self.__writer is not None:
            self.__writer.remove_done_callback(self.__reset_writer)
        self.__writer = writer
        writer.add_done_callback(self.__reset_writer)

    def is_ssl(self) -> bool:
        return self.url.scheme in _SSL_SCHEMES

    @property
    def ssl(self) -> Union["SSLContext", bool, Fingerprint]:
        return self._ssl

    @property
    def connection_key(self) -> ConnectionKey:
        if proxy_headers := self.proxy_headers:
            h: Optional[int] = hash(tuple(proxy_headers.items()))
        else:
            h = None
        url = self.url
        return tuple.__new__(
            ConnectionKey,
            (
                url.raw_host or "",
                url.port,
                url.scheme in _SSL_SCHEMES,
                self._ssl,
                self.proxy,
                self.proxy_auth,
                h,
            ),
        )

    @property
    def host(self) -> str:
        ret = self.url.raw_host
        assert ret is not None
        return ret

    @property
    def port(self) -> Optional[int]:
        return self.url.port

    @property
    def body(self) -> Union[payload.Payload, Literal[b""]]:
        """Request body."""
        # empty body is represented as bytes for backwards compatibility
        return self._body or b""

    @body.setter
    def body(self, value: Any) -> None:
        """Set request body with warning for non-autoclose payloads.

        WARNING: This setter must be called from within an event loop and is not
        thread-safe. Setting body outside of an event loop may raise RuntimeError
        when closing file-based payloads.

        DEPRECATED: Direct assignment to body is deprecated and will be removed
        in a future version. Use await update_body() instead for proper resource
        management.
        """
        # Close existing payload if present
        if self._body is not None:
            # Warn if the payload needs manual closing
