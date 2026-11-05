"""Read-only inspector for virtual Zarr structure and selected-layer metadata."""

import json
from pathlib import Path
from typing import Any

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
    QStyle,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import ensure_main_thread


def ensure_metadata_panel(viewer: Any) -> "MetadataPanel":
    """Share one right-hand inspector between the opener and reader workflows."""
    panels = viewer.window._qt_window.findChildren(MetadataPanel)
    if panels:
        panels[0].dock.show()
        return panels[0]
    panel = MetadataPanel()
    panel.viewer = viewer
    panel.dock = viewer.window.add_dock_widget(panel, name="Virtual Zarr metadata", area="right")
    viewer.layers.selection.events.active.connect(panel._active_layer_changed)
    panel._active_layer_changed()
    return panel


@ensure_main_thread
def prepare_metadata_inspectors() -> None:
    """Attach reader-only loads to live viewers, even when called by a worker."""
    from napari import Viewer

    for viewer in tuple(Viewer._instances):
        ensure_metadata_panel(viewer)


class MetadataPanel(QWidget):
    """Browse source hierarchy and attributes without accessing array values."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        self.source = QLabel()
        self.source.setWordWrap(True)
        self.source.setTextFormat(Qt.PlainText)
        layout.addWidget(self.source)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter metadata")
        self.search.setClearButtonEnabled(True)
        row.addWidget(self.search)
        self.tabs = QTabWidget()
        self.structure = QTreeWidget()
        self.structure.setHeaderLabels(["Zarr path", "Layout"])
        self.metadata = QTreeWidget()
        self.metadata.setHeaderLabels(["Property", "Value"])
        self.trees = (self.structure, self.metadata)
        for tooltip, icon, method in (
            ("Expand all", QStyle.SP_ArrowDown, "expandAll"),
            ("Collapse all", QStyle.SP_ArrowUp, "collapseAll"),
        ):
            button = QToolButton()
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.clicked.connect(lambda checked=False, method=method: getattr(self.tabs.currentWidget(), method)())
            row.addWidget(button)
        layout.addLayout(row)
        self.tabs.addTab(self.structure, "Zarr structure")
        self.tabs.addTab(self.metadata, "Metadata")
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(100)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        for tree in self.trees:
            tree.setAlternatingRowColors(True)
            tree.setUniformRowHeights(True)
            tree.setColumnWidth(0, 170)
            tree.currentItemChanged.connect(self._selected)
        self.search.textChanged.connect(self._filter)
        self.tabs.currentChanged.connect(lambda: self.detail.clear())

    def _property(self, parent: Any, name: str, value: Any) -> None:
        """Represent structured attributes as expandable keys and indexed lists."""
        if isinstance(value, dict):
            summary = f"{len(value)} properties"
            children = value.items()
        elif isinstance(value, (list, tuple)):
            summary = f"{len(value)} items"
            children = enumerate(value)
        else:
            summary = str(value)
            children = ()
        item = QTreeWidgetItem(parent, [str(name), summary[:200]])
        item.setData(0, Qt.UserRole, value)
        for key, child in children:
            self._property(item, str(key), child)

    def _active_layer_changed(self, event=None) -> None:
        """Follow image selection independently of the optional opener widget."""
        self.set_layer(self.viewer.layers.selection.active)

    def _node(self, parent: Any, name: str, node: dict) -> None:
        """Show array shape/dtype and make chunk grids/codecs inspectable."""
        metadata = node.get("metadata", {})
        layout = "group"
        if node["kind"] == "array":
            shape = " x ".join(str(size) for size in metadata["shape"])
            layout = f"{shape} | {metadata.get('data_type', metadata.get('dtype', ''))}"
        item = QTreeWidgetItem(parent, [name, layout])
        properties = metadata if node["kind"] == "array" else node["attributes"]
        item.setData(0, Qt.UserRole, properties)
        self._property(item, "Array metadata" if metadata else "Attributes", properties)
        for child in node.get("children", []):
            self._node(item, child["name"], child)

    def set_layer(self, layer: Any) -> None:
        """Display one selected layer, clearing stale metadata for other layers."""
        for tree in self.trees:
            tree.clear()
        self.detail.clear()
        self.source.clear()
        metadata = getattr(layer, "metadata", {})
        if not isinstance(metadata, dict) or "zarr_structure" not in metadata:
            return
        source = metadata.get("source", "")
        self.source.setText(Path(source).name)
        self.source.setToolTip(source)
        self._node(self.structure, "/", metadata["zarr_structure"])
        for key, value in metadata.items():
            if key != "zarr_structure":
                self._property(self.metadata, key, value)
        self.structure.expandToDepth(1)
        self.metadata.expandToDepth(0)
        self._filter(self.search.text())

    def _selected(self, item: Any, previous: Any) -> None:
        """Expose complete, selectable values without widening the tree columns."""
        self.detail.setPlainText(
            json.dumps(item.data(0, Qt.UserRole), indent=2, ensure_ascii=True, default=str) if item else ""
        )

    def _filter(self, text: str) -> None:
        """Retain matching rows and their ancestors in both inspector views."""
        text = text.casefold()

        def visit(item: QTreeWidgetItem) -> bool:
            child_matches = [visit(item.child(index)) for index in range(item.childCount())]
            matches = not text or text in f"{item.text(0)} {item.text(1)}".casefold() or any(child_matches)
            item.setHidden(not matches)
            if text and any(child_matches):
                item.setExpanded(True)
            return matches

        for tree in self.trees:
            for index in range(tree.topLevelItemCount()):
                visit(tree.topLevelItem(index))
