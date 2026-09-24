from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, QSize, QSettings
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QFileDialog, QScrollArea, QVBoxLayout, QWidget,
)

from .core import TITLE_ID, Game, Update, download_update, fetch_updates, find_library_root, pending_updates, scan_library


def resource(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets" / name


def human_size(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB" if size < 1024**3 else f"{size / 1024**3:.2f} GB"


class Worker(QObject):
    checked = Signal(str, str, object)
    downloaded = Signal(str, str)
    progress = Signal(str, int, int)
    error = Signal(str, str)
    finished = Signal()

    def __init__(self, mode: str, games: list[Game] | None = None, updates: list[Update] | None = None,
                 title_id: str = "", destination: Path | None = None):
        super().__init__()
        self.mode = mode
        self.games = games or []
        self.updates = updates or []
        self.title_id = title_id
        self.destination = destination

    def run(self):
        try:
            if self.mode == "check":
                for game in self.games:
                    try:
                        title, updates = fetch_updates(game.title_id)
                        self.checked.emit(game.title_id, title, updates)
                    except Exception as exc:
                        self.error.emit(game.title_id, str(exc))
            elif self.mode == "download" and self.destination:
                for update in self.updates:
                    try:
                        path = download_update(
                            update, self.title_id, self.destination,
                            lambda done, total: self.progress.emit(self.title_id, done, total),
                        )
                        self.downloaded.emit(self.title_id, str(path))
                    except Exception as exc:
                        self.error.emit(self.title_id, f"v{update.version}: {exc}")
                        break
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vita Pulse — Game Updater for Vita3K")
        self.setMinimumSize(1060, 780)
        self.resize(1280, 900)
        self.setWindowIcon(QIcon(str(resource("vita-pulse.png"))))
        self.settings = QSettings("Vita Pulse", "Game Updater")
        saved_root = self.settings.value("library_root", "")
        self.root = find_library_root(Path(saved_root)) if saved_root else find_library_root()
        self.download_dir = Path(self.settings.value("download_dir", str(Path.home() / "Downloads" / "Vita3K Game Updates")))
        self.games: list[Game] = []
        self.available: dict[str, list[Update]] = {}
        self.selected_id: str | None = None
        self.busy = False
        self.current_mode = ""
        self.check_errors = 0
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._build()
        self._style()
        self.reload_library()

    def _label(self, text: str, kind: str = "") -> QLabel:
        label = QLabel(text)
        if kind:
            label.setObjectName(kind)
        return label

    def _button(self, text: str, action, kind: str = "secondary") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(kind)
        button.clicked.connect(action)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _build(self):
        center = QWidget()
        self.setCentralWidget(center)
        page = QVBoxLayout(center)
        page.setContentsMargins(32, 24, 32, 24)
        page.setSpacing(22)

        top = QHBoxLayout()
        top.setSpacing(15)
        logo = QLabel()
        logo.setPixmap(QIcon(str(resource("vita-pulse.png"))).pixmap(QSize(54, 54)))
        top.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(self._label("VITA PULSE", "brand"))
        brand.addWidget(self._label("GAME UPDATE STUDIO", "eyebrow"))
        top.addLayout(brand)
        top.addStretch()
        top.addWidget(self._button("Choose Vita3K folder", self.choose_root))
        top.addWidget(self._button("Download location", self.choose_download))
        page.addLayout(top)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 23, 28, 23)
        hero_layout.setSpacing(8)
        hero_layout.addWidget(self._label("YOUR LIBRARY, IN SYNC", "eyebrow"))
        hero_layout.addWidget(self._label("The next chapter is ready.", "heroTitle"))
        self.hero_subtitle = self._label("Select your Vita3K content folder to scan installed games.", "heroBody")
        self.hero_subtitle.setWordWrap(True)
        hero_layout.addWidget(self.hero_subtitle)
        page.addWidget(hero)

        body = QHBoxLayout()
        body.setSpacing(20)
        left = QFrame()
        left.setObjectName("panel")
        left.setMinimumWidth(450)
        l = QVBoxLayout(left)
        l.setContentsMargins(22, 20, 22, 20)
        l.setSpacing(14)
        row = QHBoxLayout()
        row.addWidget(self._label("Installed games", "section"))
        row.addStretch()
        self.count_label = self._label("0 TITLES", "eyebrow")
        row.addWidget(self.count_label)
        l.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by game name or title ID")
        self.search.textChanged.connect(self.populate_list)
        l.addWidget(self.search)
        self.game_list = QListWidget()
        self.game_list.currentItemChanged.connect(self.on_selected)
        l.addWidget(self.game_list, 1)
        actions = QHBoxLayout()
        self.check_button = self._button("Check all updates", self.check_all, "primary")
        actions.addWidget(self.check_button)
        actions.addWidget(self._button("Lookup title ID", self.lookup_id))
        actions.addWidget(self._button("Rescan", self.reload_library))
        l.addLayout(actions)
        body.addWidget(left, 5)

        right = QFrame()
        right.setObjectName("panel")
        r = QVBoxLayout(right)
        r.setContentsMargins(25, 22, 25, 22)
        r.setSpacing(15)
        r.addWidget(self._label("TITLE INSPECTOR", "eyebrow"))
        self.detail_icon = QLabel()
        self.detail_icon.setFixedSize(72, 72)
        self.detail_icon.hide()
        r.addWidget(self.detail_icon)
        self.detail_title = self._label("Select a game", "detailTitle")
        self.detail_title.setWordWrap(True)
        r.addWidget(self.detail_title)
        self.detail_id = self._label("Explore updates and download verified packages.", "muted")
        self.detail_id.setWordWrap(True)
        r.addWidget(self.detail_id)
        self.detail_state = self._label("READY TO SCAN", "status")
        r.addWidget(self.detail_state)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("rule")
        r.addWidget(line)
        r.addWidget(self._label("AVAILABLE RELEASES", "eyebrow"))
        self.release_area = QScrollArea()
        self.release_area.setWidgetResizable(True)
        self.release_area.setFrameShape(QFrame.NoFrame)
        self.release_content = QWidget()
        self.release_layout = QVBoxLayout(self.release_content)
        self.release_layout.setContentsMargins(0, 0, 0, 0)
        self.release_layout.setSpacing(9)
        self.release_layout.addStretch()
        self.release_area.setWidget(self.release_content)
        r.addWidget(self.release_area, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        r.addWidget(self.progress_bar)
        self.progress_text = self._label("", "muted")
        r.addWidget(self.progress_text)
        self.download_button = self._button("Download available updates", self.download_selected, "primary")
        self.download_button.setEnabled(False)
        r.addWidget(self.download_button)
        self.open_folder_button = self._button("Open package folder", self.open_packages)
        r.addWidget(self.open_folder_button)
        self.install_hint = self._label(
            "Install verified .pkg files in Vita3K through File → Install Package. "
            "Vita3K handles the final installation and may request your own license data.", "muted"
        )
        self.install_hint.setWordWrap(True)
        r.addWidget(self.install_hint)
        body.addWidget(right, 4)
        page.addLayout(body, 1)

        footer = QHBoxLayout()
        self.path_label = self._label("NO LIBRARY SELECTED", "footer")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        footer.addWidget(self.path_label, 1)
        self.activity = self._label("Idle", "footer")
        footer.addWidget(self.activity)
        page.addLayout(footer)

    def _style(self):
        self.setStyleSheet("""
            QWidget { background: #0b1220; color: #e7f2fc; font-family: 'Segoe UI'; font-size: 13px; }
            QFrame#panel { background: #121e30; border: 1px solid #283c55; border-radius: 18px; }
            QFrame#hero { background: #172840; border: 1px solid #31536a; border-radius: 19px; }
            QLabel { background: transparent; }
            QLabel#brand { font-size: 20px; font-weight: 800; letter-spacing: 3px; color: #edffff; }
            QLabel#eyebrow { color: #76dbc9; font-size: 10px; font-weight: 800; letter-spacing: 2px; }
            QLabel#heroTitle { font-size: 30px; font-weight: 750; color: #f7ffff; }
            QLabel#heroBody { color: #a9c5d7; font-size: 14px; }
            QLabel#section { font-size: 18px; font-weight: 700; }
            QLabel#detailTitle { font-size: 23px; font-weight: 750; }
            QLabel#muted, QLabel#footer { color: #96adbf; }
            QLabel#footer { font-size: 11px; }
            QLabel#status { background: #183c45; color: #82ead5; border-radius: 9px; padding: 9px 13px; font-weight: 800; }
            QLineEdit { background: #0d1929; border: 1px solid #30445c; border-radius: 10px; padding: 12px; selection-background-color: #42b9b8; }
            QLineEdit:focus { border-color: #58d7c7; }
            QListWidget { background: transparent; border: none; outline: none; }
            QListWidget::item { padding: 2px; margin-bottom: 7px; border: 1px solid #2a4056; border-radius: 10px; background: #17263a; }
            QListWidget::item:selected { background: #1e4556; border: 1px solid #5dddd0; color: #ffffff; }
            QListWidget::item:hover { background: #203950; }
            QLabel#listTitle { font-size: 14px; font-weight: 700; color: #f1f8ff; }
            QLabel#listMeta { font-size: 11px; color: #9bb8c9; }
            QPushButton { min-height: 24px; background: #1b3047; border: 1px solid #3b5b72; border-radius: 10px; padding: 11px 16px; font-weight: 650; }
            QPushButton:hover { background: #28435c; border-color: #72e7d2; }
            QPushButton#primary { background: #5ce0c9; border-color: #5ce0c9; color: #0a2230; font-weight: 800; }
            QPushButton#primary:hover { background: #9bf5e3; }
            QPushButton:disabled { background: #2b3b4a; border-color: #2b3b4a; color: #718596; }
            QScrollArea, QScrollArea QWidget { background: transparent; }
            QProgressBar { background: #142539; border: 0; border-radius: 5px; height: 10px; }
            QProgressBar::chunk { background: #69e5ce; border-radius: 5px; }
            QFrame#rule { color: #34495f; }
        """)

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(self, "Choose Vita3K content folder", str(self.root or Path.home()))
        if path:
            root = find_library_root(Path(path))
            if not root:
                QMessageBox.warning(self, "Folder not found", "Choose the Vita3K folder that contains ux0/app, or its parent folder.")
                return
            self.root = root
            self.settings.setValue("library_root", str(root))
            self.reload_library()

    def choose_download(self):
        path = QFileDialog.getExistingDirectory(self, "Choose package download folder", str(self.download_dir))
        if path:
            self.download_dir = Path(path)
            self.settings.setValue("download_dir", path)
            self.activity.setText(f"Downloads: {path}")

    def reload_library(self):
        self.available.clear()
        self.games = []
        if self.root:
            try:
                self.games = scan_library(self.root)
                self.path_label.setText(str(self.root))
                self.hero_subtitle.setText(f"{len(self.games)} installed titles found. Check Sony's update feed to see what is ready.")
            except Exception as exc:
                QMessageBox.warning(self, "Library scan failed", str(exc))
        else:
            self.path_label.setText("NO LIBRARY SELECTED")
        self.count_label.setText(f"{len(self.games)} TITLES")
        self.populate_list()

    def populate_list(self):
        query = self.search.text().lower().strip()
        keep = self.selected_id
        self.game_list.blockSignals(True)
        self.game_list.clear()
        for game in self.games:
            if query and query not in game.title.lower() and query not in game.title_id.lower():
                continue
            updates = pending_updates(game.installed_version, self.available.get(game.title_id, []))
            suffix = f"  ·  {len(updates)} update(s) ready" if updates else ""
            item = QListWidgetItem()
            item.setData(Qt.UserRole, game.title_id)
            item.setSizeHint(QSize(0, 68))
            self.game_list.addItem(item)
            card = QWidget()
            card.setStyleSheet("background: transparent;")
            card.setAttribute(Qt.WA_TransparentForMouseEvents)
            stack = QVBoxLayout(card)
            stack.setContentsMargins(10, 6, 10, 6)
            stack.setSpacing(2)
            name = self._label(game.title, "listTitle")
            name.setToolTip(game.title)
            meta = self._label(f"{game.title_id}  ·  v{game.installed_version}{suffix}", "listMeta")
            stack.addWidget(name)
            stack.addWidget(meta)
            self.game_list.setItemWidget(item, card)
            if game.title_id == keep:
                self.game_list.setCurrentItem(item)
        self.game_list.blockSignals(False)
        if self.game_list.currentItem() is None and self.game_list.count():
            self.game_list.setCurrentRow(0)
        else:
            self.refresh_detail()

    def on_selected(self, current, _previous):
        self.selected_id = current.data(Qt.UserRole) if current else None
        self.refresh_detail()

    def refresh_detail(self):
        game = next((g for g in self.games if g.title_id == self.selected_id), None)
        while self.release_layout.count() > 1:
            item = self.release_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not game:
            self.detail_title.setText("Select a game")
            self.detail_id.setText("Explore updates and download verified packages.")
            self.detail_state.setText("READY TO SCAN")
            self.detail_icon.clear()
            self.detail_icon.hide()
            self.download_button.hide()
            self.open_folder_button.hide()
            self.release_layout.insertWidget(0, self._label("Select a title to see its releases.", "muted"))
            return
        self.detail_title.setText(game.title)
        version_text = "Title ID lookup" if game.installed_version == "0.00" else f"Installed version {game.installed_version}"
        self.detail_id.setText(f"{game.title_id}  •  {version_text}")
        self.detail_icon.show()
        self.download_button.show()
        self.open_folder_button.show()
        icon = QPixmap(str(game.icon_path)) if game.icon_path else QPixmap()
        if icon.isNull():
            icon = QIcon(str(resource("vita-pulse.png"))).pixmap(QSize(72, 72))
        self.detail_icon.setPixmap(icon.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        updates = self.available.get(game.title_id)
        pending = pending_updates(game.installed_version, updates or [])
        if updates is None:
            state = "NOT CHECKED YET"
        elif pending:
            state = f"{len(pending)} UPDATE(S) AVAILABLE"
        else:
            state = "UP TO DATE / NO UPDATE FOUND"
        self.detail_state.setText(state)
        self.download_button.setEnabled(bool(pending) and not self.busy)
        if not updates:
            self.release_layout.insertWidget(0, self._label("Run an update check to see release details.", "muted"))
        else:
            for update in updates:
                card = QFrame()
                card.setStyleSheet("QFrame { background: #192b40; border: 1px solid #30475e; border-radius: 9px; } QLabel { border: 0; background: transparent; }")
                layout = QHBoxLayout(card)
                layout.setContentsMargins(13, 10, 13, 10)
                layout.addWidget(self._label(f"v{update.version}  ·  {update.kind}", "section"))
                layout.addStretch()
                layout.addWidget(self._label(human_size(update.size), "muted"))
                self.release_layout.insertWidget(self.release_layout.count() - 1, card)

    def lookup_id(self):
        title_id = self.search.text().strip().upper()
        if not TITLE_ID.fullmatch(title_id):
            QMessageBox.information(self, "Title ID", "Enter a nine-character Vita title ID such as PCSA00007 in the search box.")
            return
        game = next((g for g in self.games if g.title_id == title_id), None)
        if game is None:
            game = Game(title_id, title_id, "0.00", None)
            self.games.append(game)
            self.count_label.setText(f"{len(self.games)} TITLES")
        self.search.setText(title_id)
        self.selected_id = title_id
        self.populate_list()
        if not self.busy:
            self.activity.setText(f"Checking {title_id}…")
            self._start_worker(Worker("check", games=[game]))

    def _start_worker(self, worker: Worker):
        self.busy = True
        self.current_mode = worker.mode
        self.check_button.setEnabled(False)
        self.download_button.setEnabled(False)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.checked.connect(self._on_checked)
        worker.downloaded.connect(self._on_downloaded)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def check_all(self):
        if self.busy or not self.games:
            if not self.games:
                QMessageBox.information(self, "No games found", "Choose a Vita3K folder with installed games first.")
            return
        self.activity.setText(f"Checking {len(self.games)} titles…")
        self.check_errors = 0
        self.progress_bar.hide()
        self._start_worker(Worker("check", games=self.games))

    def _on_checked(self, title_id: str, title: str, updates: list[Update]):
        self.available[title_id] = updates
        self.activity.setText(f"Checked {title_id} • {len(updates)} releases")
        self.populate_list()

    def _on_downloaded(self, title_id: str, path: str):
        self.activity.setText(f"Verified {Path(path).name}")
        self.progress_text.setText("Verified against Sony's SHA-1 metadata")

    def _on_progress(self, title_id: str, done: int, total: int):
        self.progress_bar.show()
        self.progress_bar.setValue(int(done * 100 / total) if total else 0)
        self.progress_text.setText(f"{title_id}  ·  {human_size(done)} / {human_size(total)}")

    def _on_error(self, title_id: str, message: str):
        if self.current_mode == "check":
            self.check_errors += 1
            self.activity.setText(f"Could not check {title_id}: {message[:70]}")
        else:
            self.activity.setText(f"Download failed: {title_id}")
            QMessageBox.warning(self, f"{title_id} download error", message)

    def _on_finished(self):
        self.busy = False
        self.check_button.setEnabled(True)
        if self.current_mode == "check":
            count = len(self.available)
            self.activity.setText(f"Checked {count} title(s)" + (f" · {self.check_errors} failed" if self.check_errors else ""))
        self.refresh_detail()
        self._thread = None
        self._worker = None
        self.current_mode = ""

    def download_selected(self):
        game = next((g for g in self.games if g.title_id == self.selected_id), None)
        if not game or self.busy:
            return
        updates = pending_updates(game.installed_version, self.available.get(game.title_id, []))
        if not updates:
            return
        total = sum(u.size for u in updates)
        answer = QMessageBox.question(
            self, "Download game updates",
            f"Download {len(updates)} verified package(s) for {game.title} ({human_size(total)})?\n\n"
            "They will be saved for installation through Vita3K.",
        )
        if answer != QMessageBox.Yes:
            return
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.activity.setText(f"Downloading {game.title_id}…")
        self._start_worker(Worker("download", updates=updates, title_id=game.title_id, destination=self.download_dir))

    def open_packages(self):
        folder = self.download_dir / self.selected_id if self.selected_id else self.download_dir
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "Operation in progress", "Wait for the current check or download to finish before closing Vita Pulse.")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Vita Pulse")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
