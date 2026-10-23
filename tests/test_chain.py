"""The chain engine: what a spec may say, what a run yields, and what it counts.

The counts carry the weight. A run records no reason per input, so the
only evidence that nothing was lost is that every input checksum either
produced something or was counted as emptied, by the stage that emptied
it. See ``docs/adr/0045``.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from strata.contracts import Text
from strata.contracts import sample_types as types
from strata.prepare.chain import (
    Chain,
    ChainError,
    Counts,
    Drop,
    Emptied,
    Filter,
    Input,
    Item,
    Produced,
    Spec,
    Split,
    StageParams,
    Transform,
    run_chain,
)


class Note(Text):
    """A text with something to say about itself: the subtype the kinds test needs."""

    segment: ClassVar[str] = "note"


class Decode(Transform):
    name = "decode"
    version = "1"
    produces = "note"

    def transform(self, item, source):
        return Item(item.body.replace(b"\r\n", b"\n")).with_facts(names=list(source.names))


class Lines(Split):
    name = "lines"
    version = "1"
    accepts = "note"
    produces = "note"

    def split(self, item, source):
        for number, line in enumerate(item.body.split(b"\n")):
            yield Item(line, item.facts).with_facts(line=number) if line else Drop("blank")


class ShortParams(StageParams):
    limit: int = 10


class Short(Filter[ShortParams]):
    name = "short"
    version = "1"
    accepts = "text"
    produces = "note"
    Params = ShortParams

    def keep(self, item, source):
        return item if len(item.body) <= self.params.limit else Drop("too_long")


class Broken(Transform):
    name = "broken"
    version = "1"
    accepts = "note"
    produces = "note"

    def transform(self, item, source):
        raise RuntimeError("a fault in the code")


class Unversioned(Transform):
    name = "unversioned"
    produces = "note"


def _entry(name: str, obj, dist: str = "test-stages"):
    return SimpleNamespace(
        name=name,
        value=f"{obj.__module__}:{obj.__name__}",
        dist=SimpleNamespace(name=dist, version="9.9"),
        load=lambda: obj,
    )


@pytest.fixture(autouse=True)
def installed(monkeypatch):
    stages = [Decode, Lines, Short, Broken, Unversioned]
    monkeypatch.setattr("strata.prepare.chain.entries", lambda: [_entry(s.name, s) for s in stages])
    builtin = types.entries()
    monkeypatch.setattr(types, "entries", lambda: [*builtin, _entry("note", Note)])


def spec(*stages: str, output: str = "note", **params) -> Spec:
    blocks = []
    for name in stages:
        lines = [f'name = "{name}"'] + [f"{k} = {v}" for k, v in params.get(name, {}).items()]
        blocks.append("[[stage]]\n" + "\n".join(lines))
    return Spec.from_toml(
        'name = "trial"\n\n'
        + "\n\n".join(blocks)
        + f'\n\n[output]\ncollection = "c"\ntype = "{output}"\n'
    )


def inputs(tmp_path: Path, **files: bytes) -> list[Input]:
    found = []
    for checksum, body in files.items():
        path = tmp_path / checksum  # no extension, as an Enron file has none
        path.write_bytes(body)
        found.append(Input(checksum=checksum, path=path, names=(f"c/{checksum}",)))
    return found


# ----------------------------------------------------------------------
# What a spec may say
# ----------------------------------------------------------------------


def test_a_stage_is_found_by_name_and_its_producer_recorded():
    chain = Chain.load(spec("decode", "lines"))
    assert [type(s) for s in chain.stages] == [Decode, Lines]
    assert chain.producers["decode"] == {"distribution": "test-stages", "version": "9.9"}


def test_an_unknown_stage_says_what_is_installed():
    with pytest.raises(ChainError, match="decode"):
        Chain.load(spec("nothing-like-it"))


def test_an_unknown_parameter_is_refused():
    with pytest.raises(ChainError, match="short"):
        Chain.load(spec("decode", "short", short={"lmit": 3}))


def test_a_stage_with_no_version_is_refused():
    with pytest.raises(ChainError, match="no version"):
        Chain.load(spec("decode", "unversioned"))


def test_a_stage_handed_the_wrong_kind_is_refused_before_anything_is_read():
    with pytest.raises(ChainError, match="reads 'note', and is handed 'bytes'"):
        Chain.load(spec("lines"))


def test_a_subtype_fits_where_its_parent_is_read():
    # Short reads text, and a note is a text
    Chain.load(spec("decode", "short"))


def test_the_last_stage_has_to_produce_the_output_type():
    with pytest.raises(ChainError, match="ends with 'note'"):
        Chain.load(spec("decode", output="image"))


def test_a_written_default_and_an_omitted_one_are_the_same_spec():
    omitted = Chain.load(spec("decode", "short"))
    written = Chain.load(spec("decode", "short", short={"limit": 10}))
    assert omitted.canonical() == written.canonical()
    assert omitted.identity == written.identity


def test_a_new_stage_version_is_a_new_chain_with_the_same_spec(monkeypatch):
    before = Chain.load(spec("decode"))
    monkeypatch.setattr(Decode, "version", "2")
    after = Chain.load(spec("decode"))
    assert before.spec_hash == after.spec_hash
    assert before.identity != after.identity


def test_a_rule_file_edited_in_place_is_a_new_chain(monkeypatch):
    before = Chain.load(spec("decode"))
    monkeypatch.setattr(Decode, "rules", lambda self: {"patterns": "ab" * 32})
    after = Chain.load(spec("decode"))
    assert after.rules == {"decode": {"patterns": "ab" * 32}}
    assert before.identity != after.identity


# ----------------------------------------------------------------------
# What a run yields and counts
# ----------------------------------------------------------------------


def test_every_input_produces_or_is_counted_as_emptied(tmp_path):
    chain = Chain.load(spec("decode", "lines", "short", short={"limit": 5}))
    counts = Counts()
    found = list(
        run_chain(
            chain,
            inputs(tmp_path, a=b"one\r\n\r\ntwo", b=b"\n\n", c=b"far too long"),
            counts,
        )
    )
    produced = [f for f in found if isinstance(f, Produced)]
    assert [(p.input, p.index, p.body) for p in produced] == [("a", 0, b"one"), ("a", 1, b"two")]
    assert produced[1].facts == {"names": ["c/a"], "line": 2}
    assert [f for f in found if isinstance(f, Emptied)] == [
        Emptied("b", "lines", "blank"),
        Emptied("c", "short", "too_long"),
    ]
    assert counts.balanced()
    assert counts.as_record() == {
        "inputs": 3,
        "produced": 1,
        "emptied": {"lines/blank": 1, "short/too_long": 1},
        # a's blank line, dropped from an input that still produced
        "dropped": {"lines/blank": 1},
        "repeated": 0,
    }


def test_a_fact_already_recorded_keeps_its_first_value():
    assert Item(b"", {"line": 1}).with_facts(line=2).facts == {"line": 1}


def test_outputs_with_the_same_bytes_are_counted(tmp_path):
    counts = Counts()
    list(run_chain(Chain.load(spec("decode", "lines")), inputs(tmp_path, a=b"x\nx"), counts))
    assert counts.repeated == 1


def test_a_checksum_handed_over_twice_is_refused(tmp_path):
    [one] = inputs(tmp_path, a=b"x")
    with pytest.raises(ChainError, match="twice"):
        list(run_chain(Chain.load(spec("decode")), [one, one]))


def test_a_stage_that_raises_aborts_the_run_naming_the_input(tmp_path):
    with pytest.raises(ChainError, match="'broken' failed on input a"):
        list(run_chain(Chain.load(spec("decode", "broken")), inputs(tmp_path, a=b"x")))


def test_the_same_input_gives_the_same_output(tmp_path):
    chain = Chain.load(spec("decode", "lines"))
    files = inputs(tmp_path, a=b"one\ntwo")
    assert list(run_chain(chain, files)) == list(run_chain(chain, files))
