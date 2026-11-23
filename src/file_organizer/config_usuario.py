"""Preferencias del usuario entre sesiones (última carpeta, backup, integridad, agrupación...).

Se guardan en ``~/.file-organizer/config.json`` y las comparten la GUI, el menú y la consola.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from file_organizer.clasificador import CRITERIO_DEFECTO, CriterioAgrupacion
from file_organizer.integridad import MODO_INTEGRIDAD_DEFECTO, ModoIntegridad
from file_organizer.utilidades import PERMISOS_PRIVADOS, escribir_json_atomico

logger = logging.getLogger(__name__)

NOMBRE_ARCHIVO_CONFIG = "config.json"

# Preferencias por defecto; completan lo que le falte a un fichero de una versión anterior.
CONFIG_DEFECTO: dict[str, object] = {
    "ultima_carpeta": "",
    "ultimo_destino": "",
    "usar_destino": False,
    "ultima_config": "",
    "backup": False,
    "recursivo_watch": False,
    "borrar_original": False,
    "modo_integridad": MODO_INTEGRIDAD_DEFECTO.value,
    "agrupar": CRITERIO_DEFECTO.value,
    "mover_duplicados": False,
}


def ruta_config_por_defecto() -> Path:
    """Ruta del fichero de preferencias.

    La variable de entorno FILE_ORGANIZER_CONFIG la cambia (los tests la usan para no tocar la
    real).
    """
    ruta_forzada = os.environ.get("FILE_ORGANIZER_CONFIG")
    if ruta_forzada:
        return Path(ruta_forzada)
    return Path.home() / ".file-organizer" / NOMBRE_ARCHIVO_CONFIG


class ConfiguracionUsuario:
    """Preferencias del usuario, leídas y guardadas en un JSON.

    Cada instancia lee el fichero al cargar: así ve lo último que guardó otra ventana o proceso.
    """

    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = Path(ruta) if ruta else ruta_config_por_defecto()
        self.datos: dict[str, object] = dict(CONFIG_DEFECTO)

    def cargar(self) -> ConfiguracionUsuario:
        """Lee las preferencias (si el fichero falta o está dañado, quedan las de por defecto) y
        devuelve la propia instancia.
        """
        if not self.ruta.exists():
            return self
        try:
            with open(self.ruta, encoding="utf-8") as f:
                guardado = json.load(f)
            if isinstance(guardado, dict):
                self.datos.update({k: guardado[k] for k in guardado if k in CONFIG_DEFECTO})
            else:
                logger.warning(
                    f"Las preferencias guardadas en {self.ruta} no son válidas; se usan las de por "
                    "defecto"
                )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                f"No se pudieron leer las preferencias ({e}); se usan las de por defecto"
            )
        return self

    def guardar(self) -> None:
        """Guarda las preferencias sin dejar nunca el fichero a medias.

        Solo su dueño puede leerlas (en macOS y Linux): llevan las rutas de las últimas carpetas
        usadas. Su carpeta, si hay que crearla, también es privada.
        """
        self.ruta.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        escribir_json_atomico(self.ruta, self.datos, permisos=PERMISOS_PRIVADOS)
        logger.debug(f"Configuración guardada en {self.ruta}")

    def obtener(self, clave: str, defecto: object = None) -> object:
        """Valor de una preferencia (o ``defecto`` si no existe)."""
        return self.datos.get(clave, defecto)

    def establecer(self, clave: str, valor: object) -> None:
        """Cambia una preferencia en memoria; ``guardar()`` la persiste."""
        self.datos[clave] = valor

    def modo_integridad(self) -> ModoIntegridad:
        """Modo de integridad guardado, o el de por defecto si falta o no es válido."""
        try:
            return ModoIntegridad(str(self.obtener("modo_integridad")))
        except ValueError:
            return MODO_INTEGRIDAD_DEFECTO

    def criterio_agrupacion(self) -> CriterioAgrupacion:
        """Criterio de agrupación guardado, o el de por defecto si falta o no es válido."""
        try:
            return CriterioAgrupacion(str(self.obtener("agrupar")))
        except ValueError:
            return CRITERIO_DEFECTO

    def actualizar(self, **valores: object) -> None:
        """Cambia varias preferencias a la vez, en memoria."""
        self.datos.update(valores)
