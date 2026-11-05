"""Minimal napari reader for refkv's virtual OME-Zarr images."""

from ._reader import napari_get_reader, read_images

__all__ = ["napari_get_reader", "read_images"]
__version__ = "0.1.0"
