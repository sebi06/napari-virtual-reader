"""Small, asynchronous file opener; napari owns all display controls."""

import logging
from pathlib import Path

import napari
from napari.qt.threading import create_worker
from qtpy.QtCore import Qt, Slot
from qtpy.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ._metadata import ensure_metadata_panel
from ._reader import IMAGE_SUFFIXES, read_and_save_image, read_images

logger = logging.getLogger(__name__)


class RefkvWidget(QWidget):
    """Open images through refkv, preparing layers away from the Qt main thread."""

    def __init__(self, viewer: napari.Viewer) -> None:
        super().__init__()
        self.viewer = viewer
        self._worker = None
        self._closed = False
        self._archive_path = None
        self.metadata_panel = ensure_metadata_panel(viewer)
        self._metadata_dock = self.metadata_panel.dock
        self.setMinimumWidth(260)
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.filename = QLineEdit()
        self.filename.setPlaceholderText("Image file or VZIP archive")
        self.filename.setAcceptDrops(False)
        self.filename.textChanged.connect(self._update_open)
        self.filename.returnPressed.connect(self._open)
        self.browse = QToolButton()
        self.browse.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.browse.setToolTip("Choose image file or VZIP archive")
        self.browse.setAccessibleName("Choose image file")
        self.browse.clicked.connect(self._choose_file)
        row.addWidget(self.filename)
        row.addWidget(self.browse)
        layout.addLayout(row)
        self.save_archive = QCheckBox("Save VZIP archive")
        self.save_archive.setToolTip("Save references without copying image pixels")
        layout.addWidget(self.save_archive)
        self.open_button = QPushButton("Open")
        self.open_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.open_button.clicked.connect(self._open)
        layout.addWidget(self.open_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        layout.addStretch()
        self._update_open()

    def _update_open(self) -> None:
        """Enable opening only when there is an input and no active worker."""
        self.filename.setToolTip(self.filename.text())
        self.open_button.setEnabled(bool(self.filename.text().strip()) and self._worker is None)
        self.save_archive.setEnabled(
            self._worker is None and Path(self.filename.text().strip()).suffix.lower() != ".vzip"
        )

    def _choose_file(self) -> None:
        """Select an input without generating files or changing source images."""
        patterns = " ".join(f"*{suffix}" for suffix in IMAGE_SUFFIXES)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open as virtual OME-Zarr",
            self.filename.text(),
            f"Images and archives ({patterns});;All files (*)",
        )
        if path:
            self.filename.setText(path)

    def _open(self) -> None:
        """Snapshot the source and build metadata and lazy layer graphs off-thread."""
        path = self.filename.text().strip()
        if not path or self._worker is not None:
            return
        self._archive_path = None
        operation = read_images
        arguments = (path,)
        options = {}
        if self.save_archive.isChecked() and Path(path).suffix.lower() != ".vzip":
            destination, _ = QFileDialog.getSaveFileName(
                self,
                "Save VZIP archive",
                str(Path(path).with_suffix(".vzip")),
                "VZIP archives (*.vzip)",
                options=QFileDialog.DontConfirmOverwrite,
            )
            if not destination:
                return
            output = Path(destination).expanduser().resolve()
            if not output.suffix:
                output = output.with_suffix(".vzip")
            if output == Path(path).expanduser().resolve() or output.suffix.lower() != ".vzip":
                self._failed(ValueError("Choose a separate .vzip archive destination."))
                return
            overwrite = output.exists()
            if (
                overwrite
                and QMessageBox.question(
                    self,
                    "Replace VZIP archive?",
                    f"Replace {output.name}?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
            self._archive_path = output
            operation = read_and_save_image
            arguments = (path, str(output))
            options = {"overwrite": overwrite}
        self._closed = False
        self.status.setText(f"Opening {Path(path).name}...")
        self._worker = create_worker(
            operation,
            *arguments,
            _start_thread=False,
            _ignore_errors=True,
            **options,
        )
        self._worker.returned.connect(self._loaded)
        self._worker.errored.connect(self._failed)
        self._worker.finished.connect(self._finished)
        self.filename.setEnabled(False)
        self.browse.setEnabled(False)
        self._update_open()
        self.progress.show()
        self._worker.start()

    @Slot(object)
    def _loaded(self, layers: list) -> None:
        """Add layers on the owning viewer's Qt thread, not in the worker."""
        if self._closed:
            return
        added = []
        try:
            for data, kwargs, _ in layers:
                added.append(self.viewer.add_image(data, **kwargs))
        except Exception as error:
            for layer in added:
                self.viewer.layers.remove(layer)
            self._failed(error)
            return
        message = f"Opened {len(added)} image layer(s)"
        if self._archive_path is not None:
            message += f"; saved {self._archive_path.name}"
        self.status.setText(message)
        self.status.setToolTip(str(self._archive_path) if self._archive_path else "")

    @Slot(object)
    def _failed(self, error: Exception) -> None:
        """Report external file/codec errors without silently switching readers."""
        from napari.utils.notifications import show_error

        logger.error(
            "Virtual image opening failed: %s",
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
        if not self._closed:
            self.status.setText(str(error))
            show_error(str(error))

    def _finished(self) -> None:
        """Restore controls after either successful preparation or failure."""
        self._worker = None
        self.progress.hide()
        self.filename.setEnabled(True)
        self.browse.setEnabled(True)
        self._update_open()

    def dragEnterEvent(self, event) -> None:
        """Accept a single local file when no load is in progress."""
        urls = event.mimeData().urls()
        if self._worker is None and len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        """Select a dropped input; opening remains an explicit action."""
        urls = event.mimeData().urls()
        if self._worker is None and len(urls) == 1 and urls[0].isLocalFile():
            self.filename.setText(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            event.ignore()

    def closeEvent(self, event) -> None:
        """Discard worker results if the dock is closed during preparation."""
        self._closed = True
        super().closeEvent(event)
