# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import annotations

import binascii
import enum
import os
import re
import typing
import warnings
from base64 import encodebytes as _base64_encode
from dataclasses import dataclass

from cryptography import utils
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import (
    dsa,
    ec,
    ed25519,
    padding,
    rsa,
)
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils
from cryptography.hazmat.primitives.ciphers import (
    AEADDecryptionContext,
    Cipher,
    algorithms,
    modes,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    KeySerializationEncryption,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    _KeySerializationEncryption,
)

try:
    from bcrypt import kdf as _bcrypt_kdf

    _bcrypt_supported = True
except ImportError:
    _bcrypt_supported = False

    def _bcrypt_kdf(
        password: bytes,
        salt: bytes,
        desired_key_bytes: int,
        rounds: int,
        ignore_few_rounds: bool = False,
    ) -> bytes:
        raise UnsupportedAlgorithm("Need bcrypt module")


_SSH_ED25519 = b"ssh-ed25519"
_SSH_RSA = b"ssh-rsa"
_SSH_DSA = b"ssh-dss"
_ECDSA_NISTP256 = b"ecdsa-sha2-nistp256"
_ECDSA_NISTP384 = b"ecdsa-sha2-nistp384"
_ECDSA_NISTP521 = b"ecdsa-sha2-nistp521"
_CERT_SUFFIX = b"-cert-v01@openssh.com"

# U2F application string suffixed pubkey
_SK_SSH_ED25519 = b"sk-ssh-ed25519@openssh.com"
_SK_SSH_ECDSA_NISTP256 = b"sk-ecdsa-sha2-nistp256@openssh.com"

# These are not key types, only algorithms, so they cannot appear
# as a public key type
_SSH_RSA_SHA256 = b"rsa-sha2-256"
_SSH_RSA_SHA512 = b"rsa-sha2-512"

_SSH_PUBKEY_RC = re.compile(rb"\A(\S+)[ \t]+(\S+)")
_SK_MAGIC = b"openssh-key-v1\0"
_SK_START = b"-----BEGIN OPENSSH PRIVATE KEY-----"
_SK_END = b"-----END OPENSSH PRIVATE KEY-----"
_BCRYPT = b"bcrypt"
_NONE = b"none"
_DEFAULT_CIPHER = b"aes256-ctr"
_DEFAULT_ROUNDS = 16

# re is only way to work on bytes-like data
_PEM_RC = re.compile(_SK_START + b"(.*?)" + _SK_END, re.DOTALL)

# padding for max blocksize
_PADDING = memoryview(bytearray(range(1, 1 + 16)))


@dataclass
class _SSHCipher:
    alg: type[algorithms.AES]
    key_len: int
    mode: type[modes.CTR] | type[modes.CBC] | type[modes.GCM]
    block_len: int
    iv_len: int
    tag_len: int | None
    is_aead: bool


# ciphers that are actually used in key wrapping
_SSH_CIPHERS: dict[bytes, _SSHCipher] = {
    b"aes256-ctr": _SSHCipher(
        alg=algorithms.AES,
        key_len=32,
        mode=modes.CTR,
        block_len=16,
        iv_len=16,
        tag_len=None,
        is_aead=False,
    ),
    b"aes256-cbc": _SSHCipher(
        alg=algorithms.AES,
        key_len=32,
        mode=modes.CBC,
        block_len=16,
        iv_len=16,
        tag_len=None,
        is_aead=False,
    ),
    b"aes256-gcm@openssh.com": _SSHCipher(
        alg=algorithms.AES,
        key_len=32,
        mode=modes.GCM,
        block_len=16,
        iv_len=12,
        tag_len=16,
        is_aead=True,
    ),
}

# map local curve name to key type
_ECDSA_KEY_TYPE = {
    "secp256r1": _ECDSA_NISTP256,
    "secp384r1": _ECDSA_NISTP384,
    "secp521r1": _ECDSA_NISTP521,
}


def _get_ssh_key_type(key: SSHPrivateKeyTypes | SSHPublicKeyTypes) -> bytes:
    if isinstance(key, ec.EllipticCurvePrivateKey):
        key_type = _ecdsa_key_type(key.public_key())
    elif isinstance(key, ec.EllipticCurvePublicKey):
        key_type = _ecdsa_key_type(key)
    elif isinstance(key, (rsa.RSAPrivateKey, rsa.RSAPublicKey)):
        key_type = _SSH_RSA
    elif isinstance(key, (dsa.DSAPrivateKey, dsa.DSAPublicKey)):
        key_type = _SSH_DSA
    elif isinstance(
        key, (ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey)
    ):
        key_type = _SSH_ED25519
    else:
        raise ValueError("Unsupported key type")

    return key_type


def _ecdsa_key_type(public_key: ec.EllipticCurvePublicKey) -> bytes:
    """Return SSH key_type and curve_name for private key."""
    curve = public_key.curve
    if curve.name not in _ECDSA_KEY_TYPE:
        raise ValueError(
            f"Unsupported curve for ssh private key: {curve.name!r}"
        )
    return _ECDSA_KEY_TYPE[curve.name]


def _ssh_pem_encode(
    data: utils.Buffer,
    prefix: bytes = _SK_START + b"\n",
    suffix: bytes = _SK_END + b"\n",
) -> bytes:
    return b"".join([prefix, _base64_encode(data), suffix])


def _check_block_size(data: utils.Buffer, block_len: int) -> None:
    """Require data to be full blocks"""
    if not data or len(data) % block_len != 0:
        raise ValueError("Corrupt data: missing padding")


def _check_empty(data: utils.Buffer) -> None:
    """All data should have been parsed."""
    if data:
        raise ValueError("Corrupt data: unparsed data")


def _init_cipher(
    ciphername: bytes,
    password: bytes | None,
    salt: bytes,
    rounds: int,
) -> Cipher[modes.CBC | modes.CTR | modes.GCM]:
    """Generate key + iv and return cipher."""
    if not password:
        raise TypeError(
            "Key is password-protected, but password was not provided."
        )

    ciph = _SSH_CIPHERS[ciphername]
    seed = _bcrypt_kdf(
        password, salt, ciph.key_len + ciph.iv_len, rounds, True
    )
    return Cipher(
        ciph.alg(seed[: ciph.key_len]),
        ciph.mode(seed[ciph.key_len :]),
    )


def _get_u32(data: memoryview) -> tuple[int, memoryview]:
    """Uint32"""
    if len(data) < 4:
        raise ValueError("Invalid data")
    return int.from_bytes(data[:4], byteorder="big"), data[4:]


def _get_u64(data: memoryview) -> tuple[int, memoryview]:
    """Uint64"""
    if len(data) < 8:
        raise ValueError("Invalid data")
    return int.from_bytes(data[:8], byteorder="big"), data[8:]


def _get_sshstr(data: memoryview) -> tuple[memoryview, memoryview]:
    """Bytes with u32 length prefix"""
    n, data = _get_u32(data)
    if n > len(data):
        raise ValueError("Invalid data")
    return data[:n], data[n:]


def _get_mpint(data: memoryview) -> tuple[int, memoryview]:
    """Big integer."""
    val, data = _get_sshstr(data)
    if val and val[0] > 0x7F:
        raise ValueError("Invalid data")
    return int.from_bytes(val, "big"), data


def _to_mpint(val: int) -> bytes:
    """Storage format for signed bigint."""
    if val < 0:
        raise ValueError("negative mpint not allowed")
    if not val:
        return b""
    nbytes = (val.bit_length() + 8) // 8
    return utils.int_to_bytes(val, nbytes)


class _FragList:
    """Build recursive structure without data copy."""

    flist: list[utils.Buffer]

    def __init__(self, init: list[utils.Buffer] | None = None) -> None:
        self.flist = []
        if init:
            self.flist.extend(init)

    def put_raw(self, val: utils.Buffer) -> None:
        """Add plain bytes"""
        self.flist.append(val)

    def put_u32(self, val: int) -> None:
        """Big-endian uint32"""
        self.flist.append(val.to_bytes(length=4, byteorder="big"))

    def put_u64(self, val: int) -> None:
        """Big-endian uint64"""
        self.flist.append(val.to_bytes(length=8, byteorder="big"))

    def put_sshstr(self, val: bytes | _FragList) -> None:
        """Bytes prefixed with u32 length"""
        if isinstance(val, (bytes, memoryview, bytearray)):
            self.put_u32(len(val))
            self.flist.append(val)
        else:
            self.put_u32(val.size())
            self.flist.extend(val.flist)

    def put_mpint(self, val: int) -> None:
        """Big-endian bigint prefixed with u32 length"""
        self.put_sshstr(_to_mpint(val))

    def size(self) -> int:
        """Current number of bytes"""
        return sum(map(len, self.flist))

    def render(self, dstbuf: memoryview, pos: int = 0) -> int:
        """Write into bytearray"""
        for frag in self.flist:
            flen = len(frag)
            start, pos = pos, pos + flen
            dstbuf[start:pos] = frag
        return pos

    def tobytes(self) -> bytes:
        """Return as bytes"""
        buf = memoryview(bytearray(self.size()))
        self.render(buf)
        return buf.tobytes()


class _SSHFormatRSA:
    """Format for RSA keys.

    Public:
        mpint e, n
    Private:
        mpint n, e, d, iqmp, p, q
    """

    def get_public(
        self, data: memoryview
    ) -> tuple[tuple[int, int], memoryview]:
        """RSA public fields"""
        e, data = _get_mpint(data)
        n, data = _get_mpint(data)
        return (e, n), data

    def load_public(
        self, data: memoryview
    ) -> tuple[rsa.RSAPublicKey, memoryview]:
        """Make RSA public key from data."""
        (e, n), data = self.get_public(data)
        public_numbers = rsa.RSAPublicNumbers(e, n)
        public_key = public_numbers.public_key()
        return public_key, data

    def load_private(
        self, data: memoryview, pubfields, unsafe_skip_rsa_key_validation: bool
    ) -> tuple[rsa.RSAPrivateKey, memoryview]:
        """Make RSA private key from data."""
        n, data = _get_mpint(data)
        e, data = _get_mpint(data)
        d, data = _get_mpint(data)
        iqmp, data = _get_mpint(data)
        p, data = _get_mpint(data)
        q, data = _get_mpint(data)

        if (e, n) != pubfields:
            raise ValueError("Corrupt data: rsa field mismatch")
        dmp1 = rsa.rsa_crt_dmp1(d, p)
        dmq1 = rsa.rsa_crt_dmq1(d, q)
        public_numbers = rsa.RSAPublicNumbers(e, n)
        private_numbers = rsa.RSAPrivateNumbers(
            p, q, d, dmp1, dmq1, iqmp, public_numbers
        )
        private_key = private_numbers.private_key(
            unsafe_skip_rsa_key_validation=unsafe_skip_rsa_key_validation
        )
        return private_key, data

    def encode_public(
        self, public_key: rsa.RSAPublicKey, f_pub: _FragList
    ) -> None:
        """Write RSA public key"""
        pubn = public_key.public_numbers()
        f_pub.put_mpint(pubn.e)
        f_pub.put_mpint(pubn.n)

    def encode_private(
        self, private_key: rsa.RSAPrivateKey, f_priv: _FragList
    ) -> None:
        """Write RSA private key"""
        private_numbers = private_key.private_numbers()
        public_numbers = private_numbers.public_numbers

        f_priv.put_mpint(public_numbers.n)
        f_priv.put_mpint(public_numbers.e)

        f_priv.put_mpint(private_numbers.d)
        f_priv.put_mpint(private_numbers.iqmp)
        f_priv.put_mpint(private_numbers.p)
        f_priv.put_mpint(private_numbers.q)


class _SSHFormatDSA:
    """Format for DSA keys.

    Public:
        mpint p, q, g, y
    Private:
        mpint p, q, g, y, x
    """

    def get_public(self, data: memoryview) -> tuple[tuple, memoryview]:
        """DSA public fields"""
        p, data = _get_mpint(data)
        q, data = _get_mpint(data)
        g, data = _get_mpint(data)
        y, data = _get_mpint(data)
        return (p, q, g, y), data

    def load_public(
        self, data: memoryview
    ) -> tuple[dsa.DSAPublicKey, memoryview]:
        """Make DSA public key from data."""
        (p, q, g, y), data = self.get_public(data)
        parameter_numbers = dsa.DSAParameterNumbers(p, q, g)
        public_numbers = dsa.DSAPublicNumbers(y, parameter_numbers)
        self._validate(public_numbers)
        public_key = public_numbers.public_key()
        return public_key, data

    def load_private(
        self, data: memoryview, pubfields, unsafe_skip_rsa_key_validation: bool
    ) -> tuple[dsa.DSAPrivateKey, memoryview]:
        """Make DSA private key from data."""
        (p, q, g, y), data = self.get_public(data)
        x, data = _get_mpint(data)

        if (p, q, g, y) != pubfields:
            raise ValueError("Corrupt data: dsa field mismatch")
        parameter_numbers = dsa.DSAParameterNumbers(p, q, g)
        public_numbers = dsa.DSAPublicNumbers(y, parameter_numbers)
        self._validate(public_numbers)
        private_numbers = dsa.DSAPrivateNumbers(x, public_numbers)
        private_key = private_numbers.private_key()
        return private_key, data

    def encode_public(
        self, public_key: dsa.DSAPublicKey, f_pub: _FragList
    ) -> None:
        """Write DSA public key"""
        public_numbers = public_key.public_numbers()
        parameter_numbers = public_numbers.parameter_numbers
        self._validate(public_numbers)

        f_pub.put_mpint(parameter_numbers.p)
        f_pub.put_mpint(parameter_numbers.q)
        f_pub.put_mpint(parameter_numbers.g)
        f_pub.put_mpint(public_numbers.y)

    def encode_private(
        self, private_key: dsa.DSAPrivateKey, f_priv: _FragList
    ) -> None:
        """Write DSA private key"""
        self.encode_public(private_key.public_key(), f_priv)
        f_priv.put_mpint(private_key.private_numbers().x)

    def _validate(self, public_numbers: dsa.DSAPublicNumbers) -> None:
        parameter_numbers = public_numbers.parameter_numbers
        if parameter_numbers.p.bit_length() != 1024:
            raise ValueError("SSH supports only 1024 bit DSA keys")


class _SSHFormatECDSA:
    """Format for ECDSA keys.

    Public:
        str curve
        bytes point
    Private:
        str curve
        bytes point
        mpint secret
    """

    def __init__(self, ssh_curve_name: bytes, curve: ec.EllipticCurve):
        self.ssh_curve_name = ssh_curve_name
        self.curve = curve

    def get_public(
        self, data: memoryview
    ) -> tuple[tuple[memoryview, memoryview], memoryview]:
        """ECDSA public fields"""
        curve, data = _get_sshstr(data)
        point, data = _get_sshstr(data)
        if curve != self.ssh_curve_name:
            raise ValueError("Curve name mismatch")
        if len(point) == 0:
            raise ValueError("Invalid EC point: empty data")
        if point[0] != 4:
            raise NotImplementedError("Need uncompressed point")
        return (curve, point), data

    def load_public(
        self, data: memoryview
    ) -> tuple[ec.EllipticCurvePublicKey, memoryview]:
        """Make ECDSA public key from data."""
        (_, point), data = self.get_public(data)
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            self.curve, point.tobytes()
        )
        return public_key, data

    def load_private(
        self, data: memoryview, pubfields, unsafe_skip_rsa_key_validation: bool
    ) -> tuple[ec.EllipticCurvePrivateKey, memoryview]:
        """Make ECDSA private key from data."""
        (curve_name, point), data = self.get_public(data)
        secret, data = _get_mpint(data)

        if (curve_name, point) != pubfields:
            raise ValueError("Corrupt data: ecdsa field mismatch")
        private_key = ec.derive_private_key(secret, self.curve)
        return private_key, data

    def encode_public(
        self, public_key: ec.EllipticCurvePublicKey, f_pub: _FragList
    ) -> None:
        """Write ECDSA public key"""
        point = public_key.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        f_pub.put_sshstr(self.ssh_curve_name)
        f_pub.put_sshstr(point)

    def encode_private(
        self, private_key: ec.EllipticCurvePrivateKey, f_priv: _FragList
    ) -> None:
        """Write ECDSA private key"""
        public_key = private_key.public_key()
        private_numbers = private_key.private_numbers()

        self.encode_public(public_key, f_priv)
        f_priv.put_mpint(private_numbers.private_value)


class _SSHFormatEd25519:
    """Format for Ed25519 keys.

    Public:
        bytes point
    Private:
        bytes point
        bytes secret_and_point
    """

    def get_public(
        self, data: memoryview
    ) -> tuple[tuple[memoryview], memoryview]:
        """Ed25519 public fields"""
        point, data = _get_sshstr(data)
        return (point,), data

    def load_public(
        self, data: memoryview
    ) -> tuple[ed25519.Ed25519PublicKey, memoryview]:
        """Make Ed25519 public key from data."""
        (point,), data = self.get_public(data)
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(
            point.tobytes()
        )
        return public_key, data

    def load_private(
        self, data: memoryview, pubfields, unsafe_skip_rsa_key_validation: bool
    ) -> tuple[ed25519.Ed25519PrivateKey, memoryview]:
        """Make Ed25519 private key from data."""
        (point,), data = self.get_public(data)
        keypair, data = _get_sshstr(data)

        secret = keypair[:32]
        point2 = keypair[32:]
        if point != point2 or (point,) != pubfields:
            raise ValueError("Corrupt data: ed25519 field mismatch")
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(secret)
        return private_key, data

    def encode_public(
        self, public_key: ed25519.Ed25519PublicKey, f_pub: _FragList
    ) -> None:
        """Write Ed25519 public key"""
        raw_public_key = public_key.public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        f_pub.put_sshstr(raw_public_key)

    def encode_private(
        self, private_key: ed25519.Ed25519PrivateKey, f_priv: _FragList
    ) -> None:
        """Write Ed25519 private key"""
        public_key = private_key.public_key()
        raw_private_key = private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )
        raw_public_key = public_key.public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        f_keypair = _FragList([raw_private_key, raw_public_key])

        self.encode_public(public_key, f_priv)
        f_priv.put_sshstr(f_keypair)


def load_application(data) -> tuple[memoryview, memoryview]:
    """
    U2F application strings
    """
    application, data = _get_sshstr(data)
    if not application.tobytes().startswith(b"ssh:"):
        raise ValueError(
            "U2F application string does not start with b'ssh:' "
            f"({application})"
        )
    return application, data


class _SSHFormatSKEd25519:
    """
    The format of a sk-ssh-ed25519@openssh.com public key is:

        string		"sk-ssh-ed25519@openssh.com"
        string		public key
        string		application (user-specified, but typically "ssh:")
    """

    def load_public(
        self, data: memoryview
    ) -> tuple[ed25519.Ed25519PublicKey, memoryview]:
        """Make Ed25519 public key from data."""
        public_key, data = _lookup_kformat(_SSH_ED25519).load_public(data)
        _, data = load_application(data)
        return public_key, data

    def get_public(self, data: memoryview) -> typing.NoReturn:
        # Confusingly `get_public` is an entry point used by private key
        # loading.
        raise UnsupportedAlgorithm(
            "sk-ssh-ed25519 private keys cannot be loaded"
        )


class _SSHFormatSKECDSA:
    """
    The format of a sk-ecdsa-sha2-nistp256@openssh.com public key is:

        string		"sk-ecdsa-sha2-nistp256@openssh.com"
        string		curve name
        ec_point	Q
        string		application (user-specified, but typically "ssh:")
    """

    def load_public(
        self, data: memoryview
    ) -> tuple[ec.EllipticCurvePublicKey, memoryview]:
        """Make ECDSA public key from data."""
        public_key, data = _lookup_kformat(_ECDSA_NISTP256).load_public(data)
        _, data = load_application(data)
        return public_key, data

    def get_public(self, data: memoryview) -> typing.NoReturn:
        # Confusingly `get_public` is an entry point used by private key
        # loading.
        raise UnsupportedAlgorithm(
            "sk-ecdsa-sha2-nistp256 private keys cannot be loaded"
        )


_KEY_FORMATS = {
    _SSH_RSA: _SSHFormatRSA(),
    _SSH_DSA: _SSHFormatDSA(),
    _SSH_ED25519: _SSHFormatEd25519(),
    _ECDSA_NISTP256: _SSHFormatECDSA(b"nistp256", ec.SECP256R1()),
    _ECDSA_NISTP384: _SSHFormatECDSA(b"nistp384", ec.SECP384R1()),
    _ECDSA_NISTP521: _SSHFormatECDSA(b"nistp521", ec.SECP521R1()),
    _SK_SSH_ED25519: _SSHFormatSKEd25519(),
    _SK_SSH_ECDSA_NISTP256: _SSHFormatSKECDSA(),
}


def _lookup_kformat(key_type: utils.Buffer):
    """Return valid format or throw error"""
    if not isinstance(key_type, bytes):
        key_type = memoryview(key_type).tobytes()
    if key_type in _KEY_FORMATS:
        return _KEY_FORMATS[key_type]
    raise UnsupportedAlgorithm(f"Unsupported key type: {key_type!r}")


SSHPrivateKeyTypes = typing.Union[
    ec.EllipticCurvePrivateKey,
    rsa.RSAPrivateKey,
    dsa.DSAPrivateKey,
    ed25519.Ed25519PrivateKey,
]


def load_ssh_private_key(
    data: utils.Buffer,
    password: bytes | None,
    backend: typing.Any = None,
    *,
    unsafe_skip_rsa_key_validation: bool = False,
) -> SSHPrivateKeyTypes:
    """Load private key from OpenSSH custom encoding."""
    utils._check_byteslike("data", data)
    if password is not None:
        utils._check_bytes("password", password)

    m = _PEM_RC.search(data)
    if not m:
        raise ValueError("Not OpenSSH private key format")
    p1 = m.start(1)
    p2 = m.end(1)
    data = binascii.a2b_base64(memoryview(data)[p1:p2])
    if not data.startswith(_SK_MAGIC):
        raise ValueError("Not OpenSSH private key format")
    data = memoryview(data)[len(_SK_MAGIC) :]

    # parse header
    ciphername, data = _get_sshstr(data)
    kdfname, data = _get_sshstr(data)
    kdfoptions, data = _get_sshstr(data)
    nkeys, data = _get_u32(data)
    if nkeys != 1:
        raise ValueError("Only one key supported")

    # load public key data
    pubdata, data = _get_sshstr(data)
    pub_key_type, pubdata = _get_sshstr(pubdata)
    kformat = _lookup_kformat(pub_key_type)
    pubfields, pubdata = kformat.get_public(pubdata)
    _check_empty(pubdata)

    if ciphername != _NONE or kdfname != _NONE:
        ciphername_bytes = ciphername.tobytes()
        if ciphername_bytes not in _SSH_CIPHERS:
            raise UnsupportedAlgorithm(
                f"Unsupported cipher: {ciphername_bytes!r}"
            )
        if kdfname != _BCRYPT:
            raise UnsupportedAlgorithm(f"Unsupported KDF: {kdfname!r}")
        blklen = _SSH_CIPHERS[ciphername_bytes].block_len
        tag_len = _SSH_CIPHERS[ciphername_bytes].tag_len
        # load secret data
        edata, data = _get_sshstr(data)
        # see https://bugzilla.mindrot.org/show_bug.cgi?id=3553 for
        # information about how OpenSSH handles AEAD tags
        if _SSH_CIPHERS[ciphername_bytes].is_aead:
            tag = bytes(data)
            if len(tag) != tag_len:
                raise ValueError("Corrupt data: invalid tag length for cipher")
        else:
            _check_empty(data)
        _check_block_size(edata, blklen)
        salt, kbuf = _get_sshstr(kdfoptions)
        rounds, kbuf = _get_u32(kbuf)
        _check_empty(kbuf)
        ciph = _init_cipher(ciphername_bytes, password, salt.tobytes(), rounds)
        dec = ciph.decryptor()
        edata = memoryview(dec.update(edata))
        if _SSH_CIPHERS[ciphername_bytes].is_aead:
            assert isinstance(dec, AEADDecryptionContext)
            _check_empty(dec.finalize_with_tag(tag))
        else:
            # _check_block_size requires data to be a full block so there
            # should be no output from finalize
            _check_empty(dec.finalize())
    else:
        if password:
            raise TypeError(
                "Password was given but private key is not encrypted."
            )
        # load secret data
        edata, data = _get_sshstr(data)
        _check_empty(data)
        blklen = 8
        _check_block_size(edata, blklen)
    ck1, edata = _get_u32(edata)
    ck2, edata = _get_u32(edata)
    if ck1 != ck2:
        raise ValueError("Corrupt data: broken checksum")

    # load per-key struct
    key_type, edata = _get_sshstr(edata)
    if key_type != pub_key_type:
        raise ValueError("Corrupt data: key type mismatch")
    private_key, edata = kformat.load_private(
        edata,
        pubfields,
        unsafe_skip_rsa_key_validation=unsafe_skip_rsa_key_validation,
    )
    # We don't use the comment
    _, edata = _get_sshstr(edata)

    # yes, SSH does padding check *after* all other parsing is done.
    # need to follow as it writes zero-byte padding too.
    if edata != _PADDING[: len(edata)]:
        raise ValueError("Corrupt data: invalid padding")

    if isinstance(private_key, dsa.DSAPrivateKey):
        warnings.warn(
            "SSH DSA keys are deprecated and will be removed in a future "
            "release.",
            utils.DeprecatedIn40,
            stacklevel=2,
        )

    return private_key


def _serialize_ssh_private_key(
    private_key: SSHPrivateKeyTypes,
    password: bytes,
    encryption_algorithm: KeySerializationEncryption,
) -> bytes:
    """Serialize private key with OpenSSH custom encoding."""
    utils._check_bytes("password", password)
    if isinstance(private_key, dsa.DSAPrivateKey):
        warnings.warn(
            "SSH DSA key support is deprecated and will be "
            "removed in a future release",
            utils.DeprecatedIn40,
            stacklevel=4,
        )

    key_type = _get_ssh_key_type(private_key)
    kformat = _lookup_kformat(key_type)

    # setup parameters
    f_kdfoptions = _FragList()
    if password:
        ciphername = _DEFAULT_CIPHER
        blklen = _SSH_CIPHERS[ciphername].block_len
        kdfname = _BCRYPT
        rounds = _DEFAULT_ROUNDS
        if (
            isinstance(encryption_algorithm, _KeySerializationEncryption)
            and encryption_algorithm._kdf_rounds is not None
        ):
            rounds = encryption_algorithm._kdf_rounds
        salt = os.urandom(16)
        f_kdfoptions.put_sshstr(salt)
        f_kdfoptions.put_u32(rounds)
        ciph = _init_cipher(ciphername, password, salt, rounds)
    else:
        ciphername = kdfname = _NONE
        blklen = 8
        ciph = None
    nkeys = 1
    checkval = os.urandom(4)
    comment = b""

    # encode public and private parts together
    f_public_key = _FragList()
    f_public_key.put_sshstr(key_type)
    kformat.encode_public(private_key.public_key(), f_public_key)

    f_secrets = _FragList([checkval, checkval])
    f_secrets.put_sshstr(key_type)
    kformat.encode_private(private_key, f_secrets)
    f_secrets.put_sshstr(comment)
    f_secrets.put_raw(_PADDING[: blklen - (f_secrets.size() % blklen)])

    # top-level structure
    f_main = _FragList()
    f_main.put_raw(_SK_MAGIC)
    f_main.put_sshstr(ciphername)
    f_main.put_sshstr(kdfname)
    f_main.put_sshstr(f_kdfoptions)
    f_main.put_u32(nkeys)
    f_main.put_sshstr(f_public_key)
    f_main.put_sshstr(f_secrets)

    # copy result info bytearray
    slen = f_secrets.size()
    mlen = f_main.size()
    buf = memoryview(bytearray(mlen + blklen))
    f_main.render(buf)
    ofs = mlen - slen

    # encrypt in-place
    if ciph is not None:
        ciph.encryptor().update_into(buf[ofs:mlen], buf[ofs:])

    return _ssh_pem_encode(buf[:mlen])


SSHPublicKeyTypes = typing.Union[
    ec.EllipticCurvePublicKey,
    rsa.RSAPublicKey,
    dsa.DSAPublicKey,
    ed25519.Ed25519PublicKey,
]

SSHCertPublicKeyTypes = typing.Union[
    ec.EllipticCurvePublicKey,
    rsa.RSAPublicKey,
    ed25519.Ed25519PublicKey,
]


class SSHCertificateType(enum.Enum):
    USER = 1
    HOST = 2


class SSHCertificate:
    def __init__(
        self,
        _nonce: memoryview,
        _public_key: SSHPublicKeyTypes,
        _serial: int,
        _cctype: int,
        _key_id: memoryview,
        _valid_principals: list[bytes],
        _valid_after: int,
        _valid_before: int,
        _critical_options: dict[bytes, bytes],
        _extensions: dict[bytes, bytes],
        _sig_type: memoryview,
        _sig_key: memoryview,
        _inner_sig_type: memoryview,
        _signature: memoryview,
        _tbs_cert_body: memoryview,
        _cert_key_type: bytes,
        _cert_body: memoryview,
    ):
        self._nonce = _nonce
        self._public_key = _public_key
        self._serial = _serial
        try:
            self._type = SSHCertificateType(_cctype)
        except ValueError:
            raise ValueError("Invalid certificate type")
        self._key_id = _key_id
        self._valid_principals = _valid_principals
        self._valid_after = _valid_after
        self._valid_before = _valid_before
        self._critical_options = _critical_options
        self._extensions = _extensions
        self._sig_type = _sig_type
        self._sig_key = _sig_key
        self._inner_sig_type = _inner_sig_type
        self._signature = _signature
        self._cert_key_type = _cert_key_type
        self._cert_body = _cert_body
        self._tbs_cert_body = _tbs_cert_body

    @property
    def nonce(self) -> bytes:
        return bytes(self._nonce)

    def public_key(self) -> SSHCertPublicKeyTypes:
        # make mypy happy until we remove DSA support entirely and
        # the underlying union won't have a disallowed type
        return typing.cast(SSHCertPublicKeyTypes, self._public_key)

    @property
    def serial(self) -> int:
        return self._serial

    @property
    def type(self) -> SSHCertificateType:
        return self._type

    @property
    def key_id(self) -> bytes:
        return bytes(self._key_id)

    @property
    def valid_principals(self) -> list[bytes]:
        return self._valid_principals

    @property
    def valid_before(self) -> int:
        return self._valid_before

    @property
    def valid_after(self) -> int:
        return self._valid_after

    @property
    def critical_options(self) -> dict[bytes, bytes]:
        return self._critical_options

    @property
    def extensions(self) -> dict[bytes, bytes]:
        return self._extensions

    def signature_key(self) -> SSHCertPublicKeyTypes:
        sigformat = _lookup_kformat(self._sig_type)
        signature_key, sigkey_rest = sigformat.load_public(self._sig_key)
        _check_empty(sigkey_rest)
        return signature_key

    def public_bytes(self) -> bytes:
        return (
            bytes(self._cert_key_type)
            + b" "
            + binascii.b2a_base64(bytes(self._cert_body), newline=False)
        )

    def verify_cert_signature(self) -> None:
        signature_key = self.signature_key()
        if isinstance(signature_key, ed25519.Ed25519PublicKey):
            signature_key.verify(
                bytes(self._signature), bytes(self._tbs_cert_body)
            )
        elif isinstance(signature_key, ec.EllipticCurvePublicKey):
            # The signature is encoded as a pair of big-endian integers
            r, data = _get_mpint(self._signature)
            s, data = _get_mpint(data)
            _check_empty(data)
