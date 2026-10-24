"""``strata-prepare``: a refusal is one line on stderr, and ``--json`` is the record whole."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from strata.prepare import Prepared, Preparer
from strata.prepare.cli import main


class Lines(Preparer):
    """One text file into one file per line, enough to be a corpus."""

    name = "lines"
    produces = "text"
    sources = frozenset({"log"})

    def prepare(self, source: Path, out_dir: Path, *, root: Path):
        for index, line in enumerate(source.read_text().splitlines()):
            path = out_dir / f"{source.stem}-{index:03d}.txt"
            path.write_text(line)
            yield Prepared(path=path, metadata={"line": index})


@pytest.fixture
def installed(monkeypatch):
    entry = SimpleNamespace(
        name="lines",
        value=f"{__name__}:Lines",
        dist=SimpleNamespace(name="test-plugin"),
        load=lambda: Lines,
    )
    monkeypatch.setattr("strata.prepare.preparers.entries", lambda: [entry])


def test_a_refusal_is_one_line_on_stderr_and_nothing_on_stdout(tmp_path, capsys):
    assert main(["run", "--from", str(tmp_path / "absent"), "--to", str(tmp_path / "out")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{tmp_path / 'absent'} is not a directory.\n"


def test_json_is_the_preparers_whole(installed, capsys):
    assert main(["preparers"]) == 0
    assert "lines          reads .log -> text" in capsys.readouterr().out

    assert main(["--json", "preparers"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"name": "lines", "produces": "text", "reads": ["log"]}]


def test_json_is_the_run_report_whole(installed, tmp_path, capsys):
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.log").write_text("one\ntwo\n")
    (source / "skip.bin").write_bytes(b"x")
    out = tmp_path / "corpus"

    args = ["run", "--from", str(source), "--to", str(out), "--preparer", "lines"]
    assert main(args) == 0
    text = capsys.readouterr().out
    assert "lines: 1 sources -> 2 text files" in text
    assert "1 files it does not read were left where they are" in text

    assert main(["--json", *args]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"preparer", "produces", "sources", "written", "index", "left_out"}
    assert payload["preparer"] == "lines" and payload["sources"] == 1
