"""Menú interactivo de la consola: se abre al ejecutar el programa sin argumentos.

Pregunta paso a paso lo que hace falta y recuerda las últimas respuestas (las comparte con la
GUI).
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TypeVar

from colorama import Fore, Style

from file_organizer import __version__
from file_organizer.clasificador import (
    CRITERIO_DEFECTO,
    DESCRIPCIONES_AGRUPACION,
    EXPLICACION_FECHA,
    NOMBRE_EXPORTACION_DEFECTO,
    CriterioAgrupacion,
    ruta_exportacion,
)
from file_organizer.cli.argumentos import configurar_registro
from file_organizer.cli.comandos import (
    Indicaciones,
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
    ejecutar_simulacion,
    ejecutar_vigilar,
    pedir_contrasena_cli,
)
from file_organizer.cli.salida import (
    imprimir_aviso,
    imprimir_error,
    imprimir_excepcion,
    imprimir_exito,
    imprimir_info,
    imprimir_reglas,
)
from file_organizer.cli.validadores import (
    validar_carpeta,
    validar_configuracion,
    validar_existente,
)
from file_organizer.config_usuario import ConfiguracionUsuario
from file_organizer.duplicados import CARPETA_DUPLICADOS, AccionDuplicados
from file_organizer.excepciones import ErrorReglas
from file_organizer.integridad import (
    DESCRIPCIONES_INTEGRIDAD,
    MODO_INTEGRIDAD_DEFECTO,
    ModoIntegridad,
)
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.reglas import Regla, ReglasCarpeta
from file_organizer.rutas import preparar_ruta


def _pedir_ruta_cifrado(accion: str) -> Path | None:
    """Pide la ruta del fichero o carpeta a cifrar o descifrar; None si no se escribe ninguna o
    no existe.
    """
    texto_ruta = input(
        f"\n {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
        f"{Fore.GREEN}{accion}{Style.RESET_ALL} > "
    )
    return validar_existente(
        texto_ruta, vacia=f"No has escrito qué fichero o carpeta quieres {accion}"
    )


def _pedir_metodo_clave() -> tuple[str | None, str | None]:
    """Pide el método (contraseña o fichero de clave) y el dato; ``(contraseña, ruta de clave)``."""
    print(f"\n {Fore.CYAN}Método de clave:{Style.RESET_ALL}")
    print(f"   {Fore.GREEN}[1]{Style.RESET_ALL} Contraseña")
    print(f"   {Fore.GREEN}[2]{Style.RESET_ALL} Fichero de clave (.key)")

    metodo = input(
        f"\n {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
        f"{Fore.GREEN}método{Style.RESET_ALL} > "
    ).strip()

    if metodo == "1":
        return pedir_contrasena_cli(), None

    elif metodo == "2":
        texto_archivo_clave = input(
            f" {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
            f"{Fore.GREEN}fichero de clave{Style.RESET_ALL} > "
        )
        ruta_clave = validar_existente(
            texto_archivo_clave, vacia="No has escrito qué fichero de clave usar"
        )
        return None, str(ruta_clave) if ruta_clave is not None else None

    imprimir_error("Opción no válida. Elige 1 (contraseña) o 2 (fichero de clave)")
    return None, None


def _pedir_borrar_original() -> bool:
    """Si se quiere borrar el original tras cifrar o descifrar (solo con «s»)."""
    respuesta = (
        input(
            f"\n {Fore.YELLOW}¿Borrar los ficheros originales al terminar? (s/n):{Style.RESET_ALL} "
        )
        .strip()
        .lower()
    )
    return respuesta == "s"


def _pausa() -> None:
    """Espera a que se pulse ENTER antes de volver a mostrar el menú."""
    input(f"\n {Fore.YELLOW}Pulsa ENTER para continuar...{Style.RESET_ALL}")


def ejecutar_menu_cifrado() -> None:
    """Submenú de cifrado: cifrar, descifrar o generar una clave nueva."""
    while True:
        print(f"\n {Fore.CYAN}{Style.BRIGHT}{'=' * 45}{Style.RESET_ALL}")
        print(f"   {Fore.CYAN}{Style.BRIGHT} CIFRADO / DESCIFRADO{Style.RESET_ALL}")
        print(f"   {Fore.CYAN}{Style.BRIGHT}{'=' * 45}{Style.RESET_ALL}\n")
        print(f"   {Fore.GREEN}[1]{Style.RESET_ALL} Cifrar fichero o carpeta")
        print(f"   {Fore.GREEN}[2]{Style.RESET_ALL} Descifrar fichero o carpeta")
        print(f"   {Fore.GREEN}[3]{Style.RESET_ALL} Generar clave nueva (.key)")
        print(f"   {Fore.RED}  [4]{Style.RESET_ALL} Volver al menú principal")

        opcion = input(
            f"\n {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~$ {Style.RESET_ALL}"
        ).strip()

        if opcion == "4":
            return

        if opcion not in ("1", "2", "3"):
            imprimir_error("Opción no válida. Elige 1, 2, 3 o 4")
            _pausa()
            continue

        if opcion == "3":
            texto_ruta_clave = input(
                f"\n {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
                f"{Fore.GREEN}dónde guardar la clave nueva (.key){Style.RESET_ALL} > "
            ).strip()
            ejecutar_generar_clave(texto_ruta_clave)
            _pausa()
            continue

        accion = "cifrar" if opcion == "1" else "descifrar"
        ruta = _pedir_ruta_cifrado(accion)
        if ruta is None:
            _pausa()
            continue

        contrasena, texto_archivo_clave = _pedir_metodo_clave()
        if not contrasena and not texto_archivo_clave:
            _pausa()
            continue

        borrar_original = _pedir_borrar_original()

        if opcion == "1":
            ejecutar_cifrar(str(ruta), contrasena, texto_archivo_clave, borrar_original, True)
        else:
            ejecutar_descifrar(str(ruta), contrasena, texto_archivo_clave, borrar_original, True)

        _pausa()


def ejecutar_menu_reglas() -> None:
    """Submenú de reglas: pide la carpeta y deja ver, añadir y quitar sus reglas."""
    carpeta = _pedir_carpeta()
    if carpeta is None:
        _pausa()
        return
    reglas = ReglasCarpeta(carpeta)
    while True:
        try:
            reglas.cargar()
        except ErrorReglas as e:
            imprimir_excepcion(e, "Reglas")
            _pausa()
            return
        imprimir_reglas(reglas, "Añade una con la opción 1, por ejemplo: *.pdf → Documentos")
        print(f"\n   {Fore.GREEN}[1]{Style.RESET_ALL} Añadir regla")
        print(f"   {Fore.GREEN}[2]{Style.RESET_ALL} Quitar regla")
        print(f"   {Fore.RED}[3]{Style.RESET_ALL} Volver al menú principal")
        opcion = input(
            f"\n {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~$ {Style.RESET_ALL}"
        ).strip()
        if opcion == "3":
            return
        if opcion == "1":
            _agregar_regla(reglas)
        elif opcion == "2":
            _quitar_regla(reglas)
        else:
            imprimir_error("Opción no válida. Elige 1, 2 o 3")


def _agregar_regla(reglas: ReglasCarpeta) -> None:
    """Pide el patrón y la carpeta de una regla nueva y la añade al final."""
    print(
        f"\n {Style.DIM}Patrón: nombre con comodines (* y ?), sin distinguir mayúsculas: "
        f"*.pdf, factura*, IMG_????.jpg{Style.RESET_ALL}"
    )
    patron = input(
        f" {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
        f"{Fore.GREEN}patrón{Style.RESET_ALL} > "
    )
    print(
        f" {Style.DIM}Carpeta: dentro del destino; {{año}} y {{mes}} se cambian por la fecha "
        f"del fichero: Documentos, Trabajo/Facturas, Fotos/{{año}}/{{mes}}{Style.RESET_ALL}"
    )
    carpeta = input(
        f" {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
        f"{Fore.GREEN}carpeta{Style.RESET_ALL} > "
    )
    try:
        regla = Regla.crear(patron, carpeta)
        reglas.agregar(regla)
    except ErrorReglas as e:
        imprimir_error(str(e))
        return
    except OSError as e:
        imprimir_error(f"No se pudieron guardar las reglas: {e}")
        return
    imprimir_exito(f"Regla añadida: {regla.texto()}")


def _quitar_regla(reglas: ReglasCarpeta) -> None:
    """Pide el número de una regla y la quita."""
    if not reglas.reglas:
        imprimir_info("No hay reglas que quitar")
        return
    texto = input(
        f" {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
        f"{Fore.GREEN}número de la regla{Style.RESET_ALL} > "
    ).strip()
    if not texto.isdigit():
        imprimir_error("Escribe el número de la regla")
        return
    try:
        quitada = reglas.quitar(int(texto))
    except ErrorReglas as e:
        imprimir_error(str(e))
        return
    except OSError as e:
        imprimir_error(f"No se pudieron guardar las reglas: {e}")
        return
    imprimir_exito(f"Regla quitada: {quitada.texto()}")


# Respuesta a la pregunta "config" para usar las categorías por defecto.
CATEGORIAS_POR_DEFECTO = "-"


def _guardar_preferencias(**valores: object) -> None:
    """Guarda preferencias (las comparten el menú y la GUI); si no se puede, sigue sin ellas."""
    try:
        cfg = ConfiguracionUsuario().cargar()
        cfg.actualizar(**valores)
        cfg.guardar()
    except OSError:
        pass  # no poder recordarlas no impide usar el programa


def _pista(texto: str) -> str:
    """Texto atenuado entre corchetes que explica qué hace ENTER."""
    return f" {Style.DIM}[{texto}]{Style.RESET_ALL}"


def _pedir_carpeta() -> Path | None:
    """Pide una carpeta y la valida; None si no sirve.

    ENTER reutiliza la última carpeta usada (desde el menú, la GUI o la consola), si sigue
    existiendo. La elegida se recuerda.
    """
    ultima = str(ConfiguracionUsuario().cargar().obtener("ultima_carpeta", "") or "")
    if ultima and not Path(ultima).is_dir():
        ultima = ""
    texto_carpeta = input(
        f"\n {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
        f"{Fore.GREEN}carpeta{Style.RESET_ALL}"
        f"{_pista(f'ENTER = {ultima}') if ultima else ''} > "
    )
    carpeta = validar_carpeta(
        texto_carpeta if preparar_ruta(texto_carpeta) is not None else ultima,
        vacia=(
            "No has escrito ninguna carpeta. ENTER solo repite la última que usaste, y todavía "
            "no hay ninguna."
        ),
    )
    if carpeta is not None:
        # Ruta completa, para reutilizarla aunque el menú se abra desde otra carpeta.
        _guardar_preferencias(ultima_carpeta=str(carpeta.resolve()))
    return carpeta


def _pedir_config(recordar: bool = True) -> tuple[bool, str | None]:
    """Pide el fichero de categorías; devuelve ``(válido, ruta)``, con ruta None para las de por
    defecto.

    ENTER reutiliza el último, si sigue existiendo; «-» usa las de por defecto. Si el fichero no
    sirve, válido es False y la operación no debe seguir: organizar con otras categorías haría
    algo distinto de lo pedido. Con ``recordar``, la elección se guarda.
    """
    ultima = str(ConfiguracionUsuario().cargar().obtener("ultima_config", "") or "")
    if ultima and not Path(ultima).is_file():
        ultima = ""
    pista = (
        f"ENTER = {ultima}, {CATEGORIAS_POR_DEFECTO} = categorías por defecto"
        if ultima
        else "ENTER = categorías por defecto"
    )
    texto_config = input(
        f"  {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
        f"{Fore.GREEN}config{Style.RESET_ALL}{_pista(pista)} > "
    ).strip()

    usar_defecto = texto_config == CATEGORIAS_POR_DEFECTO
    if usar_defecto:
        config = None
    elif preparar_ruta(texto_config) is not None:
        config = texto_config
    else:
        config = ultima or None
    if config is not None:
        ruta_config = validar_configuracion(config)
        if ruta_config is None:
            return False, None
        config = str(ruta_config)
    if recordar:
        _guardar_preferencias(ultima_config=str(Path(config).resolve()) if config else "")
    return True, config


def _pedir_si_no(pregunta: str, defecto: bool = False) -> bool:
    """Pregunta de sí/no; ENTER (o cualquier otra respuesta) devuelve ``defecto``, que se muestra
    en mayúscula.
    """
    opciones = "S/n" if defecto else "s/N"
    respuesta = input(f" {Fore.YELLOW}{pregunta} ({opciones}):{Style.RESET_ALL} ").strip().lower()
    if respuesta in ("s", "si", "sí"):
        return True
    if respuesta in ("n", "no"):
        return False
    return defecto


def _pedir_preferencia_si_no(pregunta: str, clave: str) -> bool:
    """Pregunta de sí/no que propone la última respuesta (guardada en ``clave``, compartida con la
    GUI).
    """
    defecto = bool(ConfiguracionUsuario().cargar().obtener(clave, False))
    respuesta = _pedir_si_no(pregunta, defecto)
    _guardar_preferencias(**{clave: respuesta})
    return respuesta


def _exportar_categorias() -> None:
    """Exporta las categorías a un JSON para editarlas y usarlas después con --config."""
    print(
        f"\n {Fore.CYAN}Exportar categorías{Style.RESET_ALL} {Style.DIM}(un JSON que puedes editar y usar después como config){Style.RESET_ALL}"
    )
    # Elegir qué categorías exportar no cambia el fichero que se usa al organizar.
    valida, config = _pedir_config(recordar=False)
    if not valida:
        return
    texto_destino = input(
        f"  {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~${Style.RESET_ALL} "
        f"{Fore.GREEN}guardar en{Style.RESET_ALL}"
        f"{_pista(f'ENTER = {NOMBRE_EXPORTACION_DEFECTO}')} > "
    )
    ruta = preparar_ruta(texto_destino) or Path(NOMBRE_EXPORTACION_DEFECTO)
    texto_destino = str(ruta)
    destino = ruta_exportacion(ruta)
    sobrescribir = False
    if destino.exists():
        sobrescribir = _pedir_si_no(f"{destino} ya existe. ¿Sobrescribirlo?")
        if not sobrescribir:
            imprimir_info("No se ha exportado nada.", separar=False)
            return
    ejecutar_exportar_categorias(texto_destino, config, sobrescribir=sobrescribir)


def _pedir_modo_integridad() -> ModoIntegridad:
    """Muestra los modos de integridad con su descripción y guarda el elegido.

    ENTER mantiene el último elegido (la primera vez, el recomendado).
    """
    cfg = ConfiguracionUsuario().cargar()
    actual = cfg.modo_integridad()
    modos = list(ModoIntegridad)

    print(
        f"\n {Fore.CYAN}Integridad al deshacer{Style.RESET_ALL} {Style.DIM}(cómo se comprueba si un fichero cambió){Style.RESET_ALL}"
    )
    for numero, modo in enumerate(modos, start=1):
        descripcion = DESCRIPCIONES_INTEGRIDAD[modo]
        marca = f" {Fore.YELLOW}← actual{Style.RESET_ALL}" if modo is actual else ""
        print(
            f"   {Fore.GREEN}[{numero}]{Style.RESET_ALL} {Style.BRIGHT}{descripcion.titulo}{Style.RESET_ALL}{marca}"
        )
        for linea in textwrap.wrap(descripcion.detalle, 70):
            print(f"       {Style.DIM}{linea}{Style.RESET_ALL}")

    elegido = _elegir_numero(modos, actual, "Modo", DESCRIPCIONES_INTEGRIDAD[actual].titulo)
    _guardar_preferencias(modo_integridad=elegido.value)
    return elegido


def _pedir_agrupacion() -> CriterioAgrupacion:
    """Muestra las formas de agrupar, con un ejemplo de cada una, y guarda la elegida.

    ENTER mantiene la última elegida (la primera vez, por tipo).
    """
    actual = ConfiguracionUsuario().cargar().criterio_agrupacion()
    criterios = list(CriterioAgrupacion)

    print(
        f"\n {Fore.CYAN}Agrupar por{Style.RESET_ALL} {Style.DIM}(en qué carpetas quedan los ficheros){Style.RESET_ALL}"
    )
    for numero, criterio in enumerate(criterios, start=1):
        descripcion = DESCRIPCIONES_AGRUPACION[criterio]
        marca = f" {Fore.YELLOW}← actual{Style.RESET_ALL}" if criterio is actual else ""
        print(
            f"   {Fore.GREEN}[{numero}]{Style.RESET_ALL} {Style.BRIGHT}{descripcion.titulo:<14}{Style.RESET_ALL}"
            f"{Style.DIM}{descripcion.ejemplo}{Style.RESET_ALL}{marca}"
        )
    for linea in textwrap.wrap(EXPLICACION_FECHA, 70):
        print(f"       {Style.DIM}{linea}{Style.RESET_ALL}")

    elegido = _elegir_numero(criterios, actual, "Agrupar", DESCRIPCIONES_AGRUPACION[actual].titulo)
    _guardar_preferencias(agrupar=elegido.value)
    return elegido


def _pedir_accion_duplicados() -> AccionDuplicados:
    """Pregunta si apartar los duplicados (si no, solo se avisa); propone la última respuesta."""
    apartar = _pedir_preferencia_si_no(
        f"¿Apartar los duplicados en {CARPETA_DUPLICADOS}/ en vez de solo avisar?",
        "mover_duplicados",
    )
    return AccionDuplicados.MOVER if apartar else AccionDuplicados.AVISAR


_Opcion = TypeVar("_Opcion", bound=StrEnum)


def _elegir_numero(opciones: list[_Opcion], actual: _Opcion, pregunta: str, nombre: str) -> _Opcion:
    """Pide el número de una de las ``opciones``; ENTER (o uno no válido, avisando) deja ``actual``.

    ``nombre`` es el de la opción actual, para la pista de ENTER.
    """
    respuesta = input(
        f" {Fore.YELLOW}{pregunta} (1-{len(opciones)}, ENTER = {nombre}):{Style.RESET_ALL} "
    ).strip()
    if respuesta.isdigit() and 1 <= int(respuesta) <= len(opciones):
        return opciones[int(respuesta) - 1]
    if respuesta:
        imprimir_aviso(f"Opción no válida; se mantiene «{nombre}»", separar=False)
    return actual


def ejecutar_menu() -> int:
    """Muestra el menú hasta que se elige salir; devuelve el código de salida."""
    configurar_registro()
    try:
        return _bucle_menu()
    except EOFError:
        # Ctrl+D, o una entrada redirigida que se acaba: se sale como con la opción 11.
        print(f"\n\n {Fore.RED}Saliendo del programa...{Style.RESET_ALL}\n")
        return 0


# Opciones del menú: número, texto y estilo (principales en verde, herramientas en cian,
# salir en rojo).
_PRINCIPAL = Fore.GREEN + Style.BRIGHT
_OPCIONES_MENU = [
    ("01", "Organizar carpeta", _PRINCIPAL),
    ("02", "Simular organización", _PRINCIPAL),
    ("03", "Deshacer el último paso", _PRINCIPAL),
    ("04", "Ver historial", _PRINCIPAL),
    ("05", "Limpiar historial", _PRINCIPAL),
    ("06", "Ver estadísticas", _PRINCIPAL),
    ("07", "Vigilancia en tiempo real", Fore.CYAN),
    ("08", "Cifrado / Descifrado", Fore.CYAN),
    ("09", "Exportar categorías", Fore.CYAN),
    ("10", "Reglas de una carpeta", Fore.CYAN),
    ("11", "Salir del programa", Fore.RED),
]

# Indicaciones para seguir que valen en el menú (las de por defecto hablan de opciones como
# --simular o --destino, que aquí no existen).
INDICACIONES_MENU = Indicaciones(
    aplicar_simulacion="Para hacerlo de verdad, elige la opción 01 (Organizar carpeta).",
    otra_carpeta_destino=(
        "Si la organizaste en otra carpeta de destino, el menú no puede usarla: hazlo desde la "
        "ventana (file-organizer-gui) o con file-organizer --carpeta ... --destino ..."
    ),
)

# Lo que hace cada opción con el organizador de la carpeta elegida.
_ACCIONES_CON_ORGANIZADOR: dict[str, Callable[[OrganizadorArchivos], int]] = {
    "01": ejecutar_organizar,
    "02": partial(ejecutar_simulacion, indicaciones=INDICACIONES_MENU),
    "03": partial(ejecutar_deshacer, indicaciones=INDICACIONES_MENU),
    "04": partial(ejecutar_historial, indicaciones=INDICACIONES_MENU),
    "05": partial(ejecutar_limpiar_historial, indicaciones=INDICACIONES_MENU),
    "06": partial(ejecutar_estadisticas, indicaciones=INDICACIONES_MENU),
}
# Opciones que preguntan qué fichero de categorías usar (las demás no clasifican nada).
_OPCIONES_CON_CATEGORIAS = ("01", "02", "06", "07")
# Opciones que mueven (o simulan mover) ficheros y por eso preguntan cómo agruparlos.
_OPCIONES_QUE_AGRUPAN = ("01", "02", "07")


def _bucle_menu() -> int:
    while True:
        _mostrar_menu()
        opcion = input(
            f" {Fore.YELLOW}{Style.BRIGHT}root@fileorganizer:~$ {Style.RESET_ALL}"
        ).strip()

        if opcion == "11":
            print(f"\n {Fore.RED}Saliendo del programa...{Style.RESET_ALL}\n")
            return 0
        if opcion == "08":
            ejecutar_menu_cifrado()  # tiene su propio menú y sus pausas
            continue
        if opcion == "10":
            ejecutar_menu_reglas()  # tiene su propio menú
            continue

        if opcion == "09":
            _exportar_categorias()
        elif opcion in _ACCIONES_CON_ORGANIZADOR or opcion == "07":
            _opcion_de_carpeta(opcion)
        else:
            imprimir_error("Opción no válida. Elige entre 01 y 11")
        _pausa()


def _mostrar_menu() -> None:
    print(f" {Fore.CYAN}{Style.BRIGHT}{'=' * 50}{Style.RESET_ALL}")
    print(
        f" {Fore.CYAN}{Style.BRIGHT}             FILE ORGANIZER - v{__version__} {Style.RESET_ALL}"
    )
    print(f" {Fore.CYAN}{Style.BRIGHT}              [ SISTEMA ACTIVO ]   {Style.RESET_ALL}")
    print(f" {Fore.CYAN}{Style.BRIGHT}{'=' * 50}{Style.RESET_ALL}")
    for numero, descripcion, estilo in _OPCIONES_MENU:
        print(f" {Fore.GREEN}[{numero}]{Style.RESET_ALL} {estilo}{descripcion}{Style.RESET_ALL}")
    print()
    print(
        f"  {Style.DIM} [ Todas las opciones de los comandos: file-organizer --help ] "
        f"{Style.RESET_ALL}"
    )
    print()


def _opcion_de_carpeta(opcion: str) -> None:
    """Opciones 01 a 07: piden la carpeta (y cómo agrupar y las categorías, si clasifican) y la
    ejecutan.

    Agrupando solo por fecha no se pregunta por las categorías, que no se usan.
    """
    carpeta = _pedir_carpeta()
    if carpeta is None:
        return

    agrupar = CRITERIO_DEFECTO
    duplicados = AccionDuplicados.AVISAR
    if opcion in _OPCIONES_QUE_AGRUPAN:
        agrupar = _pedir_agrupacion()
        duplicados = _pedir_accion_duplicados()
    config = None
    if opcion in _OPCIONES_CON_CATEGORIAS and agrupar is not CriterioAgrupacion.FECHA:
        valida, config = _pedir_config()
        if not valida:
            return

    if opcion == "07":
        recursivo = _pedir_preferencia_si_no("¿Vigilar también las subcarpetas?", "recursivo_watch")
        modo_integridad = _pedir_modo_integridad()
        print(
            f"\n {Fore.CYAN}[VIGILANTE]{Style.RESET_ALL} Pulsa Ctrl+C para detener la vigilancia."
        )
        ejecutar_vigilar(
            carpeta,
            config=config,
            recursivo=recursivo,
            modo_integridad=modo_integridad,
            agrupar=agrupar,
            duplicados=duplicados,
        )
        return

    # Solo al organizar de verdad se pregunta por el backup y por la integridad.
    organizar = opcion == "01"
    backup = (
        _pedir_preferencia_si_no("¿Crear un backup .zip antes de organizar?", "backup")
        if organizar
        else False
    )
    modo_integridad = _pedir_modo_integridad() if organizar else MODO_INTEGRIDAD_DEFECTO

    org = crear_organizador(
        carpeta,
        simular=(opcion == "02"),
        config=config,
        backup=backup,
        modo_integridad=modo_integridad,
        agrupar=agrupar,
        duplicados=duplicados,
    )
    if org is None:
        return
    print()
    _ACCIONES_CON_ORGANIZADOR[opcion](org)
