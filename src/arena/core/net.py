"""One HTTP opener that never follows a redirect, for every request that carries a credential.

`urllib.request.urlopen` follows 301, 302 and 303, and re-sends the caller's headers to whatever
host the response names. A request that carries an `Authorization` header or an API key therefore
hands that credential to a host the operator never configured. Every arena request that carries a
credential uses `no_redirect_opener()` so a redirect surfaces as an `HTTPError` instead.
"""

from __future__ import annotations

import urllib.request
from typing import Any


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect. Returning `None` makes the response surface as an `HTTPError`."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def no_redirect_opener(*handlers: Any) -> urllib.request.OpenerDirector:
    """An opener that refuses redirects, with any extra handlers installed alongside."""
    return urllib.request.build_opener(NoRedirect, *handlers)
