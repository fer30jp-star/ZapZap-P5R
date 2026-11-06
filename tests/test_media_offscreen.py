"""Prueba de GIF y videos: MP4 H.264/AAC como los manda WhatsApp, sin pantalla.

Crea con ffmpeg del sistema (necesita libx264) un «GIF» (MP4 sin audio), un
video con audio AAC y una nota de audio M4A; los mete en la página como blob:
igual que WhatsApp y comprueba que, con el gancho de media.js, se reproducen.

    ~/.local/share/zapzap-p5r/venv/bin/python tests/test_media_offscreen.py
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Igual que smoke_offscreen.py: sin pantalla ni GPU (si no, Chromium se cae al mostrar la vista).
for _name in ("DISPLAY", "WAYLAND_DISPLAY"):
    os.environ.pop(_name, None)
os.environ.update({"QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
                   "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu"})

from PyQt6.QtCore import QTimer, QUrl  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

from p5r_mod.media import MediaTranscoder  # noqa: E402

TIMEOUT_MS = 90_000

SAMPLES = {
    # nombre: (argumentos de ffmpeg, tipo del blob, etiqueta, cómo se asigna)
    "gif": (["-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            "video/mp4", "video", "attribute"),
    "video": (["-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
              "video/mp4", "video", "property"),
    "audio": (["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", "-f", "mp4"],
              "audio/mp4", "audio", "source"),
}

PAGE_JS = r"""
(() => {
  const samples = %s;
  window.__results = {};
  for (const [name, s] of Object.entries(samples)) {
    const bytes = Uint8Array.from(atob(s.data), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: s.type }));
    const el = document.createElement(s.tag);
    el.muted = true;
    const r = (window.__results[name] = { canPlayNative: el.canPlayType(s.type), played: null, events: [] });
    for (const ev of ["error", "loadeddata", "playing"]) el.addEventListener(ev, () => r.events.push(ev));
    if (s.how === "attribute") el.setAttribute("src", url);
    else if (s.how === "property") el.src = url;
    else { const src = document.createElement("source"); el.appendChild(src); src.src = url; }
    document.body.appendChild(el);
    el.play().then(() => (r.played = true), (e) => (r.played = "rechazado: " + e.name + " " + e.message));
    r.el = el;
  }
})();
"""

CHECK_JS = r"""
JSON.stringify(Object.fromEntries(Object.entries(window.__results || {}).map(([k, r]) => [k, {
  canPlayNative: r.canPlayNative, played: r.played, events: r.events,
  readyState: r.el.readyState, width: r.el.videoWidth || 0, time: r.el.currentTime,
  src: (r.el.currentSrc || "").slice(0, 5), error: r.el.error && r.el.error.code,
}])))
"""


def make_samples(tmp):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("Falta ffmpeg en el sistema para crear los videos de prueba")
    out = {}
    for name, (args, mime, tag, how) in SAMPLES.items():
        path = os.path.join(tmp, f"{name}.mp4")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args, path], check=True)
        with open(path, "rb") as handle:
            out[name] = {"data": base64.b64encode(handle.read()).decode(), "type": mime, "tag": tag, "how": how}
    return out


def main():
    with tempfile.TemporaryDirectory() as tmp:
        samples = make_samples(tmp)
    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(800, 600)
    view.show()  # oculta, Chromium pausa los videos en segundo plano
    page = view.page()
    transcoder = MediaTranscoder(app, lambda: [page])
    transcoder.attach(page, already_loaded=False)
    transcoder.start()
    outcome = {"ok": False, "last": None}

    def check():
        page.runJavaScript(CHECK_JS, 0, on_check)

    def on_check(result):
        data = json.loads(result or "{}")
        outcome["last"] = data
        # Sin clic del usuario Chromium no deja reproducir <audio> (aunque esté
        # en silencio): para el audio basta con que cargue.
        if data and all(r["readyState"] >= 2 and r["src"] == "blob:" and not r["error"]
                        and (name == "audio" or (r["played"] is True and r["width"] > 0))
                        for name, r in data.items()):
            outcome["ok"] = True
            app.quit()

    def loaded(_ok):
        page.runJavaScript(PAGE_JS % json.dumps(samples), 0)
        timer.start()

    timer = QTimer()
    timer.setInterval(500)
    timer.timeout.connect(check)
    view.loadFinished.connect(loaded)
    view.setHtml("<!doctype html><html><body></body></html>", QUrl("https://example.com/"))
    QTimer.singleShot(TIMEOUT_MS, app.quit)
    app.exec()
    transcoder.stop()

    print(json.dumps(outcome["last"], indent=1))
    if not outcome["ok"]:
        print("FALLÓ: no todos los medios se reproducen")
        return 1
    print("OK: GIF, video y audio MP4 se reproducen tras convertirse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
