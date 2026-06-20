from __future__ import annotations

import webbrowser
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    start_requested = pyqtSignal(dict)
    stop_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def closeEvent(self, event) -> None:
        self.close_requested.emit()
        event.accept()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lisa LiveTrading")
        self.resize(640, 480)
        self._last_output_folder: Path | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel("File (ảnh/video):"))
        self.ref_edit = QLineEdit()
        ref_row.addWidget(self.ref_edit, 1)
        ref_btn = QPushButton("Browse")
        ref_btn.clicked.connect(self._browse_ref)
        ref_row.addWidget(ref_btn)
        root.addLayout(ref_row)

        pr_row = QHBoxLayout()
        pr_row.addWidget(QLabel("Prompts:"))
        self.prompts_edit = QLineEdit()
        pr_row.addWidget(self.prompts_edit, 1)
        pr_btn = QPushButton("Browse")
        pr_btn.clicked.connect(self._browse_prompts)
        pr_row.addWidget(pr_btn)
        root.addLayout(pr_row)

        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Aspect:"))
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(["9:16", "16:9", "1:1"])
        opt_row.addWidget(self.aspect_combo)
        opt_row.addSpacing(20)
        opt_row.addWidget(QLabel("Duration:"))
        self.duration_combo = QComboBox()
        self.duration_combo.addItems(["5", "10", "15"])
        self.duration_combo.setCurrentText("10")
        opt_row.addWidget(self.duration_combo)
        opt_row.addSpacing(20)
        opt_row.addWidget(QLabel("Số prompt (0 = all):"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 9999)
        self.limit_spin.setValue(0)
        opt_row.addWidget(self.limit_spin)
        opt_row.addStretch(1)
        root.addLayout(opt_row)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._emit_start)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        self.open_btn = QPushButton("Open Folder")
        self.open_btn.clicked.connect(self._open_folder_clicked)
        self.open_btn.setEnabled(False)
        btn_row.addWidget(self.open_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.status_label = QLabel("Ready.")
        root.addWidget(self.status_label)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

    def _browse_ref(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self,
            "Pick reference image or video",
            filter="Image or Video (*.png *.jpg *.jpeg *.mp4 *.mov *.webm *.mkv);;All files (*.*)",
        )
        if p:
            self.ref_edit.setText(p)

    def _browse_prompts(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Pick prompts.json", filter="JSON (*.json)")
        if p:
            self.prompts_edit.setText(p)

    def _emit_start(self) -> None:
        payload = {
            "ref": self.ref_edit.text().strip(),
            "prompts": self.prompts_edit.text().strip(),
            "aspect": self.aspect_combo.currentText(),
            "duration": int(self.duration_combo.currentText()),
            "limit": int(self.limit_spin.value()),
        }
        if not payload["ref"] or not payload["prompts"]:
            self.set_status("Pick both ref and prompts before starting.")
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.start_requested.emit(payload)

    def _open_folder_clicked(self) -> None:
        if self._last_output_folder and self._last_output_folder.exists():
            webbrowser.open(self._last_output_folder.as_uri())

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def set_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)

    def set_output_folder(self, folder: Path) -> None:
        self._last_output_folder = folder
        self.open_btn.setEnabled(True)

    def reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
