"""Integración con la ventana de ZapZap.

- Inyecta el runtime web (tema + tarjetas de aviso) en cada página de WhatsApp.
- Agrega el menú «Persona 5», el botón de la barra lateral y los atajos.
- Guarda el estado y sincroniza las tarjetas que se crean en la página.

Todo se descubre con Qt (``allWidgets``, ``findChild``) en vez de parchear
clases de ZapZap, para que una actualización del original no rompa el fork.
"""

import functools
import json
import logging
import os
import subprocess
import sys

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFrame, QMenu, QMenuBar, QMessageBox, QPushButton

from p5r_mod import __mod_name__, __version__
from p5r_mod.bootstrap import original_command_args
from p5r_mod.media import MediaTranscoder
from p5r_mod.state import P5RState

logger = logging.getLogger("zapzap-p5r")

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = "zapzap_p5r_runtime"
BOOT_PLACEHOLDER = "__P5R_BOOT__"
WHATSAPP_HOST = "web.whatsapp.com"
SCAN_INTERVAL_MS = 1500
SYNC_INTERVAL_MS = 2500

THEME_SHORTCUT = "Ctrl+Shift+P"
CARDS_SHORTCUT = "Ctrl+Shift+K"

# Barra lateral roja: se lee bien tanto con los iconos claros como oscuros de ZapZap.
NATIVE_QSS = """
/* zapzap-p5r:start */
QFrame#browser_sidebar {
    background-color: #d0021b;
    border-right: 3px solid #000000;
}
QMenuBar {
    background-color: #0b0b0b;
    color: #f4f4f4;
    border-bottom: 2px solid #e60012;
}
QMenuBar::item:selected, QMenuBar::item:pressed {
    background-color: #e60012;
    color: #ffffff;
}
QMenu {
    background-color: #0b0b0b;
    color: #f4f4f4;
    border: 2px solid #e60012;
}
QMenu::item:selected {
    background-color: #e60012;
    color: #ffffff;
}
QMenu::separator {
    height: 2px;
    background: #e60012;
    margin: 4px 8px;
}
/* zapzap-p5r:end */
"""


def _read_text(*parts):
    with open(os.path.join(MOD_DIR, *parts), encoding="utf-8") as handle:
        return handle.read()


def _alive(obj):
    return obj is not None and not sip.isdeleted(obj)


def safe_slot(func):
    """En PyQt6 una excepción sin atrapar en un callback cierra la app: aquí no."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.exception("Error en %s; ZapZap sigue normal", func.__name__)
            return None

    return wrapper


class P5RController(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.state = P5RState()
        self._css = _read_text("web", "p5r.css")
        self._js = _read_text("web", "p5r.js")
        if self._js.count(BOOT_PLACEHOLDER) != 1:
            raise RuntimeError("p5r.js debe contener exactamente un " + BOOT_PLACEHOLDER)
        self._icon = QIcon(os.path.join(MOD_DIR, "assets", "p5r-toggle.svg"))
        self._pages = {}
        self._toggle_widgets = []
        self._gpu_actions = []
        self._styled_windows = []

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(SCAN_INTERVAL_MS)
        self._scan_timer.timeout.connect(self._scan)

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(SYNC_INTERVAL_MS)
        self._sync_timer.timeout.connect(self._sync_pages)

        # GIF y videos: independiente del tema, siempre activo.
        self._media = MediaTranscoder(self, self._whatsapp_pages)

    # ------------------------------------------------------------------ ciclo
    def start(self):
        logger.info("%s %s activo (estado: %s)", __mod_name__, __version__, self.state.path)
        self._scan()
        self._scan_timer.start()
        self._sync_timer.start()
        self._media.start()

    @safe_slot
    def _scan(self):
        for widget in self.app.allWidgets():
            if isinstance(widget, QWebEngineView):
                page = widget.page()
                if _alive(page):
                    self._attach_page(page)
        for window in self.app.topLevelWidgets():
            self._install_window_ui(window)

    # ------------------------------------------------------- páginas web
    def _attach_page(self, page):
        key = id(page)
        known = self._pages.get(key)
        if known is not None and known[0] is page:
            return
        profile = page.profile()
        account_id = (profile.storageName() if _alive(profile) else "") or "default"
        self._pages[key] = (page, account_id)
        page.destroyed.connect(lambda *_args, k=key: self._pages.pop(k, None))
        self._install_script(page, account_id)
        try:
            self._media.attach(page, already_loaded=page.url().host() == WHATSAPP_HOST)
        except Exception:
            logger.exception("No se pudo preparar la reproducción de GIF y videos")
        if page.url().host() == WHATSAPP_HOST:
            # La página ya estaba cargada: aplica el mod sin recargar WhatsApp.
            page.runJavaScript(self._build_source(account_id), 0)
        logger.debug("Página enganchada (cuenta %s)", account_id)

    def _live_pages(self):
        for key, (page, account_id) in list(self._pages.items()):
            if not _alive(page):
                self._pages.pop(key, None)
                continue
            yield page, account_id

    def _whatsapp_pages(self):
        return [page for page, _account in self._live_pages() if page.url().host() == WHATSAPP_HOST]

    def _build_source(self, account_id):
        cards = self.state.cards_for(account_id)
        boot = {
            "version": __version__,
            "accountId": account_id,
            "theme": self.state.theme_enabled,
            "cards": cards["cards"],
            "cardsRev": cards["rev"],
            "tombstones": cards["tombstones"],
            "css": self._css,
            "shortcuts": {"theme": THEME_SHORTCUT, "cards": CARDS_SHORTCUT},
        }
        return self._js.replace(BOOT_PLACEHOLDER, json.dumps(boot, ensure_ascii=False))

    def _install_script(self, page, account_id):
        """(Re)instala el script para que cada recarga arranque con el estado actual."""
        scripts = page.scripts()
        for old in scripts.find(SCRIPT_NAME):
            scripts.remove(old)
        script = QWebEngineScript()
        script.setName(SCRIPT_NAME)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(self._build_source(account_id))
        scripts.insert(script)

    def _run_js(self, page, code, callback=None):
        if callback is None:
            page.runJavaScript(code, 0)
        else:
            page.runJavaScript(code, 0, callback)

    @safe_slot
    def _sync_pages(self):
        for page, account_id in self._live_pages():
            if page.url().host() != WHATSAPP_HOST:
                continue
            self._run_js(
                page,
                "window.__zapzapP5R ? window.__zapzapP5R.drain() : null",
                lambda result, p=page, a=account_id: self._on_drain(p, a, result),
            )

    @safe_slot
    def _on_drain(self, page, account_id, result):
        if not result or not _alive(page):
            return
        try:
            data = json.loads(result)
        except (TypeError, ValueError):
            return
        if "cardsRev" in data and self.state.update_cards(
            account_id, data.get("cardsRev"), data.get("cards"), data.get("tombstones")
        ):
            self._install_script(page, account_id)
        for request in data.get("requests", [])[:10]:
            kind = request.get("type") if isinstance(request, dict) else None
            if kind == "setTheme":
                self.set_theme(bool(request.get("value")), source_page=page)
            elif kind == "switchToOriginal":
                QTimer.singleShot(0, self.switch_to_original)

    def _active_page(self):
        candidates = []
        for widget in self.app.allWidgets():
            if isinstance(widget, QWebEngineView) and widget.isVisible():
                page = widget.page()
                if _alive(page) and page.url().host() == WHATSAPP_HOST:
                    candidates.append((widget.window().isActiveWindow(), page))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1] if candidates else None

    # ------------------------------------------------------------- acciones
    # Conectar siempre con lambdas explícitas: safe_slot atrapa el TypeError
    # con el que PyQt reintentaría la llamada sin el argumento `checked`.
    @safe_slot
    def set_theme(self, enabled, source_page=None):
        self.state.set_theme(enabled)
        enabled = self.state.theme_enabled
        literal = json.dumps(enabled)
        for page, account_id in self._live_pages():
            if page is not source_page and page.url().host() == WHATSAPP_HOST:
                self._run_js(page, f"window.__zapzapP5R && window.__zapzapP5R.setTheme({literal}, true)")
            self._install_script(page, account_id)
        self._refresh_toggle_widgets()
        self._refresh_native_style()

    @safe_slot
    def set_gpu_compat(self, enabled):
        if not self.state.set_gpu_compat(enabled):
            return
        alive = []
        for action in self._gpu_actions:
            if _alive(action):
                action.blockSignals(True)
                action.setChecked(self.state.gpu_compat)
                action.blockSignals(False)
                alive.append(action)
        self._gpu_actions = alive
        status = "activada" if self.state.gpu_compat else "desactivada"
        answer = QMessageBox.question(
            self.app.activeWindow(),
            "Compatibilidad gráfica",
            f"Compatibilidad gráfica {status}.\n\n"
            "Actívala si ZapZap se traba o se cierra solo. Se aplica al reiniciar;\n"
            "tu sesión de WhatsApp no cambia.\n\n"
            "¿Reiniciar ahora?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        restart = getattr(self.app, "restartApplication", None)
        if callable(restart) and restart() is not False:
            return
        QMessageBox.information(self.app.activeWindow(), "Compatibilidad gráfica",
                                "No se pudo reiniciar solo: cierra ZapZap P5R y vuelve a abrirlo.")

    @safe_slot
    def open_cards_panel(self):
        self._call_active("openPanel()")

    @safe_slot
    def new_card_for_active_chat(self):
        self._call_active("openEditorForActiveChat()")

    def _call_active(self, call):
        page = self._active_page()
        if page is None:
            return
        self._run_js(page, f"window.__zapzapP5R && window.__zapzapP5R.{call}")

    @safe_slot
    def switch_to_original(self):
        parent = self.app.activeWindow()
        answer = QMessageBox.question(
            parent,
            "Volver a ZapZap original",
            "ZapZap se reiniciará tal cual viene de GitHub, sin el mod de Persona 5.\n"
            "Tu sesión de WhatsApp y tus chats no cambian.\n\n"
            "Para regresar, abre «ZapZap P5R» desde el menú de aplicaciones.\n\n"
            "¿Continuar?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        args = original_command_args()
        logger.info("Reiniciando como ZapZap original: %s", args)
        sys.argv[:] = args
        restart = getattr(self.app, "restartApplication", None)
        if callable(restart) and restart() is not False:
            return
        # Respaldo por si ZapZap cambia su reinicio: lanza con retraso para que
        # esta instancia libere el bloqueo de instancia única antes.
        delayed = "import subprocess, sys, time; time.sleep(2); subprocess.Popen(sys.argv[1:])"
        subprocess.Popen(
            [sys.executable, "-c", delayed, sys.executable, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.app.quit()

    @safe_slot
    def export_diagnosis(self):
        """Guarda la estructura de la página (sin textos ni nombres) para ajustar el mod."""
        page = self._active_page()
        if page is None:
            QMessageBox.information(self.app.activeWindow(), __mod_name__,
                                    "Abre ZapZap con WhatsApp cargado y un chat abierto.")
            return
        self._run_js(page, "window.__zapzapP5R ? window.__zapzapP5R.diagnose() : null",
                     self._save_diagnosis)

    @safe_slot
    def _save_diagnosis(self, report):
        if not report:
            return
        path = os.path.join(os.path.dirname(self.state.path), "diagnostico-estilo.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(report)
        logger.info("Diagnóstico de estilo guardado en %s", path)
        QMessageBox.information(
            self.app.activeWindow(),
            "Diagnóstico de estilo",
            "Se guardó la estructura de la página, sin textos, nombres ni fotos:\n\n"
            f"{path}\n\n"
            "Sirve para ajustar la estética cuando WhatsApp cambia su diseño.",
        )

    @safe_slot
    def show_about(self):
        try:
            import zapzap

            zapzap_version = zapzap.__version__
            zapzap_path = os.path.dirname(os.path.dirname(os.path.abspath(zapzap.__file__)))
        except Exception:
            zapzap_version, zapzap_path = "?", "?"
        QMessageBox.about(
            self.app.activeWindow(),
            f"Acerca de {__mod_name__}",
            f"<b>{__mod_name__} {__version__}</b><br>"
            f"Fork personalizado sobre ZapZap {zapzap_version} (código de GitHub).<br>"
            f"<code>{zapzap_path}</code><br><br>"
            "ZapZap se actualiza solo a cada versión estable nueva; el mod se "
            "aplica encima sin modificar sus archivos.<br><br>"
            f"Estética Persona 5: <b>{THEME_SHORTCUT}</b><br>"
            f"Tarjetas de aviso: <b>{CARDS_SHORTCUT}</b><br><br>"
            f"Estado guardado en:<br><code>{self.state.path}</code>",
        )

    # ------------------------------------------------------------ UI nativa
    def _install_window_ui(self, window):
        if not _alive(window):
            return
        if not window.property("p5r_menu_installed"):
            menubar = window.findChild(QMenuBar)
            if menubar is not None:
                self._install_menu(window, menubar)
                window.setProperty("p5r_menu_installed", True)
        if not window.property("p5r_sidebar_installed"):
            frame = window.findChild(QFrame, "settings_buttons_layout")
            if frame is not None and frame.layout() is not None:
                self._install_sidebar_button(frame)
                window.setProperty("p5r_sidebar_installed", True)
        if window.property("p5r_menu_installed") or window.property("p5r_sidebar_installed"):
            if window not in self._styled_windows:
                self._styled_windows.append(window)
                self._apply_native_style(window)

    def _build_menu(self, parent, window=None):
        menu = QMenu("&Persona 5", parent)
        menu.setObjectName("p5r_menu")

        theme_action = QAction("Estética Persona 5", menu)
        theme_action.setCheckable(True)
        theme_action.setChecked(self.state.theme_enabled)
        theme_action.setShortcut(QKeySequence(THEME_SHORTCUT))
        theme_action.triggered.connect(lambda checked: self.set_theme(checked))
        self._toggle_widgets.append(theme_action)

        cards_action = QAction("Tarjetas de aviso…", menu)
        cards_action.setShortcut(QKeySequence(CARDS_SHORTCUT))
        cards_action.triggered.connect(lambda _checked=False: self.open_cards_panel())

        new_card_action = QAction("Nueva tarjeta para el chat abierto…", menu)
        new_card_action.triggered.connect(lambda _checked=False: self.new_card_for_active_chat())

        original_action = QAction("Volver a ZapZap original (sin mod)…", menu)
        original_action.triggered.connect(lambda _checked=False: self.switch_to_original())

        diagnose_action = QAction("Exportar diagnóstico de estilo (sin textos)…", menu)
        diagnose_action.triggered.connect(lambda _checked=False: self.export_diagnosis())

        about_action = QAction(f"Acerca de {__mod_name__}", menu)
        about_action.triggered.connect(lambda _checked=False: self.show_about())

        menu.addAction(theme_action)
        if sys.platform == "win32":
            gpu_action = QAction("Compatibilidad gráfica (evita cierres)", menu)
            gpu_action.setCheckable(True)
            gpu_action.setChecked(self.state.gpu_compat)
            gpu_action.setToolTip("Dibuja sin composición por GPU. Se aplica al reiniciar.")
            gpu_action.triggered.connect(lambda checked: self.set_gpu_compat(checked))
            self._gpu_actions.append(gpu_action)
            menu.addAction(gpu_action)
        menu.addSeparator()
        menu.addAction(cards_action)
        menu.addAction(new_card_action)
        menu.addSeparator()
        menu.addAction(original_action)
        menu.addAction(diagnose_action)
        menu.addAction(about_action)

        if window is not None:
            # Atajos activos aunque la barra de menú esté oculta.
            window.addAction(theme_action)
            window.addAction(cards_action)
        return menu

    def _install_menu(self, window, menubar):
        menu = self._build_menu(menubar, window)
        help_menu = window.findChild(QMenu, "menuHelp")
        if help_menu is not None and help_menu.menuAction() in menubar.actions():
            menubar.insertMenu(help_menu.menuAction(), menu)
        else:
            menubar.addMenu(menu)

    def _install_sidebar_button(self, frame):
        try:
            from zapzap.ui.components.browser_sidebar import BrowserSidebarButton

            button = BrowserSidebarButton("p5r_toggle", frame)
        except Exception:
            button = QPushButton(frame)
            button.setObjectName("p5r_toggle")
            button.setFlat(True)
            button.setFixedSize(40, 40)
        button.setCheckable(True)
        button.setChecked(self.state.theme_enabled)
        button.setIcon(self._icon)
        button.setToolTip(
            f"Estética Persona 5 ({THEME_SHORTCUT})\nClic derecho: tarjetas de aviso y más"
        )
        button.clicked.connect(lambda checked: self.set_theme(checked))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda pos, b=button: self._show_button_menu(b, pos)
        )
        self._toggle_widgets.append(button)

        layout = frame.layout()
        divider = frame.findChild(QFrame, "line_2")
        index = layout.indexOf(divider) if divider is not None else -1
        layout.insertWidget(index + 1 if index >= 0 else 0, button)

    @safe_slot
    def _show_button_menu(self, button, pos):
        menu = self._build_menu(button)
        menu.exec(button.mapToGlobal(pos))
        menu.deleteLater()

    def _refresh_toggle_widgets(self):
        alive = []
        for widget in self._toggle_widgets:
            if not _alive(widget):
                continue
            widget.blockSignals(True)
            widget.setChecked(self.state.theme_enabled)
            widget.blockSignals(False)
            alive.append(widget)
        self._toggle_widgets = alive

    def _apply_native_style(self, window):
        current = window.styleSheet() or ""
        start = current.find("/* zapzap-p5r:start */")
        if start >= 0:
            end = current.find("/* zapzap-p5r:end */", start)
            current = current[:start] + current[end + len("/* zapzap-p5r:end */"):]
        new_style = current.strip()
        if self.state.theme_enabled:
            new_style = f"{new_style}\n{NATIVE_QSS}" if new_style else NATIVE_QSS
        if new_style != (window.styleSheet() or ""):
            window.setStyleSheet(new_style)

    def _refresh_native_style(self):
        self._styled_windows = [w for w in self._styled_windows if _alive(w)]
        for window in self._styled_windows:
            self._apply_native_style(window)
