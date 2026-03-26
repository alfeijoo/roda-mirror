#!/bin/bash
# build_appimage.sh — Empaqueta RodaMirroring como AppImage
# Uso: bash build_appimage.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="RodaMirroring"
APPDIR="$SCRIPT_DIR/AppDir"

echo "==> [1/5] Instalando dependencias Python..."
pip install pyinstaller PyQt5 mss pillow --break-system-packages --quiet

echo "==> [2/5] Generando binario con PyInstaller..."
export PATH="$HOME/.local/bin:$PATH"
pyinstaller --onefile --noconsole --name "$APP_NAME" \
    --add-data "$SCRIPT_DIR/roda_mirror.png:." \
    "$SCRIPT_DIR/screen_mirror.py"

echo "==> [3/5] Construyendo estructura AppDir..."
mkdir -p "$APPDIR/usr/bin"
cp "$SCRIPT_DIR/dist/$APP_NAME" "$APPDIR/usr/bin/$APP_NAME"

# Icono SVG
cp "$SCRIPT_DIR/roda_mirror.png" "$APPDIR/roda_mirror.png"

# .desktop
cat > "$APPDIR/roda_mirror.desktop" << 'DEOF'
[Desktop Entry]
Name=Roda Mirroring
Exec=RodaMirroring
Icon=roda_mirror
Type=Application
Categories=Utility;
DEOF

# AppRun
cat > "$APPDIR/AppRun" << 'AEOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/RodaMirroring" "$@"
AEOF
chmod +x "$APPDIR/AppRun"

echo "==> [4/5] Descargando appimagetool..."
wget -q "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
     -O "$SCRIPT_DIR/appimagetool"
chmod +x "$SCRIPT_DIR/appimagetool"

echo "==> [5/5] Generando AppImage..."
cd "$SCRIPT_DIR"
ARCH=x86_64 ./appimagetool AppDir "${APP_NAME}-x86_64.AppImage"

echo ""
echo "✅ Listo: ${APP_NAME}-x86_64.AppImage"
echo "   Para ejecutar: ./${APP_NAME}-x86_64.AppImage"
echo "   Para instalar: mueve el .AppImage a ~/Applications/ y dale permisos con chmod +x"
