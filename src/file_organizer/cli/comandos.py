"""Cada operación de la consola: organizar, simular, deshacer, vigilar, historial, cifrar...

Las usan la línea de comandos (aplicacion.py) y el menú (interactivo.py). Devuelven el código de
salida: 0 si va bien, 1 si hay un error.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from colorama import Fore, Style

from file_organizer.cifrador import AccionCifrado, Cifrador
from file_organizer.clasificador import CRITERIO_DEFECTO, Clasificador, CriterioAgrupacion
from file_organizer.cli.salida import (
    barra_progreso_consola,
    confirmar,
    imprimir_aviso,
    imprimir_error,
    imprimir_excepcion,
    imprimir_exito,
    imprimir_info,
    imprimir_reglas,
    imprimir_resumen,
    notificador_vigilante,
)
from file_organizer.cli.validadores import validar_existente, validar_fichero_nuevo
from file_organizer.config_usuario import ConfiguracionUsuario
from file_organizer.duplicados import ACCION_DUPLICADOS_DEFECTO, AccionDuplicados
from file_organizer.excepciones import (
    ErrorArchivoClaveYaExiste,
    ErrorCarpetaEnUso,
    ErrorConfiguracionNoEncontrada,
    ErrorFormatoConfiguracion,
    ErrorHistorialCorrupto,
    ErrorHistorialNoEncontrado,
    ErrorOrganizador,
    ErrorOrigenNoEncontrado,
    ErrorReglas,
)
from file_organizer.historial import RegistroMovimiento
from file_organizer.integridad import MODO_INTEGRIDAD_DEFECTO, ModoIntegridad
from file_organizer.mover_archivos import ResultadoDeshacer
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.reglas import Regla, ReglasCarpeta
from file_organizer.rutas import preparar_ruta
from file_organizer.vigilante import VigilanteCarpeta

logger = logging.getLogger(__name__)


def _recordar_carpeta(carpeta: Path, destino: str | None = None, config: str | None = None) -> None:
    """Guarda en las preferencias la carpeta usada y, si se indicaron, el destino y las categorías.

    Lo que no se indica se conserva: organizar sin ``--config`` no borra el último fichero de
    categorías que el menú ofrece reutilizar.
    """
    # Rutas completas, para reutilizarlas aunque se trabaje desde otra carpeta.
    valores: dict[str, object] = {"ultima_carpeta": str(Path(carpeta).resolve())}
    if destino:
        valores["ultimo_destino"] = str(Path(destino).resolve())
    if config:
        valores["ultima_config"] = str(Path(config).resolve())
    try:
        cfg = ConfiguracionUsuario().cargar()
        cfg.actualizar(**valores)
        cfg.guardar()
    except OSError as e:
        logger.debug(f"No se pudo guardar la configuración persistente: {e}")


def crear_organizador(
    carpeta: Path,
    simular: bool = False,
    destino: str | None = None,
    config: str | None = None,
    backup: bool = False,
    modo_integridad: ModoIntegridad = MODO_INTEGRIDAD_DEFECTO,
    agrupar: CriterioAgrupacion = CRITERIO_DEFECTO,
    duplicados: AccionDuplicados = ACCION_DUPLICADOS_DEFECTO,
) -> OrganizadorArchivos | None:
    """Organizador para la carpeta, o None (tras avisar) si el fichero de categorías no sirve."""
    try:
        return OrganizadorArchivos(
            carpeta_origen=carpeta,
            modo_simulacion=simular,
            carpeta_destino=destino,
            ruta_config=config,
            backup=backup,
            modo_integridad=modo_integridad,
            agrupar=agrupar,
            duplicados=duplicados,
        )
    except (ErrorConfiguracionNoEncontrada, ErrorFormatoConfiguracion) as e:
        imprimir_excepcion(e, "Categorías")
        return None


def ejecutar_organizar(org: OrganizadorArchivos) -> int:
    """Organiza la carpeta, recuerda la elección y muestra los avisos y el resumen."""
    try:
        org.ejecutar(progreso=barra_progreso_consola)
    except (ErrorOrigenNoEncontrado, ErrorCarpetaEnUso) as e:
        imprimir_excepcion(e)
        return 1
    except ErrorReglas as e:
        imprimir_excepcion(e, "Reglas")
        return 1

    ruta_config = org.ruta_config
    _recordar_carpeta(org.carpeta_origen, config=str(ruta_config) if ruta_config else None)
    for aviso in org.avisos:
        imprimir_aviso(aviso)
    imprimir_resumen(org)
    return 0


@dataclass(frozen=True)
class Indicaciones:
    """Lo que se le dice al usuario para seguir, que cambia según use los comandos o el menú."""

    # Cómo organizar de verdad después de simular.
    aplicar_simulacion: str
    # Qué hacer si se organizó en otra carpeta de destino (el historial se guarda allí).
    otra_carpeta_destino: str


INDICACIONES_COMANDOS = Indicaciones(
    aplicar_simulacion="Para hacerlo de verdad, ejecuta lo mismo sin --simular.",
    otra_carpeta_destino=(
        "Si la organizaste en otra carpeta de destino, indica también esa carpeta con --destino."
    ),
)


def ejecutar_simulacion(
    org: OrganizadorArchivos, indicaciones: Indicaciones = INDICACIONES_COMANDOS
) -> int:
    """Muestra qué se movería y adónde, sin mover nada."""
    print()
    try:
        org.ejecutar()
    except ErrorOrigenNoEncontrado as e:
        imprimir_excepcion(e)
        return 1
    except ErrorReglas as e:
        imprimir_excepcion(e, "Reglas")
        return 1

    imprimir_resumen(org)
    print(
        f"\n {Fore.YELLOW}[SIMULACIÓN]{Style.RESET_ALL} No se ha movido nada. "
        f"{indicaciones.aplicar_simulacion}"
    )
    return 0


def ejecutar_deshacer(
    org: OrganizadorArchivos, indicaciones: Indicaciones = INDICACIONES_COMANDOS
) -> int:
    """Deshace el último paso y muestra qué se restauró, con qué nombre y qué paso queda."""
    try:
        resultado = org.deshacer(progreso=barra_progreso_consola)
    except ErrorHistorialCorrupto as e:
        imprimir_excepcion(e, "Historial")
        return 1
    except ErrorCarpetaEnUso as e:
        imprimir_excepcion(e)
        return 1

    if resultado is None:
        imprimir_info("No hay historial guardado para deshacer")
        _imprimir_pista(indicaciones.otra_carpeta_destino)
        return 0

    if resultado.exitosos == 0 and resultado.fallidos == 0:
        imprimir_info("No queda nada por deshacer en el historial")
        return 0

    print(
        f"\n {Fore.GREEN}Restaurados: {resultado.exitosos}{Style.RESET_ALL} | "
        f"{Fore.RED}Fallidos: {resultado.fallidos}{Style.RESET_ALL}"
    )
    for registro, restaurado in resultado.renombrados:
        print(
            f"  {Fore.YELLOW}!{Style.RESET_ALL} {registro.origen.name} ya existía: "
            f"restaurado como {Fore.WHITE}{restaurado.name}{Style.RESET_ALL}"
        )
    for registro, error in resultado.errores:
        print(f"  {Fore.RED}✗ {registro.destino.name}: {error}{Style.RESET_ALL}")
    _imprimir_siguiente_paso(resultado)

    return 0


def _imprimir_pista(texto: str) -> None:
    """Una indicación atenuada bajo el mensaje anterior."""
    print(f" {Style.DIM}{texto}{Style.RESET_ALL}")


def _imprimir_siguiente_paso(resultado: ResultadoDeshacer) -> None:
    """Dice qué se deshará la próxima vez, si queda algún paso."""
    if resultado.siguiente is None:
        return
    print(
        f"\n {Style.DIM}Quedan {resultado.pasos_restantes} paso(s) en el historial: el próximo "
        f"que se deshará es la {resultado.siguiente.descripcion()} "
        f"({len(resultado.siguiente.registros)} fichero(s)).{Style.RESET_ALL}"
    )


def ejecutar_vigilar(
    carpeta: Path,
    config: str | None = None,
    destino: str | None = None,
    recursivo: bool = False,
    modo_integridad: ModoIntegridad = MODO_INTEGRIDAD_DEFECTO,
    agrupar: CriterioAgrupacion = CRITERIO_DEFECTO,
    duplicados: AccionDuplicados = ACCION_DUPLICADOS_DEFECTO,
) -> int:
    """Vigila la carpeta (y sus subcarpetas, si ``recursivo``) hasta Ctrl+C."""
    try:
        vigilante = VigilanteCarpeta(
            carpeta=carpeta,
            ruta_config=config,
            carpeta_destino=Path(destino) if destino else None,
            recursivo=recursivo,
            modo_integridad=modo_integridad,
            agrupar=agrupar,
            duplicados=duplicados,
            notificador=notificador_vigilante,
        )
    except (ErrorConfiguracionNoEncontrada, ErrorFormatoConfiguracion) as e:
        imprimir_excepcion(e, "Categorías")
        return 1
    _recordar_carpeta(carpeta, destino, config)
    try:
        vigilante.iniciar()
    except ErrorCarpetaEnUso as e:
        imprimir_excepcion(e)
        return 1
    except ErrorReglas as e:
        imprimir_excepcion(e, "Reglas")
        return 1
    return 0


# Cómo añadir la primera regla, desde la línea de comandos.
PISTA_SIN_REGLAS_CLI = (
    'Añade una con --agregar-regla PATRON CARPETA, por ejemplo: --agregar-regla "*.pdf" Documentos'
)


def ejecutar_reglas(
    carpeta: Path, agregar: list[str] | None = None, quitar: int | None = None
) -> int:
    """Añade o quita una regla de la carpeta, si se pide, y muestra cómo quedan."""
    reglas = ReglasCarpeta(carpeta)
    try:
        if agregar:
            regla = Regla.crear(*agregar)
            reglas.agregar(regla)
            imprimir_exito(f"Regla añadida: {regla.texto()}")
        elif quitar is not None:
            imprimir_exito(f"Regla quitada: {reglas.quitar(quitar).texto()}")
        else:
            reglas.cargar()
    except ErrorReglas as e:
        imprimir_excepcion(e, "Reglas")
        return 1
    except OSError as e:
        imprimir_excepcion(e, "No se pudieron guardar las reglas")
        return 1
    imprimir_reglas(reglas, PISTA_SIN_REGLAS_CLI)
    return 0


def _cargar_historial(
    org: OrganizadorArchivos, mensaje_vacio: str, indicaciones: Indicaciones
) -> int | None:
    """Carga el historial; devuelve el código de salida si no hay nada que mostrar, o None si lo
    hay.
    """
    try:
        org.cargar_historial()
    except ErrorHistorialNoEncontrado:
        imprimir_info(f"{mensaje_vacio}")
        _imprimir_pista(indicaciones.otra_carpeta_destino)
        return 0
    except ErrorHistorialCorrupto as e:
        imprimir_excepcion(e, "Historial")
        return 1
    return None


def ejecutar_historial(
    org: OrganizadorArchivos, indicaciones: Indicaciones = INDICACIONES_COMANDOS
) -> int:
    """Muestra los pasos del historial, del que se deshará primero al más antiguo, por categoría."""
    codigo = _cargar_historial(org, "No hay historial guardado", indicaciones)
    if codigo is not None:
        return codigo
    operaciones = org.operaciones()

    if not operaciones:
        imprimir_info("No hay historial guardado")
        return 0

    print(f"\n {Fore.CYAN}{Style.BRIGHT}{'=' * 55}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}HISTORIAL DE MOVIMIENTOS{Style.RESET_ALL}")
    print(f" {Fore.CYAN}{Style.BRIGHT}{'=' * 55}{Style.RESET_ALL}")
    print(
        f"  {Style.DIM}Deshacer revierte un paso cada vez, empezando por el 1 (el más reciente)"
        f"{Style.RESET_ALL}"
    )

    # Del más reciente (el que se deshará primero) al más antiguo.
    for numero, operacion in enumerate(reversed(operaciones), start=1):
        print(
            f"\n {Fore.CYAN}{Style.BRIGHT}PASO {numero}{Style.RESET_ALL} · "
            f"{operacion.descripcion()} ({len(operacion.registros)} fichero(s))"
        )
        por_categoria: dict[str, list[RegistroMovimiento]] = defaultdict(list)
        for registro in operacion.registros:
            por_categoria[registro.categoria].append(registro)
        for categoria, registros in sorted(por_categoria.items()):
            print(f"   {Fore.GREEN}📁 {categoria}/{Style.RESET_ALL} ({len(registros)} fichero(s))")
            for r in registros:
                print(f"       {Fore.WHITE}{r.origen.name}{Style.RESET_ALL}")
                print(
                    f"       {Style.DIM}└─ movido el {r.fecha:%d/%m/%Y a las %H:%M:%S}"
                    f"{Style.RESET_ALL}"
                )

    total = sum(len(o.registros) for o in operaciones)
    print(f"\n {Fore.CYAN}{Style.BRIGHT}{'=' * 55}{Style.RESET_ALL}")
    print(
        f"  {Fore.YELLOW}Total: {total} movimiento(s) en {len(operaciones)} paso(s)"
        f"{Style.RESET_ALL}"
    )
    print(f" {Fore.CYAN}{Style.BRIGHT}{'=' * 55}{Style.RESET_ALL}")
    return 0


def ejecutar_limpiar_historial(
    org: OrganizadorArchivos, indicaciones: Indicaciones = INDICACIONES_COMANDOS
) -> int:
    """Pide confirmación y borra el historial (los ficheros no se mueven)."""
    codigo = _cargar_historial(org, "No hay historial que limpiar", indicaciones)
    if codigo is not None:
        return codigo
    operaciones = org.operaciones()
    total = sum(len(o.registros) for o in operaciones)

    if not total:
        imprimir_info("No hay historial que limpiar")
        return 0

    print(
        f"\n {Fore.RED}[¡AVISO!]{Style.RESET_ALL} Se eliminará el historial de {Fore.YELLOW}{total}{Style.RESET_ALL} movimiento(s) en {len(operaciones)} paso(s)"
    )
    print(
        f"  {Style.DIM}Los ficheros se quedan donde están, pero ya no se podrá deshacer lo "
        f"organizado.{Style.RESET_ALL}"
    )
    if not confirmar(
        f"\n {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} ¿Deseas continuar? (s/n): "
    ):
        print(f"\n {Fore.YELLOW}Operación cancelada{Style.RESET_ALL}")
        return 0

    try:
        org.limpiar_historial()
    except ErrorCarpetaEnUso as e:
        imprimir_excepcion(e)
        return 1
    except (ErrorHistorialNoEncontrado, ErrorHistorialCorrupto) as e:
        imprimir_excepcion(e, "Historial")
        return 1
    print(f"\n {Fore.GREEN}Historial borrado. Los ficheros siguen donde estaban.{Style.RESET_ALL}")
    return 0


def ejecutar_estadisticas(
    org: OrganizadorArchivos, indicaciones: Indicaciones = INDICACIONES_COMANDOS
) -> int:
    """Muestra cuántos ficheros fueron a cada categoría en el último paso."""
    codigo = _cargar_historial(org, "No hay estadísticas guardadas.", indicaciones)
    if codigo is not None:
        return codigo
    if not org.estadisticas():
        imprimir_info("No hay estadísticas guardadas.")
        print(
            f" {Style.DIM}Organiza una carpeta primero para ver las estadísticas{Style.RESET_ALL}"
        )
        return 0

    operaciones = org.operaciones()
    print(
        f"\n {Style.DIM}Último paso del historial: {operaciones[-1].descripcion()}{Style.RESET_ALL}"
    )
    _imprimir_estadisticas_detalladas(org)
    return 0


def _imprimir_estadisticas_detalladas(org: OrganizadorArchivos) -> None:
    """Una barra por categoría con su número de ficheros y su porcentaje."""
    estadisticas = org.estadisticas()
    if not estadisticas:
        return

    print(f"\n {Fore.CYAN}{Style.BRIGHT}--- ESTADÍSTICAS DETALLADAS ---{Style.RESET_ALL}")
    total = sum(estadisticas.values())
    # Al agrupar por fecha los nombres son más largos («Imagenes/2024/03»).
    ancho = max(15, *(len(categoria) for categoria in estadisticas))

    for categoria, cantidad in sorted(estadisticas.items(), key=lambda x: x[1], reverse=True):
        porcentaje = (cantidad / total) * 100
        barra = "█" * int(porcentaje / 5)
        print(
            f" {Fore.GREEN}{categoria:<{ancho}}{Style.RESET_ALL} "
            f"{Fore.CYAN}{barra:<20}{Style.RESET_ALL} "
            f"{Fore.YELLOW}{cantidad:>3}{Style.RESET_ALL} "
            f"fichero(s) ({porcentaje:.1f}%)"
        )

    print(
        f" {Fore.WHITE}{Style.BRIGHT}{'TOTAL':<{ancho}}{Style.RESET_ALL} "
        f"{'':20} "
        f"{Fore.YELLOW}{Style.BRIGHT}{total:>3}{Style.RESET_ALL} fichero(s)"
    )


def ejecutar_generar_clave(texto_ruta_clave: str) -> int:
    """Genera una clave de cifrado en la ruta indicada; si ya existe, no la toca."""
    ruta = validar_fichero_nuevo(
        texto_ruta_clave,
        vacia="Indica dónde guardar la clave nueva, por ejemplo mi_clave.key",
    )
    if ruta is None:
        return 1
    try:
        ruta_clave = Cifrador.generar_clave(ruta)
    except ErrorArchivoClaveYaExiste as e:
        imprimir_excepcion(e)
        return 1
    print(
        f"\n {Fore.GREEN}✓{Style.RESET_ALL} Clave generada: {Fore.WHITE}{ruta_clave}{Style.RESET_ALL}"
    )
    print(
        f" {Fore.RED}{Style.BRIGHT}[IMPORTANTE]{Style.RESET_ALL} Guarda este fichero en un lugar seguro."
    )
    print(
        f" {Style.DIM}Si lo pierdes, no podrás recuperar los ficheros cifrados con él.{Style.RESET_ALL}"
    )
    return 0


def ejecutar_exportar_categorias(
    texto_destino: str, config: str | None = None, sobrescribir: bool = False
) -> int:
    """Exporta a un JSON las categorías en uso (las de ``config`` o las de por defecto).

    Sirve de plantilla para ``--config``. No sobrescribe un fichero existente salvo con
    ``sobrescribir``.
    """
    ruta_destino = preparar_ruta(texto_destino)
    if ruta_destino is None:
        imprimir_error("Indica dónde guardar las categorías, por ejemplo categorias.json")
        return 1
    ruta_config = preparar_ruta(config)
    try:
        clasificador = Clasificador(ruta_config=ruta_config)
    except (ErrorConfiguracionNoEncontrada, ErrorFormatoConfiguracion) as e:
        imprimir_excepcion(e, "Categorías")
        return 1

    try:
        ruta = clasificador.exportar_categorias(ruta_destino, sobrescribir=sobrescribir)
    except FileExistsError as e:
        imprimir_error(f"{e}")
        print(
            f"    {Style.DIM}Elige otro nombre o borra el fichero antes de exportar.{Style.RESET_ALL}"
        )
        return 1
    except OSError as e:
        imprimir_error(f"No se pudieron exportar las categorías: {e}")
        return 1

    cantidad = len(clasificador.obtener_categorias())
    origen = f"de {clasificador.ruta_config}" if clasificador.ruta_config else "por defecto"
    print(
        f"\n {Fore.GREEN}✓{Style.RESET_ALL} {cantidad} categorías ({origen}) exportadas a "
        f"{Fore.WHITE}{ruta.resolve()}{Style.RESET_ALL}"
    )
    print(
        f" {Style.DIM}Edítalo y úsalo con: file-organizer --carpeta RUTA --config "
        f'"{ruta.resolve()}"{Style.RESET_ALL}'
    )
    return 0


def _contar_archivos_afectados(objetivo: Path, recursivo: bool, accion: AccionCifrado) -> int:
    """Ficheros que cifraría o descifraría la operación (1 si es un fichero suelto)."""
    if objetivo.is_file():
        return 1
    return len(Cifrador.ficheros_de_carpeta(objetivo, accion, recursivo))


def _confirmar_operacion_masiva(cantidad: int, accion: str) -> bool:
    """Pide confirmación si la operación afecta a varios ficheros; False si no hay ninguno."""
    if cantidad == 0:
        imprimir_info(f"No se encontraron ficheros para {accion}")
        return False

    if cantidad == 1:
        return True

    imprimir_aviso(
        f"Se van a {accion} {Fore.CYAN}{Style.BRIGHT}{cantidad}{Style.RESET_ALL} ficheros"
    )
    return confirmar(f" {Fore.YELLOW}¿Deseas continuar? (s/n):{Style.RESET_ALL} ")


def pedir_contrasena_cli(confirmar: bool = False) -> str | None:
    """Pide la contraseña sin mostrarla (dos veces con ``confirmar``); None si se cancela o no
    coincide.
    """
    try:
        contrasena = getpass("Contraseña (no se mostrará en pantalla): ")
        if confirmar and contrasena != getpass("Repite la contraseña: "):
            imprimir_error("Las contraseñas no coinciden")
            return None
    except (EOFError, KeyboardInterrupt):
        imprimir_info("Operación cancelada")
        return None

    if not contrasena:
        imprimir_error("La contraseña no puede estar vacía")
        return None
    return contrasena


@dataclass(frozen=True)
class _TextosAccion:
    """Lo que la consola dice al cifrar o al descifrar."""

    carpeta: str  # "Cifrando carpeta:"
    hechos: str  # "Cifrados"
    etiqueta: str  # "Cifrar"


_TEXTOS_ACCION = {
    AccionCifrado.CIFRAR: _TextosAccion("Cifrando carpeta:", "Cifrados", "Cifrar"),
    AccionCifrado.DESCIFRAR: _TextosAccion("Descifrando carpeta:", "Descifrados", "Descifrar"),
}


def ejecutar_cifrar(
    texto_objetivo: str,
    contrasena: str | None,
    texto_archivo_clave: str | None,
    borrar_original: bool,
    recursivo: bool,
) -> int:
    """Cifra un fichero o una carpeta con la contraseña o el fichero de clave indicado."""
    return _ejecutar_accion_cifrado(
        AccionCifrado.CIFRAR,
        texto_objetivo,
        contrasena,
        texto_archivo_clave,
        borrar_original,
        recursivo,
    )


def ejecutar_descifrar(
    texto_objetivo: str,
    contrasena: str | None,
    texto_archivo_clave: str | None,
    borrar_original: bool,
    recursivo: bool,
) -> int:
    """Descifra un fichero o una carpeta con la contraseña o el fichero de clave indicado."""
    return _ejecutar_accion_cifrado(
        AccionCifrado.DESCIFRAR,
        texto_objetivo,
        contrasena,
        texto_archivo_clave,
        borrar_original,
        recursivo,
    )


def _ejecutar_accion_cifrado(
    accion: AccionCifrado,
    texto_objetivo: str,
    contrasena: str | None,
    texto_archivo_clave: str | None,
    borrar_original: bool,
    recursivo: bool,
) -> int:
    """Cifra o descifra un fichero o una carpeta; si son varios ficheros, pide confirmación."""
    textos = _TEXTOS_ACCION[accion]
    objetivo = validar_existente(
        texto_objetivo, vacia=f"Indica qué fichero o carpeta quieres {accion.value}"
    )
    if objetivo is None:
        return 1

    ruta_clave = preparar_ruta(texto_archivo_clave)
    if not contrasena and ruta_clave is None:
        imprimir_error("Debes indicar --contrasena o --archivo-clave")
        return 1

    cantidad = _contar_archivos_afectados(objetivo, recursivo, accion)
    if not _confirmar_operacion_masiva(cantidad, accion):
        print(f"\n {Fore.YELLOW}Operación cancelada{Style.RESET_ALL}")
        return 0

    if objetivo.is_dir():
        print(f"\n {Fore.CYAN}{textos.carpeta}{Style.RESET_ALL} {objetivo}\n")
        try:
            resultado = Cifrador.procesar_carpeta(
                objetivo,
                accion,
                contrasena=contrasena,
                ruta_clave=ruta_clave,
                borrar_original=borrar_original,
                recursivo=recursivo,
                progreso=barra_progreso_consola,
            )
        except ErrorOrganizador as e:
            imprimir_excepcion(e)
            return 1

        for r in resultado.resultados:
            imprimir_exito(r.mensaje)
        for fichero, error in resultado.errores:
            imprimir_excepcion(error, fichero.name)

        print(
            f"\n {Fore.GREEN}{textos.hechos}: {resultado.exitosos}{Style.RESET_ALL} | "
            f"{Fore.RED}Fallidos: {resultado.fallidos}{Style.RESET_ALL}"
        )
    else:
        try:
            r = Cifrador.procesar_archivo(
                objetivo,
                accion,
                contrasena=contrasena,
                ruta_clave=ruta_clave,
                borrar_original=borrar_original,
            )
            imprimir_exito(r.mensaje, textos.etiqueta)
        except ErrorOrganizador as e:
            imprimir_excepcion(e, textos.etiqueta)
            return 1

    return 0
