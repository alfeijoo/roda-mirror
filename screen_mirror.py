#!/usr/bin/env python3
"""
screen_mirror.py
Captura una región de pantalla y la muestra en una ventana compartible en Google Meet.
"""

import sys
import os
import mss
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, QRect
from PyQt5.QtGui import QImage, QPixmap, QPainter, QColor, QFont, QIcon

# ── Configuración por defecto ──────────────────────────────────────────────────
DEFAULT_REGION = {"left": 0, "top": 0, "width": 1920, "height": 1080}
FPS = 15  # capturas por segundo


class OverlaySelector(QWidget):
    """Ventana semitransparente para seleccionar la región a capturar."""

    def __init__(self, screen_geom, on_confirm):
        super().__init__()
        self.screen_geom = screen_geom
        self.on_confirm = on_confirm
        self.origin = None
        self.current = None
        self.selecting = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(screen_geom)
        self.setWindowOpacity(0.35)
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
        if self.origin and self.current:
            rect = QRect(self.origin, self.current).normalized()
            painter.fillRect(rect, QColor(255, 255, 255, 0))
            painter.setPen(QColor(0, 200, 255))
            painter.drawRect(rect)

    def mousePressEvent(self, e):
        self.origin = e.pos()
        self.selecting = True

    def mouseMoveEvent(self, e):
        if self.selecting:
            self.current = e.pos()
            self.update()

    def mouseReleaseEvent(self, e):
        self.selecting = False
        if self.origin and self.current:
            rect = QRect(self.origin, self.current).normalized()
            region = {
                "left":   self.screen_geom.x() + rect.x(),
                "top":    self.screen_geom.y() + rect.y(),
                "width":  rect.width(),
                "height": rect.height(),
            }
            self.close()
            self.on_confirm(region)


class MirrorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.region = DEFAULT_REGION.copy()
        self.capturing = False
        self.sct = mss.mss()
        self.selector = None

        self.setWindowTitle("Roda Mirroring")
        self.setMinimumSize(640, 400)
        self.resize(960, 560)

        # ── Layout central ────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # Barra de controles superior (en widget propio para poder ocultarla)
        self.toolbar = QWidget()
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(0, 0, 0, 0)

        self.btn_select = QPushButton("📐 Seleccionar región")
        self.btn_select.clicked.connect(self.start_selection)
        bar.addWidget(self.btn_select)

        self.btn_half_left = QPushButton("◧ Mitad izquierda")
        self.btn_half_left.clicked.connect(self.set_left_half)
        bar.addWidget(self.btn_half_left)

        self.btn_half_right = QPushButton("◨ Mitad derecha")
        self.btn_half_right.clicked.connect(self.set_right_half)
        bar.addWidget(self.btn_half_right)

        bar.addStretch()

        fps_label = QLabel("FPS:")
        bar.addWidget(fps_label)
        self.fps_slider = QSlider(Qt.Horizontal)
        self.fps_slider.setRange(5, 30)
        self.fps_slider.setValue(FPS)
        self.fps_slider.setFixedWidth(100)
        self.fps_slider.valueChanged.connect(self.update_fps)
        bar.addWidget(self.fps_slider)
        self.fps_val_label = QLabel(f"{FPS}")
        bar.addWidget(self.fps_val_label)

        bar.addSpacing(16)

        self.btn_toggle = QPushButton("▶ Iniciar captura")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.clicked.connect(self.toggle_capture)
        self.btn_toggle.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        bar.addWidget(self.btn_toggle)

        layout.addWidget(self.toolbar)

        # Aviso de flujo correcto
        self.hint = QLabel("💡 Pulsa Iniciar captura y luego selecciona esta ventana en Google Meet para compartir")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet(
            "background: #2a1f00; color: #ffcc44; font-size: 11px; "
            "padding: 5px; border: 1px solid #ffcc44;"
        )
        layout.addWidget(self.hint)

        # Área de visualización — contenedor con icono + texto splash o captura
        self.display_container = QWidget()
        self.display_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.display_container.setStyleSheet("background: #111; border: none;")
        display_layout = QVBoxLayout(self.display_container)
        display_layout.setAlignment(Qt.AlignCenter)
        display_layout.setSpacing(10)
        display_layout.setContentsMargins(0, 0, 0, 0)

        self.splash_icon = QLabel()
        self.splash_icon.setAlignment(Qt.AlignCenter)
        display_layout.addWidget(self.splash_icon)

        self.splash_text = QLabel("Selecciona una región y pulsa Iniciar captura")
        self.splash_text.setAlignment(Qt.AlignCenter)
        self.splash_text.setStyleSheet("color: #aaa; font-size: 12px; font-family: monospace; background: transparent; border: none;")
        display_layout.addWidget(self.splash_text)

        # QLabel para mostrar los frames de captura (oculto en splash)
        self.display = QLabel()
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.display.setStyleSheet("background: #111; border: none;")
        self.display.hide()
        display_layout.addWidget(self.display)

        layout.addWidget(self.display_container)
        self._show_splash()

        # Barra de estado inferior
        self.status = QLabel(f"Región: {self.region}")
        self.status.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self.status)

        # Timer de captura
        self.timer = QTimer()
        self.timer.timeout.connect(self.capture_frame)

    # ── Controles ─────────────────────────────────────────────────────────────

    def get_primary_screen_geom(self):
        screen = QApplication.primaryScreen().geometry()
        return screen

    def set_left_half(self):
        geom = self.get_primary_screen_geom()
        self.region = {
            "left":   geom.x(),
            "top":    geom.y(),
            "width":  geom.width() // 2,
            "height": geom.height(),
        }
        self._update_status()

    def set_right_half(self):
        geom = self.get_primary_screen_geom()
        half = geom.width() // 2
        self.region = {
            "left":   geom.x() + half,
            "top":    geom.y(),
            "width":  half,
            "height": geom.height(),
        }
        self._update_status()

    def start_selection(self):
        if self.capturing:
            self.toggle_capture()
        geom = self.get_primary_screen_geom()
        self.selector = OverlaySelector(geom, self.on_region_selected)

    def on_region_selected(self, region):
        if region["width"] > 50 and region["height"] > 50:
            self.region = region
            self._update_status()

    def update_fps(self, val):
        self.fps_val_label.setText(str(val))
        if self.capturing:
            self.timer.setInterval(1000 // val)

    def _show_splash(self):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        icon_path = os.path.join(base, "roda_mirror.png")
        if os.path.exists(icon_path):
            pix = QPixmap(icon_path).scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.splash_icon.setPixmap(pix)
        self.splash_icon.show()
        self.splash_text.show()
        self.display.hide()
        self.display_container.setStyleSheet("background: #111; border: none;")

    def toggle_capture(self):
        if self.btn_toggle.isChecked():
            self.capturing = True
            self.btn_toggle.setText("⏹ Detener")
            fps = self.fps_slider.value()
            self.timer.start(1000 // fps)
            self._enter_frameless()
        else:
            self.capturing = False
            self.btn_toggle.setText("▶ Iniciar captura")
            self.timer.stop()
            self._exit_frameless()
            self._show_splash()

    def _enter_frameless(self):
        self.toolbar.hide()
        self.hint.hide()
        self.status.hide()
        self.splash_icon.hide()
        self.splash_text.hide()
        self.display.show()
        self.centralWidget().layout().setContentsMargins(0, 0, 0, 0)
        self.centralWidget().layout().setSpacing(0)
        self.display_container.setStyleSheet("background: black; border: none;")
        r = self.region
        aspect = r["width"] / r["height"] if r["height"] else 16/9
        new_h = int(self.width() / aspect)
        self.resize(self.width(), new_h)

    def _exit_frameless(self):
        self.centralWidget().layout().setContentsMargins(8, 8, 8, 8)
        self.centralWidget().layout().setSpacing(8)
        self.toolbar.show()
        self.hint.show()
        self.status.show()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.capturing:
            self.btn_toggle.setChecked(False)
            self.toggle_capture()

    # ── Captura ───────────────────────────────────────────────────────────────

    def capture_frame(self):
        try:
            shot = self.sct.grab(self.region)
            from PIL import Image
            pil_img = Image.frombytes("RGBA", (shot.width, shot.height), shot.raw, "raw", "BGRA")
            pil_rgb = pil_img.convert("RGB")
            data = pil_rgb.tobytes()
            img = QImage(data, shot.width, shot.height, shot.width * 3, QImage.Format_RGB888)
            pix = QPixmap.fromImage(img).scaled(
                self.display.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.display.setPixmap(pix)
        except Exception as e:
            self.status.setText(f"Error captura: {e}")

    def _update_status(self):
        r = self.region
        self.status.setText(
            f"Región: x={r['left']} y={r['top']}  {r['width']}×{r['height']}px"
        )

    def closeEvent(self, event):
        self.timer.stop()
        self.sct.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    import os
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base, "roda_mirror.png")
    if os.path.exists(icon_path):
        pix = QPixmap(icon_path).scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        icon = QIcon(pix)
        app.setWindowIcon(icon)
    else:
        icon = QIcon()

    win = MirrorWindow()
    win.setWindowIcon(icon)
    win.show()
    sys.exit(app.exec_())
