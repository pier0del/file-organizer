"""Reglas de cada carpeta: «los ficheros que se llamen así, a esta carpeta».

Mandan sobre las categorías: el fichero va a la carpeta de la primera regla que coincide y, si
ninguna coincide, se clasifica como siempre. La regla hace de categoría propia: si se agrupa
por fecha, debajo de su carpeta va ``AAAA/MM`` (``Documentos/2024/03``), salvo que la regla ya
lleve ``{año}`` o ``{mes}``, que entonces se usa tal cual. Se guardan en ``.file-organizer-reglas.json``
dentro de la carpeta que se organiza, así que cada carpeta tiene las suyas:

    {"reglas": [{"patron": "factura*", "carpeta": "Facturas/{año}"},
                {"patron": "*.pdf", "carpeta": "Documentos"}]}

- ``patron``: glob sobre el nombre del fichero (``*``, ``?``, ``[]``), sin distinguir
  mayúsculas: ``*.pdf``, ``factura*``, ``IMG_????.jpg``.
- ``carpeta``: subcarpeta del destino, de uno o varios niveles (``Trabajo/Contratos``). Nunca
  fuera de él, para que todo se pueda deshacer. ``{año}`` y ``{mes}`` se cambian por la fecha
  del fichero (la de sus metadatos si la tiene, ver metadatos.py).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePath, PureWindowsPath

from file_organizer.clasificador import quitar_carpeta_de_fecha
from file_organizer.excepciones import ErrorReglas
from file_organizer.metadatos import FechaFichero, fecha_del_fichero
from file_organizer.utilidades import escribir_json_atomico

logger = logging.getLogger(__name__)

NOMBRE_ARCHIVO_REGLAS = ".file-organizer-reglas.json"

# Marcadores de fecha que se pueden poner en la carpeta de una regla ({anio}: sin ñ).
_MARCADORES_FECHA = ("{año}", "{anio}", "{mes}")
# Caracteres que no valen en un nombre de carpeta en Windows (y así las reglas valen en todos).
_CARACTERES_PROHIBIDOS = set('<>:"|?*')
# Qué casa cada marcador al reconocer una carpeta ya creada por una regla.
_PATRON_MARCADOR = {"{año}": r"\d{4}", "{anio}": r"\d{4}", "{mes}": r"(?:0[1-9]|1[0-2])"}


@dataclass(frozen=True)
class Regla:
    """Los ficheros cuyo nombre encaja con ``patron`` van a ``carpeta`` (dentro del destino)."""

    patron: str
    carpeta: str

    @classmethod
    def crear(cls, patron: str, carpeta: str) -> Regla:
        """Regla con el patrón y la carpeta comprobados y normalizados.

        Lanza ErrorReglas, con un motivo para el usuario, si alguno no vale.
        """
        return cls(_validar_patron(patron), _validar_carpeta(carpeta))

    @property
    def lleva_fecha(self) -> bool:
        """Si la carpeta lleva ``{año}`` o ``{mes}`` (entonces no se le añade otra fecha)."""
        return any(marcador in self.carpeta for marcador in _MARCADORES_FECHA)

    def coincide(self, nombre: str) -> bool:
        """Si el nombre del fichero encaja con el patrón (sin distinguir mayúsculas)."""
        return fnmatchcase(nombre.lower(), self.patron.lower())

    def carpeta_para(
        self, ruta: Path, fecha_de: Callable[[Path], FechaFichero] = fecha_del_fichero
    ) -> str:
        """Carpeta del fichero, con la fecha en vez de ``{año}`` y ``{mes}``.

        ``fecha_de`` da la fecha del fichero (el organizador pasa la que además la cuenta para
        el resumen). Lanza OSError si hace falta la fecha y no se puede consultar el fichero.
        """
        if not self.lleva_fecha:
            return self.carpeta
        fecha = fecha_de(ruta).fecha
        return (
            self.carpeta.replace("{año}", f"{fecha.year:04d}")
            .replace("{anio}", f"{fecha.year:04d}")
            .replace("{mes}", f"{fecha.month:02d}")
        )

    def es_su_carpeta(self, carpeta: PurePath) -> bool:
        """Si ``carpeta`` (relativa al destino) es una de las que crea esta regla."""
        patron = re.escape(self.carpeta)
        for marcador, equivalente in _PATRON_MARCADOR.items():
            patron = patron.replace(re.escape(marcador), equivalente)
        return re.fullmatch(patron, carpeta.as_posix()) is not None

    def texto(self) -> str:
        """«factura*  →  Facturas/{año}», para mostrarla al usuario."""
        return f"{self.patron}  →  {self.carpeta}"


def _validar_patron(patron: str) -> str:
    """Patrón sin espacios alrededor; lanza ErrorReglas si está vacío o lleva carpetas."""
    patron = patron.strip()
    if not patron:
        raise ErrorReglas("Falta el patrón (por ejemplo «*.pdf» o «factura*»)")
    if "/" in patron or "\\" in patron:
        raise ErrorReglas(
            f"El patrón se compara con el nombre del fichero, sin carpetas: «{patron}»"
        )
    return patron


def _validar_carpeta(carpeta: str) -> str:
    """Carpeta normalizada (``a/b``); lanza ErrorReglas si no es una subcarpeta del destino."""
    texto = carpeta.strip().replace("\\", "/").rstrip("/")
    if not texto:
        raise ErrorReglas("Falta la carpeta (por ejemplo «Documentos» o «Trabajo/Facturas»)")
    if texto.startswith("/") or PureWindowsPath(texto).drive:
        raise ErrorReglas(
            f"La carpeta tiene que estar dentro del destino, sin unidad ni «/» delante: «{carpeta}»"
        )
    partes = [parte.strip() for parte in texto.split("/")]
    for parte in partes:
        if parte == "..":
            raise ErrorReglas(
                f"La carpeta tiene que estar dentro del destino: no puede llevar «..»: «{carpeta}»"
            )
        if not parte or parte == ".":
            raise ErrorReglas(f"Sobra una «/» o un «.» en la carpeta: «{carpeta}»")
        if parte.startswith("."):
            raise ErrorReglas(f"La carpeta no puede ser oculta (empezar por «.»): «{parte}»")
        sin_marcadores = parte
        for marcador in _MARCADORES_FECHA:
            sin_marcadores = sin_marcadores.replace(marcador, "")
        if "{" in sin_marcadores or "}" in sin_marcadores:
            raise ErrorReglas(f"En la carpeta solo se admiten {{año}} y {{mes}}: «{parte}»")
        prohibidos = _CARACTERES_PROHIBIDOS & set(sin_marcadores)
        if prohibidos:
            raise ErrorReglas(
                f"La carpeta lleva caracteres no permitidos ({' '.join(sorted(prohibidos))}): "
                f"«{parte}»"
            )
    return "/".join(partes)


class ReglasCarpeta:
    """Las reglas de una carpeta, guardadas en su ``.file-organizer-reglas.json``.

    Si el fichero cambia mientras se usa (una vigilancia puede durar horas), las reglas se
    vuelven a leer solas; si entonces el fichero no es válido, se siguen usando las anteriores.
    """

    def __init__(self, carpeta: Path) -> None:
        self.ruta = Path(carpeta) / NOMBRE_ARCHIVO_REGLAS
        self.reglas: list[Regla] = []
        self._estado: tuple[int, int] | None = None  # tamaño y fecha del fichero al leerlo
        self._leidas = False

    def cargar(self) -> list[Regla]:
        """Lee las reglas (ninguna si no hay fichero) y las devuelve.

        Lanza ErrorReglas si el fichero no se puede leer o alguna regla no vale.
        """
        estado = self._estado_fichero()
        self.reglas = _leer(self.ruta) if estado is not None else []
        self._estado = estado
        self._leidas = True
        return self.reglas

    def regla_para(self, nombre: str) -> Regla | None:
        """Primera regla que coincide con el nombre del fichero, o None."""
        self._al_dia()
        return next((regla for regla in self.reglas if regla.coincide(nombre)), None)

    def es_carpeta_de_regla(self, carpeta: PurePath, agrupa_por_fecha: bool = False) -> bool:
        """Si ``carpeta`` (relativa al destino) es una de las que crean las reglas.

        Con ``agrupa_por_fecha`` cuenta también ``<carpeta de la regla>/AAAA/MM`` para las reglas
        sin ``{año}`` ni ``{mes}``.
        """
        try:
            self._al_dia()
        except ErrorReglas:
            return False
        if any(regla.es_su_carpeta(carpeta) for regla in self.reglas):
            return True
        sin_fecha = quitar_carpeta_de_fecha(carpeta) if agrupa_por_fecha else None
        return sin_fecha is not None and any(
            not regla.lleva_fecha and regla.es_su_carpeta(sin_fecha) for regla in self.reglas
        )

    # --------------------------------------------------------------- edición

    def agregar(self, regla: Regla) -> None:
        """Añade la regla al final (la de menos prioridad) y guarda."""
        self.cargar()
        self.reglas.append(regla)
        self._guardar()

    def quitar(self, numero: int) -> Regla:
        """Quita la regla ``numero`` (contando desde 1) y guarda; devuelve la quitada.

        Lanza ErrorReglas si no existe.
        """
        self.cargar()
        self._comprobar_numero(numero)
        quitada = self.reglas.pop(numero - 1)
        self._guardar()
        return quitada

    def mover(self, numero: int, desplazamiento: int) -> int:
        """Sube (``-1``) o baja (``+1``) la regla ``numero`` y guarda; devuelve su nuevo número.

        Si ya está en un extremo, se queda donde está. Lanza ErrorReglas si no existe.
        """
        self.cargar()
        self._comprobar_numero(numero)
        nuevo = min(max(numero + desplazamiento, 1), len(self.reglas))
        if nuevo != numero:
            self.reglas.insert(nuevo - 1, self.reglas.pop(numero - 1))
            self._guardar()
        return nuevo

    def _comprobar_numero(self, numero: int) -> None:
        if not 1 <= numero <= len(self.reglas):
            raise ErrorReglas(
                f"No hay regla {numero}: esta carpeta tiene {len(self.reglas)} regla(s)"
            )

    def _guardar(self) -> None:
        """Escribe las reglas en el fichero, sin dejarlo nunca a medias."""
        escribir_json_atomico(
            self.ruta,
            {"reglas": [{"patron": r.patron, "carpeta": r.carpeta} for r in self.reglas]},
        )
        self._estado = self._estado_fichero()

    # ------------------------------------------------------------- relectura

    def _al_dia(self) -> None:
        """Lee las reglas la primera vez y otra vez si el fichero cambió.

        La primera lectura lanza ErrorReglas si no valen; después, un fichero que deja de valer
        solo se avisa en el log y se siguen usando las reglas anteriores.
        """
        estado = self._estado_fichero()
        if self._leidas and estado == self._estado:
            return
        try:
            self.cargar()
        except ErrorReglas as error:
            if not self._leidas:
                raise
            self._estado = estado  # no volver a intentarlo hasta que cambie otra vez
            logger.warning(f"{error}. Se siguen usando las reglas anteriores.")

    def _estado_fichero(self) -> tuple[int, int] | None:
        """Tamaño y fecha de modificación del fichero, o None si no existe."""
        try:
            info = self.ruta.stat()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ErrorReglas(f"No se pueden leer las reglas de {self.ruta}: {e}") from e
        return info.st_size, info.st_mtime_ns


def _leer(ruta: Path) -> list[Regla]:
    """Reglas de un fichero; lanza ErrorReglas con la regla y el motivo si alguna no vale."""
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except json.JSONDecodeError as e:
        raise ErrorReglas(f"Las reglas de {ruta} no son un JSON válido ({e})") from e
    except OSError as e:
        raise ErrorReglas(f"No se pueden leer las reglas de {ruta}: {e}") from e

    lista = datos.get("reglas") if isinstance(datos, dict) else None
    if not isinstance(lista, list):
        raise ErrorReglas(f'Las reglas de {ruta} deben ir en una lista "reglas"')
    reglas = []
    for numero, entrada in enumerate(lista, start=1):
        if not isinstance(entrada, dict) or not all(
            isinstance(entrada.get(clave), str) for clave in ("patron", "carpeta")
        ):
            raise ErrorReglas(
                f'La regla {numero} de {ruta} debe tener "patron" y "carpeta" (textos)'
            )
        try:
            reglas.append(Regla.crear(entrada["patron"], entrada["carpeta"]))
        except ErrorReglas as e:
            raise ErrorReglas(f"La regla {numero} de {ruta} no vale: {e}") from e
    return reglas
