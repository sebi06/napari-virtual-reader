"""The minimal opener keeps preparation off-thread and display on the Qt thread."""

import threading
from pathlib import Path
from unittest.mock import Mock

import dask.array as da
import numpy as np
import pytest
import zarr
from qtpy.QtCore import Qt

from napari_refkv._reader import _layers
from napari_refkv._widget import RefkvWidget


def test_worker_opens_on_main_thread(qtbot, monkeypatch):
    """Worker preparation must not mutate the viewer."""
    main_thread = threading.get_ident()
    worker_threads = []
    display_threads = []
    viewer = Mock()
    viewer.window._qt_window.findChildren.return_value = []
    viewer.add_image.side_effect = lambda *args, **kwargs: display_threads.append(threading.get_ident())

    def prepare(path):
        worker_threads.append(threading.get_ident())
        return [(da.zeros((8, 10)), {"contrast_limits": (0, 1)}, "image")]

    monkeypatch.setattr("napari_refkv._widget.read_images", prepare)
    widget = RefkvWidget(viewer)
    qtbot.addWidget(widget)
    assert widget.minimumWidth() == 260
    assert not widget.open_button.isEnabled()
    widget.filename.setText("image.czi")
    assert widget.filename.toolTip() == "image.czi"
    qtbot.mouseClick(widget.open_button, Qt.LeftButton)
    assert not widget.open_button.isEnabled()
    qtbot.waitUntil(lambda: widget._worker is None, timeout=10000)
    assert worker_threads[0] != main_thread
    assert display_threads == [main_thread]
    assert widget.open_button.isEnabled()
    assert widget.progress.isHidden()


def test_failure_restores_controls(qtbot, monkeypatch):
    """Errors remain visible, do not add layers, and permit retry."""
    viewer = Mock()
    viewer.window._qt_window.findChildren.return_value = []
    notify = Mock()
    monkeypatch.setattr("napari.utils.notifications.show_error", notify)

    def fail(path):
        raise ValueError("Unsupported image")

    monkeypatch.setattr("napari_refkv._widget.read_images", fail)
    widget = RefkvWidget(viewer)
    qtbot.addWidget(widget)
    widget.filename.setText("image.bin")
    widget._open()
    qtbot.waitUntil(lambda: widget._worker is None, timeout=10000)
    assert widget.status.text() == "Unsupported image"
    assert widget.open_button.isEnabled()
    viewer.add_image.assert_not_called()
    notify.assert_called_once_with("Unsupported image")


def test_closed_dock_discards_results(qtbot):
    """Closing during preparation must not create unexpected viewer layers."""
    viewer = Mock()
    viewer.window._qt_window.findChildren.return_value = []
    widget = RefkvWidget(viewer)
    qtbot.addWidget(widget)
    widget.close()
    widget._loaded([(da.zeros((8, 10)), {}, "image")])
    viewer.add_image.assert_not_called()


@pytest.mark.parametrize("already_open", [False, True])
def test_metadata_dock_follows_active_layer(qtbot, make_napari_viewer, already_open):
    """Use one right-side inspector for existing images and later reader loads."""
    viewer = make_napari_viewer()
    root = zarr.open_group(zarr.storage.MemoryStore(), mode="w")
    root.create_array("0", data=np.zeros((8, 10), dtype="uint16"))
    root.attrs["ome"] = {"multiscales": [{"axes": ["y", "x"], "datasets": [{"path": "0"}]}]}
    data, kwargs, _ = _layers(root, Path("example.tif"))[0]
    first = viewer.add_image(data, **kwargs) if already_open else None
    widget = RefkvWidget(viewer)
    qtbot.addWidget(widget)
    if first is None:
        first = viewer.add_image(data, **kwargs)
    dock = widget._metadata_dock
    assert dock is not None
    assert viewer.window._qt_window.dockWidgetArea(dock) == Qt.RightDockWidgetArea
    panel = widget.metadata_panel
    assert panel.source.text() == "example.tif"
    assert panel.structure.topLevelItem(0).text(0) == "/"
    arrays = [
        item
        for item in panel.structure.findItems("0", Qt.MatchExactly | Qt.MatchRecursive, 0)
        if item.parent() is panel.structure.topLevelItem(0)
    ]
    assert len(arrays) == 1
    assert "8 x 10" in arrays[0].text(1)
    properties = panel.structure.findItems("data_type", Qt.MatchExactly | Qt.MatchRecursive, 0)
    panel.structure.setCurrentItem(properties[0])
    assert panel.detail.toPlainText() == '"uint16"'
    panel.search.setText("data_type")
    assert not properties[0].isHidden()
    assert not arrays[0].isHidden()
    hidden = panel.structure.findItems("ome", Qt.MatchExactly | Qt.MatchRecursive, 0)[0]
    assert hidden.isHidden()
    panel.search.clear()
    assert not hidden.isHidden()
    viewer.add_image(np.zeros((8, 10)), name="Unrelated image")
    assert panel.structure.topLevelItemCount() == 0
    assert panel.source.text() == ""
    viewer.layers.selection.active = first
    assert widget._metadata_dock is dock
    assert panel.source.text() == "example.tif"
    assert panel.metadata.findItems("ome", Qt.MatchExactly | Qt.MatchRecursive, 0)


def test_reader_only_open_creates_metadata_dock(tmp_path, qtbot, make_napari_viewer):
    """File/Open must show metadata without first opening the plugin widget."""
    import tifffile

    from napari_refkv._metadata import MetadataPanel

    source = tmp_path / "image.tif"
    tifffile.imwrite(source, np.zeros((8, 10), dtype="uint16"), metadata={"axes": "YX"})
    viewer = make_napari_viewer()
    assert not viewer.window._qt_window.findChildren(RefkvWidget)
    viewer.open(str(source), plugin="napari-refkv")
    qtbot.waitUntil(lambda: bool(viewer.window._qt_window.findChildren(MetadataPanel)))
    panels = viewer.window._qt_window.findChildren(MetadataPanel)
    assert len(panels) == 1
    panel = panels[0]
    assert viewer.window._qt_window.dockWidgetArea(panel.dock) == Qt.RightDockWidgetArea
    assert panel.source.text() == "image.tif"
    assert not panel.dock.isHidden()
    assert not viewer.window._qt_window.findChildren(RefkvWidget)
    panel.dock.hide()
    viewer.open(str(source), plugin="napari-refkv")
    assert not panel.dock.isHidden()
    assert len(viewer.window._qt_window.findChildren(MetadataPanel)) == 1
    widget = RefkvWidget(viewer)
    qtbot.addWidget(widget)
    assert widget.metadata_panel is panel


def test_widget_saves_archive_in_background(tmp_path, qtbot, make_napari_viewer, monkeypatch):
    """The opt-in worker writes an archive and keeps metadata and viewer loading."""
    import tifffile

    from napari_refkv._reader import read_images

    source = tmp_path / "image.tif"
    output = tmp_path / "chosen.vzip"
    expected = np.arange(80, dtype="uint16").reshape(8, 10)
    tifffile.imwrite(source, expected, metadata={"axes": "YX"})
    viewer = make_napari_viewer()
    widget = RefkvWidget(viewer)
    qtbot.addWidget(widget)
    assert not widget.save_archive.isChecked()
    widget.filename.setText(str(source))
    widget.save_archive.setChecked(True)
    monkeypatch.setattr("napari_refkv._widget.QFileDialog.getSaveFileName", lambda *args, **kwargs: (str(output), ""))
    widget._open()
    assert not widget.save_archive.isEnabled()
    qtbot.waitUntil(lambda: widget._worker is None, timeout=10000)
    assert output.is_file()
    assert len(viewer.layers) == 1
    assert "saved chosen.vzip" in widget.status.text()
    assert widget.save_archive.isEnabled()
    np.testing.assert_array_equal(read_images(str(output))[0][0].compute(), expected)
    widget.filename.setText(str(output))
    assert not widget.save_archive.isEnabled()


@pytest.mark.parametrize("choice", ["cancel", "refuse", "replace"])
def test_save_dialog_cancellation_and_replacement(tmp_path, qtbot, monkeypatch, choice):
    """Do not start a worker without a destination and explicit replacement consent."""
    from qtpy.QtWidgets import QMessageBox

    viewer = Mock()
    viewer.window._qt_window.findChildren.return_value = []
    widget = RefkvWidget(viewer)
    qtbot.addWidget(widget)
    widget.filename.setText(str(tmp_path / "image.tif"))
    widget.save_archive.setChecked(True)
    output = tmp_path / "existing.vzip"
    output.write_bytes(b"existing archive")
    destination = "" if choice == "cancel" else str(output)
    monkeypatch.setattr("napari_refkv._widget.QFileDialog.getSaveFileName", lambda *args, **kwargs: (destination, ""))
    question = Mock(return_value=QMessageBox.Yes if choice == "replace" else QMessageBox.No)
    monkeypatch.setattr("napari_refkv._widget.QMessageBox.question", question)
    create_worker = Mock()
    monkeypatch.setattr("napari_refkv._widget.create_worker", create_worker)
    widget._open()
    if choice == "replace":
        assert create_worker.call_args.args[1:] == (widget.filename.text(), str(output))
        assert create_worker.call_args.kwargs["overwrite"] is True
        create_worker.return_value.start.assert_called_once()
    else:
        create_worker.assert_not_called()
        assert widget.open_button.isEnabled()
    assert output.read_bytes() == b"existing archive"
