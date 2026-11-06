"""Plugin registration agrees with the reader's supported filename patterns."""

from importlib.metadata import distribution
from pathlib import Path
from pkgutil import resolve_name

from npe2 import PluginManifest

from napari_virtual_reader._reader import IMAGE_SUFFIXES


def test_installed_plugin_discovery():
    """npe2 discovers the installed package through its manifest entry point."""
    package = distribution("napari-virtual-reader")
    entry_points = [
        entry for entry in package.entry_points if entry.group == "napari.manifest"
    ]
    assert len(entry_points) == 1
    assert entry_points[0].name == "napari-virtual-reader"
    assert entry_points[0].value == "napari_virtual_reader:napari.yaml"
    manifest = PluginManifest.from_distribution(package.metadata["Name"])
    assert manifest.name == "napari-virtual-reader"
    assert manifest.contributions.readers
    assert manifest.contributions.widgets
    for command in manifest.contributions.commands:
        assert callable(resolve_name(command.python_name))


def test_manifest_patterns_and_commands():
    """Every supported suffix has a simple extension pattern and valid commands."""
    manifest = PluginManifest.from_file(
        Path(__file__).parents[1] / "src" / "napari_virtual_reader" / "napari.yaml"
    )
    patterns = manifest.contributions.readers[0].filename_patterns
    assert sorted(patterns) == sorted(f"*{suffix}" for suffix in IMAGE_SUFFIXES)
    for command in manifest.contributions.commands:
        assert callable(resolve_name(command.python_name))
