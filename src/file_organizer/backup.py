"""Copia de seguridad en .zip de los ficheros de una carpeta antes de organizarla.

Qué ficheros entran y cuándo se hace lo decide OrganizadorArchivos; aquí solo se crea el .zip.
"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from file_organizer.utilidades import crear_fichero_nuevo, marca_tiempo, nombres_sin_pisar

NOMBRE_CARPETA_BACKUPS = ".file-organizer-backups"

# Formatos ya comprimidos: se guardan tal cual, porque volver a comprimirlos cuesta unas 18
# veces más tiempo y no ahorra espacio.
EXTENSIONES_YA_COMPRIMIDAS = frozenset(
    {
        # imágenes
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".heic",
        ".avif",
        # vídeo y audio
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".webm",
        ".m4v",
        ".mp3",
        ".aac",
        ".ogg",
        ".flac",
        ".m4a",
        ".opus",
        # comprimidos y paquetes
        ".zip",
        ".rar",
        ".7z",
        ".gz",
        ".bz2",
        ".xz",
        ".zst",
        ".jar",
        ".apk",
        # documentos que internamente son zip
        ".docx",
        ".xlsx",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".epub",
        # ficheros cifrados por este programa
        ".enc",
    }
)


def crear_backup_zip(ficheros: list[Path], carpeta_backups: Path, momento: datetime) -> Path:
    """Guarda los ficheros en ``<carpeta_backups>/backup_<fecha>.zip`` y devuelve su ruta.

    ``momento`` da la fecha del nombre; si ya hay otro backup de ese segundo, se añade _2, _3...
    """
    carpeta_backups.mkdir(parents=True, exist_ok=True)
    fichero_zip, ruta_zip = _crear_fichero_backup(
        carpeta_backups / f"backup_{marca_tiempo(momento)}.zip"
    )
    with fichero_zip, zipfile.ZipFile(fichero_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for elemento in ficheros:
            ya_comprimido = elemento.suffix.lower() in EXTENSIONES_YA_COMPRIMIDAS
            zf.write(
                elemento,
                arcname=elemento.name,
                compress_type=zipfile.ZIP_STORED if ya_comprimido else zipfile.ZIP_DEFLATED,
            )
    return ruta_zip


def _crear_fichero_backup(ruta_deseada: Path) -> tuple[BinaryIO, Path]:
    """Abre un fichero nuevo para el backup sin pisar ninguno existente.

    Se crea en modo exclusivo: ni dos backups del mismo segundo ni dos procesos a la vez se
    pisan. Y solo lo puede leer su dueño, porque copia todos los ficheros de la carpeta, también
    los que eran privados.
    """
    candidatos = nombres_sin_pisar(ruta_deseada)
    while True:
        ruta = next(candidatos)
        try:
            return open(crear_fichero_nuevo(ruta), "wb"), ruta
        except FileExistsError:
            continue
