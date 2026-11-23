"""Opciones de la línea de comandos (argparse) y configuración del log de la consola."""

from __future__ import annotations

import argparse
import logging
import os
import textwrap
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler

from file_organizer.clasificador import (
    CRITERIO_DEFECTO,
    DESCRIPCIONES_AGRUPACION,
    EXPLICACION_FECHA,
    CriterioAgrupacion,
)
from file_organizer.cli.salida import ManejadorConsola
from file_organizer.duplicados import (
    ACCION_DUPLICADOS_DEFECTO,
    CARPETA_DUPLICADOS,
    AccionDuplicados,
)
from file_organizer.integridad import (
    DESCRIPCIONES_INTEGRIDAD,
    MODO_INTEGRIDAD_DEFECTO,
    ModoIntegridad,
)
from file_organizer.organizador import NOMBRE_ARCHIVO_LOG
from file_organizer.utilidades import PERMISOS_PRIVADOS, FiltroConsola

# Rotación de logs: hasta 1 MB por fichero y 3 copias de respaldo.
TAMANO_MAXIMO_LOG = 1_000_000
COPIAS_LOG = 3


class ManejadorLogPrivado(RotatingFileHandler):
    """El log rotativo, creado de forma que solo su dueño pueda leerlo.

    Lleva las rutas y los nombres de los ficheros organizados: igual que el historial, no debe
    poder leerlo otro usuario del equipo. Las copias rotadas se renombran y conservan los
    permisos. Un log que ya existiera se queda con los suyos.
    """

    def _open(self) -> TextIOWrapper:
        """Abre el log para añadir al final; si hay que crearlo, ya con permisos privados."""
        banderas = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        descriptor = os.open(self.baseFilename, banderas, PERMISOS_PRIVADOS)
        return open(descriptor, "a", encoding=self.encoding, errors=self.errors)


def configurar_registro(detallado: bool = False) -> None:
    """Envía el log a la consola y a ``file-organizer.log``.

    El fichero se rota al pasar de 1 MB y se guardan 3 copias (``.1``, ``.2``, ``.3``). Con
    ``detallado`` se registra también lo de nivel DEBUG.
    """
    # En la consola: sin lo que ya se imprimió en color, y sin romper la barra de progreso.
    consola = ManejadorConsola()
    consola.addFilter(FiltroConsola())
    rotativo = ManejadorLogPrivado(
        NOMBRE_ARCHIVO_LOG,
        maxBytes=TAMANO_MAXIMO_LOG,
        backupCount=COPIAS_LOG,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.DEBUG if detallado else logging.INFO,
        format="%(message)s",
        handlers=[consola, rotativo],
        force=True,
    )


def construir_parser() -> argparse.ArgumentParser:
    """Parser con todas las opciones de la consola."""
    parser = argparse.ArgumentParser(
        prog="file-organizer",
        description=(
            "Organiza automáticamente los ficheros de una carpeta por tipo, por fecha o por los dos."
        ),
        epilog=(
            "Tres formas de usarlo:\n"
            "  Ventana:          file-organizer-gui\n"
            "  Menú por consola: file-organizer (sin opciones)\n"
            "  Comandos:         file-organizer --carpeta RUTA [opciones]\n"
            "Sin instalarlo, desde la carpeta del proyecto: python gui.py y python main.py.\n"
            "\nEjemplos:\n"
            "  file-organizer-gui\n"
            "  file-organizer\n"
            "  file-organizer --carpeta C:/Descargas --simular\n"
            "  file-organizer --carpeta C:/Fotos --agrupar fecha\n"
            "  file-organizer --carpeta C:/Descargas --agrupar tipo-fecha --simular\n"
            "  file-organizer --carpeta C:/Descargas --duplicados mover\n"
            '  file-organizer --carpeta C:/Descargas --agregar-regla "factura*" "Facturas/{año}"\n'
            "  file-organizer --carpeta C:/Descargas --reglas\n"
            "  file-organizer --carpeta C:/Descargas --deshacer\n"
            "  file-organizer --carpeta C:/Descargas --estadisticas\n"
            "  file-organizer --carpeta C:/Descargas --vigilar\n"
            "  file-organizer --carpeta C:/Descargas --historial\n"
            "  file-organizer --cifrar secreto.pdf --contrasena\n"
            "  file-organizer --descifrar secreto.pdf.enc --contrasena\n"
            "  file-organizer --carpeta C:/Descargas --integridad rapida\n"
            "  file-organizer --exportar-categorias mis_categorias.json\n"
            "  file-organizer --carpeta C:/Descargas --config mis_categorias.json\n"
            "\nCómo agrupar (--agrupar):\n"
            + "".join(
                f"  {criterio.value:<12}{DESCRIPCIONES_AGRUPACION[criterio].ejemplo}\n"
                for criterio in CriterioAgrupacion
            )
            + "".join(f"  {linea}\n" for linea in textwrap.wrap(EXPLICACION_FECHA, 76))
            + "\nReglas de la carpeta (--reglas, --agregar-regla, --quitar-regla):\n"
            "  Cada carpeta puede tener sus reglas: los ficheros cuyo nombre encaja con el\n"
            "  PATRON (*.pdf, factura*, IMG_????.jpg) van a la CARPETA indicada, dentro del\n"
            "  destino, en lugar de a la de su tipo; con --agrupar fecha o tipo-fecha, debajo\n"
            "  va AAAA/MM, salvo que la carpeta lleve {año} o {mes}. Gana la primera que\n"
            "  coincide. Se guardan en la propia carpeta, en .file-organizer-reglas.json, y se\n"
            "  aplican siempre al organizar y vigilar.\n"
            + "\nModos de integridad (--integridad):\n"
            + "".join(
                f"  {modo.value:<9} {DESCRIPCIONES_INTEGRIDAD[modo].titulo}\n"
                + "".join(
                    f"            {linea}\n"
                    for linea in textwrap.wrap(DESCRIPCIONES_INTEGRIDAD[modo].detalle, 70)
                )
                for modo in ModoIntegridad
            )
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--carpeta", "-f", type=str, default=None, metavar="RUTA", help="Carpeta a organizar"
    )

    parser.add_argument(
        "--destino",
        "-d",
        type=str,
        default=None,
        metavar="RUTA",
        help="Carpeta destino donde crear las subcarpetas (por defecto: la misma que --carpeta)",
    )

    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        metavar="FICHERO",
        help="Ruta al fichero JSON con categorías personalizadas",
    )

    parser.add_argument(
        "--agrupar",
        "-a",
        choices=[criterio.value for criterio in CriterioAgrupacion],
        default=CRITERIO_DEFECTO.value,
        metavar="CRITERIO",
        help=(
            "Cómo agrupar los ficheros: "
            + "; ".join(
                f"{criterio.value} = {DESCRIPCIONES_AGRUPACION[criterio].ejemplo}"
                for criterio in CriterioAgrupacion
            )
            + f" (por defecto: {CRITERIO_DEFECTO.value}). La fecha es la de los metadatos si los"
            " hay. Ver 'Cómo agrupar' al final de esta ayuda"
        ),
    )

    parser.add_argument(
        "--duplicados",
        choices=[accion.value for accion in AccionDuplicados],
        default=ACCION_DUPLICADOS_DEFECTO.value,
        metavar="ACCION",
        help=(
            "Qué hacer con los ficheros iguales (mismo contenido) a otro ya organizado o de la "
            "misma tanda: avisar = se organizan como los demás y se listan (por defecto); "
            f"mover = se apartan en {CARPETA_DUPLICADOS}/. Nunca se borra ninguno"
        ),
    )

    parser.add_argument(
        "--exportar-categorias",
        type=str,
        default=None,
        metavar="FICHERO",
        help=(
            "Guarda las categorías en uso (las de --config o las de por defecto) en un JSON "
            "que puedes editar y usar después con --config. No sobrescribe ficheros existentes"
        ),
    )

    parser.add_argument(
        "--estadisticas",
        action="store_true",
        help="Muestra las estadísticas detalladas del último paso del historial",
    )

    parser.add_argument(
        "--backup",
        action="store_true",
        help="Crea un .zip de respaldo de la carpeta antes de organizar",
    )

    parser.add_argument(
        "--integridad",
        choices=[modo.value for modo in ModoIntegridad],
        default=None,
        metavar="MODO",
        help=(
            "Cómo comprobar, al deshacer, si un fichero cambió: "
            + "; ".join(
                f"{modo.value} = {DESCRIPCIONES_INTEGRIDAD[modo].resumen.rstrip('.')}"
                for modo in ModoIntegridad
            )
            + f". Por defecto, el último elegido o '{MODO_INTEGRIDAD_DEFECTO.value}'."
            + " Ver 'Modos de integridad' al final de esta ayuda."
        ),
    )

    parser.add_argument(
        "--detallado", "-v", action="store_true", help="Muestra mensajes de depuración detallados"
    )

    grupo_cifrado = parser.add_argument_group("Cifrado")

    grupo_cifrado.add_argument(
        "--cifrar", type=str, default=None, metavar="RUTA", help="Fichero o carpeta a cifrar"
    )

    grupo_cifrado.add_argument(
        "--descifrar", type=str, default=None, metavar="RUTA", help="Fichero o carpeta a descifrar"
    )

    grupo_material_clave = grupo_cifrado.add_mutually_exclusive_group()

    grupo_material_clave.add_argument(
        "--contrasena", action="store_true", help="Solicita la contraseña de forma oculta"
    )

    grupo_material_clave.add_argument(
        "--archivo-clave",
        type=str,
        default=None,
        metavar="FICHERO",
        help="Ruta a un fichero .key para cifrar/descifrar",
    )

    grupo_cifrado.add_argument(
        "--generar-clave",
        type=str,
        default=None,
        metavar="FICHERO",
        help="Genera una clave nueva y la guarda en la ruta indicada",
    )

    grupo_cifrado.add_argument(
        "--borrar-original",
        action="store_true",
        help="Borra el fichero original tras cifrar/descifrar",
    )

    grupo_cifrado.add_argument(
        "--recursivo",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Al cifrar/descifrar una carpeta, incluye subcarpetas (por defecto: sí; usa --no-recursivo para desactivarlo)",
    )

    grupo_modo = parser.add_mutually_exclusive_group()

    grupo_modo.add_argument(
        "--simular", action="store_true", help="Simula la organización sin mover ningún fichero"
    )

    grupo_modo.add_argument(
        "--deshacer",
        action="store_true",
        help=(
            "Deshace el último paso del historial (la última organización o sesión de "
            "vigilancia); repítelo para deshacer los anteriores, de uno en uno"
        ),
    )

    grupo_modo.add_argument(
        "--historial", action="store_true", help="Muestra el historial de movimientos guardados"
    )

    grupo_modo.add_argument(
        "--limpiar-historial",
        action="store_true",
        help=(
            "Borra el historial: los ficheros se quedan donde están, pero ya no se podrá "
            "deshacer lo organizado"
        ),
    )

    grupo_modo.add_argument(
        "--reglas",
        action="store_true",
        help="Muestra las reglas de la carpeta, por orden de prioridad",
    )

    grupo_modo.add_argument(
        "--agregar-regla",
        nargs=2,
        metavar=("PATRON", "CARPETA"),
        default=None,
        help=(
            "Añade una regla a la carpeta: los ficheros cuyo nombre encaje con PATRON "
            '(p. ej. "*.pdf" o "factura*") irán a CARPETA, dentro del destino '
            '(p. ej. "Documentos" o "Facturas/{año}")'
        ),
    )

    grupo_modo.add_argument(
        "--quitar-regla",
        type=int,
        default=None,
        metavar="N",
        help="Quita la regla número N de la carpeta (ver --reglas)",
    )

    grupo_modo.add_argument(
        "--vigilar",
        "-w",
        action="store_true",
        help="Vigila la carpeta en tiempo real y organiza automáticamente",
    )

    parser.add_argument(
        "--vigilar-recursivo",
        action="store_true",
        help="Con --vigilar, vigila también las subcarpetas de forma recursiva",
    )

    return parser
