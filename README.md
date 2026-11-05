# napari-virtual-reader

**Experimental software - Alpha status.** APIs and behavior may change without
notice. Validate results independently before relying on them for analysis.

Open image files as lazy, reference-backed OME-Zarr in napari. No pixel
conversion, copied image data, or intermediate archive is required.

The plugin uses refkv directly. It does not depend on czitools, pylibCZIrw,
or the napari-czitools plugin.

## Install

Python 3.12 or newer is required. Install into the environment running napari:

```powershell
python -m pip install napari-virtual-reader
```

A working Qt backend is required. For a fresh environment:

```powershell
python -m pip install "napari[pyqt6]" napari-virtual-reader
napari
```

Restart napari after installation. The package registers an npe2 reader and
widget through its `napari.manifest` entry point; no manual registration is
required. Check **Plugins > Plugin Manager** for **napari-virtual-reader**.

## Open an image

In napari, choose **Plugins > napari-virtual-reader > Open Data as virtual OME-ZARR**,
select the input, and click **Open**. Preparation runs in a background worker; napari
provides its normal layer, contrast, time, and Z controls.

You can also use **File > Open** or drop a file on the napari canvas and choose
the napari-virtual-reader reader. Drop a file on the plugin dock to select it without
opening it yet.

```python
import napari

viewer = napari.Viewer()
viewer.open("image.czi", plugin="napari-virtual-reader")
napari.run()
```

Raw images are virtualized in memory through `refkv.zarrify.zarrify`. Existing
`.vzip` archives are opened directly. Original image files must remain available
and unchanged for as long as their layers are used. Nothing is written beside
the source by default.

### Save a VZIP Archive

In **Plugins > napari-virtual-reader > Open Data as virtual OME-ZARR**, select an image and enable
**Save VZIP archive** before clicking **Open**. Choose the destination in the
save dialog; the suggested filename is a sibling `.vzip`. Saving runs in the
background, and the resulting image still opens lazily with the metadata panel.
The status shows the saved filename, with its full path available on hover.

Saving stores references to the original image, not converted pixel data.
The archive covers the complete source, including all scenes and pyramid levels.
Existing destinations require explicit replacement confirmation; failed writes
leave the old archive intact. Cancelling the save dialog cancels the open action.
The save option is disabled for inputs that are already VZIP archives.
File/Open and canvas drops remain in-memory operations without automatic saving.

## Metadata Inspector

The **Virtual Zarr metadata** dock opens on the right when using the refkv reader
through File/Open or drag-and-drop, without opening the plugin widget first.
It also appears with **Plugins > napari-virtual-reader > Open Data as virtual OME-ZARR**.
Both workflows share one inspector that follows the selected image layer.

- **Zarr structure** shows expandable groups and arrays, including original
  shapes, dtypes, attributes, chunk grids and codec configuration.
- **Metadata** shows the selected layer's OME metadata, source and channel
  information, plus original scene transforms for stacked CZI images.
- A filter finds matching properties; selecting a row shows its complete,
  selectable value below the tree.

The inspector uses a metadata-only snapshot collected in the background. It
does not scan pixel data or reconstruct the source hierarchy from displayed
layer shapes. Selecting an unrelated layer clears the inspector.

## Scope

- CZI, TIFF/OME-TIFF, and other refkv-supported files that expose OME image series.
  Reader registration includes common microscopy and volumetric image extensions;
  actual support is determined by refkv's parser and the file's metadata/codecs.
- One layer per channel. Compatible CZI scenes are stacked lazily along a leading
  **S** axis, in numeric series order; for example, 28 equally sized 2D scenes
  become one `(28, Y, X)` layer per channel. This also applies to CZI-derived
  VZIP archives. Other formats retain separate series layers.
- CZI stacking requires matching axes, shapes, dtypes, physical scales and
  pyramid levels. Incompatible scenes remain separate without cropping or
  resampling. Scene-dependent stage origins are replaced by local coordinates
  on the stacked layer; original origins and metadata remain available in
  the layer's `metadata["scenes"]` list.
- Stored multiscale pyramids, OME axis ordering, physical scale/origin, channel
  names, colors, and display windows.
- Regular and rectilinear chunk grids, without scanning complete lazy arrays
  for contrast limits. Files without display windows use dtype bounds (0-1 for
  floating point); adjust contrast in napari as needed.
- OME-Zarr 0.4 and 0.5 image metadata, including nested image hierarchies.

This is deliberately not a replacement for every refkv dataset interface:
non-image tables, audio, arbitrary tensors, remote inputs, and stores without
OME image metadata are outside this first version. There are no custom dimension
sliders or CZI-specific options.

## Development

```powershell
git clone https://github.com/sebi06/napari-virtual-reader.git
cd napari-virtual-reader
python -m pip install -e ".[testing]"
python -m pytest
python -m ruff check .
```

Install a Qt backend as above if the development environment does not already
provide one. Development installs register the same napari plugin as PyPI installs.

## Releases

GitHub Actions tests pull requests and pushes to `main`. Publishing runs only
after the test matrix passes on a release tag such as `v0.0.1`.

Set `project.version` in `pyproject.toml` to the intended release version before
tagging; tags do not change the package version. PyPI requires a new
version for every upload. Manual workflow runs test and build without publishing.

See the [changelog](CHANGELOG.md) for the change history and the
[release notes](_release_notes/v0.0.1.md) for v0.0.1 features, validation, and
limitations. Publication status is recorded in those files and updated after
GitHub Actions and PyPI publication are verified.
