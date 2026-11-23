"""Pestaña Cifrado: cifrar y descifrar ficheros y carpetas con una contraseña o un fichero .key.

Usa Cifrador (AES-256-GCM). Las operaciones van en un hilo aparte para no congelar la ventana;
la barra de progreso avanza por ficheros en una carpeta y por bloques en un fichero suelto.
"""

from __future__ import annotations

import threading
import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from file_organizer.cifrador import (
    AccionCifrado,
    Cifrador,
    ResultadoCifrado,
    ResultadoLote,
)
from file_organizer.excepciones import ErrorOrganizador
from file_organizer.gui import tema
from file_organizer.gui.componentes import (
    BarraProgreso,
    BotonRedondeado,
    CabeceraSeccion,
    CampoRedondeado,
    PanelRegistro,
    leer_ruta,
)
from file_organizer.rutas import comprobar_existe

_GERUNDIO = {"cifrar": "Cifrando", "descifrar": "Descifrando"}
_PARTICIPIO = {"cifrar": "Cifrados", "descifrar": "Descifrados"}


class PestanaCifrado:
    """Pestaña de cifrado y descifrado de ficheros y carpetas."""

    def __init__(self, maestro: tk.Misc, fuentes: dict[str, tema.Fuente]) -> None:
        self.fuentes = fuentes
        self._operacion_en_curso = False
        self._metodo = tk.StringVar(value="contrasena")
        self._borrar_original = tk.BooleanVar(value=False)

        self.marco = ttk.Frame(maestro, style="Tarjeta.TFrame")
        self._construir_ui()
        self._actualizar_metodo()

    def _construir_ui(self) -> None:
        """Construye los widgets de la pestaña."""
        contenido = ttk.Frame(self.marco, style="Tarjeta.TFrame")
        contenido.pack(fill="both", expand=True, padx=20, pady=(14, 14))

        CabeceraSeccion(
            contenido, texto="Cifrar / Descifrar", fondo_exterior=tema.FONDO_TARJETA
        ).pack(fill="x")

        ttk.Label(contenido, text="RUTA (fichero o carpeta)", style="Etiqueta.TLabel").pack(
            anchor="w", pady=(12, 6)
        )

        self.ruta = CampoRedondeado(contenido, fondo_exterior=tema.FONDO_TARJETA)
        self.ruta.pack(fill="x")

        fila_botones_ruta = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_botones_ruta.pack(fill="x", pady=(8, 14))

        self.boton_fichero = BotonRedondeado(
            fila_botones_ruta,
            texto="Elegir fichero...",
            variante="secundario",
            comando=self._elegir_archivo,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=14,
            pady=8,
        )
        self.boton_fichero.pack(side="left", padx=(0, 8))

        self.boton_carpeta = BotonRedondeado(
            fila_botones_ruta,
            texto="Elegir carpeta...",
            variante="secundario",
            comando=self._elegir_carpeta,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=14,
            pady=8,
        )
        self.boton_carpeta.pack(side="left")

        # Título y opciones en la misma fila, como la integridad en la pestaña Organizar.
        fila_metodo = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_metodo.pack(fill="x", pady=(0, 8))
        ttk.Label(fila_metodo, text="MÉTODO DE CLAVE", style="Etiqueta.TLabel").pack(
            side="left", padx=(0, 18)
        )

        ttk.Radiobutton(
            fila_metodo,
            text="Contraseña",
            value="contrasena",
            variable=self._metodo,
            command=self._actualizar_metodo,
        ).pack(side="left", padx=(0, 20))

        ttk.Radiobutton(
            fila_metodo,
            text="Fichero de clave",
            value="archivo_clave",
            variable=self._metodo,
            command=self._actualizar_metodo,
        ).pack(side="left")

        self.marco_contrasena = ttk.Frame(contenido, style="Tarjeta.TFrame")
        self.campo_contrasena = CampoRedondeado(
            self.marco_contrasena, fondo_exterior=tema.FONDO_TARJETA, mostrar="*"
        )
        self.campo_contrasena.pack(fill="x")

        # Campo de la ruta de la clave con sus botones a la derecha, en una sola fila.
        self.marco_archivo_clave = ttk.Frame(contenido, style="Tarjeta.TFrame")
        self.campo_archivo_clave = CampoRedondeado(
            self.marco_archivo_clave, fondo_exterior=tema.FONDO_TARJETA
        )

        self.boton_elegir_clave = BotonRedondeado(
            self.marco_archivo_clave,
            texto="Elegir clave...",
            variante="secundario",
            comando=self._elegir_clave,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=14,
            pady=8,
        )
        self.boton_generar_clave = BotonRedondeado(
            self.marco_archivo_clave,
            texto="Generar clave nueva...",
            variante="secundario",
            comando=self._generar_clave,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=14,
            pady=8,
        )
        # Primero los botones: así, si falta espacio, se estrecha el campo y no ellos.
        self.boton_generar_clave.pack(side="right")
        self.boton_elegir_clave.pack(side="right", padx=8)
        self.campo_archivo_clave.pack(side="left", fill="x", expand=True)

        # Los campos de contraseña / fichero de clave se colocan justo antes de esta fila.
        self._fila_opciones = fila_opciones = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_opciones.pack(fill="x", pady=(0, 12))

        ttk.Checkbutton(
            fila_opciones,
            text="Borrar original tras la operación",
            variable=self._borrar_original,
        ).pack(anchor="w")

        fila_botones = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_botones.pack(fill="x", pady=(0, 12))

        self.boton_cifrar = BotonRedondeado(
            fila_botones,
            texto="Cifrar",
            variante="acento",
            comando=self._cifrar,
            fondo_exterior=tema.FONDO_TARJETA,
        )
        self.boton_cifrar.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.boton_descifrar = BotonRedondeado(
            fila_botones,
            texto="Descifrar",
            variante="secundario",
            comando=self._descifrar,
            fondo_exterior=tema.FONDO_TARJETA,
        )
        self.boton_descifrar.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.progreso = BarraProgreso(contenido, en_curso=lambda: self._operacion_en_curso)
        self.progreso.pack(fill="x", pady=(0, 12))

        ttk.Label(contenido, text="RESULTADO", style="Cabecera.TLabel").pack(anchor="w")

        self.panel_registro = PanelRegistro(contenido, self.fuentes["registro"], alto_lineas=6)
        self.panel_registro.pack(fill="both", expand=True, pady=(10, 0))
        self.registro = self.panel_registro.texto

    def _actualizar_metodo(self) -> None:
        """Muestra el campo de contraseña o el de fichero de clave, según el método elegido.

        Se coloca con ``before`` para que salga justo bajo los botones de método y no al final
        de la pestaña, fuera de la ventana.
        """
        if self._metodo.get() == "contrasena":
            self.marco_archivo_clave.pack_forget()
            self.marco_contrasena.pack(fill="x", pady=(0, 12), before=self._fila_opciones)
        else:
            self.marco_contrasena.pack_forget()
            self.marco_archivo_clave.pack(fill="x", pady=(0, 12), before=self._fila_opciones)

    def _registrar_linea(self, nivel: str, etiqueta: str, mensaje: str) -> None:
        """Añade una línea coloreada al panel de resultado."""
        self.panel_registro.registrar(nivel, etiqueta, mensaje)

    def _registrar_atenuado(self, mensaje: str) -> None:
        """Añade una línea atenuada al panel de resultado."""
        self.panel_registro.registrar_atenuado(mensaje)

    def _elegir_archivo(self) -> None:
        """Abre el diálogo para elegir un fichero."""
        self.ruta.elegir_fichero("Selecciona un fichero")

    def _elegir_carpeta(self) -> None:
        """Abre el diálogo para elegir una carpeta."""
        self.ruta.elegir_carpeta("Selecciona una carpeta")

    def _elegir_clave(self) -> None:
        """Abre el diálogo para elegir un fichero de clave."""
        self.campo_archivo_clave.elegir_fichero(
            "Selecciona el fichero de clave", filetypes=[("Clave", "*.key"), ("Todos", "*.*")]
        )

    def _generar_clave(self) -> None:
        """Crea una clave nueva donde indique el usuario y la deja elegida."""
        ruta = filedialog.asksaveasfilename(
            title="Guardar clave nueva", defaultextension=".key", filetypes=[("Clave", "*.key")]
        )
        if not ruta:
            return
        try:
            # El diálogo de «Guardar» ya ha pedido confirmación si el fichero existía.
            ruta_clave = Cifrador.generar_clave(Path(ruta), sobrescribir=True)
            self.campo_archivo_clave.establecer(str(ruta_clave))
            self._registrar_linea("exito", "[ÉXITO]", f"Clave generada: {ruta_clave.name}")
            self._registrar_atenuado(
                "Guárdala en un lugar seguro - sin ella no se puede descifrar."
            )
        except OSError as e:
            self._registrar_linea("error", "[ERROR]", f"No se pudo generar la clave: {e}")

    def _obtener_material_clave(self) -> tuple[str | None, Path | None] | None:
        """``(contraseña, ruta de clave)`` según el método elegido; None si falta (y lo avisa)."""
        if self._metodo.get() == "contrasena":
            contrasena = self.campo_contrasena.obtener()
            if not contrasena:
                self._registrar_linea("aviso", "[AVISO]", "Escribe una contraseña")
                return None
            return contrasena, None
        else:
            archivo_clave = leer_ruta(
                self.campo_archivo_clave,
                comprobar_existe,
                "Elige un fichero de clave",
                self._registrar_linea,
            )
            if archivo_clave is None:
                return None
            return None, archivo_clave

    def _contar_archivos(self, objetivo: Path, modo: str) -> int:
        """Ficheros que cifraría o descifraría la operación (1 si es un fichero suelto)."""
        if objetivo.is_file():
            return 1
        return len(Cifrador.ficheros_de_carpeta(objetivo, AccionCifrado(modo)))

    def _cifrar(self) -> None:
        """Cifra el fichero o la carpeta elegida."""
        self._ejecutar(modo="cifrar")

    def _descifrar(self) -> None:
        """Descifra el fichero o la carpeta elegida."""
        self._ejecutar(modo="descifrar")

    def _ejecutar(self, modo: str) -> None:
        """Comprueba los datos, pide confirmación si son varios ficheros y lanza la operación en un
        hilo aparte.
        """
        if self._operacion_en_curso:
            return

        objetivo = leer_ruta(
            self.ruta, comprobar_existe, "Selecciona un fichero o carpeta", self._registrar_linea
        )
        if objetivo is None:
            return

        material = self._obtener_material_clave()
        if material is None:
            return
        contrasena, ruta_clave = material

        if objetivo.is_dir():
            cantidad = self._contar_archivos(objetivo, modo)
            if cantidad == 0:
                self._registrar_atenuado(f"No hay ficheros para {modo}.")
                return
            if cantidad > 1:
                confirmado = messagebox.askyesno(
                    modo.capitalize(),
                    f"Se van a {modo} {cantidad} ficheros dentro de esta carpeta.\n\n¿Deseas continuar?",
                )
                if not confirmado:
                    return

        self._establecer_botones_habilitados(False)
        self._operacion_en_curso = True
        self.progreso.empezar()
        self._registrar_linea("info", "[INFO]", f"{_GERUNDIO[modo]}: {objetivo.name or objetivo}")

        threading.Thread(
            target=self._trabajador,
            args=(objetivo, contrasena, ruta_clave, self._borrar_original.get(), modo),
            daemon=True,
        ).start()

    def _trabajador(
        self,
        objetivo: Path,
        contrasena: str | None,
        ruta_clave: Path | None,
        borrar_original: bool,
        modo: str,
    ) -> None:
        """Hilo de trabajo: cifra o descifra y pasa el resultado a la interfaz."""
        accion = AccionCifrado(modo)
        try:
            if objetivo.is_dir():
                resultado = Cifrador.procesar_carpeta(
                    objetivo,
                    accion,
                    contrasena=contrasena,
                    ruta_clave=ruta_clave,
                    borrar_original=borrar_original,
                    progreso=self.progreso.apuntar,  # por ficheros
                )
                self.marco.after(0, lambda: self._lote_terminado(resultado, modo, None))
            else:
                r = Cifrador.procesar_archivo(
                    objetivo,
                    accion,
                    contrasena=contrasena,
                    ruta_clave=ruta_clave,
                    borrar_original=borrar_original,
                    progreso=self.progreso.apuntar,  # por bytes
                )
                self.marco.after(0, lambda: self._individual_terminado(r, None))

        except ErrorOrganizador as e:
            self.marco.after(0, partial(self._individual_terminado, None, e))
        except Exception as e:
            self.marco.after(0, partial(self._individual_terminado, None, e))

    def _individual_terminado(
        self, resultado: ResultadoCifrado | None, error: Exception | None
    ) -> None:
        """Muestra en el registro el resultado de cifrar o descifrar un fichero."""
        self._establecer_botones_habilitados(True)
        self._operacion_en_curso = False
        self.progreso.terminar(completada=error is None and resultado is not None)

        if error is not None or resultado is None:
            self._registrar_linea(
                "error", "[ERROR]", str(error or "La operación no devolvió resultado")
            )
            return

        self._registrar_linea("exito", "[ÉXITO]", resultado.mensaje)

    def _lote_terminado(
        self, resultado: ResultadoLote | None, modo: str, error: Exception | None
    ) -> None:
        """Muestra en el registro el resultado de cifrar o descifrar una carpeta."""
        self._establecer_botones_habilitados(True)
        self._operacion_en_curso = False
        self.progreso.terminar(
            completada=error is None and resultado is not None and resultado.total > 0
        )

        if error is not None or resultado is None:
            self._registrar_linea(
                "error", "[ERROR]", str(error or "La operación no devolvió resultado")
            )
            return

        for r in resultado.resultados:
            self._registrar_linea("exito", "[ÉXITO]", r.mensaje)
        for fichero, err in resultado.errores:
            self._registrar_linea("error", "[ERROR]", f"{fichero.name}: {err}")

        self._registrar_atenuado(
            f"\n{_PARTICIPIO[modo]}: {resultado.exitosos} | Fallidos: {resultado.fallidos}"
        )

    def _establecer_botones_habilitados(self, habilitados: bool) -> None:
        """Habilita o deshabilita los botones mientras hay una operación en curso."""
        self.boton_cifrar.establecer_habilitado(habilitados)
        self.boton_descifrar.establecer_habilitado(habilitados)
        self.boton_fichero.establecer_habilitado(habilitados)
        self.boton_carpeta.establecer_habilitado(habilitados)
        self.boton_elegir_clave.establecer_habilitado(habilitados)
        self.boton_generar_clave.establecer_habilitado(habilitados)
