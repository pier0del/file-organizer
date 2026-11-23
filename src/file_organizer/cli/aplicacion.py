"""Punto de entrada de la consola (``file-organizer``, ``python -m file_organizer``, ``main.py``).

Sin argumentos abre el menú interactivo; con argumentos ejecuta el comando pedido (ver
comandos.py).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path

from colorama import Style

from file_organizer.cifrador import AccionCifrado
from file_organizer.clasificador import CriterioAgrupacion
from file_organizer.cli.argumentos import configurar_registro, construir_parser
from file_organizer.cli.comandos import (
    crear_organizador,
    ejecutar_cifrar,
    ejecutar_descifrar,
    ejecutar_deshacer,
    ejecutar_estadisticas,
    ejecutar_exportar_categorias,
    ejecutar_generar_clave,
    ejecutar_historial,
    ejecutar_limpiar_historial,
    ejecutar_organizar,
    ejecutar_reglas,
    ejecutar_simulacion,
    ejecutar_vigilar,
    pedir_contrasena_cli,
)
from file_organizer.cli.interactivo import ejecutar_menu
from file_organizer.cli.salida import imprimir_error
from file_organizer.cli.validadores import (
    validar_carpeta,
    validar_configuracion,
    validar_destino,
    validar_existente,
)
from file_organizer.config_usuario import ConfiguracionUsuario
from file_organizer.duplicados import AccionDuplicados
from file_organizer.integridad import ModoIntegridad
from file_organizer.rutas import preparar_ruta


def main() -> int:
    """Sin argumentos abre el menú interactivo; con argumentos, ejecuta el comando. Devuelve el
    código de salida.
    """
    if len(sys.argv) == 1:
        return ejecutar_menu()

    args = construir_parser().parse_args()
    configurar_registro(detallado=args.detallado)

    for se_pide, ejecutar in _COMANDOS_SIN_CARPETA:
        if se_pide(args):
            return ejecutar(args)
    return _ejecutar_comando_de_carpeta(args)


def _cifrar_o_descifrar(accion: AccionCifrado, args: argparse.Namespace) -> int:
    """--cifrar / --descifrar: comprueba la ruta, pide la contraseña si se indicó --contrasena y
    ejecuta la acción.

    La ruta se comprueba antes de pedir la contraseña, para no escribirla dos veces en balde.
    """
    objetivo = validar_existente(
        getattr(args, accion.value), vacia=f"Indica qué fichero o carpeta quieres {accion.value}"
    )
    if objetivo is None:
        return 1
    contrasena = None
    if args.contrasena:
        # Al cifrar se pide dos veces, para no cifrar con una contraseña mal escrita.
        contrasena = pedir_contrasena_cli(confirmar=accion is AccionCifrado.CIFRAR)
        if contrasena is None:
            return 1
    ejecutar = ejecutar_cifrar if accion is AccionCifrado.CIFRAR else ejecutar_descifrar
    return ejecutar(
        str(objetivo),
        contrasena,
        args.archivo_clave,
        args.borrar_original,
        args.recursivo,
    )


# Comandos que no necesitan --carpeta, por prioridad: se ejecuta el primero que se pida. Basta
# con que aparezca la opción, aunque sea con una ruta vacía («--cifrar ""»): así se explica que
# falta la ruta en vez de pedir --carpeta.
_COMANDOS_SIN_CARPETA: list[
    tuple[Callable[[argparse.Namespace], bool], Callable[[argparse.Namespace], int]]
] = [
    (
        lambda args: args.generar_clave is not None,
        lambda args: ejecutar_generar_clave(args.generar_clave),
    ),
    (
        lambda args: args.exportar_categorias is not None,
        lambda args: ejecutar_exportar_categorias(args.exportar_categorias, config=args.config),
    ),
    (lambda args: args.cifrar is not None, partial(_cifrar_o_descifrar, AccionCifrado.CIFRAR)),
    (
        lambda args: args.descifrar is not None,
        partial(_cifrar_o_descifrar, AccionCifrado.DESCIFRAR),
    ),
]


def _ejecutar_comando_de_carpeta(args: argparse.Namespace) -> int:
    """Comandos sobre --carpeta: organizar, simular, deshacer, historial, estadísticas, vigilar
    o ver y cambiar sus reglas.
    """
    carpeta = _carpeta_validada(args)
    if carpeta is None:
        return 1
    if args.reglas or args.agregar_regla or args.quitar_regla is not None:
        return ejecutar_reglas(carpeta, agregar=args.agregar_regla, quitar=args.quitar_regla)
    agrupar = CriterioAgrupacion(args.agrupar)
    duplicados = AccionDuplicados(args.duplicados)

    modo_integridad = (
        ModoIntegridad(args.integridad)
        if args.integridad
        else ConfiguracionUsuario().cargar().modo_integridad()
    )

    if args.vigilar:
        return ejecutar_vigilar(
            carpeta,
            config=args.config,
            destino=args.destino,
            recursivo=args.vigilar_recursivo,
            modo_integridad=modo_integridad,
            agrupar=agrupar,
            duplicados=duplicados,
        )

    # Consultas del historial: no organizan nada, así que no llevan backup ni integridad.
    consultas = (
        (args.historial, ejecutar_historial),
        (args.limpiar_historial, ejecutar_limpiar_historial),
        (args.estadisticas, ejecutar_estadisticas),
    )
    for pedida, consultar in consultas:
        if pedida:
            org = crear_organizador(carpeta, destino=args.destino, config=args.config)
            return 1 if org is None else consultar(org)

    org = crear_organizador(
        carpeta,
        simular=args.simular,
        destino=args.destino,
        config=args.config,
        backup=args.backup,
        modo_integridad=modo_integridad,
        agrupar=agrupar,
        duplicados=duplicados,
    )
    if org is None:
        return 1
    if args.deshacer:
        return ejecutar_deshacer(org)
    if args.simular:
        return ejecutar_simulacion(org)
    return ejecutar_organizar(org)


def _carpeta_validada(args: argparse.Namespace) -> Path | None:
    """La carpeta de --carpeta si ella, --config y --destino son válidos; si no, lo avisa y
    devuelve None.

    Deja en ``args.destino`` y ``args.config`` sus rutas ya preparadas (sin comillas, con ``~``
    cambiado por la carpeta personal), que son las que usan los comandos.
    """
    if preparar_ruta(args.carpeta) is None:
        imprimir_error(
            "Falta --carpeta: indica qué carpeta organizar, por ejemplo --carpeta C:/Descargas"
        )
        print(
            f"    {Style.DIM}Sin opciones, file-organizer abre el menú interactivo; "
            f"file-organizer --help muestra todas las opciones.{Style.RESET_ALL}"
        )
        return None

    carpeta = validar_carpeta(args.carpeta)
    if carpeta is None:
        return None
    # Una opción con la ruta vacía («--destino ""») es como no ponerla.
    if preparar_ruta(args.config) is None:
        args.config = None
    else:
        config = validar_configuracion(args.config)
        if config is None:
            return None
        args.config = str(config)
    if preparar_ruta(args.destino) is None:
        args.destino = None
    else:
        destino = validar_destino(args.destino)
        if destino is None:
            return None
        args.destino = str(destino)
    return carpeta
