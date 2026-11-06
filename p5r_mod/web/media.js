// ZapZap P5R — reproducción de GIF y videos de WhatsApp.
//
// El Qt WebEngine que se instala con pip viene sin códecs propietarios: no
// decodifica H.264 ni AAC, y WhatsApp manda los GIF y los videos como MP4 con
// esos códecs. Aquí se intercepta el MP4 (un blob: en memoria) antes de que
// llegue al <video>, Python lo convierte a WebM (VP8/Opus) con ffmpeg y el
// <video> recibe la versión convertida. Para WhatsApp el video solo tarda un
// poco más en cargar.
//
// Se inyecta al crear el documento, antes que el código de WhatsApp.
(() => {
  "use strict";
  if (window.__p5rMedia) return;

  const MAX_BYTES = 200 * 1024 * 1024;
  const CHUNK_BYTES = 3 * 1024 * 1024; // múltiplo de 3: cada trozo es base64 válido por sí solo
  const MAX_TRACKED = 60;
  const MAX_JOBS = 40;
  const session = Math.random().toString(36).slice(2) + Date.now().toString(36);
  const probe = document.createElement("video");

  const native = {
    createObjectURL: URL.createObjectURL,
    revokeObjectURL: URL.revokeObjectURL,
    mediaSrc: Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src"),
    sourceSrc: Object.getOwnPropertyDescriptor(HTMLSourceElement.prototype, "src"),
    setAttribute: Element.prototype.setAttribute,
    play: HTMLMediaElement.prototype.play,
    load: HTMLMediaElement.prototype.load,
  };

  const tracked = new Map(); // blob: original -> Blob (solo los que podrían ser video/audio)
  const jobs = new Map(); // blob: original -> trabajo de conversión
  const byId = new Map();
  const waiting = new WeakMap(); // elemento -> asignación diferida
  const outbox = [];
  let nextId = 1;
  const stats = { converted: 0, failed: 0 };

  const debug = (...args) => {
    if (window.__zapzapP5RDebug) console.debug("[P5R media]", ...args);
  };

  function baseType(type) {
    return String(type || "").split(";")[0].trim().toLowerCase();
  }

  // Contenedores MP4: canPlayType("video/mp4") dice «maybe» aunque falte el
  // códec, así que se pregunta por el códec que WhatsApp usa en cada uno.
  const MP4_CODEC = {
    "video/mp4": 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"',
    "video/quicktime": 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"',
    "video/3gpp": 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"',
    "audio/mp4": 'audio/mp4; codecs="mp4a.40.2"',
    "audio/x-m4a": 'audio/mp4; codecs="mp4a.40.2"',
    "audio/m4a": 'audio/mp4; codecs="mp4a.40.2"',
    "audio/aac": 'audio/mp4; codecs="mp4a.40.2"',
  };

  function playable(type) {
    const base = baseType(type);
    const full = /codecs=/i.test(type) ? type : MP4_CODEC[base] || type;
    return probe.canPlayType(full) !== "";
  }

  function worthTracking(blob) {
    const type = baseType(blob.type);
    if (!type || type === "application/octet-stream") return true; // se revisa al usarse
    return /^(video|audio)\//.test(type) && !playable(blob.type);
  }

  async function isUnplayable(blob) {
    const type = baseType(blob.type);
    if (/^(video|audio)\//.test(type)) return !playable(blob.type);
    // Sin tipo: MP4/MOV empiezan con una caja «ftyp» en el byte 4.
    const head = new Uint8Array(await blob.slice(0, 12).arrayBuffer());
    return String.fromCharCode(...head.subarray(4, 8)) === "ftyp" && !playable("video/mp4");
  }

  // --------------------------------------------------------- trabajos
  function jobFor(url, blob) {
    let job = jobs.get(url);
    if (job) return job;
    job = { id: nextId++, url, blob, state: "pending", out: null, parts: [] };
    job.promise = (async () => {
      try {
        if (!blob.size || blob.size > MAX_BYTES || !(await isUnplayable(blob))) return null;
      } catch (_error) {
        return null;
      }
      debug("convirtiendo", url, blob.type, blob.size);
      outbox.push(job);
      pump();
      return new Promise((resolve) => (job.resolve = resolve));
    })().then((out) => {
      job.state = out ? "done" : "skip";
      job.out = out;
      job.blob = null;
      job.parts = [];
      return out;
    });
    jobs.set(url, job);
    byId.set(job.id, job);
    trimJobs();
    return job;
  }

  function trimJobs() {
    if (jobs.size <= MAX_JOBS) return;
    const inUse = new Set();
    for (const el of document.querySelectorAll("video, audio")) inUse.add(el.currentSrc);
    for (const [url, job] of jobs) {
      if (jobs.size <= MAX_JOBS) break;
      if (job.state === "pending" || inUse.has(job.out)) continue;
      jobs.delete(url);
      byId.delete(job.id);
      if (job.out) native.revokeObjectURL.call(URL, job.out);
    }
  }

  // Envío a Python: poll() tiene que ser síncrono (runJavaScript no espera
  // promesas), así que el siguiente trozo se deja preparado de antemano.
  let ready = null;
  let preparing = false;
  let sending = null;

  function pump() {
    if (ready || preparing) return;
    if (!sending) {
      const job = outbox.shift();
      if (!job) return;
      sending = { job, offset: 0, index: 0, total: Math.max(1, Math.ceil(job.blob.size / CHUNK_BYTES)) };
    }
    preparing = true;
    const current = sending;
    const reader = new FileReader();
    reader.onload = () => {
      const data = String(reader.result);
      ready = JSON.stringify({
        session,
        id: current.job.id,
        index: current.index,
        total: current.total,
        data: data.slice(data.indexOf(",") + 1),
      });
      current.offset += CHUNK_BYTES;
      current.index += 1;
      if (current.index >= current.total) sending = null;
      preparing = false;
    };
    reader.onerror = () => {
      sending = null;
      preparing = false;
      finish(current.job, null);
      pump();
    };
    reader.readAsDataURL(current.job.blob.slice(current.offset, current.offset + CHUNK_BYTES));
  }

  function finish(job, out) {
    if (!job || job.state !== "pending" || !job.resolve) return;
    if (out) stats.converted += 1;
    else stats.failed += 1;
    job.resolve(out);
  }

  // ---------------------------------------------------- asignaciones
  // apply(valor) pone el src de verdad; media es el <video>/<audio> afectado.
  function assign(target, media, value, apply) {
    const url = String(value);
    if (media) waiting.delete(media);
    const known = jobs.get(url);
    if (known && known.state === "done") return apply(known.out);
    if (known && known.state === "skip") return apply(url);
    const blob = known ? null : tracked.get(url);
    if (!known && !blob) return apply(url);

    const job = known || jobFor(url, blob);
    const wait = { url, plays: [] };
    if (media) waiting.set(media, wait);
    job.promise.then((out) => {
      if (media && waiting.get(media) !== wait) return; // llegó otro src mientras tanto
      if (media) waiting.delete(media);
      apply(out || url);
      if (target !== media && media) native.load.call(media);
      if (wait.plays.length) replay(media, wait.plays);
    });
  }

  // Un play() pedido justo al cambiar el src lo aborta la propia carga: se
  // espera a que el video convertido tenga sus datos básicos.
  function replay(media, plays) {
    const run = () => {
      media.removeEventListener("loadedmetadata", run);
      media.removeEventListener("error", fail);
      for (const [resolve, reject] of plays) native.play.call(media).then(resolve, reject);
    };
    const fail = () => {
      media.removeEventListener("loadedmetadata", run);
      media.removeEventListener("error", fail);
      const error = new DOMException("No se pudo cargar el video", "NotSupportedError");
      for (const [, reject] of plays) reject(error);
    };
    if (media.readyState >= 1) return run();
    media.addEventListener("loadedmetadata", run);
    media.addEventListener("error", fail);
  }

  function mediaOf(source) {
    const parent = source.parentElement;
    return parent instanceof HTMLMediaElement ? parent : null;
  }

  URL.createObjectURL = function (obj) {
    const url = native.createObjectURL.apply(this, arguments);
    try {
      if (obj instanceof Blob && worthTracking(obj)) {
        tracked.set(url, obj);
        if (tracked.size > MAX_TRACKED) tracked.delete(tracked.keys().next().value);
      }
    } catch (_error) {
      // nunca romper a WhatsApp
    }
    return url;
  };

  URL.revokeObjectURL = function (url) {
    tracked.delete(String(url));
    return native.revokeObjectURL.apply(this, arguments);
  };

  Object.defineProperty(HTMLMediaElement.prototype, "src", {
    configurable: true,
    enumerable: native.mediaSrc.enumerable,
    get() {
      const wait = waiting.get(this);
      return wait ? wait.url : native.mediaSrc.get.call(this);
    },
    set(value) {
      assign(this, this, value, (v) => native.mediaSrc.set.call(this, v));
    },
  });

  Object.defineProperty(HTMLSourceElement.prototype, "src", {
    configurable: true,
    enumerable: native.sourceSrc.enumerable,
    get() {
      return native.sourceSrc.get.call(this);
    },
    set(value) {
      assign(this, mediaOf(this), value, (v) => native.sourceSrc.set.call(this, v));
    },
  });

  Element.prototype.setAttribute = function (name, value) {
    if (
      (this instanceof HTMLMediaElement || this instanceof HTMLSourceElement) &&
      String(name).toLowerCase() === "src"
    ) {
      const media = this instanceof HTMLMediaElement ? this : mediaOf(this);
      return assign(this, media, value, (v) => native.setAttribute.call(this, "src", v));
    }
    return native.setAttribute.apply(this, arguments);
  };

  // play() mientras se convierte: se cumple cuando el video ya convertido arranca.
  // El play() nativo de ahora no reproduce nada (aún no hay src), pero gasta el
  // clic del usuario para que Chromium deje reproducir con sonido después.
  HTMLMediaElement.prototype.play = function () {
    const wait = waiting.get(this);
    if (wait) {
      native.play.call(this).catch(() => {});
      return new Promise((resolve, reject) => wait.plays.push([resolve, reject]));
    }
    return native.play.apply(this, arguments);
  };

  // Red de seguridad: un blob que se escapó de los ganchos y no se pudo abrir.
  window.addEventListener(
    "error",
    (event) => {
      const media = event.target;
      if (!(media instanceof HTMLMediaElement) || !media.error || media.error.code !== 4) return;
      const url = native.mediaSrc.get.call(media);
      const job = jobs.get(url);
      if (job ? job.state !== "pending" : !tracked.has(url)) return;
      assign(media, media, url, (v) => native.mediaSrc.set.call(media, v));
    },
    true,
  );

  // ---------------------------------------------- puente con Python
  window.__p5rMedia = {
    poll() {
      const out = ready;
      ready = null;
      pump();
      return out;
    },
    put(sessionId, id, index, total, mime, data) {
      const job = byId.get(id);
      if (sessionId !== session || !job || job.state !== "pending") return false;
      const binary = atob(data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      job.parts[index] = bytes;
      if (job.parts.filter(Boolean).length === total) {
        debug("convertido", job.url);
        finish(job, native.createObjectURL.call(URL, new Blob(job.parts, { type: mime })));
      }
      return true;
    },
    fail(sessionId, id, reason) {
      if (sessionId !== session) return false;
      debug("no se pudo convertir", id, reason);
      finish(byId.get(id), null);
      return true;
    },
    status() {
      return JSON.stringify({ ...stats, pending: [...jobs.values()].filter((j) => j.state === "pending").length });
    },
  };
})();
