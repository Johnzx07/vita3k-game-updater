import sys

from vita_updater.ui import MainWindow, main


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        from PySide6.QtWidgets import QApplication

        app = QApplication([])
        window = MainWindow()
        assert window.windowTitle().startswith("Vita Pulse")
        window.close()
        raise SystemExit(0)
    raise SystemExit(main())
