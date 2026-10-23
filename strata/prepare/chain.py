"""A preparation as a chain of stages, declared in a spec and run one input at a time.

A stage records facts about the occurrence it reads, or changes the body,
and nothing else: it never writes an annotation. Stages are plugins under
``strata.prepare_stages``, split by cardinality into transforms (one in,
one out), filters (one in, none or one out) and splits (one in, many out).
Each declares the kind it accepts and the kind it produces, so a chain in
the wrong order is refused before anything is read. The chain runs on each
input in memory, and only what its last stage yields leaves it.

Nothing here knows a catalog. The catalog hands :func:`run_chain` its
inputs as local files and stores what comes back; canonical form, the
sample's checksum and its rows are the catalog's. See ``docs/adr/0045``,
``docs/adr/0046`` and ``docs/adr/0047``.
"""

import hashlib
import tomllib
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from strata.common import plugins
from strata.common.canonical import canonical_bytes, content_hash
from strata.contracts import SampleTypeError
from strata.contracts import sample_types as types

#: Where a distribution advertises the stages it provides.
ENTRY_POINT_GROUP = "strata.prepare_stages"

#: What the first stage is handed: the input file's bytes, nothing known yet.
BYTES = "bytes"


class ChainError(Exception):
    """A spec that cannot be run, or a chain that failed on an input."""


# ----------------------------------------------------------------------
# What flows between stages
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Input:
    """One distinct raw file, as the catalog hands it over."""

    #: The raw checksum: the input's identity, and the origin row's input.
    checksum: str
    #: A local file holding the bytes. Named by checksum, and possibly
    #: without an extension (an Enron maildir file has none), so no stage
    #: may key on its suffix.
    path: Path
    #: Every ``collection/path`` naming these bytes, sorted. Context for a
    #: stage that wants it; none is privileged.
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    """A body, and the facts recorded so far about the occurrence it came from."""

    body: bytes
    facts: dict[str, Any] = field(default_factory=dict)

    def with_facts(self, **facts: Any) -> "Item":
        """This item with more facts. A fact already recorded keeps its value."""
        return replace(self, facts={**facts, **self.facts})


@dataclass(frozen=True)
class Drop:
    """An item a stage left out, and why, under a name the stage chooses."""

    reason: str


# ----------------------------------------------------------------------
# The stage contract
# ----------------------------------------------------------------------


class StageParams(BaseModel):
    """A stage's parameters. An unknown key is an error, not a no-op."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Stage[P: StageParams = StageParams]:
    """One step of a chain. Subclass :class:`Transform`, :class:`Filter` or :class:`Split`."""

    #: How a spec names this stage.
    name: ClassVar[str] = ""
    #: Changes whenever what the stage produces from the same input changes.
    #: Part of the chain's identity, so a new version is a different chain.
    version: ClassVar[str] = ""
    #: The kind of item this reads: ``bytes`` or a registered sample type.
    accepts: ClassVar[str] = BYTES
    #: The kind it hands on. A sample type name.
    produces: ClassVar[str] = ""

    #: The model a spec's keys for this stage are validated into. A stage
    #: with parameters subclasses :class:`StageParams`, names it here, and
    #: parameterises its base with it (``Filter[MyParams]``) so ``params``
    #: type-checks.
    Params: ClassVar[type[StageParams]] = StageParams

    def __init__(self, params: P | None = None):
        self.params: P = params if params is not None else type(self).Params()  # type: ignore[assignment]

    def rules(self) -> dict[str, str]:
        """Every rule file this stage reads, by name, as the sha256 of its bytes.

        A rule edited in place changes what the stage does without changing
        its version or the spec, so its digest is part of the chain's
        identity. See ``docs/adr/0045``.
        """
        return {}

    def _run(self, item: Item, source: Input) -> list[Item | Drop]:
        raise NotImplementedError


class Transform[P: StageParams = StageParams](Stage[P]):
    """One item in, one out."""

    def transform(self, item: Item, source: Input) -> Item:
        raise NotImplementedError

    def _run(self, item: Item, source: Input) -> list[Item | Drop]:
        return [self.transform(item, source)]


class Filter[P: StageParams = StageParams](Stage[P]):
    """One item in, the same item or a :class:`Drop` out. Counted either way."""

    def keep(self, item: Item, source: Input) -> Item | Drop:
        raise NotImplementedError

    def _run(self, item: Item, source: Input) -> list[Item | Drop]:
        return [self.keep(item, source)]


class Split[P: StageParams = StageParams](Stage[P]):
    """One item in, any number out, each of which may be a :class:`Drop`."""

    def split(self, item: Item, source: Input) -> Iterable[Item | Drop]:
        raise NotImplementedError

    def _run(self, item: Item, source: Input) -> list[Item | Drop]:
        return list(self.split(item, source))


# ----------------------------------------------------------------------
# The spec
# ----------------------------------------------------------------------


class StageRef(BaseModel):
    """A stage as a spec names it: its name, and every other key a parameter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _params_beside_the_name(cls, data: Any) -> Any:
        # Written as `[[stage]] name = "x"  key = value`, as a reader expects
        if isinstance(data, dict) and "params" not in data:
            rest = {k: v for k, v in data.items() if k != "name"}
            return {"name": data.get("name"), "params": rest}
        return data


class OutputDecl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    collection: str = Field(min_length=1)
    type: str = Field(min_length=1)


class Spec(BaseModel):
    """A preparation as written: a chain of stages, and what it writes into."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    stages: list[StageRef] = Field(alias="stage", min_length=1)
    output: OutputDecl

    @classmethod
    def from_toml(cls, text: str) -> "Spec":
        try:
            return cls.model_validate(tomllib.loads(text))
        except tomllib.TOMLDecodeError as e:
            raise ChainError(f"The spec is not TOML: {e}") from None
        except ValidationError as e:
            raise ChainError(f"The spec is not a preparation: {_first(e)}") from None

    @classmethod
    def read(cls, path: Path) -> "Spec":
        return cls.from_toml(Path(path).read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# A chain: a spec with its stages resolved and checked
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Chain:
    """A spec whose stages are installed, parameterised and in an order that fits."""

    spec: Spec
    stages: tuple[Stage, ...]
    #: Which distribution provided each stage, and at which version.
    producers: dict[str, dict[str, str]]
    #: Every stage's rule files, by stage, as digests. Read once, when the
    #: chain is loaded, so a run records the rules it ran with even if a
    #: file is edited while it runs.
    rules: dict[str, dict[str, str]]
    #: The same chain: the same spec, stage versions and rule files. What
    #: "the same spec" means when a run asks whether its inputs were already
    #: prepared. See ``docs/adr/0045``.
    identity: str

    @classmethod
    def load(cls, spec: Spec) -> "Chain":
        """Resolve every stage, validate its parameters, and check the kinds line up.

        Refused before any input is read: an unknown or doubly registered
        stage, an unknown parameter, a stage handed a kind it does not
        read, or a last stage that does not produce the output type.
        """
        stages: list[Stage] = []
        producers: dict[str, dict[str, str]] = {}
        for ref in spec.stages:
            entry = plugins.find(entries(), ref.name, what="stage", error=ChainError)
            stage_cls = plugins.load(entry, Stage, error=ChainError)
            if not stage_cls.version or not stage_cls.produces:
                raise ChainError(
                    f"Stage {ref.name!r} declares no version or no kind it produces, so "
                    f"a run could not say which code made its samples."
                )
            try:
                params = stage_cls.Params.model_validate(ref.params)
            except ValidationError as e:
                raise ChainError(f"Stage {ref.name!r}: {_first(e)}") from None
            stages.append(stage_cls(params))
            dist = getattr(entry, "dist", None)
            producers[ref.name] = {
                "distribution": getattr(dist, "name", "?"),
                "version": getattr(dist, "version", "?"),
            }

        kind = BYTES
        for ref, stage in zip(spec.stages, stages, strict=True):
            if not _fits(kind, stage.accepts):
                raise ChainError(
                    f"Stage {ref.name!r} reads {stage.accepts!r}, and is handed {kind!r}. "
                    f"Put a stage before it that produces {stage.accepts!r}."
                )
            kind = stage.produces
        if not _fits(kind, spec.output.type):
            raise ChainError(
                f"The chain ends with {kind!r}, and the spec writes {spec.output.type!r} "
                f"samples. The last stage has to produce the output type."
            )
        rules = {
            ref.name: found
            for ref, stage in zip(spec.stages, stages, strict=True)
            if (found := stage.rules())
        }
        canonical = _canonical(spec, stages)
        identity = content_hash(
            {
                "spec": hashlib.sha256(canonical).hexdigest(),
                "stages": [
                    [ref.name, type(stage).version]
                    for ref, stage in zip(spec.stages, stages, strict=True)
                ],
                "rules": rules,
            }
        )
        return cls(
            spec=spec, stages=tuple(stages), producers=producers, rules=rules, identity=identity
        )

    # -- what a run records -----------------------------------------------

    def canonical(self) -> bytes:
        """The spec as canonical JSON, with every default written out.

        An omitted parameter and a written default are the same spec. See
        ``docs/adr/0037`` and ``docs/adr/0046``.
        """
        return _canonical(self.spec, self.stages)

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()

    def _pairs(self):
        return zip(self.spec.stages, self.stages, strict=True)


def _canonical(spec: Spec, stages) -> bytes:
    return canonical_bytes(
        {
            "name": spec.name,
            "stages": [
                {"name": ref.name, "params": stage.params.model_dump(mode="json")}
                for ref, stage in zip(spec.stages, stages, strict=True)
            ],
            "output": spec.output.model_dump(mode="json"),
        }
    )


def _fits(given: str, wanted: str) -> bool:
    """Whether an item of kind ``given`` may go where ``wanted`` is read.

    A kind is ``bytes`` or a sample type, and a subtype fits where its
    parent does: an email is a text.
    """
    if given == wanted:
        return True
    if BYTES in (given, wanted):
        return False
    try:
        return issubclass(types.resolve(given), types.resolve(wanted))
    except SampleTypeError as e:
        raise ChainError(str(e)) from None


# ----------------------------------------------------------------------
# Running it
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Produced:
    """One output of one input: a sample's body and its origin row's facts."""

    #: The input checksum: the origin row's input.
    input: str
    #: Position among this input's outputs; 0 unless a stage split it.
    index: int
    #: As the last stage left it. Not canonicalised: canonical form is the
    #: catalog's (``docs/adr/0010``).
    body: bytes
    #: Facts about the occurrence, for the origin row (``docs/adr/0049``).
    facts: dict[str, Any]


@dataclass(frozen=True)
class Emptied:
    """An input that produced nothing, and the stage that left it empty."""

    input: str
    stage: str
    reason: str


#: The reason an input is emptied by a split that yields nothing at all.
SPLIT_NOTHING = "nothing"


@dataclass
class Counts:
    """What a run did with its inputs, counted rather than listed (``docs/adr/0045``).

    ``inputs == produced + sum(emptied.values())`` once a run is over. An
    item dropped from an input that still produced something is in
    ``dropped``, which is not part of that sum.
    """

    inputs: int = 0
    produced: int = 0
    emptied: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)
    #: Outputs with the same bytes as an earlier output of the same input:
    #: one sample, one origin row, so their facts are not all kept.
    repeated: int = 0

    def balanced(self) -> bool:
        return self.inputs == self.produced + sum(self.emptied.values())

    def as_record(self) -> dict[str, Any]:
        """As a run row stores them: drop reasons keyed ``stage/reason``."""
        return {
            "inputs": self.inputs,
            "produced": self.produced,
            "emptied": {f"{s}/{r}": n for (s, r), n in sorted(self.emptied.items())},
            "dropped": {f"{s}/{r}": n for (s, r), n in sorted(self.dropped.items())},
            "repeated": self.repeated,
        }


def run_chain(
    chain: Chain, inputs: Iterable[Input], counts: Counts | None = None
) -> Iterator[Produced | Emptied]:
    """Run ``chain`` on each input in turn, yielding what each produced or why it produced nothing.

    A checksum handed over twice is refused: the caller collapses names to
    distinct checksums first. A stage that raises aborts the run, naming
    the input and the stage, because a raise is a fault in the code and
    not a drop. ``counts`` is filled as the run goes.
    """
    counts = counts if counts is not None else Counts()
    seen: set[str] = set()
    for source in inputs:
        if source.checksum in seen:
            raise ChainError(
                f"Input {source.checksum} was handed over twice. Collapse the names to "
                f"distinct checksums before running."
            )
        seen.add(source.checksum)
        counts.inputs += 1
        yield from _one(chain, source, counts)
    if not counts.balanced():
        # Cannot happen unless this module is wrong; said rather than trusted
        raise ChainError(f"The counts do not add up: {counts.as_record()}")


def _one(chain: Chain, source: Input, counts: Counts) -> Iterator[Produced | Emptied]:
    items: list[Item] = [Item(body=Path(source.path).read_bytes())]
    drops: list[tuple[str, str]] = []
    last_empty: tuple[str, str] | None = None
    for ref, stage in chain._pairs():
        survivors: list[Item] = []
        for item in items:
            try:
                out = stage._run(item, source)
            except Exception as e:
                raise ChainError(
                    f"Stage {ref.name!r} failed on input {source.checksum} "
                    f"({type(e).__name__}: {e})."
                ) from e
            if not out:
                last_empty = (ref.name, SPLIT_NOTHING)
            for result in out:
                if isinstance(result, Drop):
                    drops.append((ref.name, result.reason))
                    last_empty = (ref.name, result.reason)
                elif isinstance(result, Item):
                    survivors.append(result)
                else:
                    raise ChainError(
                        f"Stage {ref.name!r} returned {type(result).__name__} on input "
                        f"{source.checksum}; a stage hands on an Item or a Drop."
                    )
        items = survivors
        if not items:
            break

    if not items:
        stage_name, reason = last_empty or (chain.spec.stages[-1].name, SPLIT_NOTHING)
        counts.emptied[(stage_name, reason)] += 1
        yield Emptied(input=source.checksum, stage=stage_name, reason=reason)
        return

    counts.produced += 1
    counts.dropped.update(drops)
    bodies: set[bytes] = set()
    for index, item in enumerate(items):
        if item.body in bodies:
            counts.repeated += 1
        bodies.add(item.body)
        yield Produced(input=source.checksum, index=index, body=item.body, facts=item.facts)


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------


def entries() -> list[EntryPoint]:
    """What is installed, as entry points. The seam a test stubs to pretend otherwise."""
    return list(entry_points(group=ENTRY_POINT_GROUP))


def available() -> dict[str, str]:
    """Registered stage names, and what each resolves to."""
    return plugins.available(entries())


def _first(error: ValidationError) -> str:
    err = error.errors()[0]
    where = ".".join(str(part) for part in err["loc"])
    return f"{where}: {err['msg'].lower()}" if where else err["msg"].lower()


__all__ = [
    "BYTES",
    "ENTRY_POINT_GROUP",
    "Chain",
    "ChainError",
    "Counts",
    "Drop",
    "Emptied",
    "Filter",
    "Input",
    "Item",
    "Produced",
    "Spec",
    "Split",
    "Stage",
    "StageParams",
    "Transform",
    "available",
    "entries",
    "run_chain",
]
