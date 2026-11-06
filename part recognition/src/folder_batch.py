"""Find source photos without reimporting generated batch outputs."""

from collections import Counter
import os
from pathlib import Path
import re


def folder_images(folder):
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("Drop one folder containing JPG, JPEG, or PNG part photos.")
    images = []

    def unreadable(error):
        raise error

    for directory, subfolders, filenames in os.walk(folder, onerror=unreadable):
        subfolders[:] = sorted(name for name in subfolders if not (
            name.startswith(".") or name.casefold().startswith("recognition_")
            or re.fullmatch(r"analysis\d*", name.casefold())
            or name.casefold() in {"results", "diagnostics", "completed_batches", "captured_images"}
        ))
        images.extend(Path(directory) / name for name in filenames
                      if Path(name).suffix.casefold() in {".jpg", ".jpeg", ".png"}
                      and (Path(directory) / name).is_file())
    images.sort(key=lambda path: str(path).casefold())
    if not images:
        raise ValueError("No JPG, JPEG, or PNG photos were found in that folder.")
    duplicates = [name for name, count in Counter(path.name.casefold() for path in images).items() if count > 1]
    if duplicates:
        raise ValueError("Rename repeated photo filenames before analysis: " + ", ".join(duplicates[:5]))
    return images
