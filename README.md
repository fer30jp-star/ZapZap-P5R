# ZapZap P5R 🎭

Fork personalizado de [ZapZap](https://github.com/rafatosta/zapzap) con:

1. **Estética Persona 5 Royal** que se enciende y apaga con un botón.
2. **Tarjetas de aviso** («robo de corazón») para grupos: un aviso con fecha
   que va contando los días que faltan.

Capturas de la prueba automática (WhatsApp sin sesión + un chat de ejemplo) en
[`docs/capturas/`](docs/capturas/).

## Guía rápida (para instalarlo)

Se instala una vez y después **todo se actualiza solo**: ZapZap y este fork.

El repositorio es **privado**: pide acceso al dueño (te llega una invitación de
GitHub por correo; acéptala). La primera vez que clones, Git te pedirá iniciar
sesión en GitHub; después las actualizaciones se bajan solas.

### Windows

1. Instala **Python 3.10 o superior** desde [python.org](https://www.python.org/downloads/).
   En el instalador marca **«Add python.exe to PATH»**.
2. Instala **Git** desde [git-scm.com](https://git-scm.com/download/win) (opciones por defecto).
3. Abre una terminal (tecla Windows → «Terminal» o «PowerShell») y clona el
   proyecto en una carpeta que no vayas a mover, por ejemplo tus Documentos:

   ```powershell
   cd $HOME\Documents
   git clone https://github.com/fer30jp-star/ZapZap-P5R.git ZapZap-P5R
   ```
4. Entra a la carpeta `ZapZap-P5R` y haz **doble clic en `install.cmd`**. La
   primera vez descarga unos 200 MB (tarda unos minutos).
5. Abre **ZapZap P5R** desde el menú Inicio y escanea el código QR con tu
   teléfono: WhatsApp → **Dispositivos vinculados** → Vincular un dispositivo.

### Linux

```bash
cd ~/Documentos            # o donde quieras dejarlo (no lo muevas después)
git clone https://github.com/fer30jp-star/ZapZap-P5R.git ZapZap-P5R
cd ZapZap-P5R
./install.sh
```

Abre **ZapZap P5R** desde el menú de aplicaciones y escanea el QR.

### Primer uso

- **Activar la estética:** botón del antifaz en la barra lateral o `Ctrl+Shift+P`.
- **Tarjetas de aviso:** abre un grupo → menú **Persona 5 → Nueva tarjeta para
  el chat abierto** → «Enviar al grupo». Los que tengan ZapZap P5R la ven con su
  cuenta regresiva; se borra sola pasada la fecha.
- **Si algo se ve raro:** `Ctrl+Shift+P` apaga la estética; menú **Persona 5 →
  Volver a ZapZap original** abre ZapZap sin el mod (misma sesión).
- **GIF y videos:** el motor web que se instala no trae los códecs de los MP4
  de WhatsApp, así que el mod los convierte al abrirlos (tardan uno o dos
  segundos más en arrancar; los videos largos, algo más). La primera vez puede
  descargar ffmpeg (~30 MB) en segundo plano. En «ZapZap original» no se
  reproducen.

### Actualizaciones

- **ZapZap** (el proyecto original) se actualiza solo a cada versión estable.
- **ZapZap P5R** (este fork) se actualiza solo desde el repositorio, si lo
  instalaste con `git clone`. Si lo copiaste como carpeta o ZIP, no: vuelve a
  clonarlo con git para tener las mejoras.
- Se revisa al abrir la app (como mucho cada 12 h) y los cambios se ven la
  siguiente vez que la abres. Si una actualización falla, se vuelve sola a la
  versión anterior.
- Para buscar ya: en el menú de aplicaciones (clic derecho en el ícono, Linux)
  «Buscar actualización de ZapZap», o `zapzap-p5r --update` / `zapzap-p5r.cmd --update`.

## Cómo funciona (y por qué se actualiza solo)

ZapZap se instala **desde su código en GitHub**, nativo (sin Flatpak), en su
propio entorno de Python. Funciona en **Linux y Windows**. El mod **no
modifica** ni un archivo de ZapZap: se engancha al arrancar.

```
~/.local/share/zapzap-p5r/        (Windows: %LOCALAPPDATA%\zapzap-p5r\)
  releases/7.5.1/  cada versión estable de ZapZap, descargada de GitHub
  current.txt      la versión activa
  venv/            Python con PyQt6 + PyQt6-WebEngine
  updater.log      historial de instalaciones y actualizaciones
```

- **Actualización automática:** al abrir ZapZap P5R se busca en segundo plano
  (como mucho cada 12 h) una versión estable nueva. Se descarga, se prueba que
  carga con el mod y queda lista para la próxima vez que abras la app.
- **El fork también:** si la carpeta es un clon de git, en esa misma revisión
  se hace `git pull` (solo avanzar, nunca encima de cambios locales sin
  guardar). Si la versión nueva del mod no carga, se vuelve a la anterior.
- **Sin sustos:** si una versión nueva se cae al arrancar, el lanzador vuelve
  solo a la anterior y te avisa. La que falló no se reintenta hasta que pidas
  una actualización a mano.
- **Original con un botón:** «Volver a ZapZap original» reinicia el mismo
  ZapZap tal cual viene de GitHub, sin el mod, con tu misma sesión.
- **Respaldo extra:** el ZapZap de Flathub sigue instalado e intacto (con su
  propia sesión).
- Si algo del mod falla, se registra el error y ZapZap sigue funcionando normal.

## Instalación (detalle)

### Windows

1. Instala Python 3.10 o superior desde [python.org](https://www.python.org/downloads/)
   (marca «Add python.exe to PATH»).
2. Clona o copia la carpeta `ZapZap-P5R` a un lugar fijo de tu equipo (los
   accesos directos apuntan a ella) y, dentro de ella, ejecuta en
   PowerShell:

   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

   (o doble clic en `install.cmd`, que hace lo mismo)

   Con `-Autostart` además se abre al iniciar sesión.
3. Abre **ZapZap P5R** desde el menú Inicio (también hay accesos «original sin
   mod» y «modo seguro»). Desde una terminal: `zapzap-p5r.cmd --help`.

Desinstalar: doble clic en `uninstall.cmd` o `powershell -ExecutionPolicy Bypass -File uninstall.ps1`
(`-Purge` borra también lo descargado y las tarjetas).

### Linux

Requisitos: Python 3.10 o superior (viene en casi todas las distribuciones).
Desde la carpeta del proyecto (donde lo clonaste o copiaste):

```bash
cd ZapZap-P5R
./install.sh
```

La carpeta debe quedarse en su lugar: el acceso directo apunta a ella.

Aparece **ZapZap P5R** en el menú de aplicaciones (y el comando
`zapzap-p5r` en la terminal). Al abrirlo por primera vez, escanea el QR desde
tu teléfono: WhatsApp → *Dispositivos vinculados*. El fork es un dispositivo
vinculado aparte del ZapZap de Flathub; tus ajustes (tema oscuro, rendimiento,
notificaciones…) se copiaron del Flatpak.

En Linux, al abrir el fork se cierra el ZapZap de Flathub si está abierto,
para que no lleguen notificaciones dobles.

Para que se abra al iniciar sesión (oculto en la bandeja):

```bash
./install.sh --autostart
```

y desactiva «Iniciar con el sistema» en el ZapZap de Flathub. Dentro del fork,
la opción «Iniciar con el sistema» de ZapZap también funciona y apunta al fork.

## Uso

| Qué | Dónde |
| --- | --- |
| Encender/apagar la estética Persona 5 | Botón del antifaz en la barra lateral, menú **Persona 5**, o `Ctrl+Shift+P` |
| Ver todas las tarjetas de aviso | Menú **Persona 5 → Tarjetas de aviso…**, clic derecho en el antifaz, o `Ctrl+Shift+K` |
| Crear una tarjeta para un grupo | Abre el grupo: en modo Persona, botón de la tarjeta en el encabezado; en cualquier modo, menú **Persona 5 → Nueva tarjeta para el chat abierto** |
| Volver al ZapZap original | Menú **Persona 5 → Volver a ZapZap original (sin mod)…** |
| Regresar al fork | Abre **ZapZap P5R** desde el menú de aplicaciones |

Clic derecho sobre el ícono de ZapZap P5R en el menú: abrir el original, modo
seguro, buscar actualización o abrir el ZapZap de Flathub.

### Tarjetas de aviso (compartidas)

- Cada tarjeta tiene objetivo (título), fecha, mensaje opcional y recordatorios.
- **Enviar al grupo** la manda como un mensaje normal de WhatsApp. Quien tenga
  ZapZap P5R la recibe y se le guarda sola, con su cuenta regresiva. Quien no
  lo tenga ve un texto legible con la fecha, más una última línea
  `p5r://aviso/…` que es la que reconoce el fork.
- **Pasada la fecha se borra sola** en cada copia del fork.
- **Editar** y «Guardar y reenviar» actualiza la tarjeta para todos.
  **Eliminar → Cancelar para todos** manda un aviso de cancelación que la borra
  en todas las copias. «Solo para mí» la quita únicamente de tu computadora.
- También puedes guardarla «Solo para mí», sin enviar nada.
- Las tarjetas que llegan se detectan en vivo (aunque el chat no esté abierto)
  y, como respaldo, al abrir el chat donde está el mensaje.
- El mod solo envía mensajes cuando tú pulsas «Enviar al grupo», «Reenviar» o
  «Cancelar para todos». Si tienes un borrador escrito, no lo pisa.
- Recordatorios a 7, 3 y 1 día y el mismo día (notificación de ZapZap).
- **Enviar imagen** genera una tarjeta ilustrada y abre la vista previa de
  WhatsApp para adjuntarla; **Descargar imagen** guarda el PNG.

### Modo normal y modo Persona

- **Panel de tarjetas** (`Ctrl+Shift+K`): se cierra con su ✕, con Esc o al
  hacer clic fuera de él.
- **Modo normal:** ZapZap se ve como siempre. Lo único agregado es una
  etiqueta discreta con los días pendientes en la foto del chat (clic: abre la
  tarjeta). Crear tarjetas: menú **Persona 5 → Nueva tarjeta para el chat
  abierto** o `Ctrl+Shift+K` → «Nueva».
- **Al entrar al modo Persona** la pantalla se tiñe de rojo con ondas como de
  agua y, por debajo, WhatsApp se recarga para que todo se dibuje de cero con
  la estética; al terminar de cargar, el rojo se retira. Al salir del modo
  Persona no hay recarga.
- **Modo Persona:** lista de chats como el teléfono del juego (barras negras
  inclinadas sobre rojo, foto en marco ladeado, nombre en etiqueta); mensajes
  como cuadros de texto del juego (caja negra inclinada con un rectángulo blanco
  detrás y, en grupos, el nombre en etiqueta blanca sobre la caja; los de otros
  a la izquierda, los tuyos a la derecha);
  imágenes y stickers en marco negro ladeado; fotos a color (en gris al pasar
  el ratón), las de los grupos más grandes y con un marco de color por persona;
  aviso fijo arriba del chat (con «–» se minimiza a una etiqueta en la esquina)
  y botón de tarjeta en el encabezado.

## Comandos

En Windows, desde una terminal, usa `zapzap-p5r.cmd` con las mismas opciones.

```
zapzap-p5r              abre el fork
zapzap-p5r --original   el mismo ZapZap sin el mod (misma sesión)
zapzap-p5r --flatpak    el ZapZap de Flathub (solo Linux, sesión aparte)
zapzap-p5r --safe       el fork con el mod desactivado (diagnóstico)
zapzap-p5r --debug      registro detallado del mod en la terminal
zapzap-p5r --update     busca ya una versión nueva (con la app cerrada, también actualiza PyQt6)
zapzap-p5r --rollback   vuelve a la versión anterior de ZapZap
zapzap-p5r --status     versión activa y última búsqueda
zapzap-p5r --check      comprueba que el mod carga con la versión activa
```

## Si algo sale mal

| Situación | Qué hacer |
| --- | --- |
| La app se ve rara o algo no funciona | `Ctrl+Shift+P` apaga la estética al instante |
| Sospechas del mod | Clic derecho en el ícono → «Modo seguro», o `zapzap-p5r --safe` |
| Falla tras una actualización de ZapZap | Se revierte sola; a mano: `zapzap-p5r --rollback` |
| Quieres el ZapZap de siempre | Menú Persona 5 → «Volver a ZapZap original», o `zapzap-p5r --flatpak` |
| Ver qué pasó | `~/.local/share/zapzap-p5r/updater.log`, o `zapzap-p5r --debug` en una terminal |
| La estética no se aplica a algo (WhatsApp cambió su diseño) | Menú **Persona 5 → Exportar diagnóstico de estilo**: guarda la estructura de la página, sin textos, nombres ni fotos, en `~/.config/ZapZap-P5R/diagnostico-estilo.json` |
| Inspeccionar la página | En ZapZap, Ver → DevTools y ejecuta `__zapzapP5R.debug()` |

Desinstalar (el ZapZap de Flathub no se toca):

```bash
./uninstall.sh            # quita accesos; conserva ZapZap descargado y tus tarjetas
./uninstall.sh --purge    # también borra el ZapZap descargado, su Python y las tarjetas
```

La sesión de WhatsApp del fork y sus ajustes quedan en `~/.config/ZapZap` y
`~/.local/share/ZapZap`; bórralos a mano si ya no los quieres.

## Estructura del proyecto

```
zapzap-p5r            Lanzador para Linux (llama a p5r_mod/launcher.py)
zapzap-p5r.cmd        Lanzador para Windows (desde terminal)
install.cmd / uninstall.cmd   Doble clic para instalar / quitar en Windows
zapzap_p5r.py         Punto de entrada del fork
install.sh / .ps1     Instalan ZapZap desde GitHub y crean los accesos (Linux / Windows)
uninstall.sh / .ps1   Quitan los accesos (y con --purge / -Purge, lo descargado)
p5r_mod/
  launcher.py         Lógica del lanzador: fork u original, actualización y vuelta atrás
  updater.py          Instala, actualiza y revierte versiones de ZapZap
  bootstrap.py        Engancha el mod al arranque de ZapZap sin tocar sus archivos
  controller.py       Menú, botón lateral, atajos, inyección y sincronización
  state.py            Estado guardado (tema y tarjetas) en ~/.config/ZapZap-P5R/
  web/p5r.css         Estética Persona 5
  web/p5r.js          Runtime en WhatsApp Web: tema, tarjetas, banner, panel, imagen
  assets/             Ícono del botón
desktop/              Acceso directo e íconos de la app (.svg para Linux, .ico para Windows)
tests/                Pruebas unitarias y prueba de humo sin pantalla
```

## Notas

- La estética está pensada sobre el modo oscuro de ZapZap.
- Las fuentes de la estética (Anton, Bebas Neue, Special Elite, etc., licencia
  OFL) se cargan desde Google Fonts; sin internet se usan fuentes del sistema.
- Proyecto de fans, sin relación con ATLUS/SEGA ni con ZapZap. No incluye
  recursos del juego: colores, formas e íconos son propios.

## Desarrollo

```bash
PY=~/.local/share/zapzap-p5r/venv/bin/python
$PY -m unittest discover -s tests                 # estado y actualizador
$PY tests/smoke_offscreen.py --out /tmp/p5r-smoke  # ZapZap real sin pantalla + capturas
```

La prueba de humo usa carpetas temporales y no toca tu configuración real.
