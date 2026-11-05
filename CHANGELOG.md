# Changelog

Notable changes to napari-virtual-reader are recorded here. Publication status
is recorded separately from the release preparation date.

## 0.0.1 - 2026-11-05

### Added

- Experimental Alpha napari plugin with npe2 reader and widget registration.
- Lazy, reference-backed OME-Zarr image loading through refkv.
- Optional VZIP reference-archive saving without pixel conversion.
- Shared metadata inspector for Zarr structure and selected-layer metadata.
- Compatible CZI scene stacking and stored multiscale pyramid support.
- Automated tests, package validation, and tag-triggered PyPI publishing.

### Changed

- Renamed the project from napari-refkv to napari-virtual-reader.

### Release Status

Initial experimental Alpha release, prepared for tag `v0.0.1`. GitHub Actions
and PyPI publication are pending. See the [release notes](_release_notes/v0.0.1.md)
for features, validation, and known limitations.
