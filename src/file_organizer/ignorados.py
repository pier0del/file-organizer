"""Patrones de ficheros que no se organizan, leídos de ``.file-organizer-ignore``.

Funciona como ``.gitignore``. Lo usa OrganizadorArchivos.esta_ignorado antes de clasificar.
"""

from __future__ import annotations

import logging
from fnmatch import fnmatch
from pathlib import Path

logger = logging.getLogger(__name__)

NOMBRE_ARCHIVO_IGNORE = ".file-organizer-ignore"


class ListaIgnorados:
    """Patrones que excluyen ficheros de la organización.

    Formato del fichero, como ``.gitignore``:
    - un patrón glob por línea (``*``, ``?``, ``[]``); las vacías y las que empiezan por ``#``
      no cuentan;
    - cada patrón se compara con el nombre del fichero y con su ruta relativa a la carpeta (con
      ``/``).
    """

    def __init__(self, patrones: list[str] | None = None, base: Path | None = None) -> None:
        self.patrones: list[str] = list(patrones or [])
        self.base = base.resolve() if base else None

    @classmethod
    def desde_carpeta(cls, carpeta: Path) -> ListaIgnorados:
        """Lista con los patrones del ``.file-organizer-ignore`` de la carpeta (vacía si no hay
        fichero).
        """
        carpeta = Path(carpeta)
        ruta = carpeta / NOMBRE_ARCHIVO_IGNORE
        if not ruta.exists():
            return cls(base=carpeta)
        return cls(patrones=cls._parsear(ruta), base=carpeta)

    @staticmethod
    def _parsear(ruta: Path) -> list[str]:
        """Patrones del fichero, sin líneas vacías ni comentarios (lista vacía si no se puede
        leer).
        """
        patrones: list[str] = []
        try:
            texto = ruta.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"No se pudo leer {ruta.name}: {e}")
            return patrones

        for linea in texto.splitlines():
            limpia = linea.strip()
            if not limpia or limpia.startswith("#"):
                continue
            patrones.append(limpia)
        logger.info(
            f"Se ignoran los ficheros que encajan con {len(patrones)} patrón(es) de {ruta.name}"
        )
        return patrones

    def debe_ignorar(self, ruta: Path) -> bool:
        """Si la ruta coincide con algún patrón."""
        if not self.patrones:
            return False

        ruta = Path(ruta)
        nombre = ruta.name
        candidatos = [nombre]

        if self.base is not None:
            try:
                relativa = ruta.resolve().relative_to(self.base).as_posix()
                candidatos.append(relativa)
            except ValueError:
                pass

        for patron in self.patrones:
            objetivo = patron.rstrip("/")
            for candidato in candidatos:
                if fnmatch(candidato, objetivo) or fnmatch(candidato, f"{objetivo}/*"):
                    logger.debug(f"Ignorado '{nombre}' por el patrón '{patron}'")
                    return True
        return False

    def __len__(self) -> int:
        return len(self.patrones)
