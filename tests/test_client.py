"""Example client allows plaintext HTTP only on loopback."""

import pytest

from mcp_client import require_secure_url


@pytest.mark.parametrize(
    'url',
    [
        'https://example.com/mcp',
        'https://example.com:443/mcp',
        'http://127.0.0.1:8000/mcp',
        'http://localhost/mcp',
        'http://[::1]:8000/mcp',
    ],
)
def test_secure_urls_allowed(url):
    require_secure_url(url)


@pytest.mark.parametrize(
    'url',
    [
        'http://example.com/mcp',
        'http://127.0.0.2/mcp',
        'ftp://localhost/mcp',
    ],
)
def test_insecure_urls_rejected(url):
    with pytest.raises(ValueError):
        require_secure_url(url)
