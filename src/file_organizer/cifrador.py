"""Cifrado y descifrado de ficheros con AES-256-GCM, por bloques de 64 KiB.

La memoria usada no depende del tamaño del fichero. Formato del ``.enc``:
    cabecera (25 B) = "FORG" + modo (1 B) + salt (16 B) + tamaño de bloque (4 B)
    bloque_i        = AES-256-GCM(clave, nonce_i, datos_i, datos asociados = cabecera)
    nonce_i         = i (11 B) + 0x01 si es el último bloque, 0x00 si no

Es la construcción STREAM (Hoang et al., 2015): como el nonce lleva la posición y si es el
último, se detecta cualquier bloque reordenado, quitado o añadido y un fichero recortado; y como
la cabecera va de datos asociados, tampoco se puede alterar.

Cada fichero tiene su propia clave, derivada con un salt aleatorio:
- con contraseña: PBKDF2-HMAC-SHA256 (480 000 iteraciones);
- con fichero .key: HKDF-SHA256.

Los ficheros del formato anterior (Fernet, modos 0x01 y 0x02) se siguen pudiendo descifrar.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from file_organizer.excepciones import (
    ErrorArchivoCifradoCorrupto,
    ErrorArchivoClaveInvalido,
    ErrorArchivoClaveNoEncontrado,
    ErrorArchivoClaveYaExiste,
    ErrorCarpetaInvalida,
    ErrorClaveAmbigua,
    ErrorContrasenaInvalida,
    ErrorFaltaClave,
    ErrorNoCifrado,
    ErrorOrganizador,
    ErrorYaCifrado,
)
from file_organizer.utilidades import (
    PERMISOS_PRIVADOS,
    crear_fichero_nuevo,
    resolver_conflicto,
)
from file_organizer.utilidades import SUFIJO_CIFRADO as SUFIJO_CIFRADO

logger = logging.getLogger(__name__)

CABECERA_MAGICA = b"FORG"

# Modos actuales (por bloques, AES-256-GCM): con contraseña y con fichero .key.
MODO_CONTRASENA = b"\x03"
MODO_ARCHIVO_CLAVE = b"\x04"
# Modos del formato anterior (Fernet sobre el fichero entero): solo se descifran.
MODO_CONTRASENA_FERNET = b"\x01"
MODO_ARCHIVO_CLAVE_FERNET = b"\x02"

TAMANO_SAL = 16
TAMANO_CLAVE = 32  # AES-256
TAMANO_ETIQUETA_GCM = 16
TAMANO_BLOQUE_CIFRADO = 64 * 1024
TAMANO_BLOQUE_MAXIMO = 16 * 1024 * 1024  # límite al leer cabeceras ajenas o manipuladas
TAMANO_CABECERA = len(CABECERA_MAGICA) + 1 + TAMANO_SAL + 4
ITERACIONES_PBKDF2 = 480_000
INFO_HKDF = b"file-organizer:cifrado-por-bloques:v1"

SUFIJO_CLAVE = ".key"

# (procesados, total) para una barra de progreso: ficheros en una carpeta, bytes en un fichero.
CallbackProgreso = Callable[[int, int], None]


class AccionCifrado(StrEnum):
    """Cifrar o descifrar. Es un str: se compara igual que su texto (``"cifrar"``)."""

    CIFRAR = "cifrar"
    DESCIFRAR = "descifrar"


@dataclass
class ResultadoCifrado:
    """Resultado de cifrar o descifrar un fichero (si falla, se lanza una excepción)."""

    mensaje: str
    ruta: Path


@dataclass
class ResultadoLote:
    """Resultado de cifrar o descifrar una carpeta: cuántos ficheros salieron bien y cuántos no."""

    exitosos: int = 0
    fallidos: int = 0
    resultados: list[ResultadoCifrado] = field(default_factory=list)
    errores: list[tuple[Path, Exception]] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Ficheros procesados (exitosos + fallidos)."""
        return self.exitosos + self.fallidos


def _nonce(indice: int, ultimo: bool) -> bytes:
    """Nonce de 12 bytes del bloque ``indice``: su posición (11 B) y si es el último (1 B)."""
    return indice.to_bytes(11, "big") + (b"\x01" if ultimo else b"\x00")


def _permisos_como(origen: Path) -> int:
    """Permisos para el fichero que sale de ``origen``: los mismos, y que su dueño pueda escribirlo.

    Así cifrar y descifrar no dejan ver a nadie más de lo que dejaba ver el original: lo que se
    descifra queda como estaba el fichero antes de cifrarlo.
    """
    return (stat.S_IMODE(origen.stat().st_mode) & 0o777) | stat.S_IRUSR | stat.S_IWUSR


@contextmanager
def _escritura_atomica(
    destino_deseado: Path, permisos_finales: int
) -> Iterator[tuple[BinaryIO, list[Path]]]:
    """Escribe en un temporal oculto junto al destino y solo lo pone en su sitio si todo va bien.

    Da el fichero abierto y una lista donde, al terminar, queda la ruta final (sin pisar otra).
    Si algo falla, borra el temporal: nunca queda un fichero a medias.

    Mientras se escribe, el temporal solo lo puede leer su dueño (al descifrar lleva el contenido
    en claro); recibe ``permisos_finales`` justo antes de aparecer con su nombre.
    """
    temporal = destino_deseado.with_name(f".{destino_deseado.name}.{secrets.token_hex(4)}.tmp")
    # Fuera del try: si ese nombre ya existiera, no es nuestro y no se borra.
    descriptor = crear_fichero_nuevo(temporal)
    ruta_final: list[Path] = []
    try:
        with open(descriptor, "wb") as salida:
            yield salida, ruta_final
        os.chmod(temporal, permisos_finales)
        final = resolver_conflicto(destino_deseado)
        os.replace(temporal, final)
        ruta_final.append(final)
    finally:
        if temporal.exists():
            temporal.unlink()


class Cifrador:
    """Cifra y descifra ficheros y carpetas con una contraseña o un fichero .key.

    Todos sus métodos son estáticos: la clase no guarda estado, solo agrupa las operaciones.
    """

    @staticmethod
    def generar_clave(ruta_clave: Path, sobrescribir: bool = False) -> Path:
        """Crea una clave aleatoria en ``ruta_clave`` (permisos 0o600) y devuelve su ruta.

        Si el fichero ya existe, lanza ErrorArchivoClaveYaExiste, salvo con ``sobrescribir`` (la
        GUI lo pasa cuando el usuario ya confirmó en su diálogo): reemplazar una clave impide
        descifrar lo que se cifró con ella. Se guarda en formato Fernet (32 bytes en base64),
        así sirven también las claves antiguas.
        """
        clave = Fernet.generate_key()

        ruta_clave.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL: la comprobación y la creación son una sola operación del sistema.
        modo = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if sobrescribir else os.O_EXCL)
        try:
            descriptor = os.open(ruta_clave, modo, PERMISOS_PRIVADOS)
        except FileExistsError:
            raise ErrorArchivoClaveYaExiste(
                f"Ya existe el fichero de clave {ruta_clave}: no se sobrescribe, porque lo que "
                "se cifró con él ya no se podría descifrar. Elige otro nombre o, si estás "
                "seguro de que ya no lo necesitas, bórralo antes."
            ) from None
        with os.fdopen(descriptor, "wb") as f:
            f.write(clave)
        os.chmod(ruta_clave, PERMISOS_PRIVADOS)  # por si se sobrescribió una con otros

        logger.info(f"Clave generada y guardada en {ruta_clave}")
        return ruta_clave

    # ------------------------------------------------------------------ claves

    @staticmethod
    def _derivar_clave_de_contrasena(contrasena: str, salt: bytes) -> bytes:
        """Clave AES de 32 bytes a partir de la contraseña y el salt (PBKDF2)."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            iterations=ITERACIONES_PBKDF2,
            length=TAMANO_CLAVE,
            salt=salt,
        )
        return kdf.derive(contrasena.encode("utf-8"))

    @staticmethod
    def _leer_archivo_clave(ruta_clave: Path) -> bytes:
        """Contenido (en base64) de un fichero .key; ErrorArchivoClaveNoEncontrado si no existe."""
        if not ruta_clave.exists():
            raise ErrorArchivoClaveNoEncontrado(f"No se encontró el fichero de clave: {ruta_clave}")
        with open(ruta_clave, "rb") as f:
            return f.read().strip()

    @staticmethod
    def _decodificar_archivo_clave(ruta_clave: Path) -> bytes:
        """Los 32 bytes de la clave de un .key (ErrorArchivoClaveInvalido si no es válida)."""
        contenido = Cifrador._leer_archivo_clave(ruta_clave)
        try:
            clave = base64.urlsafe_b64decode(contenido)
        except (binascii.Error, ValueError):
            clave = b""
        if len(clave) != TAMANO_CLAVE:
            raise ErrorArchivoClaveInvalido(
                f"El fichero no contiene una clave válida: {ruta_clave}"
            )
        return clave

    @staticmethod
    def _derivar_clave_de_archivo(ruta_clave: Path, salt: bytes) -> bytes:
        """Clave AES propia del fichero, a partir del .key y del salt (HKDF)."""
        kdf = HKDF(algorithm=hashes.SHA256(), length=TAMANO_CLAVE, salt=salt, info=INFO_HKDF)
        return kdf.derive(Cifrador._decodificar_archivo_clave(ruta_clave))

    @staticmethod
    def _derivar_clave(contrasena: str | None, ruta_clave: Path | None, salt: bytes) -> bytes:
        """Clave AES de un fichero: de la contraseña (PBKDF2) o del .key (HKDF), con su salt."""
        if contrasena:
            return Cifrador._derivar_clave_de_contrasena(contrasena, salt)
        assert ruta_clave is not None  # garantizado por _validar_material_clave
        return Cifrador._derivar_clave_de_archivo(ruta_clave, salt)

    @staticmethod
    def _validar_material_clave(contrasena: str | None, ruta_clave: Path | None) -> None:
        """Exige contraseña o fichero de clave, pero no los dos (ErrorFaltaClave,
        ErrorClaveAmbigua).
        """
        if not contrasena and not ruta_clave:
            raise ErrorFaltaClave("Debes proporcionar una contraseña o un fichero de clave")
        if contrasena and ruta_clave:
            raise ErrorClaveAmbigua(
                "Proporciona solo una contraseña o un fichero de clave, no ambos"
            )

    # ---------------------------------------------------------------- ficheros

    @staticmethod
    def cifrar_archivo(
        ruta_archivo: Path,
        ruta_clave: Path | None = None,
        contrasena: str | None = None,
        borrar_original: bool = False,
        progreso: CallbackProgreso | None = None,
    ) -> ResultadoCifrado:
        """Cifra el fichero en ``<nombre>.enc`` (sin pisar otro) y devuelve el resultado.

        ``progreso(bytes cifrados, tamaño)``, si se pasa, se llama tras cada bloque. Lanza
        FileNotFoundError si no existe, ErrorYaCifrado si ya es un .enc y los errores de clave
        si falta o no es válida.
        """
        Cifrador._comprobar_fichero(ruta_archivo)

        if ruta_archivo.suffix == SUFIJO_CIFRADO:
            raise ErrorYaCifrado(f"El fichero ya está cifrado: {ruta_archivo.name}")

        Cifrador._validar_material_clave(contrasena, ruta_clave)

        salt = os.urandom(TAMANO_SAL)
        clave = Cifrador._derivar_clave(contrasena, ruta_clave, salt)
        modo = MODO_CONTRASENA if contrasena else MODO_ARCHIVO_CLAVE

        tamano_bloque = TAMANO_BLOQUE_CIFRADO
        cabecera = CABECERA_MAGICA + modo + salt + tamano_bloque.to_bytes(4, "big")
        aes = AESGCM(clave)

        destino = ruta_archivo.with_suffix(ruta_archivo.suffix + SUFIJO_CIFRADO)
        total = ruta_archivo.stat().st_size
        with (
            open(ruta_archivo, "rb") as entrada,
            _escritura_atomica(destino, _permisos_como(ruta_archivo)) as (salida, final),
        ):
            salida.write(cabecera)
            indice = 0
            procesados = 0
            actual = entrada.read(tamano_bloque)
            while True:
                # Se lee un bloque por adelantado para saber si el actual es el último.
                siguiente = entrada.read(tamano_bloque)
                ultimo = not siguiente
                salida.write(aes.encrypt(_nonce(indice, ultimo), actual, cabecera))
                procesados += len(actual)
                if progreso is not None:
                    progreso(procesados, total)
                if ultimo:
                    break
                actual = siguiente
                indice += 1
        return Cifrador._terminar(
            ruta_archivo, final[0], borrar_original, "Cifrado", "Original eliminado tras cifrar"
        )

    @staticmethod
    def descifrar_archivo(
        ruta_archivo: Path,
        contrasena: str | None = None,
        ruta_clave: Path | None = None,
        borrar_original: bool = False,
        progreso: CallbackProgreso | None = None,
    ) -> ResultadoCifrado:
        """Descifra un .enc, reconociendo su formato y su tipo de clave por la cabecera.

        ``progreso(bytes leídos, tamaño)``, si se pasa, se llama tras cada bloque (el formato
        antiguo, que se descifra de una vez, solo al final). Lanza ErrorContrasenaInvalida si la
        clave no es la correcta, ErrorFaltaClave si el fichero necesita el otro tipo de clave y
        ErrorArchivoCifradoCorrupto si está dañado o manipulado.
        """
        Cifrador._comprobar_fichero(ruta_archivo)

        if ruta_archivo.suffix != SUFIJO_CIFRADO:
            raise ErrorNoCifrado(f"{ruta_archivo.name} no está cifrado (no termina en .enc)")

        Cifrador._validar_material_clave(contrasena, ruta_clave)

        with open(ruta_archivo, "rb") as entrada:
            inicio = entrada.read(len(CABECERA_MAGICA) + 1)
        if inicio[: len(CABECERA_MAGICA)] != CABECERA_MAGICA:
            raise ErrorArchivoCifradoCorrupto(
                f"{ruta_archivo.name} no se cifró con este programa o está dañado"
            )
        modo = inicio[len(CABECERA_MAGICA) :]

        formato = _FORMATOS.get(modo)
        if formato is None:
            raise ErrorArchivoCifradoCorrupto(
                f"No se reconoce cómo se cifró {ruta_archivo.name} (¿con una versión más nueva "
                "del programa?)"
            )
        if formato.usa_contrasena and not contrasena:
            raise ErrorFaltaClave(
                f"{ruta_archivo.name} se cifró con una contraseña: descífralo con ella, no con "
                "un fichero de clave"
            )
        if not formato.usa_contrasena and not ruta_clave:
            raise ErrorFaltaClave(
                f"{ruta_archivo.name} se cifró con un fichero de clave (.key): descífralo con "
                "él, no con una contraseña"
            )

        destino = formato.descifrar(
            ruta_archivo, ruta_archivo.with_suffix(""), contrasena, ruta_clave, progreso
        )

        return Cifrador._terminar(
            ruta_archivo,
            destino,
            borrar_original,
            "Descifrado",
            "Fichero cifrado eliminado tras descifrar",
        )

    @staticmethod
    def _comprobar_fichero(ruta_archivo: Path) -> None:
        """Lanza FileNotFoundError si la ruta no es un fichero que exista."""
        if not ruta_archivo.exists() or not ruta_archivo.is_file():
            raise FileNotFoundError(f"El fichero no existe: {ruta_archivo}")

    @staticmethod
    def _terminar(
        ruta_archivo: Path, destino: Path, borrar_original: bool, hecho: str, eliminado: str
    ) -> ResultadoCifrado:
        """Final común de cifrar y descifrar: borra el original si se pidió, lo anota y devuelve el
        resultado.

        ``hecho`` es "Cifrado" o "Descifrado"; ``eliminado``, lo que se anota al borrar el
        original.
        """
        if borrar_original:
            ruta_archivo.unlink()
            logger.info(f"{eliminado}: {ruta_archivo.name}")

        logger.info(f"{hecho}: {ruta_archivo.name} -> {destino.name}")
        return ResultadoCifrado(
            mensaje=f"{hecho} correctamente: {destino.name}",
            ruta=destino,
        )

    @staticmethod
    def _descifrar_por_bloques(
        ruta_archivo: Path,
        destino: Path,
        contrasena: str | None,
        ruta_clave: Path | None,
        progreso: CallbackProgreso | None = None,
    ) -> Path:
        """Descifra el formato actual bloque a bloque y devuelve la ruta escrita."""
        nombre = ruta_archivo.name
        total = ruta_archivo.stat().st_size
        with open(ruta_archivo, "rb") as entrada:
            cabecera = entrada.read(TAMANO_CABECERA)
            if len(cabecera) != TAMANO_CABECERA:
                raise ErrorArchivoCifradoCorrupto(
                    f"{nombre} está dañado: su cabecera está incompleta"
                )

            salt = cabecera[5 : 5 + TAMANO_SAL]
            tamano_bloque = int.from_bytes(cabecera[5 + TAMANO_SAL :], "big")
            if not 0 < tamano_bloque <= TAMANO_BLOQUE_MAXIMO:
                raise ErrorArchivoCifradoCorrupto(f"{nombre} está dañado: su cabecera no es válida")

            aes = AESGCM(Cifrador._derivar_clave(contrasena, ruta_clave, salt))

            tamano_cifrado = tamano_bloque + TAMANO_ETIQUETA_GCM
            actual = entrada.read(tamano_cifrado)
            if not actual:
                raise ErrorArchivoCifradoCorrupto(
                    f"{nombre} está dañado: no contiene datos cifrados"
                )

            with _escritura_atomica(destino, _permisos_como(ruta_archivo)) as (salida, final):
                indice = 0
                leidos = TAMANO_CABECERA
                while True:
                    siguiente = entrada.read(tamano_cifrado)
                    ultimo = not siguiente
                    try:
                        salida.write(aes.decrypt(_nonce(indice, ultimo), actual, cabecera))
                    except InvalidTag:
                        # Si falla el primero, lo más probable es una contraseña o clave
                        # incorrecta; si falla otro, el fichero está dañado.
                        if indice == 0:
                            raise ErrorContrasenaInvalida(
                                f"Contraseña o clave incorrecta (o fichero dañado): {nombre}"
                            ) from None
                        raise ErrorArchivoCifradoCorrupto(
                            f"El fichero cifrado está dañado o ha sido manipulado "
                            f"(bloque {indice}): {nombre}"
                        ) from None
                    leidos += len(actual)
                    if progreso is not None:
                        progreso(leidos, total)
                    if ultimo:
                        break
                    actual = siguiente
                    indice += 1
        return final[0]

    @staticmethod
    def _descifrar_fernet_antiguo(
        ruta_archivo: Path,
        destino: Path,
        contrasena: str | None,
        ruta_clave: Path | None,
        progreso: CallbackProgreso | None = None,
    ) -> Path:
        """Descifra el formato anterior (Fernet sobre el fichero entero, que se lee de una vez).

        Como se descifra de una vez, ``progreso`` solo se llama al terminar.
        """
        datos = ruta_archivo.read_bytes()
        if contrasena:
            salt = datos[5 : 5 + TAMANO_SAL]
            token = datos[5 + TAMANO_SAL :]
            clave = base64.urlsafe_b64encode(
                Cifrador._derivar_clave_de_contrasena(contrasena, salt)
            )
        else:
            assert ruta_clave is not None  # comprobado por descifrar_archivo
            token = datos[5:]
            clave = Cifrador._leer_archivo_clave(ruta_clave)

        try:
            fernet = Fernet(clave)
        except (binascii.Error, ValueError):
            raise ErrorArchivoClaveInvalido(
                f"El fichero no contiene una clave válida: {ruta_clave}"
            ) from None
        try:
            descifrado = fernet.decrypt(token)
        except InvalidToken:
            raise ErrorContrasenaInvalida(
                f"Contraseña o clave incorrecta para: {ruta_archivo.name}"
            ) from None

        with _escritura_atomica(destino, _permisos_como(ruta_archivo)) as (salida, final):
            salida.write(descifrado)
        if progreso is not None:
            progreso(len(datos), len(datos))
        return final[0]

    # ---------------------------------------------------------------- carpetas

    # ------------------------------------------------- por acción (cifrar o descifrar)

    @staticmethod
    def ficheros_de_carpeta(
        ruta_carpeta: Path, accion: AccionCifrado, recursivo: bool = True
    ) -> list[Path]:
        """Ficheros de la carpeta que procesaría ``accion``.

        Es la única regla de qué se cifra o descifra: la usan las operaciones de carpeta y
        también la consola y la GUI para contarlos antes de pedir confirmación. Al cifrar se
        saltan los .enc, las .key y los ocultos; al descifrar, solo cuentan los .enc.
        """
        return [
            f
            for f in Cifrador._listar_archivos(ruta_carpeta, recursivo)
            if _ES_PARA[AccionCifrado(accion)](f)
        ]

    @staticmethod
    def procesar_archivo(
        ruta_archivo: Path,
        accion: AccionCifrado,
        contrasena: str | None = None,
        ruta_clave: Path | None = None,
        borrar_original: bool = False,
        progreso: CallbackProgreso | None = None,
    ) -> ResultadoCifrado:
        """Cifra o descifra un fichero según ``accion``; ``progreso`` recibe los bytes hechos."""
        procesar = (
            Cifrador.cifrar_archivo
            if AccionCifrado(accion) is AccionCifrado.CIFRAR
            else Cifrador.descifrar_archivo
        )
        return procesar(
            ruta_archivo,
            contrasena=contrasena,
            ruta_clave=ruta_clave,
            borrar_original=borrar_original,
            progreso=progreso,
        )

    @staticmethod
    def procesar_carpeta(
        ruta_carpeta: Path,
        accion: AccionCifrado,
        contrasena: str | None = None,
        ruta_clave: Path | None = None,
        borrar_original: bool = False,
        recursivo: bool = True,
        progreso: CallbackProgreso | None = None,
    ) -> ResultadoLote:
        """Cifra o descifra los ficheros de la carpeta según ``accion``.

        Un fallo en un fichero no detiene los demás: queda en ``errores`` del resultado. Lanza
        ErrorCarpetaInvalida si la carpeta no existe.
        """
        if not ruta_carpeta.exists() or not ruta_carpeta.is_dir():
            raise ErrorCarpetaInvalida(f"La carpeta no existe: {ruta_carpeta}")

        Cifrador._validar_material_clave(contrasena, ruta_clave)

        resultado = ResultadoLote()
        ficheros = Cifrador.ficheros_de_carpeta(ruta_carpeta, accion, recursivo)
        total = len(ficheros)

        for indice, fichero in enumerate(ficheros, start=1):
            try:
                r = Cifrador.procesar_archivo(
                    fichero,
                    accion,
                    contrasena=contrasena,
                    ruta_clave=ruta_clave,
                    borrar_original=borrar_original,
                )
                resultado.resultados.append(r)
                resultado.exitosos += 1

            except (ErrorOrganizador, OSError) as e:
                logger.warning(f"No se pudo {accion} {fichero.name}: {e}")
                resultado.errores.append((fichero, e))
                resultado.fallidos += 1

            if progreso is not None:
                progreso(indice, total)

        return resultado

    @staticmethod
    def _listar_archivos(ruta_carpeta: Path, recursivo: bool) -> list[Path]:
        """Ficheros de la carpeta, con los de sus subcarpetas si ``recursivo``."""
        if recursivo:
            return [f for f in ruta_carpeta.rglob("*") if f.is_file()]
        return [f for f in ruta_carpeta.iterdir() if f.is_file()]


# Qué ficheros de una carpeta procesa cada acción.
_ES_PARA: dict[AccionCifrado, Callable[[Path], bool]] = {
    AccionCifrado.CIFRAR: lambda f: (
        f.suffix not in (SUFIJO_CLAVE, SUFIJO_CIFRADO) and not f.name.startswith(".")
    ),
    AccionCifrado.DESCIFRAR: lambda f: f.suffix == SUFIJO_CIFRADO,
}


@dataclass(frozen=True)
class _FormatoCifrado:
    """Cómo se descifra un fichero según el modo que indica su cabecera."""

    usa_contrasena: bool  # True: se descifra con contraseña; False: con un fichero .key
    # (fichero, destino, contraseña, clave, progreso) -> ruta escrita
    descifrar: Callable[[Path, Path, str | None, Path | None, CallbackProgreso | None], Path]


# Formatos que se saben descifrar: uno nuevo es una entrada más, sin tocar descifrar_archivo.
_FORMATOS: dict[bytes, _FormatoCifrado] = {
    MODO_CONTRASENA: _FormatoCifrado(True, Cifrador._descifrar_por_bloques),
    MODO_ARCHIVO_CLAVE: _FormatoCifrado(False, Cifrador._descifrar_por_bloques),
    MODO_CONTRASENA_FERNET: _FormatoCifrado(True, Cifrador._descifrar_fernet_antiguo),
    MODO_ARCHIVO_CLAVE_FERNET: _FormatoCifrado(False, Cifrador._descifrar_fernet_antiguo),
}
