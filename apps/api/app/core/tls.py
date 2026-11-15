"""TLS certificate fingerprint pinning for the Proxmox VE / PBS API clients.

Self-signed Proxmox installs are common. Instead of `verify=False`, the
operator pins the SHA-256 fingerprint of the server certificate. The check
runs inside the TLS handshake of *every* connection (`wrap_socket`), before a
single HTTP byte — and therefore before the API token — is sent. A mismatch
aborts the connection with `ssl.SSLCertVerificationError`.

httpx/httpcore (sync) call `ssl_context.wrap_socket()` with the default
`do_handshake_on_connect=True`, so the peer certificate is available as soon
as `wrap_socket` returns.
"""

from __future__ import annotations

import hashlib
import ssl
from typing import Any

FINGERPRINT_HEX_LENGTH = 64


class FingerprintFormatError(ValueError):
    pass


def normalize_fingerprint(value: str | None) -> str:
    """'ab:cd…' / 'ABCD…' → 'AB:CD:…' (empty stays empty)."""
    cleaned = (value or "").strip().replace(":", "").replace(" ", "").upper()
    if cleaned and (
        len(cleaned) != FINGERPRINT_HEX_LENGTH or any(char not in "0123456789ABCDEF" for char in cleaned)
    ):
        raise FingerprintFormatError("TLS fingerprint must be a SHA-256 hex digest (64 hex digits, colons allowed).")
    return ":".join(cleaned[index : index + 2] for index in range(0, len(cleaned), 2))


def certificate_fingerprint(der_certificate: bytes) -> str:
    return normalize_fingerprint(hashlib.sha256(der_certificate).hexdigest())


class PinnedSSLContext(ssl.SSLContext):
    """SSLContext that checks (and optionally records) the peer certificate fingerprint."""

    pinned_fingerprint: str = ""
    observed: list[str] | None = None

    def wrap_socket(self, *args: Any, **kwargs: Any) -> ssl.SSLSocket:  # type: ignore[override]
        sock = super().wrap_socket(*args, **kwargs)
        der = sock.getpeercert(binary_form=True)
        if der is None:
            sock.close()
            raise ssl.SSLCertVerificationError("Server presented no TLS certificate.")
        fingerprint = certificate_fingerprint(der)
        if self.observed is not None:
            self.observed.append(fingerprint)
        if self.pinned_fingerprint and fingerprint != self.pinned_fingerprint:
            sock.close()
            raise ssl.SSLCertVerificationError(
                f"TLS fingerprint mismatch: server presents {fingerprint}, pinned {self.pinned_fingerprint}."
            )
        return sock


def build_tls_verify(
    verify_ssl: bool,
    fingerprint: str | None,
    *,
    observed: list[str] | None = None,
) -> bool | ssl.SSLContext:
    """Value for httpx `verify=`.

    * pinned fingerprint → PinnedSSLContext (CA chain also checked if verify_ssl).
    * observed list given → context that records the fingerprint it sees.
    * otherwise → plain bool (legacy behaviour, unchanged).
    """
    pinned = normalize_fingerprint(fingerprint)
    if not pinned and observed is None:
        return verify_ssl

    context = PinnedSSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if verify_ssl:
        context.load_default_certs()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
    else:
        # The fingerprint replaces CA validation for self-signed certificates.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    context.pinned_fingerprint = pinned
    context.observed = observed
    return context
