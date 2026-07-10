#!/usr/bin/env python3
"""
RecoverSD - Interfaz grafica (asistente paso a paso).

Ventana con botones encima del motor de recover.py. Funciona en Windows y macOS
usando Tkinter (incluido en Python, sin dependencias extra).

Ejecutar:
    python3 gui.py

Para leer un disco crudo directamente hacen falta permisos de administrador:
    - macOS:   sudo python3 gui.py
    - Windows: ejecutar la consola / el .exe como administrador
Si no, se puede trabajar sobre una imagen .img sin permisos especiales.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import recover

PAD = 16
TYPE_LABELS = [
    ("jpg", "Fotos JPG"),
    ("heic", "Fotos HEIC (iPhone)"),
    ("png", "Fotos PNG"),
    ("mp4", "Videos MP4"),
    ("mov", "Videos MOV"),
]


def open_folder(path):
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)  # noqa
        else:
            subprocess.run(["xdg-open", path])
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RecoverSD")
        self.geometry("560x520")
        self.minsize(520, 480)

        # estado compartido entre pantallas
        self.source = None            # ruta de la fuente (imagen o dispositivo)
        self.source_label = ""        # texto legible de la fuente
        self.source_internal = False  # True si parece el disco del sistema
        self.types = {ext: tk.BooleanVar(value=True) for ext, _ in TYPE_LABELS}
        self.outdir = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Recuperadas"))
        self.disks = []

        # cola/hilo del escaneo
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None

        self.container = ttk.Frame(self, padding=PAD)
        self.container.pack(fill="both", expand=True)
        self.show_welcome()

    # -- utilidades de layout --------------------------------------------- #
    def clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    def title_label(self, text):
        ttk.Label(self.container, text=text, font=("Helvetica", 18, "bold")).pack(
            anchor="w", pady=(0, PAD))

    def nav(self, back=None, next_text="Siguiente", next_cmd=None, next_enabled=True):
        bar = ttk.Frame(self.container)
        bar.pack(side="bottom", fill="x", pady=(PAD, 0))
        if back:
            ttk.Button(bar, text="Atras", command=back).pack(side="left")
        if next_cmd:
            b = ttk.Button(bar, text=next_text, command=next_cmd)
            b.pack(side="right")
            if not next_enabled:
                b.state(["disabled"])
            return b

    # -- Pantalla 1: bienvenida ------------------------------------------- #
    def show_welcome(self):
        self.clear()
        self.title_label("RecoverSD")
        ttk.Label(self.container, wraplength=500, justify="left", text=(
            "Recupera fotos y videos borrados o perdidos de tarjetas microSD, "
            "camaras y celulares.\n\n"
            "Como funciona: lee la tarjeta entera y reconstruye los archivos "
            "aunque se haya perdido la lista de carpetas.")).pack(anchor="w")
        warn = ttk.Label(self.container, wraplength=500, justify="left", foreground="#b00", text=(
            "\nImportante: no guardes nada nuevo en la tarjeta antes de recuperar. "
            "Cada archivo nuevo puede pisar una foto vieja para siempre."))
        warn.pack(anchor="w")
        self.nav(next_text="Comenzar", next_cmd=self.show_source)

    # -- Pantalla 2: elegir la fuente ------------------------------------- #
    def show_source(self):
        self.clear()
        self.title_label("De donde recuperar")
        ttk.Label(self.container, text="Elegi la tarjeta / disco a analizar:").pack(anchor="w")

        box = ttk.Frame(self.container)
        box.pack(fill="both", expand=True, pady=8)
        self.disk_list = tk.Listbox(box, height=7, activestyle="none")
        self.disk_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, command=self.disk_list.yview)
        sb.pack(side="right", fill="y")
        self.disk_list.config(yscrollcommand=sb.set)
        self.disk_list.bind("<<ListboxSelect>>", self._on_disk_select)

        row = ttk.Frame(self.container)
        row.pack(fill="x")
        ttk.Button(row, text="Actualizar", command=self._load_disks).pack(side="left")
        ttk.Button(row, text="Usar una imagen .img...", command=self._pick_image).pack(side="left", padx=8)

        self.source_info = ttk.Label(self.container, wraplength=500, justify="left", text="")
        self.source_info.pack(anchor="w", pady=(10, 0))

        self.src_next = self.nav(back=self.show_welcome, next_cmd=self.show_options,
                                 next_enabled=False)
        self._load_disks()

    def _load_disks(self):
        self.disk_list.delete(0, "end")
        self.disks = recover.list_disks()
        if not self.disks:
            self.disk_list.insert("end", "  (no se detectaron discos: usa 'Usar una imagen .img...')")
            self.disk_list.config(state="disabled")
            return
        self.disk_list.config(state="normal")
        for d in self.disks:
            tag = "SISTEMA" if d["internal"] else "extraible"
            self.disk_list.insert(
                "end", f"  {recover.human(d['size']):>10}  [{tag}]  {d['name']}  ({d['path']})")

    def _on_disk_select(self, _evt):
        if not self.disks:
            return
        sel = self.disk_list.curselection()
        if not sel:
            return
        d = self.disks[sel[0]]
        self.source = d["path"]
        self.source_label = f"{d['name']} ({recover.human(d['size'])})"
        self.source_internal = d["internal"]
        msg = f"Seleccionado: {self.source_label}\n{d['path']}"
        if d["internal"]:
            msg += "\n\nATENCION: esto parece el disco del sistema, no una tarjeta. Revisa bien."
            self.source_info.config(foreground="#b00")
        else:
            self.source_info.config(foreground="")
        self.source_info.config(text=msg)
        self.src_next.state(["!disabled"])

    def _pick_image(self):
        path = filedialog.askopenfilename(
            title="Elegi la imagen de la tarjeta",
            filetypes=[("Imagenes de disco", "*.img *.dd *.raw *.iso"), ("Todos", "*.*")])
        if not path:
            return
        self.source = path
        self.source_internal = False
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        self.source_label = f"{os.path.basename(path)} ({recover.human(size)})"
        self.source_info.config(foreground="",
                                text=f"Seleccionado: {self.source_label}\n{path}")
        self.disk_list.selection_clear(0, "end")
        self.src_next.state(["!disabled"])

    # -- Pantalla 3: que recuperar ---------------------------------------- #
    def show_options(self):
        self.clear()
        self.title_label("Que recuperar")
        ttk.Label(self.container, text="Marca los tipos de archivo a buscar:").pack(anchor="w", pady=(0, 8))
        for ext, label in TYPE_LABELS:
            ttk.Checkbutton(self.container, text=label, variable=self.types[ext]).pack(anchor="w")
        ttk.Label(self.container, wraplength=500, justify="left", foreground="#555", text=(
            "\nSe hace un escaneo profundo (recorre toda la tarjeta sector por "
            "sector). Es el modo que mas archivos encuentra.")).pack(anchor="w")
        self.nav(back=self.show_source, next_cmd=self._go_dest)

    def _go_dest(self):
        if not any(v.get() for v in self.types.values()):
            messagebox.showwarning("RecoverSD", "Elegi al menos un tipo de archivo.")
            return
        self.show_dest()

    # -- Pantalla 4: destino ---------------------------------------------- #
    def show_dest(self):
        self.clear()
        self.title_label("Donde guardar")
        ttk.Label(self.container, text="Los archivos recuperados se guardaran en:").pack(anchor="w")
        ttk.Label(self.container, textvariable=self.outdir, wraplength=500,
                  foreground="#036").pack(anchor="w", pady=8)
        ttk.Button(self.container, text="Cambiar carpeta...", command=self._pick_dest).pack(anchor="w")
        ttk.Label(self.container, wraplength=500, justify="left", foreground="#b00", text=(
            "\nNo elijas una carpeta que este en la misma tarjeta que estas "
            "recuperando: la sobreescribirias.")).pack(anchor="w")
        self.nav(back=self.show_options, next_text="Iniciar escaneo", next_cmd=self._start_scan)

    def _pick_dest(self):
        path = filedialog.askdirectory(title="Elegi la carpeta destino")
        if path:
            self.outdir.set(path)

    # -- Pantalla 5: escaneo ---------------------------------------------- #
    def _start_scan(self):
        outdir = self.outdir.get()
        src_abs = os.path.abspath(self.source)
        if os.path.abspath(outdir).startswith(src_abs + os.sep):
            messagebox.showerror("RecoverSD", "El destino no puede estar dentro de la fuente.")
            return
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("RecoverSD", f"No se pudo crear la carpeta destino:\n{e}")
            return

        enabled = {ext for ext, v in self.types.items() if v.get()}
        self.stop_event.clear()
        self.q = queue.Queue()

        self.clear()
        self.title_label("Escaneando...")
        self.pbar = ttk.Progressbar(self.container, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", pady=8)
        self.pct_lbl = ttk.Label(self.container, text="0 %", font=("Helvetica", 13))
        self.pct_lbl.pack(anchor="w")
        self.count_lbl = ttk.Label(self.container, text="Fotos: 0    Videos: 0", font=("Helvetica", 13))
        self.count_lbl.pack(anchor="w", pady=(8, 0))
        self.speed_lbl = ttk.Label(self.container, text="", foreground="#555")
        self.speed_lbl.pack(anchor="w")
        bar = ttk.Frame(self.container)
        bar.pack(side="bottom", fill="x", pady=(PAD, 0))
        ttk.Button(bar, text="Cancelar", command=self._cancel_scan).pack(side="right")

        self._scan_start = None
        self.worker = threading.Thread(
            target=self._scan_worker, args=(outdir, enabled), daemon=True)
        self.worker.start()
        self.after(100, self._poll)

    def _scan_worker(self, outdir, enabled):
        try:
            reader = recover.Reader(self.source)
        except OSError as e:
            hint = ""
            if recover.is_raw_device(self.source):
                hint = ("\n\nPara leer un disco directamente hacen falta permisos "
                        "de administrador (sudo en Mac, 'Ejecutar como administrador' "
                        "en Windows), o usa una imagen .img.")
            self.q.put(("error", f"No se pudo abrir la fuente:\n{e}{hint}"))
            return
        try:
            counters = recover.scan(
                reader, enabled, outdir,
                on_progress=lambda c, t, co: self.q.put(("progress", c, t, co)),
                should_stop=self.stop_event.is_set)
            self.q.put(("done", counters, outdir))
        except Exception as e:
            self.q.put(("error", f"Error durante el escaneo:\n{e}"))
        finally:
            reader.close()

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self._update_progress(*msg[1:])
                elif kind == "done":
                    self.show_results(msg[1], msg[2])
                    return
                elif kind == "error":
                    messagebox.showerror("RecoverSD", msg[1])
                    self.show_source()
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _update_progress(self, cursor, total, counters):
        import time
        if self._scan_start is None:
            self._scan_start = time.time()
        fotos = counters["jpg"] + counters["png"] + counters["heic"]
        videos = counters["mp4"] + counters["mov"]
        self.count_lbl.config(text=f"Fotos: {fotos}    Videos: {videos}")
        elapsed = max(time.time() - self._scan_start, 0.001)
        speed = cursor / elapsed
        if total:
            pct = 100 * cursor / total
            self.pbar.config(mode="determinate", value=pct)
            eta = (total - cursor) / speed if speed else 0
            self.pct_lbl.config(text=f"{pct:.1f} %   (faltan ~{int(eta // 60):02d}:{int(eta % 60):02d})")
        else:
            self.pbar.config(mode="indeterminate")
            self.pbar.start(20)
            self.pct_lbl.config(text=f"Escaneado {recover.human(cursor)}")
        self.speed_lbl.config(text=f"Velocidad: {recover.human(speed)}/s")

    def _cancel_scan(self):
        self.stop_event.set()

    # -- Pantalla 6: resultados ------------------------------------------- #
    def show_results(self, counters, outdir):
        self.clear()
        canceled = self.stop_event.is_set()
        self.title_label("Escaneo cancelado" if canceled else "Recuperacion finalizada")
        fotos = counters["jpg"] + counters["png"] + counters["heic"]
        videos = counters["mp4"] + counters["mov"]
        ttk.Label(self.container, font=("Helvetica", 14), text=(
            f"Fotos recuperadas:  {fotos}\n"
            f"Videos recuperados: {videos}")).pack(anchor="w", pady=8)
        ttk.Label(self.container, wraplength=500, justify="left",
                  text=f"Guardados en:\n{outdir}").pack(anchor="w")
        bar = ttk.Frame(self.container)
        bar.pack(side="bottom", fill="x", pady=(PAD, 0))
        ttk.Button(bar, text="Escanear otra tarjeta", command=self.show_source).pack(side="left")
        ttk.Button(bar, text="Abrir carpeta", command=lambda: open_folder(outdir)).pack(side="right")


def main():
    try:
        app = App()
    except tk.TclError as e:
        print(f"No se pudo abrir la ventana (¿hay entorno grafico?): {e}", file=sys.stderr)
        sys.exit(1)
    app.mainloop()


if __name__ == "__main__":
    main()
