"""Jerarquía de excepciones propias, para distinguir cada tipo de error controlado."""

from __future__ import annotations

from pathlib import Path


class ErrorOrganizador(Exception):
    """Base para los errores controlados de la aplicación."""


class ErrorRuta(ErrorOrganizador):
    """Una ruta que indicó el usuario no sirve.

    Guarda aparte el motivo y la ruta para que cada interfaz los muestre a su manera (la consola,
    por ejemplo, resalta la ruta).
    """

    def __init__(self, motivo: str, ruta: Path) -> None:
        super().__init__(f"{motivo}: {ruta}")
        self.motivo = motivo
        self.ruta = ruta


class ErrorConfiguracion(ErrorOrganizador):
    """Error relacionado con la configuración."""


class ErrorConfiguracionNoEncontrada(ErrorConfiguracion):
    """No se encontró el fichero de configuración."""


class ErrorFormatoConfiguracion(ErrorConfiguracion):
    """El fichero de configuración tiene un formato inválido."""


class ErrorReglas(ErrorFormatoConfiguracion):
    """Una regla de la carpeta, o su fichero de reglas, no es válida."""


class ErrorOrganizacion(ErrorOrganizador):
    """Error durante la organización de ficheros."""


class ErrorOrigenNoEncontrado(ErrorOrganizacion):
    """La carpeta de origen no existe."""


class ErrorMoverArchivo(ErrorOrganizacion):
    """No se pudo mover un fichero."""


class ErrorHistorial(ErrorOrganizacion):
    """Error relacionado con el historial de movimientos."""


class ErrorHistorialNoEncontrado(ErrorHistorial):
    """No existe el historial de movimientos."""


class ErrorHistorialCorrupto(ErrorHistorial):
    """El historial no se puede interpretar."""


class ErrorCarpetaEnUso(ErrorOrganizacion):
    """Otra operación (de este u otro proceso) está usando la carpeta y su historial."""


class ErrorCifrado(ErrorOrganizador):
    """Error relacionado con el cifrado."""


class ErrorCarpetaInvalida(ErrorCifrado):
    """La ruta indicada no es una carpeta válida o no existe."""


class ErrorFaltaClave(ErrorCifrado):
    """No se proporcionó una contraseña ni un fichero de clave."""


class ErrorClaveAmbigua(ErrorCifrado):
    """Se proporcionaron dos mecanismos de clave a la vez."""


class ErrorYaCifrado(ErrorCifrado):
    """El fichero ya está cifrado."""


class ErrorNoCifrado(ErrorCifrado):
    """El fichero no está cifrado."""


class ErrorArchivoClaveNoEncontrado(ErrorCifrado):
    """No se encontró el fichero de clave."""


class ErrorContrasenaInvalida(ErrorCifrado):
    """La contraseña o clave no permite descifrar el fichero."""


class ErrorArchivoCifradoCorrupto(ErrorCifrado):
    """El fichero cifrado tiene un formato inválido o está corrupto."""


class ErrorArchivoClaveInvalido(ErrorCifrado):
    """El fichero de clave existe pero su contenido no es una clave válida."""


class ErrorArchivoClaveYaExiste(ErrorCifrado):
    """Ya hay un fichero de clave en esa ruta y sobrescribirlo impediría descifrar lo cifrado con
    él.
    """
