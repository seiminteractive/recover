#!/usr/bin/env python3
"""
RecoverSD v1 - Motor de recuperacion de fotos y videos para microSD.

Lee una fuente byte a byte (una imagen .img o un dispositivo de bloques) y
reconstruye archivos siguiendo su estructura interna (file carving):

    - JPG  : SOI (FF D8 FF) -> se recorre por marcadores hasta EOI (FF D9)
    - PNG  : firma de 8 bytes -> se recorren chunks hasta IEND
    - HEIC : caja ISO-BMFF 'ftyp' con brand heic/heix/mif1
    - MP4  : caja ISO-BMFF 'ftyp' con brand isom/mp42/...
    - MOV  : caja ISO-BMFF 'ftyp' con brand 'qt  '

Sin dependencias externas: solo la libreria estandar. Funciona en Windows,
macOS y Linux. La logica de recuperacion es identica en todos; lo unico que
cambia por sistema es como se lista/abre el disco crudo (clase Reader).

Uso por linea de comandos:
    python3 recover.py FUENTE DESTINO [--types jpg,png,heic,mp4,mov]
    python3 recover.py --list        # lista los discos conectados

La interfaz grafica (gui.py) usa este mismo modulo.
"""

import argparse
import json
import os
import subprocess
import sys
import time

SECTOR = 512
BLOCK = 4 * 1024 * 1024        # bloque de lectura/copia
CHUNK = 8 * 1024 * 1024        # ventana de escaneo de firmas
OVERLAP = 32                   # solape entre ventanas (firmas en el borde)

# Marcadores validos justo despues de FF D8 FF en un JPEG real. Reduce
# muchisimo los falsos positivos frente a buscar solo "FF D8 FF".
JPEG_NEXT = {0xE0, 0xE1, 0xE2, 0xE3, 0xEC, 0xED, 0xEE, 0xDB, 0xC0, 0xC4, 0xFE}
PNG_SIG = b"\x89PNG\r\n\x1a\n"

MIN_SIZE = {"jpg": 2 * 1024, "png": 1024, "heic": 8 * 1024, "mp4": 64 * 1024, "mov": 64 * 1024}
CAP = {"jpg": 128 << 20, "png": 128 << 20, "heic": 128 << 20, "mp4": 8 << 30, "mov": 8 << 30}
ALL_TYPES = ("jpg", "png", "heic", "mp4", "mov")


# --------------------------------------------------------------------------- #
#  Utilidades
# --------------------------------------------------------------------------- #
def human(n):
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def printable(b):
    return all(0x20 <= c <= 0x7E for c in b)


def is_raw_device(path):
    """True si la ruta es un dispositivo crudo que exige lecturas alineadas."""
    if sys.platform == "win32":
        return path.upper().startswith("\\\\.\\PHYSICALDRIVE")
    return path.startswith("/dev/")


# --------------------------------------------------------------------------- #
#  Reader: abstrae imagen vs. dispositivo (con alineacion a sector si hace falta)
# --------------------------------------------------------------------------- #
class Reader:
    def __init__(self, path):
        self.path = path
        self.raw = is_raw_device(path)
        self.f = open(path, "rb", buffering=0)
        self.size = self._detect_size()

    def _detect_size(self):
        try:
            s = os.path.getsize(self.path)
            if s > 0:
                return s
        except OSError:
            pass
        try:
            cur = self.f.tell()
            self.f.seek(0, os.SEEK_END)
            s = self.f.tell()
            self.f.seek(cur)
            if s > 0:
                return s
        except OSError:
            pass
        return _device_size(self.path)   # ultimo recurso, por SO

    def read_at(self, off, length):
        """Lee 'length' bytes desde 'off'. Alinea a sector en dispositivos crudos."""
        if not self.raw:
            self.f.seek(off)
            return self.f.read(length)
        start = off & ~(SECTOR - 1)
        end = (off + length + SECTOR - 1) & ~(SECTOR - 1)
        if self.size:
            end = min(end, ((self.size + SECTOR - 1) & ~(SECTOR - 1)))
        self.f.seek(start)
        try:
            data = self.f.read(end - start)
        except OSError:
            return b""
        return data[off - start: off - start + length]

    def close(self):
        try:
            self.f.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
#  Listado de discos (para la interfaz y para --list). Best-effort por SO.
# --------------------------------------------------------------------------- #
def list_disks():
    """Devuelve [{'path','name','size','internal'}]. [] si no se pudo."""
    try:
        if sys.platform == "darwin":
            return _mac_disks()
        if sys.platform == "win32":
            return _win_disks()
        if sys.platform.startswith("linux"):
            return _linux_disks()
    except Exception:
        return []
    return []


def _mac_disks():
    import plistlib
    out = subprocess.run(["diskutil", "list", "-plist", "physical"],
                         capture_output=True).stdout
    names = plistlib.loads(out).get("WholeDisks", [])
    disks = []
    for name in names:
        info = subprocess.run(["diskutil", "info", "-plist", name],
                              capture_output=True).stdout
        di = plistlib.loads(info)
        disks.append({
            "path": "/dev/" + name,
            "name": di.get("MediaName") or di.get("IORegistryEntryName") or name,
            "size": di.get("TotalSize") or di.get("Size"),
            "internal": bool(di.get("Internal", True)),
        })
    return disks


def _win_disks():
    ps = "Get-Disk | Select-Object Number,FriendlyName,Size,BusType | ConvertTo-Json"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True).stdout
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    disks = []
    for d in data:
        bus = str(d.get("BusType", ""))
        disks.append({
            "path": f"\\\\.\\PhysicalDrive{d['Number']}",
            "name": d.get("FriendlyName") or "Disco",
            "size": d.get("Size"),
            "internal": bus not in ("USB", "SD", "MMC"),
        })
    return disks


def _linux_disks():
    out = subprocess.run(
        ["lsblk", "-dbno", "NAME,SIZE,MODEL,RM", "--json"],
        capture_output=True, text=True).stdout
    disks = []
    for d in json.loads(out).get("blockdevices", []):
        disks.append({
            "path": "/dev/" + d["name"],
            "name": (d.get("model") or "Disco").strip(),
            "size": int(d["size"]) if d.get("size") else None,
            "internal": str(d.get("rm", "0")) == "0",
        })
    return disks


def _device_size(path):
    for d in list_disks():
        if d["path"] == path:
            return d["size"]
    return None


# --------------------------------------------------------------------------- #
#  Carvers: reciben (reader, start) y devuelven el offset final EXCLUSIVO, o None.
# --------------------------------------------------------------------------- #
def _cap_end(reader, start, cap):
    if reader.size:
        return start + min(cap, reader.size - start)
    return start + cap


def _parse_jpeg(data):
    """Recorre marcadores JPEG. Devuelve (estado, fin): estado ok/more/bad."""
    n = len(data)
    i = 2  # ya sabemos que empieza con FF D8
    while i < n - 1:
        if data[i] != 0xFF:
            return ("bad", None)
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            return ("more", None)
        m = data[i]
        i += 1
        if m == 0xD9:                       # EOI -> fin del archivo
            return ("ok", i)
        if m == 0x01 or 0xD0 <= m <= 0xD7:   # marcadores sin longitud
            continue
        if i + 2 > n:
            return ("more", None)
        seglen = (data[i] << 8) | data[i + 1]
        if m == 0xDA:                       # SOS: sigue el flujo comprimido
            i += seglen
            while i < n - 1:
                if data[i] == 0xFF:
                    nx = data[i + 1]
                    if nx == 0x00 or 0xD0 <= nx <= 0xD7:
                        i += 2                # byte "stuffed" o marcador RST
                        continue
                    break                     # proximo marcador real
                i += 1
            continue
        i += seglen                         # segmento con longitud: se salta
    return ("more", None)


def carve_jpeg(reader, start):
    cap = _cap_end(reader, start, CAP["jpg"]) - start
    window = 2 * 1024 * 1024
    while True:
        data = reader.read_at(start, min(window, cap))
        status, end = _parse_jpeg(data)
        if status == "ok":
            return start + end
        if status == "bad":
            return None
        if len(data) < window or window >= cap:   # EOF o tope sin EOI
            return None
        window = min(window * 8, cap)


def carve_png(reader, start):
    i = start + 8
    limit = _cap_end(reader, start, CAP["png"])
    while i + 8 <= limit:
        hdr = reader.read_at(i, 8)
        if len(hdr) < 8:
            return None
        clen = int.from_bytes(hdr[0:4], "big")
        ctype = hdr[4:8]
        if not printable(ctype):
            return None
        i += 8 + clen + 4                    # datos + CRC
        if ctype == b"IEND":
            return i
    return None


def carve_bmff(reader, start):
    """ISO base media (MP4/MOV/HEIC). Camina cajas de nivel superior."""
    i = start
    limit = _cap_end(reader, start, CAP["mp4"])
    while i + 8 <= limit:
        hdr = reader.read_at(i, 16)
        if len(hdr) < 8:
            return i if i > start else None   # EOF: tomamos lo acumulado
        box = int.from_bytes(hdr[0:4], "big")
        btype = hdr[4:8]
        if not printable(btype):
            return i if i > start else None
        if box == 1:                         # largesize de 64 bits
            if len(hdr) < 16:
                return i if i > start else None
            box = int.from_bytes(hdr[8:16], "big")
        elif box == 0:                       # se extiende hasta EOF
            return limit
        if box < 8:
            return i if i > start else None
        i += box
    return min(i, limit)


def bmff_ext(reader, start, enabled):
    """Determina heic/mp4/mov segun el 'major brand' del ftyp."""
    brand = reader.read_at(start + 8, 4)
    if brand == b"qt  ":
        ext = "mov"
    elif brand[:3] in (b"hei", b"mif", b"msf", b"hev", b"avi"):
        ext = "heic"
    else:
        ext = "mp4"
    return ext if ext in enabled else None


# --------------------------------------------------------------------------- #
#  Busqueda de firmas dentro de una ventana
# --------------------------------------------------------------------------- #
def find_first(buf, enabled):
    """Devuelve (indice_local, tipo) de la primera firma, o None."""
    best = None
    if "jpg" in enabled:
        i = buf.find(b"\xff\xd8\xff")
        while i != -1:
            if i + 3 < len(buf) and buf[i + 3] in JPEG_NEXT:
                best = (i, "jpg")
                break
            i = buf.find(b"\xff\xd8\xff", i + 1)
    if "png" in enabled:
        i = buf.find(PNG_SIG)
        if i != -1 and (best is None or i < best[0]):
            best = (i, "png")
    if enabled & {"heic", "mp4", "mov"}:
        i = buf.find(b"ftyp")
        if i >= 4 and (best is None or (i - 4) < best[0]):
            best = (i - 4, "bmff")
    return best


def copy_range(reader, start, end, path):
    remaining = end - start
    pos = start
    with open(path, "wb") as out:
        while remaining > 0:
            b = reader.read_at(pos, min(BLOCK, remaining))
            if not b:
                break
            out.write(b)
            pos += len(b)
            remaining -= len(b)


# --------------------------------------------------------------------------- #
#  Bucle principal de escaneo (dirigible por callbacks para la interfaz)
# --------------------------------------------------------------------------- #
def scan(reader, enabled, outdir, on_progress=None, should_stop=None):
    """
    Escanea 'reader' y escribe los archivos recuperados en 'outdir'.

    on_progress(cursor, total, counters) -> se llama periodicamente.
    should_stop() -> si devuelve True, corta de forma ordenada.
    Devuelve el dict de contadores por tipo.
    """
    counters = {ext: 0 for ext in ALL_TYPES}
    for ext in ALL_TYPES:
        os.makedirs(os.path.join(outdir, ext), exist_ok=True)

    csv = open(os.path.join(outdir, "recuperados.csv"), "w")
    csv.write("tipo,archivo,offset,tamano_bytes\n")

    cursor = 0
    total = reader.size or 0
    last = [0.0]
    t0 = time.time()

    def tick():
        if on_progress is None:
            last[0] = _cli_progress(cursor, total, counters, t0, last[0])
        elif time.time() - last[0] >= 0.2:
            on_progress(cursor, total, dict(counters))
            last[0] = time.time()

    while True:
        if should_stop and should_stop():
            break
        buf = reader.read_at(cursor, CHUNK + OVERLAP)
        if len(buf) <= OVERLAP:
            break

        hit = find_first(buf, enabled)
        if hit is None:
            cursor += len(buf) - OVERLAP
            tick()
            continue

        local, kind = hit
        off = cursor + local
        if kind == "jpg":
            end, ext = carve_jpeg(reader, off), "jpg"
        elif kind == "png":
            end, ext = carve_png(reader, off), "png"
        else:
            ext = bmff_ext(reader, off, enabled)
            end = carve_bmff(reader, off) if ext else off + 8

        if ext and end and (end - off) >= MIN_SIZE.get(ext, 0):
            counters[ext] += 1
            name = f"recuperada_{counters[ext]:05d}.{ext}"
            copy_range(reader, off, end, os.path.join(outdir, ext, name))
            csv.write(f"{ext},{name},{off},{end - off}\n")
            cursor = end
        else:
            cursor = off + 2

        tick()

    csv.close()
    if on_progress is None:
        print()
    else:
        on_progress(cursor, total, dict(counters))
    return counters


def _cli_progress(cursor, total, counters, t0, last):
    now = time.time()
    if now - last < 0.5:
        return last
    elapsed = max(now - t0, 0.001)
    speed = cursor / elapsed
    fotos = counters["jpg"] + counters["png"] + counters["heic"]
    videos = counters["mp4"] + counters["mov"]
    if total:
        pct = 100 * cursor / total
        eta = (total - cursor) / speed if speed else 0
        bar = "#" * int(pct / 5) + "-" * (20 - int(pct / 5))
        line = (f"\r{bar} {pct:5.1f}%  fotos:{fotos:>4} videos:{videos:>3}  "
                f"{human(speed)}/s  ETA {int(eta // 60):02d}:{int(eta % 60):02d}")
    else:
        line = (f"\rEscaneado {human(cursor):>10}  fotos:{fotos:>4} "
                f"videos:{videos:>3}  {human(speed)}/s")
    sys.stdout.write(line[:110])
    sys.stdout.flush()
    return now


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def print_disks():
    disks = list_disks()
    if not disks:
        print("No se pudieron listar discos automaticamente en este sistema.")
        return
    print("Discos detectados:\n")
    for d in disks:
        tag = "sistema" if d["internal"] else "extraible"
        print(f"  {d['path']:<22} {human(d['size']):>10}  {tag:<9} {d['name']}")


def main():
    ap = argparse.ArgumentParser(description="Recuperador de fotos/videos por file carving.")
    ap.add_argument("source", nargs="?", help="imagen .img o dispositivo")
    ap.add_argument("outdir", nargs="?", help="carpeta destino (NO en la misma tarjeta)")
    ap.add_argument("--types", default=",".join(ALL_TYPES),
                    help="tipos a recuperar, separados por coma")
    ap.add_argument("--list", action="store_true", help="lista discos conectados y sale")
    args = ap.parse_args()

    if args.list:
        print_disks()
        return
    if not args.source or not args.outdir:
        ap.error("faltan FUENTE y/o DESTINO (usa --list para ver los discos)")

    enabled = {t.strip().lower() for t in args.types.split(",") if t.strip()}
    unknown = enabled - set(ALL_TYPES)
    if unknown:
        ap.error(f"tipos desconocidos: {', '.join(unknown)}")
    if os.path.abspath(args.outdir).startswith(os.path.abspath(args.source) + os.sep):
        ap.error("el destino no puede estar dentro de la fuente")

    try:
        reader = Reader(args.source)
    except OSError as e:
        print(f"No se pudo abrir la fuente: {e}", file=sys.stderr)
        if is_raw_device(args.source):
            print("Sugerencia: los discos crudos requieren admin/root "
                  "(sudo en Mac, 'Ejecutar como administrador' en Windows).",
                  file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.outdir, exist_ok=True)
    print("=" * 44)
    print(" RecoverSD v1")
    print("=" * 44)
    print(f"Fuente : {args.source}  ({human(reader.size)})")
    print(f"Destino: {args.outdir}")
    print(f"Tipos  : {', '.join(sorted(enabled))}")
    print("-" * 44)

    try:
        counters = scan(reader, enabled, args.outdir)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        counters = None
    finally:
        reader.close()

    if counters:
        fotos = counters["jpg"] + counters["png"] + counters["heic"]
        videos = counters["mp4"] + counters["mov"]
        print("-" * 44)
        print(f"Fotos recuperadas : {fotos}")
        print(f"Videos recuperados: {videos}")
        print(f"Detalle en        : {os.path.join(args.outdir, 'recuperados.csv')}")


if __name__ == "__main__":
    main()
