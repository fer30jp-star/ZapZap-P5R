"""Conversión de GIF y videos de WhatsApp a un formato que Qt WebEngine reproduce.

El PyQt6-WebEngine de pip no trae H.264 ni AAC (códecs propietarios), y
WhatsApp manda los GIF y los videos como MP4 con esos códecs: se quedan sin
reproducir. ``web/media.js`` intercepta esos MP4 en la página y los manda aquí
por trozos; ffmpeg los convierte a WebM (VP8 + Opus) y se devuelven a la página.

ffmpeg sale del paquete ``imageio-ffmpeg`` (trae su propio binario, también en
Windows) o, si no está, del sistema. Si no hay ninguno, se instala
``imageio-ffmpeg`` en segundo plano la primera vez que hace falta.
"""

import base64
import binascii
import collections
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWebEngineCore import QWebEngineScript

logger = logging.getLogger("zapzap-p5r")

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = "zapzap_p5r_media"
POLL_INTERVAL_MS = 400
CHUNK_CHARS = 2_000_000  # múltiplo de 4: cada trozo es base64 válido
MAX_INPUT_BYTES = 200 * 1024 * 1024
TRANSCODE_TIMEOUT_S = 900
MAX_WIDTH = 1280
FFMPEG_PACKAGE = "imageio-ffmpeg"
# WhatsApp crea un blob nuevo cada vez que vuelve a pintar un mensaje: se
# recuerdan las últimas conversiones (y los fallos) por su contenido.
CACHE_ENTRIES = 24

POLL_JS = "window.__p5rMedia ? window.__p5rMedia.poll() : null"
_VIDEO_STREAM = re.compile(r"Stream #\d+:\d+.*?: Video: (?!.*attached pic)", re.M)


def _no_window():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _has_encoders(path):
    try:
        out = subprocess.run([path, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             timeout=30, **_no_window()).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return " libvpx " in out and " libopus " in out


def _bundled_ffmpeg():
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class FFmpeg:
    """Busca (y si hace falta instala) un ffmpeg con VP8 y Opus. Seguro entre hilos."""

    def __init__(self):
        self._lock = threading.Lock()
        self._path = None
        self._install_tried = False

    def path(self):
        with self._lock:
            if self._path:
                return self._path
            self._path = self._find()
            if not self._path and not self._install_tried:
                self._install_tried = True
                self._install()
                self._path = self._find()
            if self._path:
                logger.info("ffmpeg para GIF y videos: %s", self._path)
            return self._path

    @staticmethod
    def _find():
        for candidate in (_bundled_ffmpeg(), shutil.which("ffmpeg")):
            if candidate and _has_encoders(candidate):
                return candidate
        return None

    @staticmethod
    def _install():
        logger.info("Instalando %s para poder reproducir GIF y videos…", FFMPEG_PACKAGE)
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                            FFMPEG_PACKAGE], check=True, capture_output=True, text=True, timeout=900,
                           **_no_window())
            import importlib

            importlib.invalidate_caches()
        except (OSError, subprocess.SubprocessError) as error:
            detail = getattr(error, "stderr", "") or error
            logger.error("No se pudo instalar %s: %s", FFMPEG_PACKAGE, str(detail).strip()[-400:])


def _first_error(stderr):
    """La primera línea útil de ffmpeg (p. ej. «moov atom not found» de un MP4 a medias)."""
    for line in (stderr or "").splitlines():
        line = re.sub(r"^\[[^]]*\]\s*", "", line.strip())
        if line:
            return line[:200]
    return "ffmpeg falló sin mensaje"


def transcode(ffmpeg, data):
    """MP4 (u otro) -> (tipo MIME, bytes WebM). Lanza RuntimeError si no se puede."""
    with tempfile.TemporaryDirectory(prefix="zapzap-p5r-media-") as tmp:
        source = os.path.join(tmp, "in")
        target = os.path.join(tmp, "out.webm")
        with open(source, "wb") as handle:
            handle.write(data)
        probe = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", source], capture_output=True,
                               text=True, errors="replace", timeout=60, **_no_window())
        has_video = bool(_VIDEO_STREAM.search(probe.stderr))
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", source,
               "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1",
               "-c:a", "libopus", "-b:a", "96k"]
        if has_video:
            scale = (f"scale='if(gt(iw,ih),min({MAX_WIDTH},iw),-2)':"
                     f"'if(gt(iw,ih),-2,min({MAX_WIDTH},ih))'")
            cmd += ["-map", "0:v:0", "-vf", scale, "-pix_fmt", "yuv420p",
                    "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8",
                    "-crf", "24", "-b:v", "2M", "-auto-alt-ref", "0", "-threads", "4"]
        else:
            cmd += ["-vn"]
        cmd += ["-f", "webm", target]
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                timeout=TRANSCODE_TIMEOUT_S, **_no_window())
        if result.returncode != 0 or not os.path.isfile(target) or not os.path.getsize(target):
            raise RuntimeError(_first_error(result.stderr))
        with open(target, "rb") as handle:
            return ("video/webm" if has_video else "audio/webm"), handle.read()


class MediaTranscoder(QObject):
    """Recoge de las páginas los videos que no se pueden reproducir y devuelve su versión WebM.

    ``pages`` es una función que devuelve las páginas de WhatsApp a atender.
    """

    def __init__(self, parent, pages):
        super().__init__(parent)
        self._pages = pages
        with open(os.path.join(MOD_DIR, "web", "media.js"), encoding="utf-8") as handle:
            self._js = handle.read()
        self._ffmpeg = FFmpeg()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="p5r-media")
        self._incoming = {}  # (página, sesión, id) -> [trozos]
        self._polling = set()  # páginas con un poll en curso
        self._running = []  # (página, sesión, id, future)
        self._cache = collections.OrderedDict()  # sha256 del original -> future
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -------------------------------------------------------------- páginas
    def attach(self, page, already_loaded):
        """Instala el gancho antes que el código de WhatsApp (y ya mismo si la página estaba cargada)."""
        scripts = page.scripts()
        for old in scripts.find(SCRIPT_NAME):
            scripts.remove(old)
        script = QWebEngineScript()
        script.setName(SCRIPT_NAME)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(self._js)
        scripts.insert(script)
        if already_loaded:
            page.runJavaScript(self._js, 0)

    # ---------------------------------------------------------------- ciclo
    def _tick(self):
        try:
            self._collect_finished()
            for page in self._pages():
                self._poll(page)
        except Exception:
            logger.exception("Error en la conversión de videos; ZapZap sigue normal")

    def _poll(self, page):
        key = id(page)
        if key in self._polling or sip.isdeleted(page):
            return
        self._polling.add(key)
        page.runJavaScript(POLL_JS, 0, lambda result, p=page: self._on_poll(p, result))

    def _on_poll(self, page, result):
        self._polling.discard(id(page))
        try:
            if not result or sip.isdeleted(page):
                return
            message = json.loads(result)
            session, job_id = str(message["session"]), int(message["id"])
            index, total = int(message["index"]), int(message["total"])
            key = (id(page), session, job_id)
            parts = self._incoming.setdefault(key, [])
            parts.append(base64.b64decode(message["data"]))
            if index + 1 >= total:
                del self._incoming[key]
                data = b"".join(parts)
                logger.debug("Convirtiendo video de %d KB", len(data) // 1024)
                if len(data) > MAX_INPUT_BYTES:
                    self._fail(page, session, job_id, "demasiado grande")
                else:
                    self._running.append((page, session, job_id, self._convert(data)))
            # Hay más trozos (o más videos) en camino: se piden sin esperar al temporizador.
            self._poll(page)
        except (KeyError, TypeError, ValueError, binascii.Error):
            logger.exception("Mensaje de video inválido")
        except Exception:
            logger.exception("Error en la conversión de videos; ZapZap sigue normal")

    def _convert(self, data):
        """Future con (MIME, bytes); reutiliza la conversión si ya se hizo (o va en curso)."""
        digest = hashlib.sha256(data).hexdigest()
        future = self._cache.get(digest)
        if future is None or future.cancelled():
            future = self._pool.submit(self._work, data)
            self._cache[digest] = future
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
        else:
            logger.debug("Video ya convertido antes: se reutiliza")
        self._cache.move_to_end(digest)
        return future

    def _work(self, data):
        ffmpeg = self._ffmpeg.path()
        if not ffmpeg:
            raise RuntimeError("no hay ffmpeg con VP8/Opus")
        return transcode(ffmpeg, data)

    def _collect_finished(self):
        still = []
        for page, session, job_id, future in self._running:
            if not future.done():
                still.append((page, session, job_id, future))
                continue
            if sip.isdeleted(page):
                continue
            try:
                mime, data = future.result()
            except Exception as error:
                # «moov atom not found»: trozo a medias que WhatsApp mete en un blob; es normal.
                level = logging.DEBUG if "moov atom not found" in str(error) else logging.WARNING
                logger.log(level, "No se pudo convertir un video: %s", error)
                self._fail(page, session, job_id, str(error)[:200])
                continue
            self._deliver(page, session, job_id, mime, data)
        self._running = still

    def _deliver(self, page, session, job_id, mime, data):
        encoded = base64.b64encode(data).decode("ascii")
        chunks = [encoded[i:i + CHUNK_CHARS] for i in range(0, len(encoded), CHUNK_CHARS)] or [""]
        head = f"window.__p5rMedia && window.__p5rMedia.put({json.dumps(session)}, {job_id}, "
        for index, chunk in enumerate(chunks):
            page.runJavaScript(f"{head}{index}, {len(chunks)}, {json.dumps(mime)}, '{chunk}')", 0)
        logger.debug("Video convertido: %d KB", len(data) // 1024)

    def _fail(self, page, session, job_id, reason):
        page.runJavaScript(
            f"window.__p5rMedia && window.__p5rMedia.fail({json.dumps(session)}, {job_id}, {json.dumps(reason)})", 0
        )
