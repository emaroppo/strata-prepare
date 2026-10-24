"""Getting a corpus into the shape a sample type declares.

A preparer writes files a type admits and an index of what it knew about
each, and stops; a catalog ingests what the index names. Each ships as its
own distribution. See ``docs/adr/0033`` and ``docs/adr/0040``.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import ClassVar

from pydantic import ValidationError

from strata.common import plugins
from strata.contracts import (
    PREPARED_FORMAT,
    PREPARED_NAME,
    AnyValue,
    PreparedError,
    PreparedFormatError,
    PreparedIndex,
    PreparedSample,
)

#: Where a distribution advertises the preparers it provides.
ENTRY_POINT_GROUP = "strata.preparers"


class PreparerError(Exception):
    """A preparer that cannot be found, or cannot be run on what it was given."""


@dataclass(frozen=True)
class Prepared:
    """One sample a preparer wrote, and what it knew about it."""

    #: The file written, inside the output directory.
    path: Path
    #: Recorded on the sample at ingest, checked against the type's ``Metadata``. docs/adr/0040
    metadata: dict = field(default_factory=dict)
    #: An annotation the corpus arrived with: a candidate, never an answer. docs/adr/0028
    value: AnyValue | None = None


class Preparer:
    """A conversion from some source format into one sample type's shape."""

    #: How a project names this preparer.
    name: ClassVar[str] = ""

    #: The sample type this produces, by name; the output has to satisfy it. docs/adr/0033
    produces: ClassVar[str] = ""

    #: Extensions this consumes, lowercase and without the dot.
    sources: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        bad = [e for e in cls.sources if e != e.lower().lstrip(".")]
        if bad:
            raise PreparerError(
                f"{cls.__name__} lists source extension(s) {sorted(bad)} with a "
                f"dot or capitals. They are matched against a lowercased "
                f"suffix, so these would never match anything."
            )

    def allows(self, path: Path) -> bool:
        """Whether this preparer admits a source file, by extension."""
        suffix = Path(path).suffix.lower().lstrip(".")
        return bool(suffix) and suffix in self.sources

    def report(self) -> dict[str, int]:
        """What this preparer did not carry across, counted. See ``docs/adr/0036``."""
        return {}

    def prepare(self, source: Path, out_dir: Path, *, root: Path) -> Iterable[Prepared]:
        """Turn one source file into the samples it holds, written under ``out_dir``.

        ``root`` is the source directory ``source`` sits under. A re-run
        must produce the same bytes at the same paths. See ``docs/adr/0010``.
        """
        raise NotImplementedError


# ----------------------------------------------------------------------
# The index on disk
# ----------------------------------------------------------------------


def load_index(out_dir: Path) -> PreparedIndex | None:
    """The index in ``out_dir``, or None where nothing was prepared there yet.

    One that cannot be read is refused rather than replaced. See ``docs/adr/0036``.
    """
    path = Path(out_dir) / PREPARED_NAME
    if not path.exists():
        return None
    try:
        return PreparedIndex.from_json(path.read_text(encoding="utf-8"))
    except PreparedFormatError as e:
        raise PreparerError(
            f"{path}: {e} It cannot be added to, so remove it and prepare the whole source."
        ) from None
    except (ValidationError, ValueError) as e:
        raise PreparerError(
            f"{path} cannot be read ({str(e).strip().splitlines()[0]}). Preparing into "
            f"it would forget every file it names; remove it, and prepare the whole source."
        ) from None


def save_index(index: PreparedIndex, out_dir: Path) -> Path:
    path = Path(out_dir) / PREPARED_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index.to_json(), encoding="utf-8")
    return path


def relative_key(path: Path, root: Path) -> str:
    """How a file is named in the index: relative to the root, posix-style.

    A file outside the root is refused. See ``docs/adr/0033``.
    """
    path, root = Path(path), Path(root)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise PreparerError(f"{path} is outside {root}, where it was to be written.") from None


# ----------------------------------------------------------------------
# Running one
# ----------------------------------------------------------------------


def run(
    preparer: Preparer,
    sources: Iterable[Path],
    out_dir: Path,
    *,
    root: Path | None = None,
    on_source=None,
) -> PreparedIndex:
    """Convert ``sources`` into ``out_dir``, and record what was written.

    ``root`` is the directory the sources are under; each source's own
    directory where none is given. The index is merged with whatever is
    already there rather than replacing it. See ``docs/adr/0033``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = load_index(out_dir)

    written: dict[str, PreparedSample] = {}
    for source in sources:
        source = Path(source)
        if not preparer.allows(source):
            raise PreparerError(
                f"{type(preparer).__name__} does not admit {source.name} "
                f"(it reads: {', '.join('.' + e for e in sorted(preparer.sources))})"
            )
        under = Path(root) if root is not None else source.parent
        if not source.resolve().is_relative_to(under.resolve()):
            raise PreparerError(f"{source} is not under {under}, the root it was given.")
        for prepared in preparer.prepare(source, out_dir, root=under):
            written[relative_key(prepared.path, out_dir)] = PreparedSample(
                metadata=prepared.metadata,
                value=prepared.value,
            )
        if on_source is not None:
            on_source(source)

    index = PreparedIndex(
        version=PREPARED_FORMAT,
        type=type(preparer).produces,
        produced_by=type(preparer).name,
        samples=written,
    )
    if existing is not None:
        try:
            index = existing.merge(index)
        except PreparedError as e:
            raise PreparerError(str(e)) from None
    save_index(index, out_dir)
    return index


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------


def entries():
    """What is installed, as entry points. The seam a test stubs to pretend otherwise."""
    return list(entry_points(group=ENTRY_POINT_GROUP))


def available() -> dict[str, str]:
    """Registered preparer names, and what each resolves to."""
    return plugins.available(entries())


def resolve(name: str) -> type[Preparer]:
    """The class a preparer name refers to; a name registered twice is refused.

    See ``docs/adr/0033``.
    """
    entry = plugins.find(entries(), name, what="preparer", error=PreparerError)
    return plugins.load(entry, Preparer, error=PreparerError)


def for_source(produces: str, path: Path) -> type[Preparer]:
    """The preparer that turns this file into ``produces``; two candidates are
    refused, not picked. See ``docs/adr/0033``."""
    candidates = []
    for name in sorted(available()):
        try:
            cls = resolve(name)
        except PreparerError:
            # A plugin that fails to import is reported by resolve(), not here
            continue
        if cls.produces == produces and cls().allows(path):
            candidates.append(cls)

    if not candidates:
        known = ", ".join(sorted(available())) or "none"
        raise PreparerError(
            f"Nothing installed here prepares {Path(path).suffix or 'that'} files "
            f"into '{produces}' samples. Installed preparers: {known}."
        )
    if len(candidates) > 1:
        names = ", ".join(sorted(c.name for c in candidates))
        raise PreparerError(
            f"{len(candidates)} preparers turn {Path(path).suffix} files into "
            f"'{produces}' samples ({names}). Name one in [data] preparer."
        )
    return candidates[0]


__all__ = [
    "ENTRY_POINT_GROUP",
    "Prepared",
    "Preparer",
    "PreparerError",
    "available",
    "entries",
    "for_source",
    "load_index",
    "relative_key",
    "resolve",
    "run",
    "save_index",
]
