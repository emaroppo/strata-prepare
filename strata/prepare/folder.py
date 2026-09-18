"""A directory of files that are already what a type stores.

The preparation such a corpus needs is a declaration, not a conversion:
each file is carried into the output where it sat, hardlinked where the
filesystem allows and copied where it does not, and the index says what
type they are. Frames say which video too, from the directory each sits
in, which is the one thing a catalog used to guess for itself. See
``docs/adr/0023`` and ``docs/adr/0040``.
"""

import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from strata.contracts import Frames, Image, Text

from .preparers import Prepared, Preparer, PreparerError


def carry(source: Path, target: Path) -> None:
    """``source``'s bytes at ``target``, sharing its inode where possible.

    Nothing is rewritten where ``target`` already holds these bytes, so a
    re-run over a grown directory touches only what is new.
    """
    if target.exists():
        if os.path.samefile(source, target) or target.read_bytes() == source.read_bytes():
            return
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        # Another filesystem, or one without hardlinks
        shutil.copyfile(source, target)


class FolderPreparer(Preparer):
    """Each file carried across as it is, to the same place under the output."""

    def prepare(self, source: Path, out_dir: Path, *, root: Path) -> Iterable[Prepared]:
        relative = Path(source).resolve().relative_to(Path(root).resolve())
        target = Path(out_dir) / relative
        carry(Path(source), target)
        return [Prepared(target, metadata=self.metadata_for(relative))]

    def metadata_for(self, relative: Path) -> dict:
        """What sitting at ``relative`` under the root says about a file."""
        return {}


class ImageFolder(FolderPreparer):
    """A directory of pictures, each standing on its own."""

    name: ClassVar[str] = "image-folder"
    produces: ClassVar[str] = "image"
    sources: ClassVar[frozenset[str]] = Image.extensions


class FramesFolder(FolderPreparer):
    """Frames already extracted, one directory per video.

    The directory is the video, however deep it sits, so a video's frames
    stay on one side of a split frozen with ``group_by = "video"``. A frame
    directly in the root belongs to no video and is refused rather than
    made a group of one.
    """

    name: ClassVar[str] = "frames-folder"
    produces: ClassVar[str] = "frames"
    sources: ClassVar[frozenset[str]] = Frames.extensions

    def metadata_for(self, relative: Path) -> dict:
        if relative.parent == Path("."):
            raise PreparerError(
                f"{relative} sits directly in the source root, so which video it "
                f"came from is unknown. Put each video's frames in a directory of "
                f"its own."
            )
        return {Frames.VIDEO: relative.parent.as_posix()}


class TextFolder(FolderPreparer):
    """A directory of documents with no annotations riding on them.

    Carried byte for byte; the catalog stores them in its canonical form.
    A corpus arriving with spans needs a preparer that writes canonical
    text itself, since the spans address what it writes. See
    ``docs/adr/0040``.
    """

    name: ClassVar[str] = "text-folder"
    produces: ClassVar[str] = "text"
    sources: ClassVar[frozenset[str]] = Text.extensions


__all__ = ["FolderPreparer", "FramesFolder", "ImageFolder", "TextFolder", "carry"]
