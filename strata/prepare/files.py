"""Small things preparers do to files."""

import hashlib
from pathlib import Path

#: Read in pieces, so a video is never held whole in memory.
CHUNK = 1 << 20


def checksum_of(path: Path) -> str:
    """The sha256 of a file's bytes, the way a catalog addresses a sample.

    For naming what a preparer writes after what it read, so a renamed
    source prepares to the same place (``docs/adr/0033``).
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["checksum_of"]
