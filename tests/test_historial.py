"""Tests del almacén del historial por sí solo: leer y escribir el fichero, sin mover nada."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from file_organizer.excepciones import ErrorHistorialCorrupto, ErrorHistorialNoEncontrado
from file_organizer.historial import (
    NOMBRE_ARCHIVO_HISTORIAL,
    TIPO_ORGANIZAR,
    TIPO_VIGILANCIA,
    AlmacenHistorial,
    Operacion,
    RegistroMovimiento,
)
from file_organizer.organizador import OrganizadorArchivos


@pytest.fixture
def almacen(tmp_path: Path) -> AlmacenHistorial:
    """Almacén del historial de una carpeta organizada en ella misma."""
    return AlmacenHistorial(tmp_path.resolve(), tmp_path.resolve())


def _registro(carpeta: Path, nombre: str, categoria: str) -> RegistroMovimiento:
    """El movimiento de ``nombre`` a la carpeta ``categoria``."""
    return RegistroMovimiento(
        origen=carpeta / nombre,
        destino=carpeta / categoria / nombre,
        categoria=categoria,
        fecha=datetime(2026, 11, 13, 10, 30),
        checksum="abc",
        tamano=3,
        mtime_ns=123,
    )


def test_lo_guardado_se_lee_igual(almacen: AlmacenHistorial, tmp_path: Path):
    """Dos pasos guardados se leen idénticos, y el fichero lleva la versión 3 del formato."""
    carpeta = tmp_path.resolve()
    pasos = [
        Operacion(
            TIPO_ORGANIZAR,
            datetime(2026, 11, 13, 10, 0),
            [_registro(carpeta, "a.pdf", "Documentos")],
        ),
        Operacion(
            TIPO_VIGILANCIA,
            datetime(2026, 11, 13, 11, 0),
            [_registro(carpeta, "b.jpg", "Imagenes")],
        ),
    ]

    almacen.guardar(pasos)

    assert almacen.cargar() == pasos
    assert (
        json.loads((tmp_path / NOMBRE_ARCHIVO_HISTORIAL).read_text(encoding="utf-8"))["version"]
        == 3
    )


def test_sin_fichero_no_hay_historial(almacen: AlmacenHistorial):
    """Sin fichero, cargar lanza ErrorHistorialNoEncontrado."""
    with pytest.raises(ErrorHistorialNoEncontrado):
        almacen.cargar()


def test_el_historial_de_otras_carpetas_no_se_usa(tmp_path: Path):
    """Un historial guardado para otro origen se rechaza como de otras carpetas."""
    carpeta = tmp_path.resolve()
    AlmacenHistorial(carpeta, carpeta).guardar(
        [
            Operacion(
                TIPO_ORGANIZAR, datetime(2026, 1, 1), [_registro(carpeta, "a.pdf", "Documentos")]
            )
        ]
    )
    otro_origen = carpeta / "otra"

    with pytest.raises(ErrorHistorialCorrupto, match="es de otras carpetas"):
        AlmacenHistorial(otro_origen, carpeta).cargar()


def test_apartar_lo_renombra_y_deja_sitio_a_uno_nuevo(almacen: AlmacenHistorial, tmp_path: Path):
    """Apartar guarda una copia con el contenido tal cual y deja libre el nombre; si no hay nada,
    devuelve None.
    """
    (tmp_path / NOMBRE_ARCHIVO_HISTORIAL).write_text("{ roto")

    copia = almacen.apartar()

    assert copia is not None and copia.read_text() == "{ roto"
    assert not (tmp_path / NOMBRE_ARCHIVO_HISTORIAL).exists()
    assert almacen.apartar() is None  # ya no queda nada que apartar


def test_sin_carpetas_configuradas_no_se_escribe_nada():
    """Sin carpetas de origen y destino, guardar falla en lugar de escribir donde sea."""
    with pytest.raises(ErrorHistorialCorrupto, match="No se ha configurado"):
        AlmacenHistorial(None, None).guardar([])


def test_un_historial_antiguo_con_fechas_mezcladas_es_corrupto(tmp_path: Path):
    """Fechas con y sin zona horaria no se pueden comparar: es un formato inválido, no un
    error sin tratar."""
    carpeta = tmp_path.resolve()
    registros = [
        {
            "origen": str(carpeta / n),
            "destino": str(carpeta / "D" / n),
            "categoria": "D",
            "fecha": f,
        }
        for n, f in (("a", "2026-01-01T10:00:00"), ("b", "2026-01-01T10:00:00+00:00"))
    ]
    (carpeta / NOMBRE_ARCHIVO_HISTORIAL).write_text(
        json.dumps(
            {
                "version": 2,
                "carpeta_origen": str(carpeta),
                "carpeta_destino": str(carpeta),
                "registros": registros,
            }
        )
    )
    with pytest.raises(ErrorHistorialCorrupto, match="está dañado"):
        AlmacenHistorial(carpeta, carpeta).cargar()


def _historial_antiguo(
    carpeta_origen: Path, carpeta_destino: Path, registros: list[tuple[str, str, str]], tipo: str
) -> None:
    """Escribe un historial como los de antes: rutas de ficheros relativas (origen, destino,
    categoría), tal como quedaban al organizar con ``--carpeta Descargas``."""
    datos = {
        "version": 3,
        "carpeta_origen": str(carpeta_origen),
        "carpeta_destino": str(carpeta_destino),
        "operaciones": [
            {
                "tipo": tipo,
                "inicio": "2026-11-01T10:00:00",
                "registros": [
                    {"origen": o, "destino": d, "categoria": c, "fecha": "2026-11-01T10:00:00"}
                    for o, d, c in registros
                ],
            }
        ],
    }
    (carpeta_destino / NOMBRE_ARCHIVO_HISTORIAL).write_text(json.dumps(datos), encoding="utf-8")


class TestRutasRelativas:
    """Organizar con una ruta relativa ya no deja un historial que solo vale desde una carpeta."""

    def test_se_guardan_rutas_completas_y_se_deshace_desde_otra_carpeta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """El caso del fallo: organizar con «--carpeta Descargas» y deshacer desde otro sitio."""
        descargas = tmp_path / "Descargas"
        descargas.mkdir()
        (descargas / "foto.jpg").write_text("x")
        monkeypatch.chdir(tmp_path)
        OrganizadorArchivos(carpeta_origen=Path("Descargas")).ejecutar()

        datos = json.loads((descargas / NOMBRE_ARCHIVO_HISTORIAL).read_text(encoding="utf-8"))
        registro = datos["operaciones"][0]["registros"][0]
        assert Path(registro["origen"]).is_absolute() and Path(registro["destino"]).is_absolute()

        otra = tmp_path / "otra"
        otra.mkdir()
        monkeypatch.chdir(otra)
        resultado = OrganizadorArchivos(carpeta_origen=descargas).deshacer()
        assert resultado is not None and resultado.exitosos == 1
        assert (descargas / "foto.jpg").exists()

    @pytest.mark.parametrize(
        ("origen", "destino", "categoria", "esperado_origen", "esperado_destino"),
        [
            # --carpeta Descargas
            ("Descargas/a.jpg", "Descargas/Imagenes/a.jpg", "Imagenes", "a.jpg", "Imagenes/a.jpg"),
            # --carpeta . (desde la propia carpeta)
            ("a.jpg", "Imagenes/a.jpg", "Imagenes", "a.jpg", "Imagenes/a.jpg"),
            # --carpeta ../Descargas
            (
                "../Descargas/a.jpg",
                "../Descargas/Imagenes/a.jpg",
                "Imagenes",
                "a.jpg",
                "Imagenes/a.jpg",
            ),
            # renombrado para no pisar otro, y una categoría de varios niveles
            (
                "Descargas/a.jpg",
                "Descargas/Imagenes/2024/03/a_1.jpg",
                "Imagenes/2024/03",
                "a.jpg",
                "Imagenes/2024/03/a_1.jpg",
            ),
            # vigilando subcarpetas: el fichero venía de una
            (
                "Descargas/viaje/a.jpg",
                "Descargas/Imagenes/a.jpg",
                "Imagenes",
                "viaje/a.jpg",
                "Imagenes/a.jpg",
            ),
        ],
        ids=["relativa", "punto", "dos-puntos", "renombrado-y-fecha", "subcarpeta"],
    )
    def test_se_completan_las_de_historiales_antiguos(
        self,
        tmp_path: Path,
        origen: str,
        destino: str,
        categoria: str,
        esperado_origen: str,
        esperado_destino: str,
    ):
        """Sin saber desde dónde se guardaron: el destino sale de carpeta, categoría y nombre, y
        el origen, de lo que sigue al comienzo que comparte con el destino."""
        carpeta = tmp_path.resolve()
        _historial_antiguo(carpeta, carpeta, [(origen, destino, categoria)], TIPO_VIGILANCIA)

        registro = AlmacenHistorial(carpeta, carpeta).cargar()[0].registros[0]

        assert registro.origen == carpeta / esperado_origen
        assert registro.destino == carpeta / esperado_destino

    def test_con_otra_carpeta_de_destino_el_origen_estaba_suelto(self, tmp_path: Path):
        """Si las carpetas son distintas, el fichero salió de la raíz del origen (lo que
        organiza «organizar»)."""
        origen = (tmp_path / "Descargas").resolve()
        destino = (tmp_path / "Ordenado").resolve()
        destino.mkdir()
        _historial_antiguo(
            origen,
            destino,
            [("Descargas/a.jpg", "Ordenado/Imagenes/a.jpg", "Imagenes")],
            TIPO_ORGANIZAR,
        )

        registro = AlmacenHistorial(origen, destino).cargar()[0].registros[0]

        assert (registro.origen, registro.destino) == (origen / "a.jpg", destino / "Imagenes/a.jpg")

    def test_una_categoria_manipulada_no_saca_nada_de_la_carpeta(self, tmp_path: Path):
        """Lo completado se comprueba como cualquier ruta: una categoría con «..» se rechaza."""
        carpeta = tmp_path.resolve()
        _historial_antiguo(
            carpeta, carpeta, [("a.jpg", "../../fuera/a.jpg", "../../fuera")], TIPO_ORGANIZAR
        )

        with pytest.raises(ErrorHistorialCorrupto, match="fuera de estas carpetas"):
            AlmacenHistorial(carpeta, carpeta).cargar()

    def test_al_guardar_ya_quedan_completas(self, tmp_path: Path):
        """Tras leer un historial antiguo, lo que se guarda lleva rutas completas."""
        carpeta = tmp_path.resolve()
        _historial_antiguo(
            carpeta, carpeta, [("a.jpg", "Imagenes/a.jpg", "Imagenes")], TIPO_ORGANIZAR
        )
        almacen = AlmacenHistorial(carpeta, carpeta)

        almacen.guardar(almacen.cargar())

        datos = json.loads((carpeta / NOMBRE_ARCHIVO_HISTORIAL).read_text(encoding="utf-8"))
        assert datos["operaciones"][0]["registros"][0]["origen"] == str(carpeta / "a.jpg")
