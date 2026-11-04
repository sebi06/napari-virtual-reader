"""Read reference-backed OME images without converting or copying their pixels."""

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import dask.array as da
import numpy as np

IMAGE_SUFFIXES = (
    ".czi",
    ".tif",
    ".tiff",
    ".svs",
    ".ndpi",
    ".nd2",
    ".lif",
    ".ims",
    ".vsi",
    ".qptiff",
    ".dcm",
    ".dicom",
    ".nii",
    ".nii.gz",
    ".nrrd",
    ".nhdr",
    ".mrc",
    ".map",
    ".vzip",
)


class _ChunkedArray:
    """Adapt regular and rectilinear Zarr grids to Dask's array protocol."""

    def __init__(self, array: Any) -> None:
        self._array = array
        self.shape = array.shape
        self.dtype = array.dtype
        self.ndim = array.ndim
        self.chunks = getattr(array, "read_chunk_sizes", None)
        if self.chunks is None:
            self.chunks = array.chunks

    def __getitem__(self, selection: Any) -> np.ndarray:
        """Read only the slice requested by a Dask task."""
        return self._array[selection]


def _image_groups(group: Any, name: str = "") -> Iterator[tuple[str, Any, dict]]:
    """Find OME 0.4/0.5 image groups, including scenes and plate fields."""
    attributes = dict(group.attrs)
    ome = attributes.get("ome", attributes)
    if ome.get("multiscales"):
        yield name, group, ome
        return
    children = sorted(
        group.groups(),
        key=lambda item: (
            not item[0].isdecimal(),
            int(item[0]) if item[0].isdecimal() else item[0],
        ),
    )
    for child_name, child in children:
        if child_name not in {"refkv_source", "tiles", "labels"}:
            yield from _image_groups(child, f"{name}/{child_name}".strip("/"))


def _lazy_array(array: Any) -> da.Array:
    """Build a graph without probing pixels or using Zarr's regular-only chunks."""
    adapted = _ChunkedArray(array)
    return da.from_array(
        adapted,
        chunks=adapted.chunks,
        meta=np.empty((0,) * array.ndim, dtype=array.dtype),
    )


def _contrast_limits(dtype: np.dtype, channel: dict) -> tuple[float, float]:
    """Use embedded windows or dtype bounds instead of scanning a lazy volume."""
    fallback = (0.0, 1.0)
    if np.issubdtype(dtype, np.integer):
        limits = np.iinfo(dtype)
        fallback = (float(limits.min), float(limits.max))
    window = channel.get("window", {})
    low, high = window.get("start"), window.get("end")
    if low is not None and high is not None:
        if np.isfinite(low) and np.isfinite(high) and low < high:
            return float(low), float(high)
    return fallback


def _stack_czi_scenes(layers: list[tuple[Any, dict, str]], source: Path) -> list[tuple[Any, dict, str]]:
    """Stack compatible CZI series per channel while retaining scene provenance."""
    channels = {}
    for layer in layers:
        metadata = layer[1]["metadata"]
        key = (metadata["multiscale_index"], metadata["channel"])
        channels.setdefault(key, []).append(layer)
    result = []
    for scenes in channels.values():
        first = scenes[0][1]
        levels = [data if kwargs["multiscale"] else [data] for data, kwargs, _ in scenes]
        compatible = all(
            kwargs["axis_labels"] == first["axis_labels"]
            and np.allclose(kwargs["scale"], first["scale"])
            and len(scene_levels) == len(levels[0])
            and all(
                level.shape == reference.shape and level.dtype == reference.dtype
                for level, reference in zip(scene_levels, levels[0])
            )
            for scene_levels, (_, kwargs, _) in zip(levels, scenes)
        )
        if len(scenes) < 2 or "s" in (axis.lower() for axis in first["axis_labels"]) or not compatible:
            result.extend(scenes)
            continue
        stacked = [da.stack(level, axis=0) for level in zip(*levels)]
        metadata = {
            **first["metadata"],
            "scenes": [
                {
                    **kwargs["metadata"],
                    "scale": kwargs["scale"],
                    "translate": kwargs["translate"],
                }
                for _, kwargs, _ in scenes
            ],
        }
        kwargs = {
            **first,
            "name": f"{source.stem} | {metadata['channel_label']}",
            "axis_labels": ("S",) + first["axis_labels"],
            "scale": (1.0,) + first["scale"],
            "translate": (0.0,)
            + tuple(
                origin if all(scene[1]["translate"][index] == origin for scene in scenes) else 0.0
                for index, origin in enumerate(first["translate"])
            ),
            "metadata": metadata,
        }
        result.append((stacked if first["multiscale"] else stacked[0], kwargs, "image"))
    return result


def _zarr_structure(group: Any) -> dict:
    """Snapshot groups, array layout and attributes without fetching pixels."""
    children = []
    for name, node in sorted(
        group.members(),
        key=lambda item: (not item[0].isdecimal(), int(item[0]) if item[0].isdecimal() else item[0]),
    ):
        if hasattr(node, "shape"):
            children.append({"name": name, "kind": "array", "metadata": node.metadata.to_dict()})
        else:
            children.append({"name": name, **_zarr_structure(node)})
    return {"kind": "group", "attributes": dict(group.attrs), "children": children}


def _layers(root: Any, source: Path) -> list[tuple[Any, dict, str]]:
    """Translate OME axes, stored pyramids, transforms and channels to layers."""
    from napari.utils.colormaps import Colormap

    layers = []
    for group_name, group, ome in _image_groups(root):
        for series_index, multiscale in enumerate(ome["multiscales"]):
            axes = tuple((axis if isinstance(axis, str) else axis["name"]).lower() for axis in multiscale["axes"])
            if len(set(axes)) != len(axes) or not {"y", "x"}.issubset(axes):
                raise ValueError(f"Unsupported image axes in {group_name}: {axes}")
            datasets = multiscale["datasets"]
            arrays = [group[dataset["path"]] for dataset in datasets]
            if not arrays or any(array.ndim != len(axes) for array in arrays):
                raise ValueError(f"Invalid OME image dimensions in {group_name}")
            channel_axis = axes.index("c") if "c" in axes else None
            channel_count = arrays[0].shape[channel_axis] if channel_axis is not None else 1
            if channel_axis is not None and any(array.shape[channel_axis] != channel_count for array in arrays):
                raise ValueError(f"Channel counts differ across levels in {group_name}")
            remaining_axes = tuple(axis for axis in axes if axis != "c")
            display_axes = tuple(axis for axis in remaining_axes if axis not in {"y", "x"}) + ("y", "x")
            permutation = tuple(remaining_axes.index(axis) for axis in display_axes)
            scale = np.ones(len(axes))
            translate = np.zeros(len(axes))
            transforms = datasets[0].get("coordinateTransformations", []) + multiscale.get(
                "coordinateTransformations", []
            )
            for transform in transforms:
                if transform["type"] == "scale":
                    factor = np.asarray(transform["scale"])
                    scale *= factor
                    translate *= factor
                elif transform["type"] == "translation":
                    translate += np.asarray(transform["translation"])
                else:
                    raise ValueError(f"Unsupported OME transform: {transform['type']}")
            lazy_levels = [_lazy_array(array) for array in arrays]
            channels = ome.get("omero", {}).get("channels", [])
            for channel_index in range(channel_count):
                selection = tuple(channel_index if axis == "c" else slice(None) for axis in axes)
                levels = [level[selection].transpose(permutation) for level in lazy_levels]
                channel = channels[channel_index] if channel_index < len(channels) else {}
                colormap = "gray"
                color = channel.get("color", "")
                if isinstance(color, str) and len(color) == 6:
                    try:
                        rgb = [int(color[start : start + 2], 16) / 255 for start in (0, 2, 4)]
                        colormap = Colormap([[0, 0, 0], rgb], name=f"refkv_{color}")
                    except ValueError:
                        pass
                series_name = multiscale.get("name") or group_name or source.stem
                if series_index:
                    series_name = f"{series_name}/{series_index}"
                label = channel.get("label", f"Channel {channel_index}")
                kwargs = {
                    "name": f"{source.stem} | {series_name} | {label}",
                    "axis_labels": display_axes,
                    "scale": tuple(scale[axes.index(axis)] for axis in display_axes),
                    "translate": tuple(translate[axes.index(axis)] for axis in display_axes),
                    "contrast_limits": _contrast_limits(arrays[0].dtype, channel),
                    "colormap": colormap,
                    "blending": "additive",
                    "multiscale": len(levels) > 1,
                    "rgb": False,
                    "metadata": {
                        "source": str(source),
                        "series": group_name,
                        "multiscale_index": series_index,
                        "channel": channel_index,
                        "channel_label": label,
                        "ome": ome,
                    },
                }
                layers.append((levels if len(levels) > 1 else levels[0], kwargs, "image"))
    if not layers:
        raise ValueError("refkv found no supported OME image series in this file.")
    if root.attrs.get("refkv_zarrified", {}).get("profile") == "czi":
        layers = _stack_czi_scenes(layers, source)
    structure = _zarr_structure(root)
    for _, kwargs, _ in layers:
        kwargs["metadata"]["zarr_structure"] = structure
    return layers


def read_images(path: str | list[str]) -> list[tuple[Any, dict, str]]:
    """Open local images or VZIP archives as lazy virtual OME-Zarr layers.

    Raw images are virtualized in memory. No archive or converted pixels are
    written. Sources must remain available while their layers are being used.
    """
    import refkv.zarr
    import zarr
    from refkv.zarrify import zarrify

    paths = path if isinstance(path, list) else [path]
    layers = []
    for item in paths:
        source = Path(item).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Image file does not exist: {source}")
        store = (
            refkv.zarr.open(str(source)) if source.suffix.lower() == ".vzip" else refkv.zarr.store(zarrify(str(source)))
        )
        root = zarr.open_group(store, mode="r")
        layers.extend(_layers(root, source))
    return layers


def read_and_save_image(path: str, archive_path: str, *, overwrite: bool = False) -> list[tuple[Any, dict, str]]:
    """Prepare lazy layers and atomically save their reference archive, not pixels."""
    import refkv.zarr
    import zarr
    from refkv.zarrify import zarrify

    source = Path(path).expanduser().resolve()
    destination = Path(archive_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Image file does not exist: {source}")
    if source.suffix.lower() == ".vzip":
        raise ValueError("The selected input is already a VZIP archive.")
    if destination == source or destination.suffix.lower() != ".vzip":
        raise ValueError("Choose a separate .vzip archive destination.")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Archive directory does not exist: {destination.parent}")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Archive already exists: {destination}")
    reference = zarrify(str(source))
    root = zarr.open_group(refkv.zarr.store(reference), mode="r")
    layers = _layers(root, source)
    with TemporaryDirectory(prefix=".refkv-", dir=destination.parent) as temporary:
        staged = Path(temporary) / destination.name
        reference.save(staged)
        if overwrite:
            os.replace(staged, destination)
        else:
            os.link(staged, destination)
    for _, kwargs, _ in layers:
        kwargs["metadata"]["vzip"] = str(destination)
    return layers


def napari_get_reader(path: str | list[str]):
    """Offer the reader for supported image extensions without opening the file."""
    paths = path if isinstance(path, list) else [path]
    if paths and all(str(item).lower().endswith(IMAGE_SUFFIXES) for item in paths):
        qt_widgets = sys.modules.get("qtpy.QtWidgets")
        if qt_widgets is not None and qt_widgets.QApplication.instance() is not None:
            from ._metadata import prepare_metadata_inspectors

            prepare_metadata_inspectors()
        return read_images
    return None
