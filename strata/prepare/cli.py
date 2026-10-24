"""strata-prepare: what can prepare a corpus here, and preparing one."""

import argparse
from dataclasses import dataclass
from pathlib import Path

from strata.common.cli import emit
from strata.common.cli import run as run_command

from .preparers import PreparerError, available, for_source, resolve, run


@dataclass
class PreparerEntry:
    name: str
    reads: list[str]
    produces: str


@dataclass
class RunReport:
    preparer: str
    produces: str
    sources: int
    written: int
    index: str
    #: What the preparer did not carry across, counted. docs/adr/0036
    left_out: dict[str, int]


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    return run_command(lambda: args.run(args), PreparerError)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="strata-prepare", description="Get raw data into the shape a catalog takes."
    )
    parser.add_argument("--json", action="store_true", help="Print the record as JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("preparers", help="The preparers installed here").set_defaults(
        run=_preparers
    )

    prepare = commands.add_parser("run", help="Prepare a source directory into a corpus")
    prepare.add_argument(
        "--from", dest="source", type=Path, required=True, help="The source directory"
    )
    prepare.add_argument(
        "--to", dest="out", type=Path, required=True, help="Where the corpus is written"
    )
    prepare.add_argument("--preparer", default="", help="Which preparer, by name")
    prepare.add_argument(
        "--produces",
        default="",
        help="The sample type wanted, to choose a preparer by it and the files' extension",
    )
    prepare.set_defaults(run=_run)
    return parser


def _preparers(args) -> int:
    entries = []
    for name in sorted(available()):
        cls = resolve(name)
        entries.append(PreparerEntry(name, sorted(cls.sources), cls.produces))

    def render(entries):
        for e in entries:
            yield f"{e.name:<14} reads {', '.join('.' + x for x in e.reads) or '-'} -> {e.produces}"
        yield "Read from what is installed. A preparer that needs a decoder is a plugin."

    emit(entries, render, as_json=args.json)
    return 0


def _run(args) -> int:
    source = args.source
    if not source.is_dir():
        raise PreparerError(f"{source} is not a directory.")
    files = [p for p in sorted(source.rglob("*")) if p.is_file()]
    if not files:
        raise PreparerError(f"{source} holds no files.")
    if args.preparer:
        cls = resolve(args.preparer)
    elif args.produces:
        cls = for_source(args.produces, files[0])
    else:
        raise PreparerError("Name a --preparer, or the sample type it --produces.")
    preparer = cls()
    admitted = [p for p in files if preparer.allows(p)]
    if not admitted:
        raise PreparerError(f"{cls.name} reads none of the files in {source}.")

    index = run(preparer, admitted, args.out, root=source)
    report = RunReport(
        preparer=cls.name,
        produces=cls.produces,
        sources=len(admitted),
        written=len(index.samples),
        index=str(args.out),
        left_out=preparer.report(),
    )

    def render(report):
        yield (
            f"{report.preparer}: {report.sources} sources -> {report.written} "
            f"{report.produces} files in {report.index}"
        )
        if skipped := len(files) - len(admitted):
            yield f"  {skipped} files it does not read were left where they are"
        for what, count in report.left_out.items():
            yield f"  {count} {what}"

    emit(report, render, as_json=args.json)
    return 0


__all__ = ["main"]
