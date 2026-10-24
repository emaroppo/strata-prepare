"""Getting raw data into the shape a catalog takes.

A preparer writes files a sample type admits and an index naming each; a
chain of stages (:mod:`.chain`) is run by the catalog. Nothing here imports
a catalog. See ``docs/adr/0040``, ``docs/adr/0046`` and ``docs/adr/0047``.
"""

from .folder import FramesFolder, ImageFolder, TextFolder
from .preparers import (
    Prepared,
    Preparer,
    PreparerError,
    available,
    for_source,
    load_index,
    resolve,
    run,
    save_index,
)

#: Modules a sibling may import by path beyond the exports. docs/adr/0015
PUBLIC_MODULES = frozenset({"chain", "conformance", "files", "folder", "preparers"})

__all__ = [
    "FramesFolder",
    "ImageFolder",
    "Prepared",
    "Preparer",
    "PreparerError",
    "TextFolder",
    "available",
    "for_source",
    "load_index",
    "resolve",
    "run",
    "save_index",
]
