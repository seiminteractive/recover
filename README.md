# RecoverSD

Recuperador de fotos y videos para microSD por *file carving* (como PhotoRec,
pero propio y especializado). Corre en **Windows y macOS**. El motor no tiene
dependencias: solo Python 3 (la librería estándar).

Reconstruye archivos leyendo la tarjeta byte a byte y siguiendo la estructura
interna de cada formato:

- **JPG** — recorre marcadores hasta EOI (no se corta en la miniatura EXIF)
- **PNG** — recorre chunks hasta IEND
- **HEIC / MP4 / MOV** — camina las cajas ISO-BMFF desde `ftyp`

Dos piezas:

- `recover.py` — el **motor** (recuperación + listado de discos). Cross-platform.
- `gui.py` — la **ventana** (asistente paso a paso con Tkinter). Cross-platform.

## Interfaz gráfica (recomendado)

```bash
python3 gui.py
```

Asistente de 6 pasos: bienvenida → elegir tarjeta (o imagen `.img`) → tipos de
archivo → carpeta destino → escaneo con barra de progreso → resultados.

Para leer un disco crudo directamente hacen falta permisos de administrador
(`sudo python3 gui.py` en Mac; "Ejecutar como administrador" en Windows). Sobre
una imagen `.img` no hacen falta permisos especiales.

## Regla de oro: primero imagen, después recuperás

Nunca trabajes sobre la tarjeta viva. Hacé una copia exacta (`.img`) y recuperá
desde ahí. Es más seguro (la tarjeta queda intacta) y más rápido para reintentar.

### En macOS

```bash
# 1. Identificá la tarjeta (fijate capacidad para no confundirte de disco)
diskutil list

# 2. Desmontala (sin expulsarla)
diskutil unmountDisk /dev/disk4

# 3. Clonala a una imagen (bs grande = más rápido). Ojo: rdisk = raw = veloz
sudo dd if=/dev/rdisk4 of=~/tarjeta.img bs=4m status=progress

# 4. Recuperá desde la imagen (sin sudo, sin riesgo)
python3 recover.py ~/tarjeta.img ~/Recuperadas
```

## Uso

```bash
python3 recover.py FUENTE DESTINO [--types jpg,png,heic,mp4,mov]
python3 recover.py --list        # lista los discos conectados

# Solo fotos:
python3 recover.py ~/tarjeta.img ~/Recuperadas --types jpg,heic,png

# Directo desde el dispositivo (usar /dev/diskN, NO rdiskN, y sudo):
sudo python3 recover.py /dev/disk4 ~/Recuperadas
```

El destino **no puede** estar dentro de la fuente. Se crea una subcarpeta por
tipo y un `recuperados.csv` con tipo, nombre, offset y tamaño de cada archivo.

## Generar el ejecutable (para que el usuario no instale nada)

El ejecutable trae Python adentro: la persona hace doble clic y funciona, sin
instalar nada. Se compila **en cada sistema por separado** (no se cruza).

### En tu máquina

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name RecoverSD gui.py
# Resultado:  dist/RecoverSD.app  (Mac)   |   dist/RecoverSD.exe  (Windows)
```

### En la nube (GitHub Actions) — genera Windows y Mac a la vez

El workflow [.github/workflows/build.yml](.github/workflows/build.yml) compila
los dos ejecutables en la nube. Se dispara:

- **A mano:** pestaña *Actions* → *Build RecoverSD* → *Run workflow*. Los `.zip`
  quedan como *Artifacts* de esa corrida.
- **Con un tag de versión:** además publica una *Release* con los ejecutables.

  ```bash
  git tag v1.0.0
  git push origin v1.0.0
  ```

> Al abrir un ejecutable sin firmar, Mac (Gatekeeper) y Windows (SmartScreen)
> muestran un aviso la primera vez. Mac: *clic derecho → Abrir*. Windows:
> *Más información → Ejecutar de todos modos*.

## Límite físico

Ninguna herramienta puede recuperar datos ya **sobreescritos**. Si el espacio de
una foto fue reutilizado por otro archivo, su contenido original ya no existe.

## Qué falta (roadmap)

- Validación real de decodificación + EXIF (fecha/cámara/GPS) con Pillow
- Recuperar nombres y fechas originales leyendo el FAT/exFAT (escaneo "rápido")
- Detección de duplicados por hash
- Galería con miniaturas y vista previa dentro de la ventana
- Firma de código (Apple Developer / Windows) para quitar el aviso de seguridad
