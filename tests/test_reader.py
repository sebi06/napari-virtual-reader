"""Virtual image reader behavior, independent of the Qt widget."""

from pathlib import Path

import numpy as np
import pytest
import zarr

from napari_refkv._reader import (
    _layers,
    napari_get_reader,
    read_and_save_image,
    read_images,
)


def test_rectilinear_layers_are_lazy(monkeypatch):
    """Keep irregular chunks and OME channels without reading any pixels on open."""
    import refkv.zarr  # noqa: F401

    root = zarr.open_group(zarr.storage.MemoryStore(), mode="w")
    group = root.create_group("0")
    data = np.arange(2 * 4 * 8 * 10, dtype="uint16").reshape(2, 4, 8, 10)
    group.create_array("0", data=data, chunks=((1, 1), (1, 3), (3, 5), (4, 6)))
    group.create_array("1", data=data[..., ::2, ::2])
    group.attrs["ome"] = {
        "multiscales": [
            {
                "axes": [{"name": axis} for axis in ("c", "z", "y", "x")],
                "datasets": [
                    {
                        "path": "0",
                        "coordinateTransformations": [{"type": "scale", "scale": [1, 2, 0.1, 0.1]}],
                    },
                    {"path": "1"},
                ],
            }
        ],
        "omero": {
            "channels": [
                {
                    "label": "DAPI",
                    "color": "0000FF",
                    "window": {"start": 10, "end": 500},
                }
            ]
        },
    }
    reads = []
    getitem = zarr.Array.__getitem__

    def record(array, selection):
        reads.append(selection)
        return getitem(array, selection)

    monkeypatch.setattr(zarr.Array, "__getitem__", record)
    layers = _layers(root, Path("example.czi"))
    assert not reads
    assert len(layers) == 2
    levels, kwargs, kind = layers[0]
    assert kind == "image"
    assert kwargs["axis_labels"] == ("z", "y", "x")
    assert kwargs["scale"] == (2, 0.1, 0.1)
    assert kwargs["contrast_limits"] == (10, 500)
    assert levels[0].chunks == ((1, 3), (3, 5), (4, 6))
    structure = kwargs["metadata"]["zarr_structure"]
    assert structure["children"][0]["name"] == "0"
    array_metadata = structure["children"][0]["children"][0]["metadata"]
    assert tuple(array_metadata["shape"]) == data.shape
    assert array_metadata["chunk_grid"]["name"] == "rectilinear"
    assert layers[1][1]["metadata"]["zarr_structure"] is structure
    np.testing.assert_array_equal(levels[0].compute(), data[0])
    np.testing.assert_array_equal(levels[1].compute(), data[0, ..., ::2, ::2])


def test_reader_selection():
    """File dispatch is case-insensitive and does not claim unrelated files."""
    assert napari_get_reader("image.CZI")
    assert napari_get_reader(["image.tif", "other.vzip"])
    assert napari_get_reader([]) is None
    assert napari_get_reader("notes.txt") is None


def test_optional_archive_save_is_lazy_and_reopens(tmp_path, monkeypatch):
    """Save complete references without reading pixels or modifying the source."""
    import tifffile

    source = tmp_path / "image.tif"
    output = tmp_path / "image.vzip"
    expected = np.arange(80, dtype="uint16").reshape(8, 10)
    tifffile.imwrite(source, expected, metadata={"axes": "YX"})
    original = source.read_bytes()
    reads = []
    getitem = zarr.Array.__getitem__

    def record(array, selection):
        reads.append(selection)
        return getitem(array, selection)

    monkeypatch.setattr(zarr.Array, "__getitem__", record)
    layers = read_and_save_image(str(source), str(output))
    reopened = read_images(str(output))
    assert not reads
    assert output.is_file()
    assert source.read_bytes() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["image.tif", "image.vzip"]
    assert layers[0][1]["metadata"]["vzip"] == str(output)
    np.testing.assert_array_equal(layers[0][0].compute(), expected)
    np.testing.assert_array_equal(reopened[0][0].compute(), expected)


def test_archive_replacement_is_explicit_and_atomic(tmp_path, monkeypatch):
    """A refused replacement or interrupted writer must preserve existing bytes."""
    import tifffile
    from refkv.refstore import ReferenceStore

    source = tmp_path / "image.tif"
    tifffile.imwrite(source, np.zeros((8, 10), dtype="uint16"), metadata={"axes": "YX"})
    output = tmp_path / "image.vzip"
    output.write_bytes(b"existing archive")
    with pytest.raises(FileExistsError):
        read_and_save_image(str(source), str(output))
    assert output.read_bytes() == b"existing archive"

    def fail(self, path):
        Path(path).write_bytes(b"partial archive")
        raise OSError("Interrupted save")

    with monkeypatch.context() as patch:
        patch.setattr(ReferenceStore, "save", fail)
        with pytest.raises(OSError, match="Interrupted save"):
            read_and_save_image(str(source), str(output), overwrite=True)
    assert output.read_bytes() == b"existing archive"
    assert len(list(tmp_path.iterdir())) == 2
    read_and_save_image(str(source), str(output), overwrite=True)
    assert read_images(str(output))[0][0].shape == (8, 10)


def test_archive_created_during_save_is_not_overwritten(tmp_path, monkeypatch):
    """No-overwrite publication must remain safe after the initial existence check."""
    import tifffile
    from refkv.refstore import ReferenceStore

    source = tmp_path / "image.tif"
    tifffile.imwrite(source, np.zeros((8, 10), dtype="uint16"), metadata={"axes": "YX"})
    output = tmp_path / "image.vzip"
    save = ReferenceStore.save

    def create_competing_archive(self, path):
        save(self, path)
        output.write_bytes(b"another writer")

    monkeypatch.setattr(ReferenceStore, "save", create_competing_archive)
    with pytest.raises(FileExistsError):
        read_and_save_image(str(source), str(output))
    assert output.read_bytes() == b"another writer"
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("suffix", [".ome.tif", ".bin"])
def test_real_tiff_and_vzip(tmp_path, archive, suffix):
    """Detect raw images by content, optionally reopen VZIP, and never write sidecars."""
    import tifffile
    from refkv.zarrify import zarrify

    expected = np.arange(8 * 10, dtype="uint16").reshape(8, 10)
    source = tmp_path / f"image{suffix}"
    tifffile.imwrite(source, expected, metadata={"axes": "YX"})
    original = source.read_bytes()
    path = source
    if archive:
        path = tmp_path / "saved.vzip"
        zarrify(str(source)).save(path)
    layers = read_images(str(path))
    assert len(layers) == 1
    np.testing.assert_array_equal(layers[0][0].compute(), expected)
    assert source.read_bytes() == original
    assert len(list(tmp_path.iterdir())) == (2 if archive else 1)


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_root_images_and_transforms(version):
    """Root-level OME images work with both attribute conventions and transforms."""
    root = zarr.open_group(zarr.storage.MemoryStore(), mode="w")
    data = np.arange(2 * 8 * 10, dtype="uint16").reshape(2, 8, 10)
    root.create_array("0", data=data)
    ome = {
        "multiscales": [
            {
                "axes": ["c", "y", "x"],
                "datasets": [
                    {
                        "path": "0",
                        "coordinateTransformations": [
                            {"type": "scale", "scale": [1, 0.1, 0.2]},
                            {"type": "translation", "translation": [0, 5, 6]},
                        ],
                    }
                ],
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1, 2, 3]},
                    {"type": "translation", "translation": [0, 1, 2]},
                ],
            }
        ],
    }
    if version == "0.4":
        root.attrs.update(ome)
    else:
        root.attrs["ome"] = ome
    layers = _layers(root, Path("example.tif"))
    assert len(layers) == 2
    assert layers[0][1]["multiscale"] is False
    np.testing.assert_allclose(layers[0][1]["scale"], (0.2, 0.6))
    np.testing.assert_allclose(layers[0][1]["translate"], (11, 20))
    np.testing.assert_array_equal(layers[1][0].compute(), data[1])


def test_missing_input(tmp_path):
    """Missing source paths fail clearly rather than returning an empty viewer."""
    with pytest.raises(FileNotFoundError, match="does not exist"):
        read_images(str(tmp_path / "missing.czi"))


def test_non_image_dataset():
    """A valid Zarr hierarchy without OME images is not silently accepted."""
    root = zarr.open_group(zarr.storage.MemoryStore(), mode="w")
    root.create_array("tensor", data=np.zeros((8, 10)))
    with pytest.raises(ValueError, match="no supported OME image series"):
        _layers(root, Path("tensor.vzip"))


@pytest.mark.parametrize("source_name", ["plate.czi", "plate.vzip"])
def test_czi_scenes_stack_lazily_per_channel(monkeypatch, source_name):
    """Stack scene-ordered pyramids without reads and retain each stage position."""
    root = zarr.open_group(zarr.storage.MemoryStore(), mode="w")
    root.attrs["refkv_zarrified"] = {"profile": "czi"}
    data = np.arange(2 * 4 * 8 * 10, dtype="uint16").reshape(2, 4, 8, 10)
    for scene in (10, 2, 0):
        group = root.create_group(str(scene))
        group.create_array("0", data=data + scene)
        group.create_array("1", data=(data + scene)[..., ::2, ::2])
        group.attrs["ome"] = {
            "multiscales": [
                {
                    "axes": ["c", "z", "y", "x"],
                    "datasets": [
                        {
                            "path": "0",
                            "coordinateTransformations": [
                                {"type": "scale", "scale": [1, 2, 0.1, 0.1]},
                                {
                                    "type": "translation",
                                    "translation": [0, 0, scene * 10, scene * 20],
                                },
                            ],
                        },
                        {"path": "1"},
                    ],
                }
            ],
            "omero": {"channels": [{"label": "DAPI"}, {"label": "GFP"}]},
        }
    reads = []
    getitem = zarr.Array.__getitem__

    def record(array, selection):
        reads.append(selection)
        return getitem(array, selection)

    monkeypatch.setattr(zarr.Array, "__getitem__", record)
    layers = _layers(root, Path(source_name))
    assert not reads
    assert len(layers) == 2
    for channel, (levels, kwargs, _) in enumerate(layers):
        assert levels[0].shape == (3, 4, 8, 10)
        assert levels[1].shape == (3, 4, 4, 5)
        assert levels[0].chunks[0] == (1, 1, 1)
        assert kwargs["axis_labels"] == ("S", "z", "y", "x")
        assert kwargs["scale"] == (1, 2, 0.1, 0.1)
        assert kwargs["translate"] == (0, 0, 0, 0)
        assert [scene["series"] for scene in kwargs["metadata"]["scenes"]] == [
            "0",
            "2",
            "10",
        ]
        assert kwargs["metadata"]["scenes"][2]["translate"] == (0, 100, 200)
        np.testing.assert_array_equal(
            levels[0].compute(),
            np.stack([data[channel] + scene for scene in (0, 2, 10)]),
        )
        np.testing.assert_array_equal(
            levels[1].compute(),
            np.stack([data[channel, ..., ::2, ::2] + scene for scene in (0, 2, 10)]),
        )


@pytest.mark.parametrize("difference", ["shape", "scale", "dtype", "levels", "non_czi"])
def test_incompatible_or_non_czi_scenes_stay_separate(difference):
    """Never crop, resample or promote unrelated series just to make a scene stack."""
    root = zarr.open_group(zarr.storage.MemoryStore(), mode="w")
    root.attrs["refkv_zarrified"] = {"profile": "tiff" if difference == "non_czi" else "czi"}
    for scene in (0, 1):
        group = root.create_group(str(scene))
        shape = (9, 10) if scene and difference == "shape" else (8, 10)
        dtype = "uint8" if scene and difference == "dtype" else "uint16"
        scale = 0.2 if scene and difference == "scale" else 0.1
        group.create_array("0", data=np.full(shape, scene, dtype=dtype))
        datasets = [
            {
                "path": "0",
                "coordinateTransformations": [{"type": "scale", "scale": [scale, scale]}],
            }
        ]
        if scene and difference == "levels":
            group.create_array("1", data=np.full((4, 5), scene, dtype=dtype))
            datasets.append({"path": "1"})
        group.attrs["ome"] = {"multiscales": [{"axes": ["y", "x"], "datasets": datasets}]}
    layers = _layers(root, Path("example.czi"))
    assert len(layers) == 2
    for scene, (data, kwargs, _) in enumerate(layers):
        assert kwargs["axis_labels"] == ("y", "x")
        array = data[0] if kwargs["multiscale"] else data
        expected_shape = (9, 10) if scene and difference == "shape" else (8, 10)
        assert array.shape == expected_shape
        np.testing.assert_array_equal(array.compute(), np.full(expected_shape, scene))
