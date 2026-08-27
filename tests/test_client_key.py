"""Who the rate limiter thinks you are, with a proxy in front.

Behind the tunnel every connection arrives from localhost, so keying on the
socket address puts the whole team in one bucket: a handful of bad logins from
any one person locks out all of them. That is a self-inflicted denial of
service, and it would look like the app being broken rather than like a rate
limiter doing its job.

`CF-Connecting-IP` is the answer here, because Cloudflare rewrites it at the
edge and a client cannot dictate it through the tunnel. `X-Forwarded-For` is the
fallback for any other front, and it is partly written by the caller -- so
reading the wrong end of it hands out a free bypass.
"""

from __future__ import annotations

import pytest
from starlette.datastructures import Headers

from ads.api.auth import _client_key


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host


class _Request:
    """Enough of a Request for the function under test."""

    def __init__(self, headers: dict[str, str], client: str | None = "10.0.0.1") -> None:
        self.headers = Headers(headers)
        self.client = _Client(client) if client else None


def test_the_forwarded_address_is_preferred_over_the_socket() -> None:
    key = _client_key(_Request({"x-forwarded-for": "203.0.113.7"}))
    assert key == "203.0.113.7"


def test_the_last_hop_wins_not_the_first() -> None:
    """The edge appends the peer it saw, so the rightmost entry is the one it
    wrote. The leftmost is whatever the caller sent."""
    request = _Request({"x-forwarded-for": "198.51.100.9, 203.0.113.7"})
    assert _client_key(request) == "203.0.113.7"


def test_a_spoofed_prefix_cannot_move_the_bucket() -> None:
    """The bypass this ordering exists to prevent: if a caller could shift its
    own key by rotating a header it controls, the limiter counts to five
    forever and never locks anything."""
    keys = {
        _client_key(_Request({"x-forwarded-for": f"{attempt}, 203.0.113.7"}))
        for attempt in ("1.1.1.1", "2.2.2.2", "3.3.3.3", "evil")
    }
    assert keys == {"203.0.113.7"}, "attacker-supplied text must not change the key"


def test_two_callers_behind_one_proxy_get_separate_buckets() -> None:
    """The lockout-everybody failure, stated as a test."""
    first = _client_key(_Request({"x-forwarded-for": "203.0.113.7"}))
    second = _client_key(_Request({"x-forwarded-for": "203.0.113.8"}))
    assert first != second


@pytest.mark.parametrize("header", ["", "   ", ",", " , "])
def test_an_empty_or_malformed_header_falls_back_to_the_socket(header: str) -> None:
    assert _client_key(_Request({"x-forwarded-for": header})) == "10.0.0.1"


def test_whitespace_around_hops_is_ignored() -> None:
    request = _Request({"x-forwarded-for": "  198.51.100.9 ,  203.0.113.7  "})
    assert _client_key(request) == "203.0.113.7"


def test_no_header_uses_the_socket_address() -> None:
    assert _client_key(_Request({})) == "10.0.0.1"


def test_no_header_and_no_client_is_still_a_usable_key() -> None:
    """A missing client happens with some ASGI transports; returning None here
    would make it an unhashable dict key at the call site."""
    assert _client_key(_Request({}, client=None)) == "unknown"


def test_the_cloudflare_header_wins_when_present() -> None:
    """Cloudflare rewrites it at the edge, so it is the one value in the request
    a caller through the tunnel cannot choose."""
    request = _Request({"cf-connecting-ip": "9.9.9.9", "x-forwarded-for": "203.0.113.7"})
    assert _client_key(request) == "9.9.9.9"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_cloudflare_header_falls_through(blank: str) -> None:
    """An empty header would otherwise become a single shared bucket named ''."""
    request = _Request({"cf-connecting-ip": blank, "x-forwarded-for": "203.0.113.7"})
    assert _client_key(request) == "203.0.113.7"
