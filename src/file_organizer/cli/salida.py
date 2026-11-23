"""Todo lo que la consola muestra: mensajes en color, preguntas, resúmenes y la barra de progreso.

También el destino del log en la consola (ManejadorConsola), que no rompe la barra.
"""

from __future__ import annotations

import logging
from collections import Counter

from colorama import Fore, Style, init

from file_organizer.duplicados import CARPETA_DUPLICADOS, AccionDuplicados
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.reglas import ReglasCarpeta
from file_organizer.utilidades import SOLO_EN_FICHERO

init(autoreset=True)

logger = logging.getLogger(__name__)


def _imprimir_mensaje(color: str, etiqueta: str, mensaje: str, separar: bool) -> None:
    """``[ETIQUETA] mensaje`` con la etiqueta en color; ``separar`` deja antes una línea en blanco."""
    print(("\n" if separar else "") + f" {color}[{etiqueta}]{Style.RESET_ALL} {mensaje}")


def imprimir_error(mensaje: str, separar: bool = True) -> None:
    """``[ERROR] mensaje``, en rojo."""
    _imprimir_mensaje(Fore.RED, "ERROR", mensaje, separar)


def imprimir_info(mensaje: str, separar: bool = True) -> None:
    """``[INFO] mensaje``, en amarillo."""
    _imprimir_mensaje(Fore.YELLOW, "INFO", mensaje, separar)


def imprimir_aviso(mensaje: str, separar: bool = True) -> None:
    """``[AVISO] mensaje``, en amarillo."""
    _imprimir_mensaje(Fore.YELLOW, "AVISO", mensaje, separar)


def resaltar(dato: object) -> str:
    """El dato (por ejemplo, una ruta) en blanco, para destacarlo dentro de un mensaje."""
    return f"{Fore.WHITE}{dato}{Style.RESET_ALL}"


def confirmar(pregunta: str) -> bool:
    """Pregunta s/n: True solo si se responde «s».

    Sin respuesta posible (Ctrl+D, o la entrada redirigida desde un fichero vacío o un script)
    cuenta como «no».
    """
    try:
        respuesta = input(pregunta)
    except EOFError:
        print()  # la pregunta se quedó sin salto de línea
        return False
    return respuesta.strip().lower() == "s"


def imprimir_excepcion(e: Exception, etiqueta: str = "") -> None:
    """Muestra el error en rojo y lo deja en el fichero de log."""
    prefijo = f"{etiqueta}: " if etiqueta else ""
    mensaje = str(e)
    print(f" {Fore.RED}✗{Style.RESET_ALL} {prefijo}{mensaje}")
    logger.warning(f"{prefijo}{type(e).__name__}: {mensaje}", extra=SOLO_EN_FICHERO)


def imprimir_exito(mensaje: str, etiqueta: str = "") -> None:
    """Muestra el mensaje en verde y lo deja en el fichero de log."""
    prefijo = f"{etiqueta}: " if etiqueta else ""
    print(f" {Fore.GREEN}✓{Style.RESET_ALL} {prefijo}{mensaje}")
    logger.info(f"{prefijo}{mensaje}", extra=SOLO_EN_FICHERO)


def imprimir_resumen(org: OrganizadorArchivos) -> None:
    """Resumen de lo que movió (o movería) ``org``, por categoría, y de los errores.

    Lista también los duplicados y, si agrupó por fecha, de dónde salieron las fechas.
    """
    # Lo que movió (o en una simulación, movería) esta ejecución, por carpeta.
    por_carpeta = Counter(registro.categoria for registro in org.ultimos_movimientos)

    if not por_carpeta and not org.ultimos_errores:
        print("\nNo había ficheros que organizar.")
    else:
        titulo = (
            "RESUMEN DE LA SIMULACIÓN (no se ha movido nada)"
            if org.modo_simulacion
            else "RESUMEN DE ORGANIZACIÓN"
        )
        print("\n" + "=" * 35)
        print(f"  {titulo}")
        print("=" * 35)
        for categoria, cantidad in sorted(por_carpeta.items()):
            print(f" {categoria}: {cantidad} fichero(s)")
        print("=" * 35)

        if org.ultimos_errores:
            print(f"\n No se pudieron organizar {len(org.ultimos_errores)} fichero(s):")
            for elemento, error in org.ultimos_errores:
                print(f"     - {elemento.name}: {error}")

    _imprimir_duplicados(org)
    resumen_reglas = org.resumen_reglas()
    if resumen_reglas:
        print(f" {Style.DIM}{resumen_reglas}{Style.RESET_ALL}")
    resumen_fechas = org.resumen_fechas()
    if resumen_fechas:
        print(f" {Style.DIM}{resumen_fechas}{Style.RESET_ALL}")


# Duplicados que se listan en la consola; el resto queda en el fichero de log.
MAX_DUPLICADOS_LISTADOS = 20


def _imprimir_duplicados(org: OrganizadorArchivos) -> None:
    """Los duplicados encontrados («copia.jpg = Imagenes/foto.jpg») y qué se hizo con ellos."""
    duplicados = org.ultimos_duplicados
    if not duplicados:
        return
    if org.duplicados is AccionDuplicados.MOVER:
        destino = f"apartados en {CARPETA_DUPLICADOS}/"
    else:
        destino = "organizados como los demás"
    print(
        f"\n {Fore.YELLOW}!{Style.RESET_ALL} {len(duplicados)} duplicado(s) "
        f"(mismo contenido que otro fichero), {destino}:"
    )
    for duplicado in duplicados[:MAX_DUPLICADOS_LISTADOS]:
        print(f"     {duplicado.texto(org.carpeta_destino)}")
    resto = len(duplicados) - MAX_DUPLICADOS_LISTADOS
    if resto > 0:
        print(f"     {Style.DIM}... y {resto} más (todos en el fichero de log){Style.RESET_ALL}")


def imprimir_reglas(reglas: ReglasCarpeta, pista_sin_reglas: str) -> None:
    """Las reglas de la carpeta, numeradas y por orden de prioridad (ya cargadas)."""
    carpeta = reglas.ruta.parent
    if not reglas.reglas:
        print(f"\n {Style.DIM}{carpeta.name or carpeta} no tiene reglas.{Style.RESET_ALL}")
        print(f" {Style.DIM}{pista_sin_reglas}{Style.RESET_ALL}")
        return
    print(
        f"\n {Fore.CYAN}{Style.BRIGHT}Reglas de {carpeta.name or carpeta}{Style.RESET_ALL} "
        f"{Style.DIM}(gana la primera que coincide){Style.RESET_ALL}"
    )
    ancho = max(len(regla.patron) for regla in reglas.reglas)
    for numero, regla in enumerate(reglas.reglas, start=1):
        print(
            f"   {Fore.GREEN}{numero}.{Style.RESET_ALL} {regla.patron:<{ancho}}  →  "
            f"{Fore.WHITE}{regla.carpeta}{Style.RESET_ALL}"
        )
    print(f" {Style.DIM}Lo que no encaja con ninguna se organiza como siempre.{Style.RESET_ALL}")


def notificador_vigilante(nivel: str, mensaje: str) -> None:
    """Muestra un aviso del vigilante, con el color de su nivel."""
    colores = {
        "info": Fore.CYAN,
        "espera": Style.DIM,
        "exito": Fore.GREEN,
        "aviso": Fore.YELLOW,
        "error": Fore.RED,
    }
    color = colores.get(nivel, "")
    print(f" {color}[VIGILANTE]{Style.RESET_ALL} {mensaje}")


def barra_progreso_consola(actual: int, total: int, ancho: int = 30) -> None:
    """Dibuja la barra de progreso en la última línea de la consola.

    Sirve de ``progreso`` para organizar y cifrar. Al llegar al total, cierra la línea.
    """
    if total <= 0:
        return
    proporcion = min(max(actual / total, 0.0), 1.0)
    llenos = int(proporcion * ancho)
    barra = "#" * llenos + "-" * (ancho - llenos)
    cifras = f"{actual}/{total} ({proporcion * 100:5.1f}%)"
    texto = f" {Fore.CYAN}[{barra}]{Style.RESET_ALL} {cifras}"
    terminada = actual >= total
    print(f"\r{texto}", end="\n" if terminada else "", flush=True)
    if terminada:
        _barra.texto, _barra.ancho = "", 0
    else:
        _barra.texto, _barra.ancho = texto, len(f" [{barra}] {cifras}")


class _BarraEnCurso:
    """La barra de progreso que está a medio dibujar en la última línea, si hay alguna."""

    texto = ""  # con colores; vacío si no hay ninguna
    ancho = 0  # caracteres visibles, para poder borrarla


_barra = _BarraEnCurso()


class ManejadorConsola(logging.StreamHandler):  # type: ignore[type-arg]
    """Destino del log en la consola que no rompe la barra de progreso.

    La barra se redibuja en su línea y no termina en salto de línea: un mensaje escrito tal cual
    se pegaría a ella. Si hay una barra a medio dibujar, la borra, escribe el mensaje y la
    vuelve a dibujar debajo.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Escribe el mensaje; si hay una barra a medio dibujar, la mueve debajo."""
        if not _barra.texto:
            super().emit(record)
            return
        print("\r" + " " * _barra.ancho + "\r", end="", flush=True)
        super().emit(record)
        print(_barra.texto, end="", flush=True)
