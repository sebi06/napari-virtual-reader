# Changelog

<!-- markdownlint-configure-file {"MD024": {"siblings_only": true}} -->

Notable changes to napari-virtual-reader are recorded here. Publication status
is recorded separately from the release preparation date.

## 0.0.2 - 2026-11-06

### Changed

- Added README screenshots of the plugin, image viewer, and metadata inspector.
- Used absolute screenshot URLs for GitHub and PyPI README rendering.
- Simplified the CZI scene-stacking description.
- Made the Releases section version-independent with permanent documentation links.

### Fixed

- Replaced bracketed filename patterns with simple extension filters such as `*.czi` and `*.nii.gz` in the napari manifest.

### Release Status

Patch release covering documentation and reader filename-pattern registration;
dependencies are unchanged.
Prepared for tag `v0.0.2`; GitHub Actions and PyPI publication are pending.
See the [release notes](_release_notes/v0.0.2.md) for details.

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
