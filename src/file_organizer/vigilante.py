"""Vigila una carpeta y organiza automáticamente los ficheros que van llegando.

Diseño:
- Los eventos de watchdog solo anotan el fichero en una cola, con la hora a la que toca
  procesarlo: el hilo de watchdog nunca se bloquea.
- Un único hilo de trabajo organiza los ficheros de uno en uno cuando vence su espera. Así,
  miles de ficheros llegando a la vez no crean miles de hilos.
- Antes de mover, comprueba que el tamaño y la fecha no cambiaron durante la espera; si
  cambiaron, el fichero se sigue escribiendo y espera otra vez.
- Las descargas de los navegadores (.crdownload, .part) se organizan al renombrarse al terminar;
  si existe la descarga en curso de un fichero, se espera a que acabe.
- Ningún error al procesar un evento o un fichero detiene la vigilancia.
- El historial se guarda cuando pasa SEGUNDOS_DEBOUNCE_GUARDADO sin movimientos.

Los avisos van a un «notificador» con un nivel (info, espera, exito, aviso, error): la consola
los imprime y la GUI los muestra en su panel. También quedan en el log, marcados SOLO_EN_FICHERO
para no verlos dos veces en la consola.
"""

from __future__ import annotations

import functools
import heapq
import itertools
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Concatenate, ParamSpec

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from file_organizer.clasificador import CRITERIO_DEFECTO, CriterioAgrupacion
from file_organizer.duplicados import ACCION_DUPLICADOS_DEFECTO, AccionDuplicados, Duplicado
from file_organizer.excepciones import ErrorOrganizador
from file_organizer.historial import TIPO_VIGILANCIA
from file_organizer.integridad import MODO_INTEGRIDAD_DEFECTO, ModoIntegridad
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.utilidades import SOLO_EN_FICHERO, esta_dentro

logger = logging.getLogger(__name__)

SEGUNDOS_ESPERA_DEFECTO = 5
SUFIJOS_TEMPORALES = {".tmp", ".crdownload", ".part", ".download"}
SEGUNDOS_DEBOUNCE_GUARDADO = 1.0

# Notificador: recibe (nivel, mensaje).
Notificador = Callable[[str, str], None]
# Estado de un fichero para saber si sigue cambiando: (tamaño, fecha de modificación en ns).
EstadoFichero = tuple[int, int]


def notificador_silencioso(nivel: str, mensaje: str) -> None:
    """Notificador por defecto: no muestra nada.

    Cada aviso ya queda en el log. La consola (cli.salida.notificador_vigilante) y la GUI pasan
    el suyo.
    """


def _ruta_evento(ruta: bytes | str) -> Path:
    """Path de la ruta de un evento de watchdog, que puede llegar como bytes."""
    return Path(os.fsdecode(ruta))


def _estado(ruta: Path) -> EstadoFichero | None:
    """Tamaño y fecha de modificación del fichero, o None si ya no existe."""
    try:
        info = ruta.stat()
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


@dataclass
class _Pendiente:
    """Fichero a la espera de ser organizado."""

    vence: float  # instante (time.monotonic) a partir del cual se puede procesar
    estado: EstadoFichero | None  # estado al programarlo, para detectar si sigue cambiando


_P = ParamSpec("_P")


def _sin_detener_la_vigilancia(
    metodo: Callable[Concatenate[ManejadorEventosCarpeta, _P], None],
) -> Callable[Concatenate[ManejadorEventosCarpeta, _P], None]:
    """Decorador: ninguna excepción sale del método; se registra y se avisa como error.

    Va en los manejadores de eventos y en el proceso de cada fichero: una excepción que saliera
    de ellos mataría el hilo de watchdog (o el de trabajo) y la vigilancia se pararía sin
    avisar.
    """

    @functools.wraps(metodo)
    def protegido(self: ManejadorEventosCarpeta, /, *args: _P.args, **kwargs: _P.kwargs) -> None:
        try:
            metodo(self, *args, **kwargs)
        except Exception as error:
            logger.exception("[VIGILANTE] Error inesperado", extra=SOLO_EN_FICHERO)
            self.notificador("error", f"Error inesperado en el vigilante: {error}")

    return protegido


class ManejadorEventosCarpeta(FileSystemEventHandler):
    """Recibe los eventos de la carpeta y organiza lo que va llegando.

    Los métodos ``on_*`` los llama watchdog. El trabajo lo hace un único hilo, que se arranca
    con ``iniciar()`` y se para con ``detener()``.
    """

    def __init__(
        self,
        organizador: OrganizadorArchivos,
        segundos_espera: float,
        notificador: Notificador | None = None,
    ) -> None:
        super().__init__()
        self.organizador = organizador
        self.segundos_espera = segundos_espera
        self.notificador = notificador or notificador_silencioso
        self._pendientes: dict[Path, _Pendiente] = {}
        # Montículo (vence, orden, ruta). Puede tener entradas obsoletas: la verdad está en
        # _pendientes, y las obsoletas se descartan o se reprograman al salir del montículo.
        self._cola: list[tuple[float, int, Path]] = []
        self._orden = itertools.count()
        self._condicion = threading.Condition()
        self._guardar_en: float | None = None
        self._cambios_sin_guardar = False
        self._detenido = False
        self._hilo: threading.Thread | None = None

    # ------------------------------------------------------------ ciclo de vida

    def iniciar(self) -> None:
        """Arranca el hilo que organiza los ficheros pendientes."""
        if self._hilo is None:
            self._hilo = threading.Thread(
                target=self._bucle_trabajo, name="vigilante-organizador", daemon=True
            )
            self._hilo.start()

    def detener(self) -> None:
        """Descarta lo pendiente, espera a que termine el fichero en curso y guarda el historial."""
        with self._condicion:
            self._detenido = True
            descartados = len(self._pendientes)
            self._pendientes.clear()
            self._cola.clear()
            self._condicion.notify_all()

        hilo = self._hilo
        if hilo is not None and hilo is not threading.current_thread():
            hilo.join(timeout=30)

        if self._cambios_sin_guardar:
            self._guardar_historial()
        if descartados:
            logger.info("[VIGILANTE] %s fichero(s) pendientes descartados al detener", descartados)

    @property
    def iniciado(self) -> bool:
        """Si se llegó a arrancar el hilo de trabajo (aunque ya se haya detenido)."""
        return self._hilo is not None

    @property
    def pendientes(self) -> int:
        """Ficheros esperando a ser organizados."""
        with self._condicion:
            return len(self._pendientes)

    # ------------------------------------------------------- eventos de watchdog

    @_sin_detener_la_vigilancia
    def on_created(self, evento: FileSystemEvent) -> None:
        """Programa la organización de un fichero nuevo tras la espera."""
        if not evento.is_directory:
            self._programar_si_procede(_ruta_evento(evento.src_path), True)

    @_sin_detener_la_vigilancia
    def on_modified(self, evento: FileSystemEvent) -> None:
        """Si un fichero pendiente se sigue escribiendo, reinicia su espera."""
        if not evento.is_directory:
            self._reprogramar_si_pendiente(_ruta_evento(evento.src_path))

    @_sin_detener_la_vigilancia
    def on_moved(self, evento: FileSystemEvent) -> None:
        """Un renombrado (p. ej. una descarga que termina) cuenta como fichero nuevo."""
        if not evento.is_directory:
            self._al_renombrar(_ruta_evento(evento.src_path), _ruta_evento(evento.dest_path))

    @_sin_detener_la_vigilancia
    def on_deleted(self, evento: FileSystemEvent) -> None:
        """Si se borra un fichero pendiente, lo olvida; si en su lugar ya hay otro, sigue
        pendiente.

        En macOS (FSEvents) los eventos llegan con retraso: cuando Firefox termina una descarga
        renombra ``informe.pdf.part`` encima del ``informe.pdf`` vacío, y el aviso de que el vacío
        se borró llega después del renombrado. Si se olvidara, la descarga ya completa no se
        organizaría nunca.
        """
        if not evento.is_directory:
            ruta_archivo = _ruta_evento(evento.src_path)
            if not ruta_archivo.exists():
                self._olvidar(ruta_archivo)

    # ------------------------------------------------------------- programación

    def _programar_si_procede(self, ruta_archivo: Path, anunciar: bool) -> None:
        """Programa el fichero si no hay que ignorarlo; con ``anunciar``, avisa de que llegó."""
        if self._deberia_ignorar(ruta_archivo):
            return
        nuevo = self._programar(ruta_archivo)
        if nuevo and anunciar:
            self.notificador("info", f"Nuevo fichero detectado: {ruta_archivo.name}")
            self.notificador(
                "espera",
                f"Esperando {self.segundos_espera} s a que termine de copiarse o descargarse...",
            )
            logger.info(
                "[VIGILANTE] Nuevo fichero detectado: %s", ruta_archivo.name, extra=SOLO_EN_FICHERO
            )

    def _programar(self, ruta_archivo: Path) -> bool:
        """Programa (o reprograma) el fichero tras ``segundos_espera``; True si es nuevo."""
        estado = _estado(ruta_archivo)
        with self._condicion:
            if self._detenido:
                return False
            vence = time.monotonic() + self.segundos_espera
            pendiente = self._pendientes.get(ruta_archivo)
            if pendiente is not None:
                # Ya tiene una entrada en el montículo: basta con mover su vencimiento.
                pendiente.vence = vence
                pendiente.estado = estado
                return False
            self._pendientes[ruta_archivo] = _Pendiente(vence, estado)
            orden = next(self._orden)
            heapq.heappush(self._cola, (vence, orden, ruta_archivo))
            if self._cola[0][1] == orden:
                self._condicion.notify()  # es lo próximo que vence: despertar al hilo
            return True

    def _reprogramar_si_pendiente(self, ruta_archivo: Path) -> None:
        """Reinicia la espera de un fichero pendiente (aún se está escribiendo)."""
        with self._condicion:
            if ruta_archivo not in self._pendientes:
                return
        logger.debug("[VIGILANTE] Fichero modificado, se reinicia la espera: %s", ruta_archivo.name)
        self._programar(ruta_archivo)

    def _al_renombrar(self, origen: Path, destino: Path) -> None:
        """Olvida el nombre antiguo y programa el nuevo si sigue dentro de la carpeta."""
        self._olvidar(origen)
        if self._dentro_de_la_carpeta(destino):
            self._programar_si_procede(destino, True)

    def _olvidar(self, ruta_archivo: Path) -> None:
        """Quita un fichero de los pendientes."""
        with self._condicion:
            self._pendientes.pop(ruta_archivo, None)

    # ----------------------------------------------------------------- filtros

    def _deberia_ignorar(self, ruta_archivo: Path) -> bool:
        """Si el fichero no se organiza: oculto, temporal, ignorado o ya en su carpeta de
        categoría.
        """
        if ruta_archivo.name.startswith("."):
            return True
        if ruta_archivo.suffix.lower() in SUFIJOS_TEMPORALES:
            logger.debug("[VIGILANTE] Fichero temporal ignorado: %s", ruta_archivo.name)
            return True
        if self.organizador.esta_ignorado(ruta_archivo):
            logger.debug("[VIGILANTE] Fichero en la lista de ignorados: %s", ruta_archivo.name)
            return True
        if self._ya_organizado(ruta_archivo):
            logger.debug("[VIGILANTE] Fichero ya organizado, se ignora: %s", ruta_archivo.name)
            return True
        return False

    def _ya_organizado(self, ruta_archivo: Path) -> bool:
        """Si el fichero ya está en una carpeta de categoría (evita bucles con subcarpetas)."""
        return self.organizador.esta_en_carpeta_de_categoria(ruta_archivo)

    def _dentro_de_la_carpeta(self, ruta_archivo: Path) -> bool:
        """Si la ruta está dentro de la carpeta vigilada."""
        try:
            return esta_dentro(ruta_archivo, self.organizador.carpeta_origen)
        except OSError:
            return False

    @staticmethod
    def _descarga_en_curso(ruta_archivo: Path) -> bool:
        """Si existe la descarga en curso de este fichero (``nombre.ext.part``, etc.)."""
        return any(
            ruta_archivo.with_name(ruta_archivo.name + sufijo).exists()
            for sufijo in SUFIJOS_TEMPORALES
        )

    # ------------------------------------------------------------- hilo de trabajo

    def _bucle_trabajo(self) -> None:
        """Hilo de trabajo: organiza los ficheros a medida que vence su espera, de uno en uno."""
        while True:
            lote = self._esperar_trabajo()
            if lote is None:
                return
            vencidos, guardar = lote
            for ruta_archivo, pendiente in vencidos:
                with self._condicion:
                    if self._detenido:
                        return
                self._procesar_archivo(ruta_archivo, pendiente)
            if guardar:
                self._guardar_historial()

    def _esperar_trabajo(self) -> tuple[list[tuple[Path, _Pendiente]], bool] | None:
        """Espera a que venza algún fichero o el guardado del historial.

        Devuelve los ficheros vencidos y si toca guardar, o None si se ha detenido.
        """
        with self._condicion:
            while not self._detenido:
                ahora = time.monotonic()
                vencidos: list[tuple[Path, _Pendiente]] = []
                while self._cola and self._cola[0][0] <= ahora:
                    _, _, ruta_archivo = heapq.heappop(self._cola)
                    pendiente = self._pendientes.get(ruta_archivo)
                    if pendiente is None:
                        continue  # ya procesado, borrado o renombrado
                    if pendiente.vence > ahora:
                        # Se modificó durante la espera: vuelve al montículo con su nueva hora.
                        heapq.heappush(
                            self._cola, (pendiente.vence, next(self._orden), ruta_archivo)
                        )
                        continue
                    del self._pendientes[ruta_archivo]
                    vencidos.append((ruta_archivo, pendiente))

                guardar = self._guardar_en is not None and self._guardar_en <= ahora
                if guardar:
                    self._guardar_en = None
                if vencidos or guardar:
                    return vencidos, guardar

                limites = [t for t in (self._guardar_en,) if t is not None]
                if self._cola:
                    limites.append(self._cola[0][0])
                self._condicion.wait(timeout=min(limites) - ahora if limites else None)
            return None

    @_sin_detener_la_vigilancia
    def _procesar_archivo(self, ruta_archivo: Path, pendiente: _Pendiente) -> None:
        """Organiza el fichero si sigue existiendo y ya no está cambiando; si cambia, espera otra
        vez.
        """
        estado = _estado(ruta_archivo)
        if estado is None:
            self._reportar_aviso(f"{ruta_archivo.name} ya no existe; no se organizará")
            return

        if estado != pendiente.estado or self._descarga_en_curso(ruta_archivo):
            logger.debug("[VIGILANTE] %s sigue cambiando; se espera más", ruta_archivo.name)
            self._programar(ruta_archivo)
            return

        self.notificador("info", f"Organizando: {ruta_archivo.name}")
        logger.info("[VIGILANTE] Organizando: %s", ruta_archivo.name, extra=SOLO_EN_FICHERO)

        try:
            resultado = self.organizador.organizar_fichero(ruta_archivo, anunciado_aparte=True)
            categoria = resultado.registro.categoria
            original = resultado.duplicado_de
            regla = resultado.regla
        except (ErrorOrganizador, OSError) as error:
            mensaje = f"No se pudo mover {ruta_archivo.name}: {error}"
            self.notificador("error", mensaje)
            logger.warning("[VIGILANTE] %s", mensaje, extra=SOLO_EN_FICHERO)
            return

        with self._condicion:
            self._cambios_sin_guardar = True
            self._guardar_en = time.monotonic() + SEGUNDOS_DEBOUNCE_GUARDADO

        por_regla = f" (regla «{regla}»)" if regla else ""
        self.notificador("exito", f"{ruta_archivo.name} -> {categoria}/{por_regla}")
        logger.info(
            "[VIGILANTE] Movido: %s -> %s/", ruta_archivo.name, categoria, extra=SOLO_EN_FICHERO
        )
        if original is not None:
            duplicado = Duplicado(ruta_archivo, original)
            self._reportar_aviso(f"Duplicado: {duplicado.texto(self.organizador.carpeta_destino)}")

    def _guardar_historial(self) -> None:
        """Guarda el historial; un fallo se avisa pero no detiene la vigilancia."""
        with self._condicion:
            self._cambios_sin_guardar = False
        try:
            self.organizador.guardar_historial()
        except (ErrorOrganizador, OSError) as error:
            self.notificador("error", f"No se pudo guardar el historial: {error}")
            logger.warning(
                "[VIGILANTE] No se pudo guardar el historial: %s", error, extra=SOLO_EN_FICHERO
            )

    def _reportar_aviso(self, mensaje: str) -> None:
        """Envía un aviso al notificador y lo deja en el log."""
        self.notificador("aviso", mensaje)
        logger.warning("[VIGILANTE] %s", mensaje, extra=SOLO_EN_FICHERO)


class VigilanteCarpeta:
    """Vigila una carpeta con watchdog y organiza lo que llega (ver ManejadorEventosCarpeta).

    Con ``organizador`` usa ese (con sus carpetas, categorías e integridad); si no, crea uno con
    ``ruta_config``, ``carpeta_destino``, ``modo_integridad``, ``agrupar`` y ``duplicados``.
    """

    def __init__(
        self,
        carpeta: Path,
        ruta_config: str | None = None,
        carpeta_destino: Path | None = None,
        espera: float = SEGUNDOS_ESPERA_DEFECTO,
        notificador: Notificador | None = None,
        recursivo: bool = False,
        modo_integridad: ModoIntegridad = MODO_INTEGRIDAD_DEFECTO,
        *,
        agrupar: CriterioAgrupacion = CRITERIO_DEFECTO,
        duplicados: AccionDuplicados = ACCION_DUPLICADOS_DEFECTO,
        organizador: OrganizadorArchivos | None = None,
    ) -> None:
        if espera < 0:
            raise ValueError("El tiempo de espera del vigilante no puede ser negativo")

        self.carpeta = Path(carpeta).absolute()  # watchdog da las rutas a partir de esta
        self.segundos_espera = espera
        self.recursivo = recursivo
        self.notificador = notificador or notificador_silencioso
        self.organizador = (
            OrganizadorArchivos(
                carpeta_origen=carpeta,
                carpeta_destino=carpeta_destino,
                ruta_config=ruta_config,
                modo_simulacion=False,
                modo_integridad=modo_integridad,
                agrupar=agrupar,
                duplicados=duplicados,
            )
            if organizador is None
            else organizador
        )
        self.manejador = ManejadorEventosCarpeta(
            self.organizador, espera, notificador=self.notificador
        )
        self.observador = Observer()
        self._evento_detencion = threading.Event()
        self._bloqueo_limpieza = threading.Lock()
        self._detenido_avisado = False

    def iniciar(self) -> None:
        """Vigila la carpeta hasta que se detiene (con detener() o Ctrl+C); bloquea mientras tanto.

        Mientras vigila, la carpeta queda bloqueada: ninguna otra operación, de esta ventana o
        de otra, puede organizarla, deshacer ni limpiar su historial. Lanza ErrorCarpetaEnUso si
        otra ya la está usando y ErrorReglas si las reglas de la carpeta no son válidas.
        """
        self.organizador.reglas.cargar()
        self.organizador.bloqueo.adquirir()
        try:
            if self._evento_detencion.is_set():  # se pidió detener antes de llegar a empezar
                return
            aviso_historial = self._continuar_historial()
            self.manejador.iniciar()
            self.observador.schedule(self.manejador, str(self.carpeta), recursive=self.recursivo)
            self.observador.start()

            self.notificador("info", f"Vigilando: {self.carpeta}")
            self.notificador(
                "info", f"Antes de mover cada fichero se esperan {self.segundos_espera} s"
            )
            if aviso_historial is not None:
                self.notificador(*aviso_historial)
            logger.info("[VIGILANTE] Vigilando: %s", self.carpeta, extra=SOLO_EN_FICHERO)

            try:
                # wait() con tiempo límite para que Ctrl+C se atienda también en Windows.
                while not self._evento_detencion.wait(timeout=0.5):
                    pass
            except KeyboardInterrupt:
                self.notificador("espera", "Deteniendo la vigilancia...")
                logger.info("[VIGILANTE] Deteniendo la vigilancia...", extra=SOLO_EN_FICHERO)
        finally:
            self._evento_detencion.set()
            self._limpiar()

    def _continuar_historial(self) -> tuple[str, str] | None:
        """Carga el historial y empieza encima un paso propio para esta vigilancia.

        Así lo que se organice al vigilar se deshace aparte (el primer «Deshacer» lo revierte) y
        no se pierde lo anterior. Si el historial no se puede usar, se aparta y se empieza otro.
        Devuelve el aviso ``(nivel, mensaje)`` que hay que mostrar, o None.
        """
        aviso = self.organizador.preparar_historial(TIPO_VIGILANCIA)
        if aviso is not None:
            return ("aviso", aviso)
        anteriores = len(self.organizador.operaciones())
        if not anteriores:
            return None
        return (
            "info",
            "Lo que se organice ahora será un paso nuevo del historial: «Deshacer» lo "
            "revertirá primero, antes que "
            + ("el paso anterior." if anteriores == 1 else f"los {anteriores} pasos anteriores."),
        )

    def detener(self) -> None:
        """Detiene la vigilancia: descarta lo pendiente, guarda el historial y suelta el bloqueo.

        Al volver, el historial ya está guardado y se puede deshacer.
        """
        self._evento_detencion.set()
        self._limpiar()

    def _limpiar(self) -> None:
        """Para el observador, guarda el historial y suelta el bloqueo.

        Se llama al detener y al acabar iniciar(), a veces a la vez desde dos hilos: cada paso
        comprueba su estado y el aviso «Detenido» sale una sola vez.
        """
        with self._bloqueo_limpieza:
            if self.observador.is_alive():
                self.observador.stop()
                self.observador.join()
            self.manejador.detener()
            self.organizador.bloqueo.liberar()
            if not self._detenido_avisado and self.manejador.iniciado:
                self._detenido_avisado = True
                self.notificador("exito", "Vigilancia detenida")
                logger.info("[VIGILANTE] Vigilancia detenida", extra=SOLO_EN_FICHERO)
