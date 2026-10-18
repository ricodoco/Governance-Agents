"""Text from channels the system does not trust (a vendor note, a message without a token, names inside a token chain).
Provenance is set by the channel code, never by anything the text says: wrap untrusted text in Untrusted() where it enters the system. The brief renderers then strip it from menus and
show it elsewhere only as sanitized, labeled text. sanitize_untrusted() makes any string one line with no double quotes, so it cannot close its container or start a line that looks like a system line."""
from __future__ import annotations

MAX_UNTRUSTED = 300


def sanitize_untrusted(text, cap: int = MAX_UNTRUSTED) -> str:
    return " ".join(str(text).replace('"', "'").split())[:cap]


class Untrusted(str):
    """A string that came from a channel the system does not trust."""
    __slots__ = ()
