# Copyright (C) 2003-2007  Robey Pointer <robeypointer@gmail.com>
# Copyright (C) 2003-2007  Robey Pointer <robeypointer@gmail.com>
#
# This file is part of paramiko.
#
# Paramiko is free software; you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2.1 of the License, or (at your option)
# any later version.
#
# Paramiko is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Paramiko; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA.

"""
Core protocol implementation
"""

import os
import socket
import sys
import threading
import time
import weakref
from hashlib import md5, sha1, sha256, sha512

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import (
    algorithms,
    Cipher,
    modes,
    aead,
)

import paramiko
from paramiko import util
from paramiko.auth_handler import AuthHandler, AuthOnlyHandler
from paramiko.ssh_gss import GSSAuth
from paramiko.channel import Channel
from paramiko.common import (
    xffffffff,
    cMSG_CHANNEL_OPEN,
    cMSG_IGNORE,
    cMSG_GLOBAL_REQUEST,
    DEBUG,
    MSG_KEXINIT,
    MSG_IGNORE,
    MSG_DISCONNECT,
    MSG_DEBUG,
    ERROR,
    WARNING,
    cMSG_UNIMPLEMENTED,
    INFO,
    cMSG_KEXINIT,
    cMSG_NEWKEYS,
    MSG_NEWKEYS,
    cMSG_REQUEST_SUCCESS,
    cMSG_REQUEST_FAILURE,
    CONNECTION_FAILED_CODE,
    OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED,
    OPEN_SUCCEEDED,
    cMSG_CHANNEL_OPEN_FAILURE,
    cMSG_CHANNEL_OPEN_SUCCESS,
    MSG_GLOBAL_REQUEST,
    MSG_REQUEST_SUCCESS,
    MSG_REQUEST_FAILURE,
    cMSG_SERVICE_REQUEST,
    MSG_SERVICE_ACCEPT,
    MSG_CHANNEL_OPEN_SUCCESS,
    MSG_CHANNEL_OPEN_FAILURE,
    MSG_CHANNEL_OPEN,
    MSG_CHANNEL_SUCCESS,
    MSG_CHANNEL_FAILURE,
    MSG_CHANNEL_DATA,
    MSG_CHANNEL_EXTENDED_DATA,
    MSG_CHANNEL_WINDOW_ADJUST,
    MSG_CHANNEL_REQUEST,
    MSG_CHANNEL_EOF,
    MSG_CHANNEL_CLOSE,
    MIN_WINDOW_SIZE,
    MIN_PACKET_SIZE,
    MAX_WINDOW_SIZE,
    DEFAULT_WINDOW_SIZE,
    DEFAULT_MAX_PACKET_SIZE,
    HIGHEST_USERAUTH_MESSAGE_ID,
    MSG_UNIMPLEMENTED,
    MSG_NAMES,
    MSG_EXT_INFO,
    cMSG_EXT_INFO,
    byte_ord,
)
from paramiko.compress import ZlibCompressor, ZlibDecompressor
from paramiko.ed25519key import Ed25519Key
from paramiko.kex_curve25519 import KexCurve25519
from paramiko.kex_gex import KexGex, KexGexSHA256
from paramiko.kex_group1 import KexGroup1
from paramiko.kex_group14 import KexGroup14, KexGroup14SHA256
from paramiko.kex_group16 import KexGroup16SHA512
from paramiko.kex_ecdh_nist import KexNistp256, KexNistp384, KexNistp521
from paramiko.kex_gss import KexGSSGex, KexGSSGroup1, KexGSSGroup14
from paramiko.message import Message
from paramiko.packet import Packetizer, NeedRekeyException
from paramiko.primes import ModulusPack
from paramiko.rsakey import RSAKey
from paramiko.ecdsakey import ECDSAKey
from paramiko.server import ServerInterface
from paramiko.sftp_client import SFTPClient
from paramiko.ssh_exception import (
    BadAuthenticationType,
    ChannelException,
    IncompatiblePeer,
    MessageOrderError,
    ProxyCommandFailure,
    SSHException,
)
from paramiko.util import (
    ClosingContextManager,
    clamp_value,
    b,
)


# TripleDES is moving from `cryptography.hazmat.primitives.ciphers.algorithms`
# in cryptography>=43.0.0 to `cryptography.hazmat.decrepit.ciphers.algorithms`
# It will be removed from `cryptography.hazmat.primitives.ciphers.algorithms`
# in cryptography==48.0.0.
#
# Source References:
# - https://github.com/pyca/cryptography/commit/722a6393e61b3ac
# - https://github.com/pyca/cryptography/pull/11407/files
try:
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
except ImportError:
    from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES


# for thread cleanup
_active_threads = []


def _join_lingering_threads():
    for thr in _active_threads:
        thr.stop_thread()


import atexit

atexit.register(_join_lingering_threads)


class Transport(threading.Thread, ClosingContextManager):
    """
    An SSH Transport attaches to a stream (usually a socket), negotiates an
    encrypted session, authenticates, and then creates stream tunnels, called
    `channels <.Channel>`, across the session.  Multiple channels can be
    multiplexed across a single session (and often are, in the case of port
    forwardings).

    Instances of this class may be used as context managers.
    """

    _ENCRYPT = object()
    _DECRYPT = object()

    _PROTO_ID = "2.0"
    _CLIENT_ID = "paramiko_{}".format(paramiko.__version__)

    # These tuples of algorithm identifiers are in preference order; do not
    # reorder without reason!
    # NOTE: if you need to modify these, we suggest leveraging the
    # `disabled_algorithms` constructor argument (also available in SSHClient)
    # instead of monkeypatching or subclassing.
    _preferred_ciphers = (
        "aes128-ctr",
        "aes192-ctr",
        "aes256-ctr",
        "aes128-cbc",
        "aes192-cbc",
        "aes256-cbc",
        "3des-cbc",
        "aes128-gcm@openssh.com",
        "aes256-gcm@openssh.com",
    )
    _preferred_macs = (
        "hmac-sha2-256",
        "hmac-sha2-512",
        "hmac-sha2-256-etm@openssh.com",
        "hmac-sha2-512-etm@openssh.com",
        "hmac-sha1",
        "hmac-md5",
        "hmac-sha1-96",
        "hmac-md5-96",
    )
    # ~= HostKeyAlgorithms in OpenSSH land
    _preferred_keys = (
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "rsa-sha2-512",
        "rsa-sha2-256",
        "ssh-rsa",
    )
    # ~= PubKeyAcceptedAlgorithms
    _preferred_pubkeys = (
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "rsa-sha2-512",
        "rsa-sha2-256",
        "ssh-rsa",
    )
    _preferred_kex = (
        "ecdh-sha2-nistp256",
        "ecdh-sha2-nistp384",
        "ecdh-sha2-nistp521",
        "diffie-hellman-group16-sha512",
        "diffie-hellman-group-exchange-sha256",
        "diffie-hellman-group14-sha256",
        "diffie-hellman-group-exchange-sha1",
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group1-sha1",
    )
    if KexCurve25519.is_available():
        _preferred_kex = ("curve25519-sha256@libssh.org",) + _preferred_kex
    _preferred_gsskex = (
        "gss-gex-sha1-toWM5Slw5Ew8Mqkay+al2g==",
        "gss-group14-sha1-toWM5Slw5Ew8Mqkay+al2g==",
        "gss-group1-sha1-toWM5Slw5Ew8Mqkay+al2g==",
    )
    _preferred_compression = ("none",)

    _cipher_info = {
        "aes128-ctr": {
            "class": algorithms.AES,
            "mode": modes.CTR,
            "block-size": 16,
            "key-size": 16,
        },
        "aes192-ctr": {
            "class": algorithms.AES,
            "mode": modes.CTR,
            "block-size": 16,
            "key-size": 24,
        },
        "aes256-ctr": {
            "class": algorithms.AES,
            "mode": modes.CTR,
            "block-size": 16,
            "key-size": 32,
        },
        "aes128-cbc": {
            "class": algorithms.AES,
            "mode": modes.CBC,
            "block-size": 16,
            "key-size": 16,
        },
        "aes192-cbc": {
            "class": algorithms.AES,
            "mode": modes.CBC,
            "block-size": 16,
            "key-size": 24,
        },
        "aes256-cbc": {
            "class": algorithms.AES,
            "mode": modes.CBC,
            "block-size": 16,
            "key-size": 32,
        },
        "3des-cbc": {
            "class": TripleDES,
            "mode": modes.CBC,
            "block-size": 8,
            "key-size": 24,
        },
        "aes128-gcm@openssh.com": {
            "class": aead.AESGCM,
            "block-size": 16,
            "iv-size": 12,
            "key-size": 16,
            "is_aead": True,
        },
        "aes256-gcm@openssh.com": {
            "class": aead.AESGCM,
            "block-size": 16,
            "iv-size": 12,
            "key-size": 32,
            "is_aead": True,
        },
    }

    _mac_info = {
        "hmac-sha1": {"class": sha1, "size": 20},
        "hmac-sha1-96": {"class": sha1, "size": 12},
        "hmac-sha2-256": {"class": sha256, "size": 32},
        "hmac-sha2-256-etm@openssh.com": {"class": sha256, "size": 32},
        "hmac-sha2-512": {"class": sha512, "size": 64},
        "hmac-sha2-512-etm@openssh.com": {"class": sha512, "size": 64},
        "hmac-md5": {"class": md5, "size": 16},
        "hmac-md5-96": {"class": md5, "size": 12},
    }

    _key_info = {
        # TODO: at some point we will want to drop this as it's no longer
        # considered secure due to using SHA-1 for signatures. OpenSSH 8.8 no
        # longer supports it. Question becomes at what point do we want to
        # prevent users with older setups from using this?
        "ssh-rsa": RSAKey,
        "ssh-rsa-cert-v01@openssh.com": RSAKey,
        "rsa-sha2-256": RSAKey,
        "rsa-sha2-256-cert-v01@openssh.com": RSAKey,
        "rsa-sha2-512": RSAKey,
        "rsa-sha2-512-cert-v01@openssh.com": RSAKey,
        "ecdsa-sha2-nistp256": ECDSAKey,
        "ecdsa-sha2-nistp256-cert-v01@openssh.com": ECDSAKey,
        "ecdsa-sha2-nistp384": ECDSAKey,
        "ecdsa-sha2-nistp384-cert-v01@openssh.com": ECDSAKey,
        "ecdsa-sha2-nistp521": ECDSAKey,
        "ecdsa-sha2-nistp521-cert-v01@openssh.com": ECDSAKey,
        "ssh-ed25519": Ed25519Key,
        "ssh-ed25519-cert-v01@openssh.com": Ed25519Key,
    }

    _kex_info = {
        "diffie-hellman-group1-sha1": KexGroup1,
        "diffie-hellman-group14-sha1": KexGroup14,
        "diffie-hellman-group-exchange-sha1": KexGex,
        "diffie-hellman-group-exchange-sha256": KexGexSHA256,
        "diffie-hellman-group14-sha256": KexGroup14SHA256,
        "diffie-hellman-group16-sha512": KexGroup16SHA512,
        "gss-group1-sha1-toWM5Slw5Ew8Mqkay+al2g==": KexGSSGroup1,
        "gss-group14-sha1-toWM5Slw5Ew8Mqkay+al2g==": KexGSSGroup14,
        "gss-gex-sha1-toWM5Slw5Ew8Mqkay+al2g==": KexGSSGex,
        "ecdh-sha2-nistp256": KexNistp256,
        "ecdh-sha2-nistp384": KexNistp384,
        "ecdh-sha2-nistp521": KexNistp521,
    }
    if KexCurve25519.is_available():
        _kex_info["curve25519-sha256@libssh.org"] = KexCurve25519

    _compression_info = {
        # zlib@openssh.com is just zlib, but only turned on after a successful
        # authentication.  openssh servers may only offer this type because
        # they've had troubles with security holes in zlib in the past.
        "zlib@openssh.com": (ZlibCompressor, ZlibDecompressor),
        "zlib": (ZlibCompressor, ZlibDecompressor),
        "none": (None, None),
    }

    _modulus_pack = None
    _active_check_timeout = 0.1

    def __init__(
        self,
        sock,
        default_window_size=DEFAULT_WINDOW_SIZE,
        default_max_packet_size=DEFAULT_MAX_PACKET_SIZE,
        gss_kex=False,
        gss_deleg_creds=True,
        disabled_algorithms=None,
        server_sig_algs=True,
        strict_kex=True,
        packetizer_class=None,
    ):
        """
        Create a new SSH session over an existing socket, or socket-like
        object.  This only creates the `.Transport` object; it doesn't begin
        the SSH session yet.  Use `connect` or `start_client` to begin a client
        session, or `start_server` to begin a server session.

        If the object is not actually a socket, it must have the following
        methods:

        - ``send(bytes)``: Writes from 1 to ``len(bytes)`` bytes, and returns
          an int representing the number of bytes written.  Returns
          0 or raises ``EOFError`` if the stream has been closed.
        - ``recv(int)``: Reads from 1 to ``int`` bytes and returns them as a
          string.  Returns 0 or raises ``EOFError`` if the stream has been
          closed.
        - ``close()``: Closes the socket.
        - ``settimeout(n)``: Sets a (float) timeout on I/O operations.

        For ease of use, you may also pass in an address (as a tuple) or a host
        string as the ``sock`` argument.  (A host string is a hostname with an
        optional port (separated by ``":"``) which will be converted into a
        tuple of ``(hostname, port)``.)  A socket will be connected to this
        address and used for communication.  Exceptions from the ``socket``
        call may be thrown in this case.

        .. note::
            Modifying the the window and packet sizes might have adverse
            effects on your channels created from this transport. The default
            values are the same as in the OpenSSH code base and have been
            battle tested.

        :param socket sock:
            a socket or socket-like object to create the session over.
        :param int default_window_size:
            sets the default window size on the transport. (defaults to
            2097152)
        :param int default_max_packet_size:
            sets the default max packet size on the transport. (defaults to
            32768)
        :param bool gss_kex:
            Whether to enable GSSAPI key exchange when GSSAPI is in play.
            Default: ``False``.
        :param bool gss_deleg_creds:
            Whether to enable GSSAPI credential delegation when GSSAPI is in
            play. Default: ``True``.
        :param dict disabled_algorithms:
            If given, must be a dictionary mapping algorithm type to an
            iterable of algorithm identifiers, which will be disabled for the
            lifetime of the transport.

            Keys should match the last word in the class' builtin algorithm
            tuple attributes, such as ``"ciphers"`` to disable names within
            ``_preferred_ciphers``; or ``"kex"`` to disable something defined
            inside ``_preferred_kex``. Values should exactly match members of
            the matching attribute.

            For example, if you need to disable
            ``diffie-hellman-group16-sha512`` key exchange (perhaps because
            your code talks to a server which implements it differently from
            Paramiko), specify ``disabled_algorithms={"kex":
            ["diffie-hellman-group16-sha512"]}``.
        :param bool server_sig_algs:
            Whether to send an extra message to compatible clients, in server
            mode, with a list of supported pubkey algorithms. Default:
            ``True``.
        :param bool strict_kex:
            Whether to advertise (and implement, if client also advertises
            support for) a "strict kex" mode for safer handshaking. Default:
            ``True``.
        :param packetizer_class:
            Which class to use for instantiating the internal packet handler.
            Default: ``None`` (i.e.: use `Packetizer` as normal).

        .. versionchanged:: 1.15
            Added the ``default_window_size`` and ``default_max_packet_size``
            arguments.
        .. versionchanged:: 1.15
            Added the ``gss_kex`` and ``gss_deleg_creds`` kwargs.
        .. versionchanged:: 2.6
            Added the ``disabled_algorithms`` kwarg.
        .. versionchanged:: 2.9
            Added the ``server_sig_algs`` kwarg.
        .. versionchanged:: 3.4
            Added the ``strict_kex`` kwarg.
        .. versionchanged:: 3.4
            Added the ``packetizer_class`` kwarg.
        """
        self.active = False
        self.hostname = None
        self.server_extensions = {}
        self.advertise_strict_kex = strict_kex
        self.agreed_on_strict_kex = False

        # TODO: these two overrides on sock's type should go away sometime, too
        # many ways to do it!
        if isinstance(sock, str):
            # convert "host:port" into (host, port)
            hl = sock.split(":", 1)
            self.hostname = hl[0]
            if len(hl) == 1:
                sock = (hl[0], 22)
            else:
                sock = (hl[0], int(hl[1]))
        if type(sock) is tuple:
            # connect to the given (host, port)
            hostname, port = sock
            self.hostname = hostname
            reason = "No suitable address family"
            addrinfos = socket.getaddrinfo(
                hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            for family, socktype, proto, canonname, sockaddr in addrinfos:
                if socktype == socket.SOCK_STREAM:
                    af = family
                    # addr = sockaddr
                    sock = socket.socket(af, socket.SOCK_STREAM)
                    try:
                        sock.connect((hostname, port))
                    except socket.error as e:
                        reason = str(e)
                    else:
                        break
            else:
                raise SSHException(
                    "Unable to connect to {}: {}".format(hostname, reason)
                )
        # okay, normal socket-ish flow here...
        threading.Thread.__init__(self)
        self.daemon = True
        self.sock = sock
        # we set the timeout so we can check self.active periodically to
        # see if we should bail. socket.timeout exception is never propagated.
        self.sock.settimeout(self._active_check_timeout)

        # negotiated crypto parameters
        self.packetizer = (packetizer_class or Packetizer)(sock)
        self.local_version = "SSH-" + self._PROTO_ID + "-" + self._CLIENT_ID
        self.remote_version = ""
        self.local_cipher = self.remote_cipher = ""
        self.local_kex_init = self.remote_kex_init = None
        self.local_mac = self.remote_mac = None
        self.local_compression = self.remote_compression = None
        self.session_id = None
        self.host_key_type = None
        self.host_key = None

        # GSS-API / SSPI Key Exchange
        self.use_gss_kex = gss_kex
        # This will be set to True if GSS-API Key Exchange was performed
        self.gss_kex_used = False
        self.kexgss_ctxt = None
        self.gss_host = None
        if self.use_gss_kex:
            self.kexgss_ctxt = GSSAuth("gssapi-keyex", gss_deleg_creds)
            self._preferred_kex = self._preferred_gsskex + self._preferred_kex

        # state used during negotiation
        self.kex_engine = None
        self.H = None
        self.K = None

        self.initial_kex_done = False
        self.in_kex = False
        self.authenticated = False
        self._expected_packet = tuple()
        # synchronization (always higher level than write_lock)
        self.lock = threading.Lock()

        # tracking open channels
        self._channels = ChannelMap()
        self.channel_events = {}  # (id -> Event)
        self.channels_seen = {}  # (id -> True)
        self._channel_counter = 0
        self.default_max_packet_size = default_max_packet_size
        self.default_window_size = default_window_size
        self._forward_agent_handler = None
        self._x11_handler = None
        self._tcp_handler = None

        self.saved_exception = None
        self.clear_to_send = threading.Event()
        self.clear_to_send_lock = threading.Lock()
        self.clear_to_send_timeout = 30.0
        self.log_name = "paramiko.transport"
        self.logger = util.get_logger(self.log_name)
        self.packetizer.set_log(self.logger)
        self.auth_handler = None
        # response Message from an arbitrary global request
        self.global_response = None
        # user-defined event callbacks
        self.completion_event = None
        # how long (seconds) to wait for the SSH banner
        self.banner_timeout = 15
        # how long (seconds) to wait for the handshake to finish after SSH
        # banner sent.
        self.handshake_timeout = 15
        # how long (seconds) to wait for the auth response.
        self.auth_timeout = 30
        # how long (seconds) to wait for opening a channel
        self.channel_timeout = 60 * 60
        self.disabled_algorithms = disabled_algorithms or {}
        self.server_sig_algs = server_sig_algs

        # server mode:
        self.server_mode = False
        self.server_object = None
        self.server_key_dict = {}
        self.server_accepts = []
        self.server_accept_cv = threading.Condition(self.lock)
        self.subsystem_table = {}

        # Handler table, now set at init time for easier per-instance
        # manipulation and subclass twiddling.
        self._handler_table = {
            MSG_EXT_INFO: self._parse_ext_info,
            MSG_NEWKEYS: self._parse_newkeys,
            MSG_GLOBAL_REQUEST: self._parse_global_request,
            MSG_REQUEST_SUCCESS: self._parse_request_success,
            MSG_REQUEST_FAILURE: self._parse_request_failure,
            MSG_CHANNEL_OPEN_SUCCESS: self._parse_channel_open_success,
            MSG_CHANNEL_OPEN_FAILURE: self._parse_channel_open_failure,
            MSG_CHANNEL_OPEN: self._parse_channel_open,
            MSG_KEXINIT: self._negotiate_keys,
        }

    def _filter_algorithm(self, type_):
        default = getattr(self, "_preferred_{}".format(type_))
        return tuple(
            x
            for x in default
            if x not in self.disabled_algorithms.get(type_, [])
        )

    @property
    def preferred_ciphers(self):
        return self._filter_algorithm("ciphers")

    @property
    def preferred_macs(self):
        return self._filter_algorithm("macs")

    @property
    def preferred_keys(self):
        # Interleave cert variants here; resistant to various background
        # overwriting of _preferred_keys, and necessary as hostkeys can't use
        # the logic pubkey auth does re: injecting/checking for certs at
        # runtime
        filtered = self._filter_algorithm("keys")
        return tuple(
            filtered
            + tuple("{}-cert-v01@openssh.com".format(x) for x in filtered)
        )

    @property
    def preferred_pubkeys(self):
        return self._filter_algorithm("pubkeys")

    @property
    def preferred_kex(self):
        return self._filter_algorithm("kex")

    @property
    def preferred_compression(self):
        return self._filter_algorithm("compression")

    def __repr__(self):
        """
        Returns a string representation of this object, for debugging.
        """
        id_ = hex(id(self) & xffffffff)
        out = "<paramiko.Transport at {}".format(id_)
        if not self.active:
            out += " (unconnected)"
        else:
            if self.local_cipher != "":
                out += " (cipher {}, {:d} bits)".format(
                    self.local_cipher,
                    self._cipher_info[self.local_cipher]["key-size"] * 8,
                )
            if self.is_authenticated():
                out += " (active; {} open channel(s))".format(
                    len(self._channels)
                )
            elif self.initial_kex_done:
                out += " (connected; awaiting auth)"
            else:
                out += " (connecting)"
        out += ">"
        return out

    def atfork(self):
        """
        Terminate this Transport without closing the session.  On posix
        systems, if a Transport is open during process forking, both parent
        and child will share the underlying socket, but only one process can
        use the connection (without corrupting the session).  Use this method
        to clean up a Transport object without disrupting the other process.

        .. versionadded:: 1.5.3
        """
        self.sock.close()
        self.close()

    def get_security_options(self):
        """
        Return a `.SecurityOptions` object which can be used to tweak the
        encryption algorithms this transport will permit (for encryption,
        digest/hash operations, public keys, and key exchanges) and the order
        of preference for them.
        """
        return SecurityOptions(self)

    def set_gss_host(self, gss_host, trust_dns=True, gssapi_requested=True):
        """
        Normalize/canonicalize ``self.gss_host`` depending on various factors.

        :param str gss_host:
            The explicitly requested GSS-oriented hostname to connect to (i.e.
            what the host's name is in the Kerberos database.) Defaults to
            ``self.hostname`` (which will be the 'real' target hostname and/or
            host portion of given socket object.)
        :param bool trust_dns:
            Indicates whether or not DNS is trusted; if true, DNS will be used
            to canonicalize the GSS hostname (which again will either be
            ``gss_host`` or the transport's default hostname.)
            (Defaults to True due to backwards compatibility.)
        :param bool gssapi_requested:
            Whether GSSAPI key exchange or authentication was even requested.
            If not, this is a no-op and nothing happens
            (and ``self.gss_host`` is not set.)
            (Defaults to True due to backwards compatibility.)
        :returns: ``None``.
        """
        # No GSSAPI in play == nothing to do
        if not gssapi_requested:
            return
        # Obtain the correct host first - did user request a GSS-specific name
        # to use that is distinct from the actual SSH target hostname?
        if gss_host is None:
            gss_host = self.hostname
        # Finally, canonicalize via DNS if DNS is trusted.
        if trust_dns and gss_host is not None:
            gss_host = socket.getfqdn(gss_host)
        # And set attribute for reference later.
        self.gss_host = gss_host

    def start_client(self, event=None, timeout=None):
        """
        Negotiate a new SSH2 session as a client.  This is the first step after
        creating a new `.Transport`.  A separate thread is created for protocol
        negotiation.

        If an event is passed in, this method returns immediately.  When
        negotiation is done (successful or not), the given ``Event`` will
        be triggered.  On failure, `is_active` will return ``False``.

        (Since 1.4) If ``event`` is ``None``, this method will not return until
        negotiation is done.  On success, the method returns normally.
        Otherwise an SSHException is raised.

        After a successful negotiation, you will usually want to authenticate,
        calling `auth_password <Transport.auth_password>` or
        `auth_publickey <Transport.auth_publickey>`.

        .. note:: `connect` is a simpler method for connecting as a client.

        .. note::
            After calling this method (or `start_server` or `connect`), you
            should no longer directly read from or write to the original socket
            object.

        :param .threading.Event event:
            an event to trigger when negotiation is complete (optional)

        :param float timeout:
            a timeout, in seconds, for SSH2 session negotiation (optional)

        :raises:
            `.SSHException` -- if negotiation fails (and no ``event`` was
            passed in)
        """
        self.active = True
        if event is not None:
            # async, return immediately and let the app poll for completion
            self.completion_event = event
            self.start()
            return

        # synchronous, wait for a result
        self.completion_event = event = threading.Event()
        self.start()
        max_time = time.time() + timeout if timeout is not None else None
        while True:
            event.wait(0.1)
            if not self.active:
                e = self.get_exception()
                if e is not None:
                    raise e
                raise SSHException("Negotiation failed.")
            if event.is_set() or (
                timeout is not None and time.time() >= max_time
            ):
                break

    def start_server(self, event=None, server=None):
        """
        Negotiate a new SSH2 session as a server.  This is the first step after
        creating a new `.Transport` and setting up your server host key(s).  A
        separate thread is created for protocol negotiation.

        If an event is passed in, this method returns immediately.  When
        negotiation is done (successful or not), the given ``Event`` will
        be triggered.  On failure, `is_active` will return ``False``.

        (Since 1.4) If ``event`` is ``None``, this method will not return until
        negotiation is done.  On success, the method returns normally.
        Otherwise an SSHException is raised.

        After a successful negotiation, the client will need to authenticate.
        Override the methods `get_allowed_auths
        <.ServerInterface.get_allowed_auths>`, `check_auth_none
        <.ServerInterface.check_auth_none>`, `check_auth_password
        <.ServerInterface.check_auth_password>`, and `check_auth_publickey
        <.ServerInterface.check_auth_publickey>` in the given ``server`` object
        to control the authentication process.

        After a successful authentication, the client should request to open a
        channel.  Override `check_channel_request
        <.ServerInterface.check_channel_request>` in the given ``server``
        object to allow channels to be opened.

        .. note::
            After calling this method (or `start_client` or `connect`), you
            should no longer directly read from or write to the original socket
            object.

        :param .threading.Event event:
            an event to trigger when negotiation is complete.
        :param .ServerInterface server:
            an object used to perform authentication and create `channels
            <.Channel>`

        :raises:
            `.SSHException` -- if negotiation fails (and no ``event`` was
            passed in)
        """
        if server is None:
            server = ServerInterface()
        self.server_mode = True
        self.server_object = server
        self.active = True
        if event is not None:
            # async, return immediately and let the app poll for completion
            self.completion_event = event
            self.start()
            return

        # synchronous, wait for a result
        self.completion_event = event = threading.Event()
        self.start()
        while True:
            event.wait(0.1)
            if not self.active:
                e = self.get_exception()
                if e is not None:
                    raise e
                raise SSHException("Negotiation failed.")
            if event.is_set():
                break

    def add_server_key(self, key):
        """
        Add a host key to the list of keys used for server mode.  When behaving
        as a server, the host key is used to sign certain packets during the
        SSH2 negotiation, so that the client can trust that we are who we say
        we are.  Because this is used for signing, the key must contain private
        key info, not just the public half.  Only one key of each type is kept.

        :param .PKey key:
            the host key (instance of some subclass) to add
        """
        self.server_key_dict[key.get_name()] = key
        # Handle SHA-2 extensions for RSA by ensuring that lookups into
        # self.server_key_dict will yield this key for any of the algorithm
        # names.
        if isinstance(key, RSAKey):
            self.server_key_dict["rsa-sha2-256"] = key
            self.server_key_dict["rsa-sha2-512"] = key

    def get_server_key(self):
        """
        Return the active host key, in server mode.  After negotiating with the
        client, this method will return the negotiated host key.  If only one
        type of host key was set with `add_server_key`, that's the only key
        that will ever be returned.  But in cases where you have set more than
        one type of host key, the key type will be negotiated by the client,
        and this method will return the key of the type agreed on.  If the host
        key has not been negotiated yet, ``None`` is returned.  In client mode,
        the behavior is undefined.

        :return:
            host key (`.PKey`) of the type negotiated by the client, or
            ``None``.
        """
        try:
            return self.server_key_dict[self.host_key_type]
        except KeyError:
            pass
        return None

    @staticmethod
    def load_server_moduli(filename=None):
        """
        (optional)
        Load a file of prime moduli for use in doing group-exchange key
        negotiation in server mode.  It's a rather obscure option and can be
        safely ignored.

        In server mode, the remote client may request "group-exchange" key
        negotiation, which asks the server to send a random prime number that
        fits certain criteria.  These primes are pretty difficult to compute,
        so they can't be generated on demand.  But many systems contain a file
        of suitable primes (usually named something like ``/etc/ssh/moduli``).
        If you call `load_server_moduli` and it returns ``True``, then this
        file of primes has been loaded and we will support "group-exchange" in
        server mode.  Otherwise server mode will just claim that it doesn't
        support that method of key negotiation.

        :param str filename:
            optional path to the moduli file, if you happen to know that it's
            not in a standard location.
        :return:
            True if a moduli file was successfully loaded; False otherwise.

        .. note:: This has no effect when used in client mode.
        """
        Transport._modulus_pack = ModulusPack()
        # places to look for the openssh "moduli" file
        file_list = ["/etc/ssh/moduli", "/usr/local/etc/moduli"]
        if filename is not None:
            file_list.insert(0, filename)
        for fn in file_list:
            try:
                Transport._modulus_pack.read_file(fn)
                return True
            except IOError:
                pass
        # none succeeded
        Transport._modulus_pack = None
        return False

    def close(self):
        """
        Close this session, and any open channels that are tied to it.
        """
        if not self.active:
            return
        self.stop_thread()
        for chan in list(self._channels.values()):
            chan._unlink()
        self.sock.close()

    def get_remote_server_key(self):
        """
        Return the host key of the server (in client mode).

        .. note::
            Previously this call returned a tuple of ``(key type, key
            string)``. You can get the same effect by calling `.PKey.get_name`
            for the key type, and ``str(key)`` for the key string.

        :raises: `.SSHException` -- if no session is currently active.

        :return: public key (`.PKey`) of the remote server
        """
        if (not self.active) or (not self.initial_kex_done):
            raise SSHException("No existing session")
        return self.host_key

    def is_active(self):
        """
        Return true if this session is active (open).

        :return:
            True if the session is still active (open); False if the session is
            closed
        """
        return self.active

    def open_session(
        self, window_size=None, max_packet_size=None, timeout=None
    ):
        """
        Request a new channel to the server, of type ``"session"``.  This is
        just an alias for calling `open_channel` with an argument of
        ``"session"``.

        .. note:: Modifying the the window and packet sizes might have adverse
            effects on the session created. The default values are the same
            as in the OpenSSH code base and have been battle tested.

        :param int window_size:
            optional window size for this session.
        :param int max_packet_size:
            optional max packet size for this session.

        :return: a new `.Channel`

        :raises:
            `.SSHException` -- if the request is rejected or the session ends
            prematurely

        .. versionchanged:: 1.13.4/1.14.3/1.15.3
            Added the ``timeout`` argument.
        .. versionchanged:: 1.15
            Added the ``window_size`` and ``max_packet_size`` arguments.
        """
        return self.open_channel(
            "session",
            window_size=window_size,
            max_packet_size=max_packet_size,
            timeout=timeout,
        )

    def open_x11_channel(self, src_addr=None):
        """
        Request a new channel to the client, of type ``"x11"``.  This
        is just an alias for ``open_channel('x11', src_addr=src_addr)``.

        :param tuple src_addr:
