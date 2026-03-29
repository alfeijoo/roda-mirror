#!/usr/bin/env python3
# Copyright (c) 2026 alfeijoo@gmail.com
"""
screen_mirror.py — Roda Mirror
-------------------------------
Captura una región de la pantalla en tiempo real y la muestra en una ventana
independiente. El objetivo es poder compartir esa ventana en Google Meet sin
exponer el resto del escritorio.

Flujo principal:
  1. El usuario selecciona una región (mitad izquierda, derecha o personalizada)
  2. Pulsa "Iniciar captura"
  3. La app empieza a reflejar esa región en tiempo real dentro de su ventana
  4. El usuario comparte esa ventana en Google Meet

Dependencias:
  - PyQt5   : interfaz gráfica y gestión de ventanas
  - mss     : captura de pantalla vía X11 shared memory (XShmGetImage)
  - Pillow  : conversión de formato de píxeles BGRA → RGB
"""

import sys
import os
import json
import math
import locale
import subprocess
import mss
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QSizePolicy,
    QCheckBox, QMenu, QAction, QWidgetAction, QActionGroup
)
from PyQt5.QtCore import Qt, QTimer, QRect, QPointF
from PyQt5.QtGui import QImage, QPixmap, QPainter, QColor, QIcon, QPen, QPainterPath

# ── Cadenas de texto por idioma ────────────────────────────────────────────────
STRINGS = {
    "es": {
        "window_title":  "Roda Mirroring",
        "settings_title": "Configuración",
        "submenu_lang":  "Idioma",
        "dnd_label":     "No Molestar durante la captura",
        "dnd_tooltip": (
            "Desactiva las notificaciones del sistema mientras la captura está activa.\n"
            "Al detener o cerrar la app se restaura el estado original."
        ),
        "btn_select":    "📐 Seleccionar región",
        "btn_left":      "◧ Mitad izquierda",
        "btn_right":     "◨ Mitad derecha",
        "btn_top":       "⬒ Mitad superior",
        "btn_bottom":    "⬓ Mitad inferior",
        "submenu_default": "Región por defecto",
        "region_left":   "Mitad izquierda",
        "region_right":  "Mitad derecha",
        "region_top":    "Mitad superior",
        "region_bottom": "Mitad inferior",
        "btn_start":     "▶ Iniciar captura",
        "btn_stop":      "⏹ Detener",
        "hint": (
            "💡 Pulsa Iniciar captura y luego selecciona esta ventana "
            "en Google Meet para compartir"
        ),
        "splash_text":   "Selecciona una región y pulsa Iniciar captura",
        "status_region": "Región: x={left} y={top}  {width}×{height}px",
        "status_error":  "Error captura: {error}",
    },
    "en": {
        "window_title":  "Roda Mirroring",
        "settings_title": "Settings",
        "submenu_lang":  "Language",
        "dnd_label":     "Do Not Disturb while capturing",
        "dnd_tooltip": (
            "Disables system notifications while capture is active.\n"
            "Original state is restored when capture stops or the app closes."
        ),
        "btn_select":    "📐 Select region",
        "btn_left":      "◧ Left half",
        "btn_right":     "◨ Right half",
        "btn_top":       "⬒ Top half",
        "btn_bottom":    "⬓ Bottom half",
        "submenu_default": "Default region",
        "region_left":   "Left half",
        "region_right":  "Right half",
        "region_top":    "Top half",
        "region_bottom": "Bottom half",
        "btn_start":     "▶ Start capture",
        "btn_stop":      "⏹ Stop",
        "hint": (
            "💡 Start capture then select this window in Google Meet to share"
        ),
        "splash_text":   "Select a region and press Start capture",
        "status_region": "Region: x={left} y={top}  {width}×{height}px",
        "status_error":  "Capture error: {error}",
    },
}

# ── Configuración por defecto ──────────────────────────────────────────────────
FPS = 15
CONFIG_PATH = os.path.expanduser("~/.config/roda_mirror/config.json")


def _detect_lang():
    """Detecta el idioma del sistema y devuelve 'es' o 'en'."""
    code = (locale.getlocale()[0] or "")[:2].lower()
    return code if code in STRINGS else "en"


class OceanBackground(QWidget):
    """
    Widget de fondo animado con líneas de nivel tipo batimetría oceánica.
    Se usa como fondo del área de splash (estado de reposo).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(40)

        self._lines = [
            (0.08,  "#0d4a42", 0.15, 0.018),
            (0.17,  "#0d5548", 0.22, 0.020),
            (0.26,  "#0e6050", 0.18, 0.022),
            (0.35,  "#0f6a58", 0.25, 0.019),
            (0.44,  "#107560", 0.20, 0.024),
            (0.53,  "#128068", 0.28, 0.021),
            (0.62,  "#148c72", 0.17, 0.026),
            (0.72,  "#169878", 0.23, 0.023),
            (0.83,  "#18a47e", 0.30, 0.025),
        ]

    def _tick(self):
        self._t += 0.03
        self.update()

    def stop_animation(self):
        self._anim_timer.stop()

    def start_animation(self):
        self._anim_timer.start(40)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        painter.fillRect(self.rect(), QColor("#071a1a"))

        steps = 80

        for i, (y_rel, color_hex, speed, amplitude) in enumerate(self._lines):
            y_base = y_rel * h
            phase = self._t * speed + i * 0.8

            pen = QPen(QColor(color_hex))
            pen.setWidthF(1.4)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)

            path = QPainterPath()
            for step in range(steps + 1):
                x = w * step / steps
                y = y_base + \
                    math.sin(x / w * 2 * math.pi * 2 + phase) * h * amplitude + \
                    math.sin(x / w * 2 * math.pi * 3.3 + phase * 1.4) * h * amplitude * 0.4
                if step == 0:
                    path.moveTo(QPointF(x, y))
                else:
                    path.lineTo(QPointF(x, y))

            painter.drawPath(path)

        painter.end()


class OverlaySelector(QWidget):
    """
    Ventana semitransparente a pantalla completa que permite al usuario
    dibujar con el ratón la región que quiere capturar.
    """

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
    """Ventana principal de la aplicación."""

    def __init__(self):
        super().__init__()
        self.capturing = False
        self.sct = mss.mss()
        self.selector = None
        self._lang = _detect_lang()
        self._default_region_key = self._load_config().get("default_region", "right")
        self.region = self._compute_preset(self._default_region_key)

        self._original_banners = self._read_banners_state()

        self.setWindowTitle("Roda Mirroring")
        self.setMinimumSize(640, 400)
        self.resize(960, 560)

        self.menuBar().setVisible(False)

        # ── Layout central ────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Barra de controles ─────────────────────────────────────────────────
        self.toolbar = QWidget()
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(0, 0, 0, 0)

        self.btn_select = QPushButton(self._s("btn_select"))
        self.btn_select.clicked.connect(self.start_selection)
        bar.addWidget(self.btn_select)

        self.btn_half_left = QPushButton(self._s("btn_left"))
        self.btn_half_left.clicked.connect(self.set_left_half)
        bar.addWidget(self.btn_half_left)

        self.btn_half_right = QPushButton(self._s("btn_right"))
        self.btn_half_right.clicked.connect(self.set_right_half)
        bar.addWidget(self.btn_half_right)

        self.btn_half_top = QPushButton(self._s("btn_top"))
        self.btn_half_top.clicked.connect(self.set_top_half)
        bar.addWidget(self.btn_half_top)

        self.btn_half_bottom = QPushButton(self._s("btn_bottom"))
        self.btn_half_bottom.clicked.connect(self.set_bottom_half)
        bar.addWidget(self.btn_half_bottom)

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

        bar.addSpacing(8)

        self.btn_toggle = QPushButton(self._s("btn_start"))
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.clicked.connect(self.toggle_capture)
        self.btn_toggle.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        bar.addWidget(self.btn_toggle)

        bar.addSpacing(4)

        # Botón de configuración — despliega QMenu al pulsarlo
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setFixedWidth(32)
        self.btn_settings.setToolTip(self._s("settings_title"))
        self.btn_settings.setStyleSheet("font-size: 14px; padding: 2px;")
        self.btn_settings.clicked.connect(self._open_settings_menu)
        bar.addWidget(self.btn_settings)

        layout.addWidget(self.toolbar)

        # ── Menú de configuración ──────────────────────────────────────────────
        self._settings_menu = QMenu(self)

        # Checkbox No Molestar (via QWidgetAction para meter un QCheckBox en el menú)
        self.chk_dnd = QCheckBox(self._s("dnd_label"))
        self.chk_dnd.setToolTip(self._s("dnd_tooltip"))
        self.chk_dnd.setContentsMargins(6, 2, 6, 2)
        dnd_action = QWidgetAction(self)
        dnd_action.setDefaultWidget(self.chk_dnd)
        self._settings_menu.addAction(dnd_action)

        self._settings_menu.addSeparator()

        # Submenú de región por defecto
        self.default_region_menu = self._settings_menu.addMenu(self._s("submenu_default"))
        default_group = QActionGroup(self)
        default_group.setExclusive(True)
        self._default_region_actions = {}
        for key, str_key in [("left", "region_left"), ("right", "region_right"),
                              ("top", "region_top"), ("bottom", "region_bottom")]:
            act = QAction(self._s(str_key), self, checkable=True)
            act.triggered.connect(lambda checked, k=key: checked and self._set_default_region(k))
            default_group.addAction(act)
            self.default_region_menu.addAction(act)
            self._default_region_actions[key] = act
        self._default_region_actions[self._default_region_key].setChecked(True)

        self._settings_menu.addSeparator()

        # Submenú de idioma con acciones exclusivas
        self.lang_menu = self._settings_menu.addMenu(self._s("submenu_lang"))
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        self.act_es = QAction("Español", self, checkable=True)
        self.act_en = QAction("English",  self, checkable=True)
        self.act_es.triggered.connect(lambda: self._set_language("es"))
        self.act_en.triggered.connect(lambda: self._set_language("en"))
        lang_group.addAction(self.act_es)
        lang_group.addAction(self.act_en)
        self.lang_menu.addAction(self.act_es)
        self.lang_menu.addAction(self.act_en)
        (self.act_es if self._lang == "es" else self.act_en).setChecked(True)

        # ── Aviso de uso ───────────────────────────────────────────────────────
        self.hint = QLabel(self._s("hint"))
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet(
            "background: #2a1f00; color: #ffcc44; font-size: 11px; "
            "padding: 5px; border: 1px solid #ffcc44;"
        )
        layout.addWidget(self.hint)

        # ── Área de visualización ──────────────────────────────────────────────
        self.display_container = OceanBackground()
        self.display_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        display_layout = QVBoxLayout(self.display_container)
        display_layout.setAlignment(Qt.AlignCenter)
        display_layout.setSpacing(10)
        display_layout.setContentsMargins(0, 0, 0, 0)

        self.splash_icon = QLabel()
        self.splash_icon.setAlignment(Qt.AlignCenter)
        display_layout.addWidget(self.splash_icon)

        self.splash_text = QLabel(self._s("splash_text"))
        self.splash_text.setAlignment(Qt.AlignCenter)
        self.splash_text.setStyleSheet(
            "color: #aaa; font-size: 12px; font-family: monospace; "
            "background: transparent; border: none;"
        )
        display_layout.addWidget(self.splash_text)

        self.display = QLabel()
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.display.setStyleSheet("background: #111; border: none;")
        self.display.hide()
        display_layout.addWidget(self.display)

        layout.addWidget(self.display_container)

        self._show_splash()

        # ── Barra de estado ────────────────────────────────────────────────────
        self.status = QLabel()
        self.status.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self.status)
        self._update_status()

        # ── Timer de captura ───────────────────────────────────────────────────
        self.timer = QTimer()
        self.timer.timeout.connect(self.capture_frame)

    # ── Idioma ─────────────────────────────────────────────────────────────────

    def _s(self, key):
        """Devuelve la cadena localizada para la clave dada."""
        return STRINGS[self._lang][key]

    def _open_settings_menu(self):
        """Despliega el menú de configuración justo debajo del botón ⚙."""
        pos = self.btn_settings.mapToGlobal(self.btn_settings.rect().bottomLeft())
        self._settings_menu.exec_(pos)

    def _set_language(self, lang):
        if lang == self._lang:
            return
        self._lang = lang
        self._retranslate_ui()

    def _retranslate_ui(self):
        """Actualiza todos los textos de la interfaz al idioma actual."""
        self.btn_select.setText(self._s("btn_select"))
        self.btn_half_left.setText(self._s("btn_left"))
        self.btn_half_right.setText(self._s("btn_right"))
        self.btn_settings.setToolTip(self._s("settings_title"))
        self.hint.setText(self._s("hint"))
        self.splash_text.setText(self._s("splash_text"))
        self.btn_toggle.setText(
            self._s("btn_stop") if self.capturing else self._s("btn_start")
        )
        self.btn_half_top.setText(self._s("btn_top"))
        self.btn_half_bottom.setText(self._s("btn_bottom"))
        self.chk_dnd.setText(self._s("dnd_label"))
        self.chk_dnd.setToolTip(self._s("dnd_tooltip"))
        self.default_region_menu.setTitle(self._s("submenu_default"))
        for key, str_key in [("left", "region_left"), ("right", "region_right"),
                              ("top", "region_top"), ("bottom", "region_bottom")]:
            self._default_region_actions[key].setText(self._s(str_key))
        self.lang_menu.setTitle(self._s("submenu_lang"))
        (self.act_es if self._lang == "es" else self.act_en).setChecked(True)
        self._update_status()

    # ── No Molestar ────────────────────────────────────────────────────────────

    def _read_banners_state(self):
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.notifications", "show-banners"],
                capture_output=True, text=True, timeout=2
            )
            return result.stdout.strip() == "true"
        except Exception:
            return True

    def _set_banners(self, enabled: bool):
        try:
            value = "true" if enabled else "false"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", value],
                timeout=2
            )
        except Exception:
            pass

    # ── Controles ──────────────────────────────────────────────────────────────

    def get_primary_screen_geom(self):
        return QApplication.primaryScreen().geometry()

    def set_left_half(self):
        self.region = self._compute_preset("left")
        self._update_status()

    def set_right_half(self):
        self.region = self._compute_preset("right")
        self._update_status()

    def set_top_half(self):
        self.region = self._compute_preset("top")
        self._update_status()

    def set_bottom_half(self):
        self.region = self._compute_preset("bottom")
        self._update_status()

    def _compute_preset(self, key):
        """Calcula el dict de región para un preset dado (left/right/top/bottom)."""
        geom = self.get_primary_screen_geom()
        w, h = geom.width(), geom.height()
        x, y = geom.x(), geom.y()
        if key == "left":
            return {"left": x,           "top": y,           "width": w // 2, "height": h}
        if key == "right":
            return {"left": x + w // 2,  "top": y,           "width": w // 2, "height": h}
        if key == "top":
            return {"left": x,           "top": y,           "width": w,      "height": h // 2}
        if key == "bottom":
            return {"left": x,           "top": y + h // 2,  "width": w,      "height": h // 2}
        return {"left": x, "top": y, "width": w, "height": h}

    def _set_default_region(self, key):
        """Cambia la región por defecto, la aplica y la persiste en disco."""
        self._default_region_key = key
        self._default_region_actions[key].setChecked(True)
        self.region = self._compute_preset(key)
        self._update_status()
        self._save_config()

    def _load_config(self):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump({"default_region": self._default_region_key}, f)
        except Exception:
            pass

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
        self.display_container.start_animation()

    def toggle_capture(self):
        if self.btn_toggle.isChecked():
            self.capturing = True
            self.btn_toggle.setText(self._s("btn_stop"))
            fps = self.fps_slider.value()
            self.timer.start(1000 // fps)
            if self.chk_dnd.isChecked():
                self._set_banners(False)
            self._enter_frameless()
        else:
            self.capturing = False
            self.btn_toggle.setText(self._s("btn_start"))
            self.timer.stop()
            if self.chk_dnd.isChecked():
                self._set_banners(self._original_banners)
            self._exit_frameless()
            self._show_splash()

    def _enter_frameless(self):
        self.toolbar.hide()
        self.hint.hide()
        self.status.hide()
        self.splash_icon.hide()
        self.splash_text.hide()
        self.display_container.stop_animation()
        self.display.show()
        self.centralWidget().layout().setContentsMargins(0, 0, 0, 0)
        self.centralWidget().layout().setSpacing(0)
        self.display_container.layout().setContentsMargins(0, 0, 0, 0)
        self.display_container.layout().setSpacing(0)
        self.display_container.setStyleSheet("background: black;")
        self.display.setStyleSheet("background: black; border: none;")
        r = self.region
        aspect = r["width"] / r["height"] if r["height"] else 16 / 9
        avail = QApplication.primaryScreen().availableGeometry()
        title_bar_h = self.frameGeometry().height() - self.height()
        max_h = avail.height() - title_bar_h

        w = self.width()
        h = int(w / aspect)
        if h > max_h:
            h = max_h
            w = int(h * aspect)
        self.resize(w, h)

    def _exit_frameless(self):
        self.centralWidget().layout().setContentsMargins(8, 8, 8, 8)
        self.centralWidget().layout().setSpacing(8)
        self.display_container.setStyleSheet("")
        self.display.setStyleSheet("")
        self.toolbar.show()
        self.hint.show()
        self.status.show()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.capturing:
            self.btn_toggle.setChecked(False)
            self.toggle_capture()

    # ── Captura ────────────────────────────────────────────────────────────────

    def capture_frame(self):
        try:
            shot = self.sct.grab(self.region)
            from PIL import Image
            pil_img = Image.frombytes("RGBA", (shot.width, shot.height), shot.raw, "raw", "BGRA")
            data = pil_img.convert("RGB").tobytes()
            img = QImage(data, shot.width, shot.height, shot.width * 3, QImage.Format_RGB888)
            pix = QPixmap.fromImage(img).scaled(
                self.display.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.display.setPixmap(pix)
        except Exception as e:
            self.status.setText(self._s("status_error").format(error=e))

    def _update_status(self):
        r = self.region
        self.status.setText(
            self._s("status_region").format(
                left=r["left"], top=r["top"],
                width=r["width"], height=r["height"]
            )
        )

    def closeEvent(self, event):
        self.timer.stop()
        self.sct.close()
        self._set_banners(self._original_banners)
        event.accept()


# ── Punto de entrada ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

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
