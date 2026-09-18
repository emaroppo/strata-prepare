# strata-prepare

Getting raw data into the shape a catalog takes. A preparer reads what a
corpus arrives as — a video, a mailbox, a folder of pictures — and writes
files a sample type admits, with a prepared index, `prepared.json`, naming
each file and what is known about it. A catalog ingests that index and
nothing else under the directory. This package never imports a catalog,
and a catalog never imports it: what passes between them is the index,
defined in `strata-contracts`.

```bash
uv add strata-prepare
uv add "strata-prepare[test]"        # the conformance suite, for a plugin's tests
```

Depends on `strata-contracts` and `strata-common`. May not import
`strata-catalog`, `strata-modelling`, `strata-labeller`, Label Studio or any
ML framework; a preparer that needs a decoder is a plugin that carries it.

## What a prepared corpus is

A directory of files, and `prepared.json` at its root:

```json
{
  "version": 2,
  "type": "frames",
  "produced_by": "video-frames",
  "samples": {
    "clip-3f2a9c1b0d4e/000030.jpg": {
      "metadata": {"video": "clip-3f2a9c1b0d4e", "frame_index": 30, "seconds": 1.0},
      "value": null
    }
  }
}
```

**One corpus is one type**, named by its registration (`image`, `frames`,
`text`, or a plugin's). A catalog refuses the corpus if any file falls
short of it: not named in the index, outside the root, an extension the
type does not admit, or metadata missing what the type's `Metadata`
requires. A frame without `video` is refused, not made a group of one.

**Metadata is what the preparer knew.** Keys a type does not require are
kept as they are. A catalog adds `source_path`, where in the corpus the
file sat, itself.

**A candidate annotation rides in `value`**, landed by a catalog under a
source of its own, never as ground truth. Its offsets address the bytes
written, so a preparer shipping spans over text writes that text in the
catalog's canonical form (UTF-8, no BOM, LF, NFC); a catalog refuses a
candidate on bytes it would rewrite.

## Preparers

| name | reads | produces | what it does |
|---|---|---|---|
| `image-folder` | the image extensions | `image` | carries each file across, hardlinked where it can be |
| `frames-folder` | the image extensions | `frames` | the same, recording each frame's directory as its `video`; a frame directly in the root is refused |
| `text-folder` | `.txt`, `.md` | `text` | carries each document across byte for byte; the catalog canonicalises it |

A folder preparer mirrors the source's layout into the output, so a
re-run over a grown source touches only what is new. Plugins add more
through the `strata.preparers` entry point group: `strata-prepare-video`
turns video into frames.

## Commands

| `strata-prepare` | |
|---|---|
| `preparers` | the preparers installed here |
| `run --from SRC --to OUT --preparer NAME` | prepare a source directory into a corpus |
| `run --from SRC --to OUT --produces TYPE` | the same, the preparer chosen by type and extension |

Every command takes `--json`. `strata-labeller prepare` runs the same thing
for a project, from its `[data]` settings.

## Writing one

Subclass `Preparer`, declare `name`, `produces` and `sources`, and
implement `prepare(source, out_dir, *, root)`, returning a `Prepared` for
each file written. Register it under `strata.preparers`. Its tests inherit
`PreparerContract` from `strata.prepare.conformance`, supplying `preparer`
and `source` fixtures, and run the promise: the output is admitted by the
type it declares and meets its metadata, the index names every file, and
the same source gives the same bytes on a second run.

## Decisions

Recorded in the strata umbrella repository's `docs/adr/` (https://github.com/emaroppo/strata/tree/main/docs/adr): a sample type is a plugin
(0010), a grouping is a metadata key (0023), an import is trusted until a
person looks (0028), a plugin's promise is an executable suite (0033), and
what enters a catalog is declared and prepared outside it (0040).

## Tests

```bash
.github/sibling-wheels.sh contracts common   # the strata packages this one needs, until they are on an index
uv sync --find-links dist --group dev --extra test
uv run pytest
```

Inside the strata workspace: `uv run pytest packages/prepare` from its root.
