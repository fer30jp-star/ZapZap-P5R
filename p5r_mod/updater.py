#!/usr/bin/env python3
"""Instala y actualiza el ZapZap (desde GitHub) sobre el que corre el fork.

Diseño para que la actualización nunca deje la app rota:

- Cada versión estable de ZapZap se descarga de GitHub (sin necesitar git) y
  se extrae en su propia carpeta (``releases/<versión>``); ``current.txt``
  dice cuál está activa (un archivo y no un enlace: funciona igual en Windows).
- Una versión nueva solo se activa si pasa una prueba de importación con el mod.
- La app en marcha usa la ruta de su versión, así que cambiar la versión activa
  mientras está abierta no la afecta: la versión nueva se usa al reabrir.
- Si la versión nueva falla al arrancar, el lanzador pide ``rollback``.

Solo usa la biblioteca estándar: corre con el Python del sistema, en Linux y en
Windows.

    updater.py install [--import-flatpak-settings]
    updater.py update [--force] [--quiet] [--deps] [--retry-skipped]
    updater.py rollback [--only-if-fresh]
    updater.py mark-good
    updater.py check
    updater.py status
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

IS_WINDOWS = sys.platform == "win32"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAGS_URL = "https://api.github.com/repos/rafatosta/zapzap/tags?per_page=100"
TARBALL_URL = "https://codeload.github.com/rafatosta/zapzap/tar.gz/refs/tags/{tag}"
USER_AGENT = "zapzap-p5r-updater"
if IS_WINDOWS:
    DATA_HOME = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    # El Python de Microsoft Store redirige en secreto lo que escribe en AppData
    # a su carpeta del paquete; otros programas (el python.exe del venv) no lo
    # ven. Usamos esa carpeta real directamente para que todos vean lo mismo.
    _store = os.path.basename(sys.base_prefix)
    if "\\WindowsApps\\" in sys.base_prefix and "__" in _store:
        _family = _store.split("_")[0] + "_" + _store.rsplit("__", 1)[1]
        DATA_HOME = os.path.join(DATA_HOME, "Packages", _family, "LocalCache", "Local")
    CONFIG_HOME = DATA_HOME
else:
    DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
P5R_HOME = os.environ.get("ZAPZAP_P5R_HOME") or os.path.join(DATA_HOME, "zapzap-p5r")
RELEASES_DIR = os.path.join(P5R_HOME, "releases")
CURRENT_FILE = os.path.join(P5R_HOME, "current.txt")
LEGACY_CURRENT_LINK = os.path.join(P5R_HOME, "current")
VENV_DIR = os.path.join(P5R_HOME, "venv")
if IS_WINDOWS:
    VENV_PYTHON = os.path.join(VENV_DIR, "Scripts", "python.exe")
    # Sin ventana de consola: es la que abre la app.
    VENV_PYTHON_GUI = os.path.join(VENV_DIR, "Scripts", "pythonw.exe")
else:
    VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")
    VENV_PYTHON_GUI = VENV_PYTHON
STATE_FILE = os.path.join(P5R_HOME, "updater.json")
LOG_FILE = os.path.join(P5R_HOME, "updater.log")

FLATPAK_CONFIG = os.path.expanduser("~/.var/app/com.rtosta.zapzap/config/ZapZap/ZapZap.conf")
NATIVE_CONFIG = os.path.join(CONFIG_HOME, "ZapZap", "ZapZap.conf")

CHECK_INTERVAL_S = 12 * 3600
KEEP_RELEASES = 3
BASE_DEPENDENCIES = ["PyQt6", "PyQt6-WebEngine"]
# Del mod, además de las de ZapZap: ffmpeg para convertir GIF y videos (ver media.py).
MOD_DEPENDENCIES = ["imageio-ffmpeg"]
TAG_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")

QUIET = False


# ---------------------------------------------------------------- utilidades
def log(message):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    os.makedirs(P5R_HOME, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    if not QUIET:
        print(f"[ZapZap P5R] {message}", flush=True)


def notify(message):
    if not IS_WINDOWS and shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "--app-name=ZapZap P5R", "--icon=zapzap-p5r", "ZapZap P5R", message],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def run(cmd, timeout=None, **kwargs):
    if IS_WINDOWS:
        # Sin ventanas de consola que parpadeen (el lanzador corre sin consola).
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(cmd, check=True, timeout=timeout, text=True, capture_output=True, **kwargs)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(P5R_HOME, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(tmp, STATE_FILE)


def version_key(tag):
    match = TAG_RE.match(tag)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def current_release():
    try:
        with open(CURRENT_FILE, encoding="utf-8") as handle:
            name = handle.read().strip()
        return name if version_key(name) else None
    except OSError:
        pass
    # Instalaciones anteriores usaban un enlace simbólico «current».
    if os.path.islink(LEGACY_CURRENT_LINK):
        name = os.path.basename(os.path.realpath(LEGACY_CURRENT_LINK))
        if version_key(name):
            point_current_to(name)
            return name
    return None


def current_dir():
    """Carpeta de la versión activa (o None si no hay ninguna)."""
    release = current_release()
    return os.path.join(RELEASES_DIR, release) if release else None


def installed_releases():
    if not os.path.isdir(RELEASES_DIR):
        return []
    names = [n for n in os.listdir(RELEASES_DIR) if version_key(n) and not n.startswith(".")]
    return sorted(names, key=version_key)


def point_current_to(release):
    os.makedirs(P5R_HOME, exist_ok=True)
    tmp = CURRENT_FILE + ".new"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(release + "\n")
    os.replace(tmp, CURRENT_FILE)
    if os.path.islink(LEGACY_CURRENT_LINK):
        os.remove(LEGACY_CURRENT_LINK)


# ------------------------------------------------------------------- piezas
def http_get(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_tags():
    """Versiones publicadas de ZapZap (etiquetas del repositorio en GitHub)."""
    tags = []
    for page in range(1, 6):
        data = json.loads(http_get(f"{TAGS_URL}&page={page}").decode("utf-8"))
        tags += [item["name"] for item in data if isinstance(item, dict) and "name" in item]
        if len(data) < 100:
            break
    return tags


def latest_tag(tags):
    stable = [t for t in tags if version_key(t)]
    if not stable:
        raise RuntimeError("No encontré versiones estables de ZapZap")
    return max(stable, key=version_key)


def venv_ok():
    if not os.path.isfile(VENV_PYTHON):
        return False
    try:
        run([VENV_PYTHON, "-c", "import PyQt6.QtWebEngineWidgets"], timeout=60)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def release_dependencies(release_dir):
    """Dependencias declaradas por esa versión de ZapZap (pyproject.toml)."""
    deps = list(BASE_DEPENDENCIES)
    try:
        with open(os.path.join(release_dir, "pyproject.toml"), encoding="utf-8") as handle:
            text = handle.read()
        block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
        if block:
            deps = re.findall(r"[\"']([^\"']+)[\"']", block.group(1)) or deps
    except OSError:
        pass
    return deps + [d for d in MOD_DEPENDENCIES if d not in deps]


def ensure_venv(dependencies, upgrade=False):
    if not os.path.isfile(VENV_PYTHON) or (not upgrade and not venv_ok()):
        if os.path.isdir(VENV_DIR):
            log("El entorno de Python está dañado (¿cambió la versión de Python?): se recrea")
            shutil.rmtree(VENV_DIR)
        log("Creando el entorno de Python de ZapZap P5R…")
        run([sys.executable, "-m", "venv", VENV_DIR], timeout=300)
        run([VENV_PYTHON, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], timeout=600)
    cmd = [VENV_PYTHON, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    cmd += ["--upgrade"] if upgrade else ["--upgrade-strategy", "only-if-needed"]
    log(("Actualizando" if upgrade else "Comprobando") + " dependencias: " + ", ".join(dependencies))
    run(cmd + dependencies, timeout=1800)


def export_release(tag):
    """Descarga la versión ``tag`` de GitHub y la extrae en releases/<tag>."""
    target = os.path.join(RELEASES_DIR, tag)
    if os.path.isdir(target):
        return target
    os.makedirs(RELEASES_DIR, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=f".{tag}-", dir=RELEASES_DIR)
    try:
        log(f"Descargando ZapZap {tag} desde GitHub…")
        archive = os.path.join(staging, "release.tar.gz")
        with open(archive, "wb") as handle:
            handle.write(http_get(TARBALL_URL.format(tag=tag), timeout=300))
        unpacked = os.path.join(staging, "unpacked")
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(unpacked, filter="data")
            except TypeError:
                # Python 3.10/3.11 antiguos no tienen «filter»: se revisan a mano.
                for member in tar.getmembers():
                    target_path = os.path.realpath(os.path.join(unpacked, member.name))
                    if not target_path.startswith(os.path.realpath(unpacked) + os.sep) or member.issym() or member.islnk():
                        raise RuntimeError(f"Archivo sospechoso en ZapZap {tag}: {member.name}")
                tar.extractall(unpacked)
        # El archivo trae una carpeta raíz «zapzap-<versión>/».
        roots = os.listdir(unpacked)
        if len(roots) != 1 or not os.path.isdir(os.path.join(unpacked, roots[0], "zapzap")):
            raise RuntimeError(f"El archivo de ZapZap {tag} no tiene la forma esperada")
        os.rename(os.path.join(unpacked, roots[0]), target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def verify_release(release_dir):
    """Comprueba que esa versión de ZapZap y el mod cargan juntos."""
    code = (
        "import sys; "
        f"sys.path[:0] = [{PROJECT_DIR!r}, {release_dir!r}]; "
        "import zapzap, zapzap.__main__, zapzap.app.application; "
        "import p5r_mod.controller; "
        "print(zapzap.__version__)"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = run([VENV_PYTHON, "-c", code], timeout=180, env=env)
    return result.stdout.strip()


def prune_releases(keep_also=None):
    releases = installed_releases()
    protected = {current_release(), keep_also}
    for name in releases[:-KEEP_RELEASES]:
        if name not in protected:
            shutil.rmtree(os.path.join(RELEASES_DIR, name), ignore_errors=True)
            log(f"Versión antigua eliminada: {name}")


def import_flatpak_settings():
    """Copia tus ajustes del ZapZap Flatpak (tema, rendimiento, notificaciones…).

    Solo si el ZapZap nativo aún no tiene configuración. No copia la sesión de
    WhatsApp: el fork se vincula como un dispositivo aparte.
    """
    if os.path.exists(NATIVE_CONFIG) or not os.path.exists(FLATPAK_CONFIG):
        return False
    with open(FLATPAK_CONFIG, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    skip_prefixes = (
        "geometry=", "windowState=",          # tamaño de ventana de otra instalación
        "last_environment=",                  # que ZapZap detecte que ahora es nativo
        "start_system=",                      # el inicio automático lo maneja el fork
        "spellCheckProvisionedLocale=",       # diccionarios en otra ruta
    )
    kept = [line for line in lines if not line.startswith(skip_prefixes)]
    os.makedirs(os.path.dirname(NATIVE_CONFIG), exist_ok=True)
    with open(NATIVE_CONFIG, "w", encoding="utf-8") as handle:
        handle.write("\n".join(kept) + "\n")
    log(f"Ajustes importados del Flatpak a {NATIVE_CONFIG}")
    return True


def update_mod(project_dir=PROJECT_DIR, verify=None):
    """Actualiza el propio ZapZap P5R desde su repositorio git (si es un clon).

    Solo avanza (fast-forward) y solo si no hay cambios locales sin guardar.
    Si la versión nueva del mod no carga con el ZapZap activo, se vuelve a la
    anterior. Los cambios se usan la próxima vez que se abre la app.
    Devuelve: "no-git", "sin-remoto", "cambios-locales", "al-dia",
    "actualizado" o "revertido".
    """
    if not os.path.isdir(os.path.join(project_dir, ".git")) or not shutil.which("git"):
        return "no-git"
    git = ["git", "-C", project_dir]
    # Sin preguntas de usuario/contraseña en segundo plano.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    if "origin" not in run(git + ["remote"], env=env).stdout.split():
        return "sin-remoto"
    if run(git + ["status", "--porcelain", "--untracked-files=no"], env=env).stdout.strip():
        log("ZapZap P5R tiene cambios locales sin guardar: no se actualiza solo")
        return "cambios-locales"
    old = run(git + ["rev-parse", "HEAD"], env=env).stdout.strip()
    run(git + ["pull", "--ff-only", "--quiet"], timeout=180, env=env)
    new = run(git + ["rev-parse", "HEAD"], env=env).stdout.strip()
    if new == old:
        return "al-dia"
    try:
        if verify is not None:
            verify()
        elif current_dir():
            verify_release(current_dir())
    except Exception as error:  # noqa: BLE001 - nunca dejar el mod roto
        run(git + ["reset", "--hard", old], env=env)
        log(f"La versión nueva de ZapZap P5R no cargó; se volvió a {old[:8]}: {error}")
        return "revertido"
    log(f"ZapZap P5R actualizado: {old[:8]} → {new[:8]}")
    notify("ZapZap P5R se actualizó. Los cambios se verán la próxima vez que lo abras.")
    return "actualizado"


# ---------------------------------------------------------------- comandos
def activate(tag, state, reason):
    release_dir = export_release(tag)
    ensure_venv(release_dependencies(release_dir))
    version = verify_release(release_dir)
    previous = current_release()
    point_current_to(tag)
    state.update({
        "current": tag,
        "previous": previous if previous != tag else state.get("previous"),
        "fresh": previous is not None and previous != tag,
        "activated_at": int(time.time()),
    })
    save_state(state)
    prune_releases(keep_also=state.get("previous"))
    log(f"ZapZap {tag} activo (versión interna {version}; motivo: {reason})")
    return tag


def cmd_install(args):
    state = load_state()
    if args.import_flatpak_settings:
        import_flatpak_settings()
    if current_release() and venv_ok():
        log(f"Ya instalado: ZapZap {current_release()}")
        return 0
    try:
        tags = fetch_tags()
    except (OSError, ValueError) as error:
        log(f"No pude consultar GitHub ({error}); uso las versiones ya descargadas")
        tags = installed_releases()
    tag = latest_tag(tags)
    activate(tag, state, "instalación")
    state["last_check"] = int(time.time())
    state["fresh"] = False
    save_state(state)
    return 0


def cmd_update(args):
    state = load_state()
    if not args.force and time.time() - state.get("last_check", 0) < CHECK_INTERVAL_S:
        return 0
    if args.retry_skipped and state.pop("skip", None):
        save_state(state)
        log("Se reintentarán las versiones que antes fallaron")
    lock_path = os.path.join(P5R_HOME, ".update.lock")
    try:
        lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock_path) < 3600:
            return 0
        os.remove(lock_path)
        lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        try:
            update_mod()
        except (subprocess.SubprocessError, OSError) as error:
            log(f"No se pudo actualizar ZapZap P5R: {error}")
        tags = fetch_tags()
        state["last_check"] = int(time.time())
        save_state(state)
        tag = latest_tag(tags)
        current = current_release()
        skipped = state.get("skip", [])
        if current and version_key(tag) <= version_key(current):
            log(f"ZapZap está al día ({current})")
        elif tag in skipped:
            log(f"ZapZap {tag} se omitió porque falló antes; se sigue con {current}")
        else:
            log(f"Nueva versión de ZapZap: {tag} (actual: {current})")
            try:
                activate(tag, state, "actualización")
            except Exception as error:  # noqa: BLE001 - nunca romper la versión actual
                log(f"La versión {tag} no pasó la prueba; se queda {current}: {error}")
                state.setdefault("skip", []).append(tag)
                save_state(state)
                return 1
            notify(f"ZapZap {tag} listo. Se usará la próxima vez que abras ZapZap P5R.")
        if args.deps:
            ensure_venv(release_dependencies(current_dir()), upgrade=True)
        return 0
    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError) as error:
        log(f"No se pudo buscar actualizaciones: {error}")
        return 1
    finally:
        os.close(lock)
        os.remove(lock_path)


def cmd_rollback(args):
    state = load_state()
    if args.only_if_fresh and not state.get("fresh"):
        return 0
    current = current_release()
    previous = state.get("previous")
    candidates = [r for r in installed_releases() if r != current]
    if previous not in candidates:
        previous = candidates[-1] if candidates else None
    if not previous:
        log("No hay una versión anterior a la que volver")
        return 1
    point_current_to(previous)
    if current:
        state.setdefault("skip", []).append(current)
    state.update({"current": previous, "previous": None, "fresh": False})
    save_state(state)
    log(f"Se volvió a ZapZap {previous} (motivo: {args.reason})")
    notify(f"ZapZap {current} falló al arrancar: se volvió a la versión {previous}.")
    return 0


def cmd_mark_good(_args):
    state = load_state()
    if state.get("fresh"):
        state["fresh"] = False
        save_state(state)
    return 0


def cmd_check(_args):
    release = current_release()
    if not release:
        print("ZapZap no está instalado todavía: ejecuta el instalador (install.sh o install.ps1)")
        return 1
    try:
        version = verify_release(current_dir())
    except subprocess.CalledProcessError as error:
        print(f"El mod NO carga con ZapZap {release}:\n{error.stderr[-1500:]}")
        return 1
    print(f"ZapZap activo: {release} (versión interna {version})")
    print("Mod P5R: carga correctamente con esta versión.")
    return 0


def cmd_status(_args):
    state = load_state()
    print(f"Carpeta:            {P5R_HOME}")
    print(f"Versión activa:     {current_release() or '(ninguna)'}")
    print(f"Versiones locales:  {', '.join(installed_releases()) or '(ninguna)'}")
    print(f"Entorno de Python:  {'OK' if venv_ok() else 'falta o está dañado'}")
    last = state.get("last_check")
    print(f"Última búsqueda:    {time.strftime('%Y-%m-%d %H:%M', time.localtime(last)) if last else 'nunca'}")
    if state.get("skip"):
        print(f"Versiones omitidas: {', '.join(state['skip'])}")
    return 0


def main(argv=None):
    global QUIET
    parser = argparse.ArgumentParser(description="Actualizador de ZapZap P5R")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install.add_argument("--import-flatpak-settings", action="store_true")
    update = sub.add_parser("update")
    update.add_argument("--force", action="store_true")
    update.add_argument("--quiet", action="store_true")
    update.add_argument("--deps", action="store_true", help="también actualiza PyQt6")
    update.add_argument("--retry-skipped", action="store_true",
                        help="reintenta versiones que antes fallaron al arrancar")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--reason", default="manual")
    rollback.add_argument("--only-if-fresh", action="store_true",
                          help="solo si la versión activa es una actualización sin probar")
    sub.add_parser("mark-good")
    sub.add_parser("check")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    QUIET = getattr(args, "quiet", False)
    handlers = {
        "install": cmd_install,
        "update": cmd_update,
        "rollback": cmd_rollback,
        "mark-good": cmd_mark_good,
        "check": cmd_check,
        "status": cmd_status,
    }
    try:
        return handlers[args.command](args)
    except subprocess.CalledProcessError as error:
        log(f"Falló: {' '.join(error.cmd[:4])}… {error.stderr.strip()[-400:] if error.stderr else ''}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
