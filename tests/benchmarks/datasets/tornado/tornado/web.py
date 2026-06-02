#
# Copyright 2009 Facebook
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""``tornado.web`` provides a simple web framework with asynchronous
features that allow it to scale to large numbers of open connections,
making it ideal for `long polling
<http://en.wikipedia.org/wiki/Push_technology#Long_polling>`_.

Here is a simple "Hello, world" example app:

.. testcode::

    import asyncio
    import tornado

    class MainHandler(tornado.web.RequestHandler):
        def get(self):
            self.write("Hello, world")

    async def main():
        application = tornado.web.Application([
            (r"/", MainHandler),
        ])
        application.listen(8888)
        await asyncio.Event().wait()

    if __name__ == "__main__":
        asyncio.run(main())

See the :doc:`guide` for additional information.

Thread-safety notes
-------------------

In general, methods on `RequestHandler` and elsewhere in Tornado are
not thread-safe. In particular, methods such as
`~RequestHandler.write()`, `~RequestHandler.finish()`, and
`~RequestHandler.flush()` must only be called from the main thread. If
you use multiple threads it is important to use `.IOLoop.add_callback`
to transfer control back to the main thread before finishing the
request, or to limit your use of other threads to
`.IOLoop.run_in_executor` and ensure that your callbacks running in
the executor do not refer to Tornado objects.

"""

import base64
import binascii
import datetime
import email.utils
import functools
import gzip
import hashlib
import hmac
import http.cookies
import mimetypes
import numbers
import os.path
import re
import socket
import sys
import threading
import time
import traceback
import types
import urllib.parse
import warnings
from inspect import isclass
from io import BytesIO
from urllib.parse import urlencode

import tornado
from tornado import escape, gen, httputil, iostream, locale, template
from tornado.concurrent import Future, future_set_result_unless_cancelled
from tornado.escape import _unicode, utf8
from tornado.httpserver import HTTPServer
from tornado.log import access_log, app_log, gen_log
from tornado.routing import (
    AnyMatches,
    DefaultHostMatches,
    HostMatches,
    ReversibleRouter,
    ReversibleRuleRouter,
    Rule,
    URLSpec,
    _RuleList,
)
from tornado.util import ObjectDict, _websocket_mask, unicode_type

url = URLSpec

from collections.abc import Awaitable, Callable, Generator, Iterable
from types import TracebackType
from typing import (
    Any,
    Optional,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

# The following types are accepted by RequestHandler.set_header
# and related methods.
_HeaderTypes = Union[bytes, unicode_type, int, numbers.Integral, datetime.datetime]

_CookieSecretTypes = Union[str, bytes, dict[int, str], dict[int, bytes]]


MIN_SUPPORTED_SIGNED_VALUE_VERSION = 1
"""The oldest signed value version supported by this version of Tornado.

Signed values older than this version cannot be decoded.

.. versionadded:: 3.2.1
"""

MAX_SUPPORTED_SIGNED_VALUE_VERSION = 2
"""The newest signed value version supported by this version of Tornado.

Signed values newer than this version cannot be decoded.

.. versionadded:: 3.2.1
"""

DEFAULT_SIGNED_VALUE_VERSION = 2
"""The signed value version produced by `.RequestHandler.create_signed_value`.

May be overridden by passing a ``version`` keyword argument.

.. versionadded:: 3.2.1
"""

DEFAULT_SIGNED_VALUE_MIN_VERSION = 1
"""The oldest signed value accepted by `.RequestHandler.get_signed_cookie`.

May be overridden by passing a ``min_version`` keyword argument.

.. versionadded:: 3.2.1
"""


class _ArgDefaultMarker:
    pass


_ARG_DEFAULT = _ArgDefaultMarker()


class RequestHandler:
    """Base class for HTTP request handlers.

    Subclasses must define at least one of the methods defined in the
    "Entry points" section below.

    Applications should not construct `RequestHandler` objects
    directly and subclasses should not override ``__init__`` (override
    `~RequestHandler.initialize` instead).

    """

    SUPPORTED_METHODS: tuple[str, ...] = (
        "GET",
        "HEAD",
        "POST",
        "DELETE",
        "PATCH",
        "PUT",
        "OPTIONS",
    )

    _template_loaders: dict[str, template.BaseLoader] = {}
    _template_loader_lock = threading.Lock()
    _remove_control_chars_regex = re.compile(r"[\x00-\x08\x0e-\x1f]")

    _stream_request_body = False

    # Will be set in _execute.
    _transforms: list["OutputTransform"]
    path_args: list[str]
    path_kwargs: dict[str, str]

    def __init__(
        self,
        application: "Application",
        request: httputil.HTTPServerRequest,
        **kwargs: Any,
    ) -> None:
        super().__init__()

        self.application = application
        self.request = request
        self._headers_written = False
        self._finished = False
        self._auto_finish = True
        self._prepared_future = None
        self.ui = ObjectDict(
            (n, self._ui_method(m)) for n, m in application.ui_methods.items()
        )
        # UIModules are available as both `modules` and `_tt_modules` in the
        # template namespace.  Historically only `modules` was available
        # but could be clobbered by user additions to the namespace.
        # The template {% module %} directive looks in `_tt_modules` to avoid
        # possible conflicts.
        self.ui["_tt_modules"] = _UIModuleNamespace(self, application.ui_modules)
        self.ui["modules"] = self.ui["_tt_modules"]
        self.clear()
        assert self.request.connection is not None
        # TODO: need to add set_close_callback to HTTPConnection interface
        self.request.connection.set_close_callback(  # type: ignore
            self.on_connection_close
        )
        self.initialize(**kwargs)  # type: ignore

    def _initialize(self) -> None:
        pass

    initialize: Callable[..., None] = _initialize
    """Hook for subclass initialization. Called for each request.

    A dictionary passed as the third argument of a ``URLSpec`` will be
    supplied as keyword arguments to ``initialize()``.

    Example::

        class ProfileHandler(RequestHandler):
            def initialize(self, database):
                self.database = database

            def get(self, username):
                ...

        app = Application([
            (r'/user/(.*)', ProfileHandler, dict(database=database)),
            ])
    """

    @property
    def settings(self) -> dict[str, Any]:
        """An alias for `self.application.settings <Application.settings>`."""
        return self.application.settings

    def _unimplemented_method(self, *args: str, **kwargs: str) -> None:
        raise HTTPError(405)

    head: Callable[..., Awaitable[None] | None] = _unimplemented_method
    get: Callable[..., Awaitable[None] | None] = _unimplemented_method
    post: Callable[..., Awaitable[None] | None] = _unimplemented_method
    delete: Callable[..., Awaitable[None] | None] = _unimplemented_method
    patch: Callable[..., Awaitable[None] | None] = _unimplemented_method
    put: Callable[..., Awaitable[None] | None] = _unimplemented_method
    options: Callable[..., Awaitable[None] | None] = _unimplemented_method

    def prepare(self) -> Awaitable[None] | None:
        """Called at the beginning of a request before  `get`/`post`/etc.

        Override this method to perform common initialization regardless
        of the request method. There is no guarantee that ``prepare`` will
        be called if an error occurs that is handled by the framework.

        Asynchronous support: Use ``async def`` or decorate this method with
        `.gen.coroutine` to make it asynchronous.
        If this method returns an  ``Awaitable`` execution will not proceed
        until the ``Awaitable`` is done.

        .. versionadded:: 3.1
           Asynchronous support.
        """
        pass

    def on_finish(self) -> None:
        """Called after the end of a request.

        Override this method to perform cleanup, logging, etc. This method is primarily intended as
        a counterpart to `prepare`. However, there are a few error cases where ``on_finish`` may be
        called when ``prepare`` has not. (These are considered bugs and may be fixed in the future,
        but for now you may need to check to see if the initialization work done in ``prepare`` has
        occurred)

        ``on_finish`` may not produce any output, as it is called after the response has been sent
        to the client.
        """
        pass

    def on_connection_close(self) -> None:
        """Called in async handlers if the client closed the connection.

        Override this to clean up resources associated with
        long-lived connections.  Note that this method is called only if
        the connection was closed during asynchronous processing; if you
        need to do cleanup after every request override `on_finish`
        instead.

        Proxies may keep a connection open for a time (perhaps
        indefinitely) after the client has gone away, so this method
        may not be called promptly after the end user closes their
        connection.
        """
        if _has_stream_request_body(self.__class__):
            if not self.request._body_future.done():
                self.request._body_future.set_exception(iostream.StreamClosedError())
                self.request._body_future.exception()

    def clear(self) -> None:
        """Resets all headers and content for this response."""
        self._headers = httputil.HTTPHeaders(
            {
                "Server": "TornadoServer/%s" % tornado.version,
                "Content-Type": "text/html; charset=UTF-8",
                "Date": httputil.format_timestamp(time.time()),
            }
        )
        self.set_default_headers()
        self._write_buffer: list[bytes] = []
        self._status_code = 200
        self._reason = httputil.responses[200]

    def set_default_headers(self) -> None:
        """Override this to set HTTP headers at the beginning of the request.

        For example, this is the place to set a custom ``Server`` header.
        Note that setting such headers in the normal flow of request
        processing may not do what you want, since headers may be reset
        during error handling.
        """
        pass

    def set_status(self, status_code: int, reason: str | None = None) -> None:
        """Sets the status code for our response.

        :arg int status_code: Response status code.
        :arg str reason: Human-readable reason phrase describing the status
            code (for example, the "Not Found" in ``HTTP/1.1 404 Not Found``).
            Normally determined automatically from `http.client.responses`; this
            argument should only be used if you need to use a non-standard
            status code.

        .. versionchanged:: 5.0

           No longer validates that the response code is in
           `http.client.responses`.
        """
        self._status_code = status_code
        if reason is not None:
            if "<" in reason or not httputil._ABNF.reason_phrase.fullmatch(reason):
                # Logically this would be better as an exception, but this method
                # is called on error-handling paths that would need some refactoring
                # to tolerate internal errors cleanly.
                #
                # The check for "<" is a defense-in-depth against XSS attacks (we also
                # escape the reason when rendering error pages).
                reason = "Unknown"
            self._reason = escape.native_str(reason)
        else:
            self._reason = httputil.responses.get(status_code, "Unknown")

    def get_status(self) -> int:
        """Returns the status code for our response."""
        return self._status_code

    def set_header(self, name: str, value: _HeaderTypes) -> None:
        """Sets the given response header name and value.

        All header values are converted to strings (`datetime` objects
        are formatted according to the HTTP specification for the
        ``Date`` header).

        """
        self._headers[name] = self._convert_header_value(value)

    def add_header(self, name: str, value: _HeaderTypes) -> None:
        """Adds the given response header and value.

        Unlike `set_header`, `add_header` may be called multiple times
        to return multiple values for the same header.
        """
        self._headers.add(name, self._convert_header_value(value))

    def clear_header(self, name: str) -> None:
        """Clears an outgoing header, undoing a previous `set_header` call.

        Note that this method does not apply to multi-valued headers
        set by `add_header`.
        """
        if name in self._headers:
            del self._headers[name]

    # https://www.rfc-editor.org/rfc/rfc9110#name-field-values
    _VALID_HEADER_CHARS = re.compile(r"[\x09\x20-\x7e\x80-\xff]*")

    def _convert_header_value(self, value: _HeaderTypes) -> str:
        # Convert the input value to a str. This type check is a bit
        # subtle: The bytes case only executes on python 3, and the
        # unicode case only executes on python 2, because the other
        # cases are covered by the first match for str.
        if isinstance(value, str):
            retval = value
        elif isinstance(value, bytes):
            # Non-ascii characters in headers are not well supported,
            # but if you pass bytes, use latin1 so they pass through as-is.
            retval = value.decode("latin1")
        elif isinstance(value, numbers.Integral):
            # return immediately since we know the converted value will be safe
            return str(value)
        elif isinstance(value, datetime.datetime):
            return httputil.format_timestamp(value)
        else:
            raise TypeError("Unsupported header value %r" % value)
        # If \n is allowed into the header, it is possible to inject
        # additional headers or split the request.
        if RequestHandler._VALID_HEADER_CHARS.fullmatch(retval) is None:
            raise ValueError("Unsafe header value %r", retval)
        return retval

    @overload
    def get_argument(self, name: str, default: str, strip: bool = True) -> str:
        pass

    @overload
    def get_argument(
        self, name: str, default: _ArgDefaultMarker = _ARG_DEFAULT, strip: bool = True
    ) -> str:
        pass

    @overload
    def get_argument(self, name: str, default: None, strip: bool = True) -> str | None:
        pass

    def get_argument(
        self,
        name: str,
        default: None | str | _ArgDefaultMarker = _ARG_DEFAULT,
        strip: bool = True,
    ) -> str | None:
        """Returns the value of the argument with the given name.

        If default is not provided, the argument is considered to be
        required, and we raise a `MissingArgumentError` if it is missing.

        If the argument appears in the request more than once, we return the
        last value.

        This method searches both the query and body arguments.
        """
        return self._get_argument(name, default, self.request.arguments, strip)

    def get_arguments(self, name: str, strip: bool = True) -> list[str]:
        """Returns a list of the arguments with the given name.

        If the argument is not present, returns an empty list.

        This method searches both the query and body arguments.
        """

        # Make sure `get_arguments` isn't accidentally being called with a
        # positional argument that's assumed to be a default (like in
        # `get_argument`.)
        assert isinstance(strip, bool)

        return self._get_arguments(name, self.request.arguments, strip)

    @overload
    def get_body_argument(self, name: str, default: str, strip: bool = True) -> str:
        pass

    @overload
    def get_body_argument(
        self, name: str, default: _ArgDefaultMarker = _ARG_DEFAULT, strip: bool = True
    ) -> str:
        pass

    @overload
    def get_body_argument(
        self, name: str, default: None, strip: bool = True
    ) -> str | None:
        pass

    def get_body_argument(
        self,
        name: str,
        default: None | str | _ArgDefaultMarker = _ARG_DEFAULT,
        strip: bool = True,
    ) -> str | None:
        """Returns the value of the argument with the given name
        from the request body.

        If default is not provided, the argument is considered to be
        required, and we raise a `MissingArgumentError` if it is missing.

        If the argument appears in the url more than once, we return the
        last value.

        .. versionadded:: 3.2
        """
        return self._get_argument(name, default, self.request.body_arguments, strip)

    def get_body_arguments(self, name: str, strip: bool = True) -> list[str]:
        """Returns a list of the body arguments with the given name.

        If the argument is not present, returns an empty list.

        .. versionadded:: 3.2
        """
        return self._get_arguments(name, self.request.body_arguments, strip)

    @overload
    def get_query_argument(self, name: str, default: str, strip: bool = True) -> str:
        pass

    @overload
    def get_query_argument(
        self, name: str, default: _ArgDefaultMarker = _ARG_DEFAULT, strip: bool = True
    ) -> str:
        pass

    @overload
    def get_query_argument(
        self, name: str, default: None, strip: bool = True
    ) -> str | None:
        pass

    def get_query_argument(
        self,
        name: str,
        default: None | str | _ArgDefaultMarker = _ARG_DEFAULT,
        strip: bool = True,
    ) -> str | None:
        """Returns the value of the argument with the given name
        from the request query string.

        If default is not provided, the argument is considered to be
        required, and we raise a `MissingArgumentError` if it is missing.

        If the argument appears in the url more than once, we return the
        last value.

        .. versionadded:: 3.2
        """
        return self._get_argument(name, default, self.request.query_arguments, strip)

    def get_query_arguments(self, name: str, strip: bool = True) -> list[str]:
        """Returns a list of the query arguments with the given name.

        If the argument is not present, returns an empty list.

        .. versionadded:: 3.2
        """
        return self._get_arguments(name, self.request.query_arguments, strip)

    def _get_argument(
        self,
        name: str,
        default: None | str | _ArgDefaultMarker,
        source: dict[str, list[bytes]],
        strip: bool = True,
    ) -> str | None:
        args = self._get_arguments(name, source, strip=strip)
        if not args:
            if isinstance(default, _ArgDefaultMarker):
                raise MissingArgumentError(name)
            return default
        return args[-1]

    def _get_arguments(
        self, name: str, source: dict[str, list[bytes]], strip: bool = True
    ) -> list[str]:
        values = []
        for v in source.get(name, []):
            s = self.decode_argument(v, name=name)
            if isinstance(s, unicode_type):
                # Get rid of any weird control chars (unless decoding gave
                # us bytes, in which case leave it alone)
                s = RequestHandler._remove_control_chars_regex.sub(" ", s)
            if strip:
                s = s.strip()
            values.append(s)
        return values

    def decode_argument(self, value: bytes, name: str | None = None) -> str:
        """Decodes an argument from the request.

        The argument has been percent-decoded and is now a byte string.
        By default, this method decodes the argument as utf-8 and returns
        a unicode string, but this may be overridden in subclasses.

        This method is used as a filter for both `get_argument()` and for
        values extracted from the url and passed to `get()`/`post()`/etc.

        The name of the argument is provided if known, but may be None
        (e.g. for unnamed groups in the url regex).
        """
        try:
            return _unicode(value)
        except UnicodeDecodeError:
            raise HTTPError(
                400, "Invalid unicode in {}: {!r}".format(name or "url", value[:40])
            )

    @property
    def cookies(self) -> dict[str, http.cookies.Morsel]:
        """An alias for
        `self.request.cookies <.httputil.HTTPServerRequest.cookies>`."""
        return self.request.cookies

    @overload
    def get_cookie(self, name: str, default: str) -> str:
        pass

    @overload
    def get_cookie(self, name: str, default: None = None) -> str | None:
        pass

    def get_cookie(self, name: str, default: str | None = None) -> str | None:
        """Returns the value of the request cookie with the given name.

        If the named cookie is not present, returns ``default``.

        This method only returns cookies that were present in the request.
        It does not see the outgoing cookies set by `set_cookie` in this
        handler.
        """
        if self.request.cookies is not None and name in self.request.cookies:
            return self.request.cookies[name].value
        return default

    def set_cookie(
        self,
        name: str,
        value: str | bytes,
        domain: str | None = None,
        expires: float | tuple | datetime.datetime | None = None,
        path: str = "/",
        expires_days: float | None = None,
        # Keyword-only args start here for historical reasons.
        *,
        max_age: int | None = None,
        httponly: bool = False,
        secure: bool = False,
        samesite: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Sets an outgoing cookie name/value with the given options.

        Newly-set cookies are not immediately visible via `get_cookie`;
        they are not present until the next request.

        Most arguments are passed directly to `http.cookies.Morsel` directly.
        See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie
        for more information.

        ``expires`` may be a numeric timestamp as returned by `time.time`,
        a time tuple as returned by `time.gmtime`, or a
        `datetime.datetime` object. ``expires_days`` is provided as a convenience
        to set an expiration time in days from today (if both are set, ``expires``
        is used).

        .. deprecated:: 6.3
           Keyword arguments are currently accepted case-insensitively.
           In Tornado 7.0 this will be changed to only accept lowercase
           arguments.
        """
        # The cookie library only accepts type str, in both python 2 and 3
        name = escape.native_str(name)
        value = escape.native_str(value)
        if re.search(r"[\x00-\x20]", value):
            # Legacy check for control characters in cookie values. This check is no longer needed
            # since the cookie library escapes these characters correctly now. It will be removed
            # in the next feature release.
            raise ValueError(f"Invalid cookie {name!r}: {value!r}")
        for attr_name, attr_value in [
            ("name", name),
            ("domain", domain),
            ("path", path),
            ("samesite", samesite),
        ]:
            # Cookie attributes may not contain control characters or semicolons (except when
            # escaped in the value). A check for control characters was added to the http.cookies
            # library in a Feb 2026 security release; as of March it still does not check for
            # semicolons.
            #
            # When a semicolon check is added to the standard library (and the release has had time
            # for adoption), this check may be removed, but be mindful of the fact that this may
            # change the timing of the exception (to the generation of the Set-Cookie header in
            # flush()). We may want to add a call to self._new_cookie.output() at the end of this
            # method to ensure that exceptions are raised when they will be most useful.
            if attr_value is not None and re.search(r"[\x00-\x20\x3b\x7f]", attr_value):
                raise http.cookies.CookieError(
                    f"Invalid cookie attribute {attr_name}={attr_value!r} for cookie {name!r}"
                )
        if not hasattr(self, "_new_cookie"):
            self._new_cookie: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
        if name in self._new_cookie:
            del self._new_cookie[name]
        self._new_cookie[name] = value
        morsel = self._new_cookie[name]
        if domain:
            morsel["domain"] = domain
        if expires_days is not None and not expires:
            expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                days=expires_days
            )
        if expires:
            morsel["expires"] = httputil.format_timestamp(expires)
        if path:
            morsel["path"] = path
        if max_age:
            # Note change from _ to -.
            morsel["max-age"] = str(max_age)
        if httponly:
            # Note that SimpleCookie ignores the value here. The presense of an
            # httponly (or secure) key is treated as true.
            morsel["httponly"] = True
        if secure:
            morsel["secure"] = True
        if samesite:
            morsel["samesite"] = samesite
        if kwargs:
            # The setitem interface is case-insensitive, so continue to support
            # kwargs for backwards compatibility until we can remove deprecated
            # features.
            for k, v in kwargs.items():
                morsel[k] = v
            warnings.warn(
                f"Deprecated arguments to set_cookie: {set(kwargs.keys())} "
                "(should be lowercase)",
                DeprecationWarning,
            )

    def clear_cookie(self, name: str, **kwargs: Any) -> None:
        """Deletes the cookie with the given name.

        This method accepts the same arguments as `set_cookie`, except for
        ``expires`` and ``max_age``. Clearing a cookie requires the same
        ``domain`` and ``path`` arguments as when it was set. In some cases the
        ``samesite`` and ``secure`` arguments are also required to match. Other
        arguments are ignored.

        Similar to `set_cookie`, the effect of this method will not be
        seen until the following request.

        .. versionchanged:: 6.3

           Now accepts all keyword arguments that ``set_cookie`` does.
           The ``samesite`` and ``secure`` flags have recently become
           required for clearing ``samesite="none"`` cookies.
        """
        for excluded_arg in ["expires", "max_age"]:
            if excluded_arg in kwargs:
                raise TypeError(
                    f"clear_cookie() got an unexpected keyword argument '{excluded_arg}'"
                )
        expires = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=365
        )
        self.set_cookie(name, value="", expires=expires, **kwargs)

    def clear_all_cookies(self, **kwargs: Any) -> None:
        """Attempt to delete all the cookies the user sent with this request.

        See `clear_cookie` for more information on keyword arguments. Due to
        limitations of the cookie protocol, it is impossible to determine on the
        server side which values are necessary for the ``domain``, ``path``,
        ``samesite``, or ``secure`` arguments, this method can only be
        successful if you consistently use the same values for these arguments
        when setting cookies.

        Similar to `set_cookie`, the effect of this method will not be seen
        until the following request.

        .. versionchanged:: 3.2

           Added the ``path`` and ``domain`` parameters.

        .. versionchanged:: 6.3

           Now accepts all keyword arguments that ``set_cookie`` does.

        .. deprecated:: 6.3

           The increasingly complex rules governing cookies have made it
           impossible for a ``clear_all_cookies`` method to work reliably
           since all we know about cookies are their names. Applications
           should generally use ``clear_cookie`` one at a time instead.
        """
        for name in self.request.cookies:
            self.clear_cookie(name, **kwargs)

    def set_signed_cookie(
        self,
        name: str,
        value: str | bytes,
        expires_days: float | None = 30,
        version: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Signs and timestamps a cookie so it cannot be forged.

        You must specify the ``cookie_secret`` setting in your Application
        to use this method. It should be a long, random sequence of bytes
        to be used as the HMAC secret for the signature.

        To read a cookie set with this method, use `get_signed_cookie()`.

        Note that the ``expires_days`` parameter sets the lifetime of the
        cookie in the browser, but is independent of the ``max_age_days``
        parameter to `get_signed_cookie`.
        A value of None limits the lifetime to the current browser session.

        Secure cookies may contain arbitrary byte values, not just unicode
        strings (unlike regular cookies)

        Similar to `set_cookie`, the effect of this method will not be
        seen until the following request.

        .. versionchanged:: 3.2.1

           Added the ``version`` argument.  Introduced cookie version 2
           and made it the default.

        .. versionchanged:: 6.3

           Renamed from ``set_secure_cookie`` to ``set_signed_cookie`` to
           avoid confusion with other uses of "secure" in cookie attributes
           and prefixes. The old name remains as an alias.
        """
        self.set_cookie(
            name,
            self.create_signed_value(name, value, version=version),
            expires_days=expires_days,
            **kwargs,
        )

    set_secure_cookie = set_signed_cookie

    def create_signed_value(
        self, name: str, value: str | bytes, version: int | None = None
    ) -> bytes:
        """Signs and timestamps a string so it cannot be forged.

        Normally used via set_signed_cookie, but provided as a separate
        method for non-cookie uses.  To decode a value not stored
        as a cookie use the optional value argument to get_signed_cookie.

        .. versionchanged:: 3.2.1

           Added the ``version`` argument.  Introduced cookie version 2
           and made it the default.
        """
        self.require_setting("cookie_secret", "secure cookies")
        secret = self.application.settings["cookie_secret"]
        key_version = None
        if isinstance(secret, dict):
            if self.application.settings.get("key_version") is None:
                raise Exception("key_version setting must be used for secret_key dicts")
            key_version = self.application.settings["key_version"]

        return create_signed_value(
            secret, name, value, version=version, key_version=key_version
        )

    def get_signed_cookie(
        self,
        name: str,
        value: str | None = None,
        max_age_days: float = 31,
        min_version: int | None = None,
    ) -> bytes | None:
        """Returns the given signed cookie if it validates, or None.

        The decoded cookie value is returned as a byte string (unlike
        `get_cookie`).

        Similar to `get_cookie`, this method only returns cookies that
        were present in the request. It does not see outgoing cookies set by
        `set_signed_cookie` in this handler.

        .. versionchanged:: 3.2.1

           Added the ``min_version`` argument.  Introduced cookie version 2;
           both versions 1 and 2 are accepted by default.

         .. versionchanged:: 6.3

           Renamed from ``get_secure_cookie`` to ``get_signed_cookie`` to
           avoid confusion with other uses of "secure" in cookie attributes
           and prefixes. The old name remains as an alias.

        """
        self.require_setting("cookie_secret", "secure cookies")
        if value is None:
            value = self.get_cookie(name)
        return decode_signed_value(
            self.application.settings["cookie_secret"],
            name,
            value,
            max_age_days=max_age_days,
            min_version=min_version,
        )

    get_secure_cookie = get_signed_cookie

    def get_signed_cookie_key_version(
        self, name: str, value: str | None = None
    ) -> int | None:
        """Returns the signing key version of the secure cookie.

        The version is returned as int.

        .. versionchanged:: 6.3

           Renamed from ``get_secure_cookie_key_version`` to
           ``set_signed_cookie_key_version`` to avoid confusion with other
           uses of "secure" in cookie attributes and prefixes. The old name
           remains as an alias.

        """
        self.require_setting("cookie_secret", "secure cookies")
        if value is None:
            value = self.get_cookie(name)
        if value is None:
            return None
        return get_signature_key_version(value)

    get_secure_cookie_key_version = get_signed_cookie_key_version

    def redirect(
        self, url: str, permanent: bool = False, status: int | None = None
    ) -> None:
        """Sends a redirect to the given (optionally relative) URL.

        If the ``status`` argument is specified, that value is used as the
        HTTP status code; otherwise either 301 (permanent) or 302
        (temporary) is chosen based on the ``permanent`` argument.
        The default is 302 (temporary).
        """
        if self._headers_written:
            raise Exception("Cannot redirect after headers have been written")
        if status is None:
            status = 301 if permanent else 302
        else:
            assert isinstance(status, int) and 300 <= status <= 399
        self.set_status(status)
        self.set_header("Location", utf8(url))
        self.finish()

    def write(self, chunk: str | bytes | dict) -> None:
        """Writes the given chunk to the output buffer.

        To write the output to the network, use the `flush()` method below.

        If the given chunk is a dictionary, we write it as JSON and set
        the Content-Type of the response to be ``application/json``.
        (if you want to send JSON as a different ``Content-Type``, call
        ``set_header`` *after* calling ``write()``).

        Note that lists are not converted to JSON because of a potential
        cross-site security vulnerability.  All JSON output should be
        wrapped in a dictionary.  More details at
        http://haacked.com/archive/2009/06/25/json-hijacking.aspx/ and
        https://github.com/facebook/tornado/issues/1009
        """
        if self._finished:
            raise RuntimeError("Cannot write() after finish()")
        if not isinstance(chunk, (bytes, unicode_type, dict)):
            message = "write() only accepts bytes, unicode, and dict objects"
            if isinstance(chunk, list):
                message += (
                    ". Lists not accepted for security reasons; see "
                    + "http://www.tornadoweb.org/en/stable/web.html#tornado.web.RequestHandler.write"  # noqa: E501
                )
            raise TypeError(message)
        if isinstance(chunk, dict):
            chunk = escape.json_encode(chunk)
            self.set_header("Content-Type", "application/json; charset=UTF-8")
        chunk = utf8(chunk)
        self._write_buffer.append(chunk)

    def render(self, template_name: str, **kwargs: Any) -> "Future[None]":
        """Renders the template with the given arguments as the response.

        ``render()`` calls ``finish()``, so no other output methods can be called
        after it.

        Returns a `.Future` with the same semantics as the one returned by `finish`.
        Awaiting this `.Future` is optional.

        .. versionchanged:: 5.1
