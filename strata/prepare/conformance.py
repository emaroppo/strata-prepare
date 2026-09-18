"""An executable specification of the preparer contract.

A preparer is the one place bytes are invented rather than carried
(``docs/adr/0033``). Written as tests a plugin runs against its own
preparer:

.. code-block:: python

    from strata.prepare.conformance import PreparerContract

    class TestMyPreparer(PreparerContract):
        @pytest.fixture
        def preparer(self):
            return MyPreparer()

        @pytest.fixture
        def source(self, tmp_path):
            path = tmp_path / "corpus.eml"
            path.write_bytes(...)
            return path

Two fixtures, and the plugin inherits the suite. A preparer to which where
a source sits matters overrides a third, ``root``, the directory the source
is under.

**Determinism is the one that matters.** A corpus that comes out different
on a second run re-checksums and detaches its annotations. See
``docs/adr/0010``.

Importing this pulls in pytest, so it lives behind the ``test`` extra and
should only ever be imported from a test module.
"""

from pathlib import Path

import pytest

from strata.contracts import PREPARED_NAME, check
from strata.contracts.sample_types import resolve

from .preparers import Preparer, PreparerError, load_index, run


def _tree(directory: Path) -> dict[str, bytes]:
    """Every file written, by relative path, minus the index itself."""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != PREPARED_NAME
    }


class PreparerContract:
    """Subclass this in a plugin's tests and supply the two fixtures."""

    @pytest.fixture
    def preparer(self) -> Preparer:
        raise NotImplementedError("Supply a `preparer` fixture returning your preparer")

    @pytest.fixture
    def source(self, tmp_path) -> Path:
        raise NotImplementedError(
            "Supply a `source` fixture returning a path to a source file your "
            "preparer can actually read"
        )

    @pytest.fixture
    def root(self, source) -> Path:
        """The directory the source is under. Its own, unless overridden."""
        return source.parent

    @pytest.fixture
    def sample_type(self, preparer):
        """The type this claims to produce, as a catalog resolves it.

        Resolved rather than declared, so a preparer naming a type nothing
        installed provides fails here rather than at the first ingest.
        """
        return resolve(type(preparer).produces)()

    # -- declarations ---------------------------------------------------

    def test_it_is_a_preparer(self, preparer):
        assert isinstance(preparer, Preparer)

    def test_it_declares_a_name(self, preparer):
        # What a project names in [data] preparer
        assert isinstance(type(preparer).name, str) and type(preparer).name

    def test_it_declares_what_it_produces(self, preparer, sample_type):
        assert type(preparer).produces

    def test_it_declares_what_it_reads(self, preparer):
        assert type(preparer).sources

    def test_it_admits_its_own_source(self, preparer, source):
        assert preparer.allows(source)

    def test_it_refuses_what_it_does_not_read(self, preparer, source, tmp_path):
        # A source it cannot read is refused rather than skipped.
        # docs/adr/0033, docs/adr/0036
        foreign = tmp_path / "elsewhere.unlikely-suffix"
        foreign.write_bytes(b"not for you")
        with pytest.raises(PreparerError):
            run(preparer, [foreign], tmp_path / "out")

    # -- what it writes -------------------------------------------------

    def test_it_writes_something(self, preparer, source, root, tmp_path):
        out = tmp_path / "out"
        run(preparer, [source], out, root=root)
        assert _tree(out), "prepared nothing from a source it admits"

    def test_the_output_is_admitted_by_the_type_it_produces(
        self, preparer, source, root, sample_type, tmp_path
    ):
        """The contract that makes 'prepare, then ingest' hold. See ``docs/adr/0033``."""
        out = tmp_path / "out"
        run(preparer, [source], out, root=root)
        for name in _tree(out):
            assert sample_type.allows(out / name), f"{name} is not a {type(sample_type).__name__}"

    def test_the_output_meets_the_type_it_produces(
        self, preparer, source, root, sample_type, tmp_path
    ):
        """What a catalog checks before it takes anything, checked here first.

        Every file named, admitted, and with the metadata its type requires,
        or a catalog refuses the whole corpus. See ``docs/adr/0040``.
        """
        out = tmp_path / "out"
        index = run(preparer, [source], out, root=root)
        checked = check(index, sample_type, type(preparer).produces, set(_tree(out)))
        assert checked.ok, "; ".join(str(v) for v in checked.violations)

    def test_it_records_every_file_it_wrote(self, preparer, source, root, tmp_path):
        out = tmp_path / "out"
        index = run(preparer, [source], out, root=root)
        assert set(index.samples) == set(_tree(out))
        # And it survives the round trip to disk, which is what a catalog reads
        loaded = load_index(out)
        assert loaded is not None
        assert loaded.samples.keys() == index.samples.keys()

    # -- determinism ----------------------------------------------------

    def test_the_same_source_gives_the_same_bytes(self, preparer, source, root, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        run(preparer, [source], first, root=root)
        run(preparer, [source], second, root=root)
        assert _tree(first) == _tree(second)

    def test_a_re_run_changes_nothing(self, preparer, source, root, tmp_path):
        """Converting a source directory that has grown must not disturb it.

        See ``docs/adr/0010``.
        """
        out = tmp_path / "out"
        run(preparer, [source], out, root=root)
        before = _tree(out)
        run(preparer, [source], out, root=root)
        assert _tree(out) == before


__all__ = ["PreparerContract"]
