"""Bloqueo de una carpeta para que dos operaciones no escriban a la vez su historial.

Organizar, deshacer, limpiar el historial y vigilar lo reescriben; si dos coinciden (por
ejemplo, la GUI vigilando y ``--deshacer`` en una terminal), una pisa a la otra. El bloqueo lo
respetan todos los procesos del programa.

Es un bloqueo del sistema operativo (``filelock``) sobre el fichero oculto
``.file-organizer.lock``: si el proceso que lo tiene muere, el sistema lo suelta solo. No se
espera: si la carpeta está ocupada, la operación falla en el acto con ErrorCarpetaEnUso.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType

from filelock import FileLock, Timeout

from file_organizer.excepciones import ErrorCarpetaEnUso

logger = logging.getLogger(__name__)

NOMBRE_ARCHIVO_BLOQUEO = ".file-organizer.lock"


class BloqueoCarpeta:
    """Bloqueo exclusivo de una carpeta, respetado por todos los procesos del programa.

    Se puede coger en un hilo y soltar en otro (la GUI vigila en un hilo y la detiene desde
    otro). Dentro de un mismo proceso, dos bloqueos de la misma carpeta también se excluyen.
    """

    def __init__(self, carpeta: str | Path) -> None:
        self.carpeta = Path(carpeta)
        self._bloqueo = FileLock(
            self.carpeta / NOMBRE_ARCHIVO_BLOQUEO, timeout=0, thread_local=False
        )

    @property
    def adquirido(self) -> bool:
        """Si este bloqueo está cogido ahora mismo."""
        return self._bloqueo.is_locked

    def adquirir(self) -> None:
        """Coge el bloqueo (creando la carpeta si no existe) o lanza ErrorCarpetaEnUso."""
        self.carpeta.mkdir(parents=True, exist_ok=True)
        try:
            self._bloqueo.acquire()
        except Timeout as e:
            raise ErrorCarpetaEnUso(
                f"La carpeta {self.carpeta} está en uso: otra ventana de File Organizer la "
                "está vigilando u organizando. Espera a que termine (o detén la vigilancia) "
                "y vuelve a intentarlo."
            ) from e
        logger.debug("Bloqueo adquirido: %s", self.carpeta)

    def liberar(self) -> None:
        """Suelta el bloqueo si está cogido (se puede llamar varias veces)."""
        if self._bloqueo.is_locked:
            self._bloqueo.release()
            logger.debug("Bloqueo liberado: %s", self.carpeta)

    def __enter__(self) -> BloqueoCarpeta:
        self.adquirir()
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        error: BaseException | None,
        traza: TracebackType | None,
    ) -> None:
        self.liberar()
