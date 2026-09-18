"""Getting raw data into the shape a catalog takes.

A preparer reads what a corpus arrives as — a video, a mailbox, a folder —
and writes files a sample type admits, with a prepared index naming each
and what is known about it. A catalog ingests that index and nothing else;
this package never imports one, and one never imports this. See
``docs/adr/0040``.

**May import:** ``contracts``, ``common``, the standard library.

**May not import:** ``strata.catalog``, ``modelling``, ``labeller``, Label
Studio, or any ML framework. A preparer that needs a decoder carries it as
its own plugin.
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

#: Modules a sibling may import by path beyond what is exported here
#: (``docs/adr/0015``): the registry, whose ``entries`` tests patch; the
#: folder preparers; what preparers share; the conformance suite, behind
#: the test extra.
PUBLIC_MODULES = frozenset({"conformance", "files", "folder", "preparers"})

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
