"""
Minimal PySide6 test app with bridge installed.
Run this, then connect Claude to the pyside6-mcp server.
"""
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLabel, QLineEdit, QCheckBox,
)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("pyside6-mcp test app")
        self.setObjectName("MainWindow")
        self.resize(400, 300)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.label = QLabel("Hello from pyside6-mcp!")
        self.label.setObjectName("label")

        self.input = QLineEdit()
        self.input.setObjectName("input")
        self.input.setPlaceholderText("Type something here…")

        self.btn = QPushButton("Click me")
        self.btn.setObjectName("btn")
        self.btn.clicked.connect(self._on_click)

        self.check = QCheckBox("Enable feature")
        self.check.setObjectName("check")

        layout.addWidget(self.label)
        layout.addWidget(self.input)
        layout.addWidget(self.btn)
        layout.addWidget(self.check)

    def _on_click(self):
        text = self.input.text() or "(empty)"
        self.label.setText(f"Button clicked! Input was: {text}")
        import logging
        logging.getLogger("test_app").info("Button clicked, input=%r", text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("pyside6-mcp-test")
    app.setApplicationVersion("0.1.0")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
