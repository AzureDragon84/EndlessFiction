import os
import re
import secrets
from typing import Optional

import base58

# Crockford base32, lowercase: no `i`, `l`, `o` or `u`, so `0/O` and `1/l/I`
# cannot be confused when a model or a human copies a handle by hand.
_TERM_HANDLE_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_TERM_HANDLE_LETTERS = "abcdefghjkmnpqrstvwxyz"
_TERM_HANDLE_SIZE = 8

# Reads are lenient (any of the 32 symbols, any position); the generator always
# starts with a letter so a handle never looks like a plain number.
TERM_HANDLE_PATTERN = re.compile(r"^t_[0-9a-hjkmnp-tv-z]{8}$")

_TERM_HANDLE_CONFUSABLES = str.maketrans({"o": "0", "i": "1", "l": "1"})


def generate_token() -> str:
    return base58.b58encode(os.urandom(7)).decode("ascii")


def generate_term_handle() -> str:
    """Short, immutable public id used in `data-g` markers, e.g. `t_7fk39m2q`."""
    suffix = secrets.choice(_TERM_HANDLE_LETTERS)
    suffix += "".join(secrets.choice(_TERM_HANDLE_ALPHABET) for _ in range(_TERM_HANDLE_SIZE - 1))
    return f"t_{suffix}"


def normalize_term_handle(value: Optional[str]) -> Optional[str]:
    """Repair a handle read back from a model or a URL; `None` when unrecoverable."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if not text.startswith("t_"):
        text = f"t_{text}"
    text = text.translate(_TERM_HANDLE_CONFUSABLES)
    return text if TERM_HANDLE_PATTERN.match(text) else None
