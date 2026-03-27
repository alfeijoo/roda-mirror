#!/usr/bin/env python3
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
import subprocess
import mss
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QSizePolicy,
    QMenuBar, QMenu, QAction, QWidgetAction, QCheckBox
)
from PyQt5.QtCore import Qt, QTimer, QRect
from PyQt5.QtGui import QImage, QPixmap, QPainter, QColor, QFont, QIcon

# ── Configuración por defecto ──────────────────────────────────────────────────
# Región inicial: pantalla completa 1920x1080. Se sobreescribe cuando el usuario
# selecciona una región o pulsa los botones de mitad izquierda/derecha.
DEFAULT_REGION = {"left": 0, "top": 0, "width": 1920, "height": 1080}

# Frecuencia de refresco por defecto en fotogramas por segundo.
# A 15 FPS el timer dispara capture_frame cada 66ms.
FPS = 15


class OverlaySelector(QWidget):
    """
    Ventana semitransparente a pantalla completa que permite al usuario
    dibujar con el ratón la región que quiere capturar.

    Cómo funciona:
      - Se abre encima de todo (WindowStaysOnTopHint)
      - El fondo es negro semitransparente (opacidad 35%)
      - El usuario arrastra el ratón para dibujar un rectángulo
      - Al soltar el botón, calcula las coordenadas absolutas en pantalla
        y las devuelve a MirrorWindow vía el callback on_confirm

    Parámetros:
      screen_geom : QRect con la geometría del monitor principal
      on_confirm  : función a llamar cuando el usuario termina la selección,
                    recibe un dict {left, top, width, height}
    """

    def __init__(self, screen_geom, on_confirm):
        super().__init__()
        self.screen_geom = screen_geom  # geometría del monitor (posición + tamaño)
        self.on_confirm = on_confirm    # callback a llamar al terminar la selección
        self.origin = None              # punto donde el usuario presionó el ratón
        self.current = None             # punto actual mientras arrastra
        self.selecting = False          # True mientras el botón del ratón está pulsado

        # Ventana sin bordes, siempre encima, sin aparecer en la barra de tareas
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        # Permite que el fondo sea transparente
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Cursor en forma de cruz para indicar que se puede seleccionar
        self.setCursor(Qt.CrossCursor)
        # Ocupa exactamente el área del monitor
        self.setGeometry(screen_geom)
        self.setWindowOpacity(0.35)
        self.show()

    def paintEvent(self, event):
        """
        Se llama automáticamente cada vez que Qt necesita redibujar la ventana.
        Pinta el fondo oscuro y el rectángulo de selección en azul cian.
        """
        painter = QPainter(self)
        # Fondo negro semitransparente sobre toda la pantalla
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
        # Si el usuario está arrastrando, dibuja el rectángulo de selección
        if self.origin and self.current:
            # normalized() garantiza que el rect es válido aunque se arrastre
            # en cualquier dirección (de derecha a izquierda, de abajo a arriba, etc.)
            rect = QRect(self.origin, self.current).normalized()
            # Interior del rectángulo completamente transparente (se ve lo que hay debajo)
            painter.fillRect(rect, QColor(255, 255, 255, 0))
            # Borde del rectángulo en azul cian
            painter.setPen(QColor(0, 200, 255))
            painter.drawRect(rect)

    def mousePressEvent(self, e):
        """
        El usuario presiona el botón del ratón: guarda el punto de origen
        y activa el modo selección.
        """
        self.origin = e.pos()
        self.selecting = True

    def mouseMoveEvent(self, e):
        """
        El usuario mueve el ratón mientras tiene el botón pulsado:
        actualiza el punto actual y fuerza un redibujado para mostrar
        el rectángulo en tiempo real.
        """
        if self.selecting:
            self.current = e.pos()
            self.update()  # provoca una llamada a paintEvent

    def mouseReleaseEvent(self, e):
        """
        El usuario suelta el botón del ratón: fin de la selección.
        Calcula las coordenadas absolutas en pantalla (sumando el offset
        del monitor por si no está en la posición 0,0) y llama al callback.
        """
        self.selecting = False
        if self.origin and self.current:
            rect = QRect(self.origin, self.current).normalized()
            # Convierte coordenadas relativas al overlay en coordenadas absolutas
            # de pantalla sumando la posición del monitor (necesario en configuraciones
            # multi-monitor donde el monitor principal no está en x=0, y=0)
            region = {
                "left":   self.screen_geom.x() + rect.x(),
                "top":    self.screen_geom.y() + rect.y(),
                "width":  rect.width(),
                "height": rect.height(),
            }
            self.close()
            self.on_confirm(region)  # devuelve la región seleccionada a MirrorWindow


class MirrorWindow(QMainWindow):
    """
    Ventana principal de la aplicación.

    Contiene:
      - Barra de controles: botones de selección de región, slider de FPS,
        botón de iniciar/detener captura
      - Aviso amarillo con instrucciones de uso con Google Meet
      - Área de visualización: muestra el splash (icono + texto) en reposo
        o los frames capturados durante la captura
      - Barra de estado: muestra las coordenadas y dimensiones de la región activa

    El ciclo de captura funciona con un QTimer que dispara capture_frame
    a la frecuencia configurada por el slider de FPS.
    """

    def __init__(self):
        super().__init__()
        # Región activa — se actualiza con los botones o el selector manual
        self.region = DEFAULT_REGION.copy()
        # Indica si la captura está en curso
        self.capturing = False
        # Instancia de mss para captura de pantalla (se mantiene abierta durante
        # toda la vida de la app para evitar el overhead de abrir/cerrar en cada frame)
        self.sct = mss.mss()
        # Referencia al OverlaySelector activo (None si no hay ninguno abierto)
        self.selector = None

        # ── Estado inicial de notificaciones del sistema ───────────────────────
        # Lee el estado actual de show-banners ANTES de que la app lo toque.
        # Este valor se restaurará siempre al salir, independientemente de lo
        # que haya pasado durante la sesión.
        self._original_banners = self._read_banners_state()

        self.setWindowTitle("Roda Mirroring")
        self.setMinimumSize(640, 400)
        self.resize(960, 560)

        # ── Menú de configuración ──────────────────────────────────────────────
        menubar = self.menuBar()
        config_menu = menubar.addMenu("⚙ Configuración")

        # Checkbox "No Molestar durante la captura"
        # Usa QWidgetAction para poder meter un QCheckBox dentro del menú
        self.chk_dnd = QCheckBox("  No Molestar durante la captura")
        self.chk_dnd.setToolTip(
            "Desactiva las notificaciones del sistema mientras la captura está activa.\n"
            "Al detener o cerrar la app se restaura el estado original."
        )
        dnd_action = QWidgetAction(self)
        dnd_action.setDefaultWidget(self.chk_dnd)
        config_menu.addAction(dnd_action)

        # ── Layout central ────────────────────────────────────────────────────
        # Widget central requerido por QMainWindow como contenedor raíz
        central = QWidget()
        self.setCentralWidget(central)
        # Layout vertical: toolbar → hint → display_container → status
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Barra de controles ─────────────────────────────────────────────────
        # Se mete en su propio QWidget para poder ocultarla con un solo .hide()
        # durante la captura, en lugar de ocultar cada botón individualmente
        self.toolbar = QWidget()
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(0, 0, 0, 0)

        # Botón que abre el OverlaySelector para dibujar una región personalizada
        self.btn_select = QPushButton("📐 Seleccionar región")
        self.btn_select.clicked.connect(self.start_selection)
        bar.addWidget(self.btn_select)

        # Botón que asigna automáticamente la mitad izquierda del monitor
        self.btn_half_left = QPushButton("◧ Mitad izquierda")
        self.btn_half_left.clicked.connect(self.set_left_half)
        bar.addWidget(self.btn_half_left)

        # Botón que asigna automáticamente la mitad derecha del monitor
        self.btn_half_right = QPushButton("◨ Mitad derecha")
        self.btn_half_right.clicked.connect(self.set_right_half)
        bar.addWidget(self.btn_half_right)

        # Espacio flexible que empuja los controles de FPS y el botón de captura
        # hacia la derecha
        bar.addStretch()

        # Slider de FPS: controla el intervalo del QTimer (5-30 FPS)
        fps_label = QLabel("FPS:")
        bar.addWidget(fps_label)
        self.fps_slider = QSlider(Qt.Horizontal)
        self.fps_slider.setRange(5, 30)
        self.fps_slider.setValue(FPS)
        self.fps_slider.setFixedWidth(100)
        # Cada cambio en el slider llama a update_fps para ajustar el timer en caliente
        self.fps_slider.valueChanged.connect(self.update_fps)
        bar.addWidget(self.fps_slider)
        # Etiqueta que muestra el valor numérico actual del slider
        self.fps_val_label = QLabel(f"{FPS}")
        bar.addWidget(self.fps_val_label)

        bar.addSpacing(16)

        # Botón principal de captura — es checkable (tiene estado on/off)
        # cuando está marcado la captura está activa, cuando no está marcado está parada
        self.btn_toggle = QPushButton("▶ Iniciar captura")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.clicked.connect(self.toggle_capture)
        self.btn_toggle.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        bar.addWidget(self.btn_toggle)

        layout.addWidget(self.toolbar)

        # ── Aviso de uso ───────────────────────────────────────────────────────
        # Recuerda al usuario el orden correcto: primero iniciar, luego compartir en Meet
        self.hint = QLabel("💡 Pulsa Iniciar captura y luego selecciona esta ventana en Google Meet para compartir")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet(
            "background: #2a1f00; color: #ffcc44; font-size: 11px; "
            "padding: 5px; border: 1px solid #ffcc44;"
        )
        layout.addWidget(self.hint)

        # ── Área de visualización ──────────────────────────────────────────────
        # Contenedor que alberga dos estados mutuamente excluyentes:
        #   - Splash (icono + texto): visible cuando no hay captura activa
        #   - Display (frames en vivo): visible durante la captura
        self.display_container = QWidget()
        self.display_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.display_container.setStyleSheet("background: #111; border: none;")
        display_layout = QVBoxLayout(self.display_container)
        display_layout.setAlignment(Qt.AlignCenter)
        display_layout.setSpacing(10)
        display_layout.setContentsMargins(0, 0, 0, 0)

        # Icono del splash — muestra roda_mirror.png centrado
        self.splash_icon = QLabel()
        self.splash_icon.setAlignment(Qt.AlignCenter)
        display_layout.addWidget(self.splash_icon)

        # Texto del splash — instrucción visible al arrancar
        self.splash_text = QLabel("Selecciona una región y pulsa Iniciar captura")
        self.splash_text.setAlignment(Qt.AlignCenter)
        self.splash_text.setStyleSheet(
            "color: #aaa; font-size: 12px; font-family: monospace; "
            "background: transparent; border: none;"
        )
        display_layout.addWidget(self.splash_text)

        # QLabel donde se pintan los frames capturados durante la captura activa
        # Empieza oculto — se muestra en _enter_frameless y se oculta en _show_splash
        self.display = QLabel()
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.display.setStyleSheet("background: #111; border: none;")
        self.display.hide()
        display_layout.addWidget(self.display)

        layout.addWidget(self.display_container)

        # Muestra el splash inicial con el icono y el texto de bienvenida
        self._show_splash()

        # ── Barra de estado ────────────────────────────────────────────────────
        # Muestra las coordenadas y dimensiones de la región activa
        self.status = QLabel(f"Región: {self.region}")
        self.status.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self.status)

        # ── Timer de captura ───────────────────────────────────────────────────
        # El timer no arranca aquí — se inicia en toggle_capture cuando el usuario
        # pulsa "Iniciar captura". Cada vez que el timer dispara llama a capture_frame.
        self.timer = QTimer()
        self.timer.timeout.connect(self.capture_frame)

    # ── No Molestar ────────────────────────────────────────────────────────────

    def _read_banners_state(self):
        """
        Lee el estado actual de show-banners en gsettings.
        Devuelve True si las notificaciones están activas, False si están desactivadas.
        Si gsettings no está disponible (distro sin GNOME) devuelve True por defecto
        para no alterar nada.
        """
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.notifications", "show-banners"],
                capture_output=True, text=True, timeout=2
            )
            return result.stdout.strip() == "true"
        except Exception:
            return True  # fallback seguro si gsettings no existe

    def _set_banners(self, enabled: bool):
        """
        Activa o desactiva las notificaciones banner del sistema via gsettings.
        Si gsettings no está disponible no hace nada (sin crash).

        Parámetros:
          enabled : True para mostrar notificaciones, False para ocultarlas
        """
        try:
            value = "true" if enabled else "false"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", value],
                timeout=2
            )
        except Exception:
            pass  # gsettings no disponible — ignorar silenciosamente

    # ── Controles ──────────────────────────────────────────────────────────────

    def get_primary_screen_geom(self):
        """
        Devuelve la geometría (posición y tamaño) del monitor principal como QRect.
        Se usa para calcular las mitades izquierda/derecha y como base para el
        OverlaySelector.
        """
        screen = QApplication.primaryScreen().geometry()
        return screen

    def set_left_half(self):
        """
        Asigna la mitad izquierda del monitor como región activa.
        El ancho es exactamente la mitad del monitor y la altura es completa.
        """
        geom = self.get_primary_screen_geom()
        self.region = {
            "left":   geom.x(),
            "top":    geom.y(),
            "width":  geom.width() // 2,  # división entera para evitar píxeles decimales
            "height": geom.height(),
        }
        self._update_status()

    def set_right_half(self):
        """
        Asigna la mitad derecha del monitor como región activa.
        El origen X empieza en la mitad del monitor para capturar solo el lado derecho.
        """
        geom = self.get_primary_screen_geom()
        half = geom.width() // 2
        self.region = {
            "left":   geom.x() + half,  # empieza en el centro del monitor
            "top":    geom.y(),
            "width":  half,
            "height": geom.height(),
        }
        self._update_status()

    def start_selection(self):
        """
        Abre el OverlaySelector para que el usuario dibuje una región personalizada.
        Si la captura estaba activa la detiene primero para evitar conflictos visuales.
        """
        if self.capturing:
            self.toggle_capture()
        geom = self.get_primary_screen_geom()
        # on_region_selected se llamará cuando el usuario termine de seleccionar
        self.selector = OverlaySelector(geom, self.on_region_selected)

    def on_region_selected(self, region):
        """
        Callback llamado por OverlaySelector cuando el usuario termina la selección.
        Valida que la región sea mínimamente usable (más de 50px en cada dimensión)
        antes de guardarla.

        Parámetros:
          region : dict {left, top, width, height} con coordenadas absolutas de pantalla
        """
        if region["width"] > 50 and region["height"] > 50:
            self.region = region
            self._update_status()

    def update_fps(self, val):
        """
        Llamado automáticamente cuando el usuario mueve el slider de FPS.
        Actualiza la etiqueta numérica y, si la captura está activa, cambia
        el intervalo del timer en caliente sin necesidad de reiniciar la captura.

        Parámetros:
          val : nuevo valor del slider (entero entre 5 y 30)
        """
        self.fps_val_label.setText(str(val))
        if self.capturing:
            # setInterval cambia el período del timer sin detenerlo
            self.timer.setInterval(1000 // val)

    def _show_splash(self):
        """
        Muestra el estado de reposo: icono del rodaballo centrado + texto de bienvenida.
        Se llama al arrancar la app y al detener la captura.

        Busca roda_mirror.png en:
          - sys._MEIPASS : directorio temporal donde PyInstaller extrae los recursos
            cuando la app se ejecuta como binario compilado (AppImage)
          - directorio del script : cuando se ejecuta directamente con python3
        """
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        icon_path = os.path.join(base, "roda_mirror.png")
        if os.path.exists(icon_path):
            # Escala el icono a 220x220 manteniendo proporciones con interpolación suave
            pix = QPixmap(icon_path).scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.splash_icon.setPixmap(pix)
        self.splash_icon.show()
        self.splash_text.show()
        self.display.hide()  # oculta el área de frames — no hay captura activa
        self.display_container.setStyleSheet("background: #111; border: none;")

    def toggle_capture(self):
        """
        Punto de entrada principal para iniciar o detener la captura.
        Es el único sitio donde el timer arranca o se para.

        Si btn_toggle está marcado (checked) → inicia la captura:
          1. Activa el flag self.capturing
          2. Calcula el intervalo en ms a partir de los FPS del slider
          3. Arranca el timer — a partir de aquí capture_frame se ejecuta periódicamente
          4. Llama a _enter_frameless para preparar la ventana

        Si btn_toggle no está marcado → detiene la captura:
          1. Desactiva el flag self.capturing
          2. Para el timer — capture_frame deja de ejecutarse
          3. Llama a _exit_frameless para restaurar la ventana
          4. Llama a _show_splash para volver al estado de reposo
        """
        if self.btn_toggle.isChecked():
            self.capturing = True
            self.btn_toggle.setText("⏹ Detener")
            fps = self.fps_slider.value()
            self.timer.start(1000 // fps)
            # Si el checkbox de No Molestar está activo, desactiva notificaciones
            if self.chk_dnd.isChecked():
                self._set_banners(False)
            self._enter_frameless()
        else:
            self.capturing = False
            self.btn_toggle.setText("▶ Iniciar captura")
            self.timer.stop()
            # Restaura el estado original de notificaciones al detener
            if self.chk_dnd.isChecked():
                self._set_banners(self._original_banners)
            self._exit_frameless()
            self._show_splash()

    def _enter_frameless(self):
        """
        Prepara la ventana para el modo captura activa:
          - Oculta todos los controles (toolbar, hint, status, splash)
          - Muestra el QLabel de display donde se pintarán los frames
          - Elimina márgenes internos para que el contenido ocupe todo el espacio
          - Redimensiona la ventana al aspect ratio de la región capturada
            para que la imagen no aparezca distorsionada

        No elimina la barra de título del sistema operativo para que Google Meet
        pueda siempre encontrar y seleccionar la ventana en su lista de ventanas.
        """
        self.toolbar.hide()
        self.hint.hide()
        self.status.hide()
        self.splash_icon.hide()
        self.splash_text.hide()
        self.menuBar().hide()  # oculta el menú de configuración durante la captura
        self.display.show()
        # Quita márgenes para que el frame ocupe todo el espacio disponible
        self.centralWidget().layout().setContentsMargins(0, 0, 0, 0)
        self.centralWidget().layout().setSpacing(0)
        self.display_container.layout().setContentsMargins(0, 0, 0, 0)
        self.display_container.layout().setSpacing(0)
        self.display_container.setStyleSheet("background: black; border: none;")
        self.display.setStyleSheet("background: black; border: none;")
        # Calcula el alto correcto manteniendo el aspect ratio de la región
        r = self.region
        aspect = r["width"] / r["height"] if r["height"] else 16/9
        new_h = int(self.width() / aspect)
        self.resize(self.width(), new_h)

    def _exit_frameless(self):
        """
        Restaura la ventana al estado de reposo tras detener la captura:
          - Devuelve los márgenes internos originales
          - Vuelve a mostrar toolbar, hint y status
        El splash se muestra por separado en toggle_capture llamando a _show_splash.
        """
        self.centralWidget().layout().setContentsMargins(8, 8, 8, 8)
        self.centralWidget().layout().setSpacing(8)
        self.menuBar().show()
        self.toolbar.show()
        self.hint.show()
        self.status.show()

    def keyPressEvent(self, event):
        """
        Intercepta pulsaciones de teclado a nivel de ventana.
        Escape durante la captura activa simula pulsar el botón de detener,
        lo que permite parar la captura y recuperar los controles sin necesidad
        de hacer clic con el ratón (útil cuando los botones están ocultos).
        """
        if event.key() == Qt.Key_Escape and self.capturing:
            # Desmarca el botón toggle antes de llamar a toggle_capture
            # para que la función detecte el estado correcto (no capturing)
            self.btn_toggle.setChecked(False)
            self.toggle_capture()

    # ── Captura ────────────────────────────────────────────────────────────────

    def capture_frame(self):
        """
        Captura un frame de la región activa y lo pinta en el display.
        Se ejecuta en cada tick del QTimer (por defecto cada 66ms = 15 FPS).

        Pipeline completo por frame:
          1. mss.grab(region) — lee los píxeles de la región desde la memoria
             compartida de X11 (XShmGetImage). Devuelve un buffer BGRA raw.
          2. Image.frombytes("RGBA", ..., "BGRA") — Pillow interpreta el buffer
             raw reordenando los canales de BGRA a RGBA.
          3. .convert("RGB") — elimina el canal Alpha (no necesario para display)
             y entrega los píxeles en formato RGB estándar.
          4. QImage(data, w, h, w*3, Format_RGB888) — empaqueta el buffer RGB
             en un objeto imagen de Qt. w*3 es el stride (bytes por fila).
          5. QPixmap.scaled() — escala la imagen al tamaño actual del QLabel
             manteniendo el aspect ratio con interpolación suave.
          6. display.setPixmap(pix) — pinta la imagen en pantalla.

        Si cualquier paso falla, muestra el error en la barra de estado
        en lugar de crashear la aplicación.
        """
        try:
            # Paso 1: captura de píxeles via mss
            shot = self.sct.grab(self.region)

            # Paso 2 y 3: conversión BGRA → RGB via Pillow
            from PIL import Image
            pil_img = Image.frombytes("RGBA", (shot.width, shot.height), shot.raw, "raw", "BGRA")
            pil_rgb = pil_img.convert("RGB")
            data = pil_rgb.tobytes()

            # Paso 4: empaquetado en QImage
            # shot.width * 3 = bytes por fila (3 canales RGB × ancho en píxeles)
            img = QImage(data, shot.width, shot.height, shot.width * 3, QImage.Format_RGB888)

            # Paso 5: escalado manteniendo aspect ratio
            pix = QPixmap.fromImage(img).scaled(
                self.display.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation  # interpolación bilineal — más suave que la rápida
            )

            # Paso 6: pintado en pantalla
            self.display.setPixmap(pix)

        except Exception as e:
            # No relanzamos la excepción para no interrumpir el timer
            self.status.setText(f"Error captura: {e}")

    def _update_status(self):
        """
        Actualiza el texto de la barra de estado inferior con las coordenadas
        y dimensiones de la región activa.
        Se llama cada vez que la región cambia (botones de mitad o selector manual).
        """
        r = self.region
        self.status.setText(
            f"Región: x={r['left']} y={r['top']}  {r['width']}×{r['height']}px"
        )

    def closeEvent(self, event):
        """
        Se ejecuta automáticamente cuando el usuario cierra la ventana.
        Para el timer, cierra mss y restaura SIEMPRE el estado original de
        notificaciones que había al arrancar la app, independientemente de si
        el checkbox estaba activo o de si la captura estaba en curso.
        """
        self.timer.stop()
        self.sct.close()
        # Restaura el estado de notificaciones al valor que tenía antes de abrir la app
        self._set_banners(self._original_banners)
        event.accept()


# ── Punto de entrada ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # QApplication es el objeto raíz de toda app PyQt5 — debe crearse antes que
    # cualquier widget
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # estilo visual consistente entre distribuciones Linux

    # Carga el icono de la app para la barra de tareas y la barra de título.
    # sys._MEIPASS existe solo cuando la app se ejecuta como binario PyInstaller
    # (dentro del AppImage). En ejecución directa usa el directorio del script.
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base, "roda_mirror.png")
    if os.path.exists(icon_path):
        # Escala a 256x256 para evitar el límite de XCB (error "Size exceeds maximum")
        # que ocurre con imágenes de alta resolución como iconos generados por IA
        pix = QPixmap(icon_path).scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        icon = QIcon(pix)
        app.setWindowIcon(icon)
    else:
        icon = QIcon()

    win = MirrorWindow()
    win.setWindowIcon(icon)
    win.show()
    # app.exec_() inicia el bucle de eventos de Qt — la app queda a la espera de
    # eventos (clicks, teclas, ticks del timer) hasta que el usuario la cierra.
    # sys.exit() propaga el código de salida de Qt al sistema operativo.
    sys.exit(app.exec_())
