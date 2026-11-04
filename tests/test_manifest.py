"""Plugin registration agrees with the reader's supported filename patterns."""

from fnmatch import fnmatchcase
from pathlib import Path
from pkgutil import resolve_name

from npe2 import PluginManifest

from napari_refkv._reader import IMAGE_SUFFIXES


def test_manifest_patterns_and_commands():
    """Every advertised suffix works in lower, upper and mixed case."""
    manifest = PluginManifest.from_file(Path(__file__).parents[1] / "src" / "napari_refkv" / "napari.yaml")
    patterns = manifest.contributions.readers[0].filename_patterns
    for suffix in IMAGE_SUFFIXES:
        for variant in (suffix, suffix.upper(), suffix.title()):
            assert any(fnmatchcase(f"image{variant}", pattern) for pattern in patterns)
    for command in manifest.contributions.commands:
        assert callable(resolve_name(command.python_name))
