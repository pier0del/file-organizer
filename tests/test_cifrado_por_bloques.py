"""Tests del cifrado por bloques (AES-256-GCM, construcción STREAM).

Comprueban que la memoria no depende del tamaño del fichero, que se detecta cualquier
manipulación (bloques truncados, reordenados, añadidos o cabecera alterada), que un
fallo nunca deja ficheros a medias y que los ficheros del formato antiguo (Fernet)
se siguen pudiendo descifrar.
"""

import base64
import os
import tracemalloc
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from file_organizer import cifrador as modulo_cifrador
from file_organizer.cifrador import (
    CABECERA_MAGICA,
    MODO_ARCHIVO_CLAVE,
    MODO_ARCHIVO_CLAVE_FERNET,
    MODO_CONTRASENA,
    MODO_CONTRASENA_FERNET,
    TAMANO_CABECERA,
    TAMANO_ETIQUETA_GCM,
    AccionCifrado,
    Cifrador,
)
from file_organizer.excepciones import (
    ErrorArchivoCifradoCorrupto,
    ErrorArchivoClaveInvalido,
    ErrorContrasenaInvalida,
)

CONTRASENA = "ClaveSegura123"
BLOQUE_PEQUENO = 64


@pytest.fixture
def bloques_pequenos(monkeypatch: pytest.MonkeyPatch) -> int:
    """Usa bloques de 64 bytes para probar los límites entre bloques con ficheros pequeños."""
    monkeypatch.setattr(modulo_cifrador, "TAMANO_BLOQUE_CIFRADO", BLOQUE_PEQUENO)
    return BLOQUE_PEQUENO


@pytest.fixture
def clave(tmp_path: Path) -> Path:
    """Un fichero .key recién generado."""
    return Cifrador.generar_clave(tmp_path / "proyecto.key")


def _crear(tmp_path: Path, nombre: str, contenido: bytes) -> Path:
    """Crea un fichero con ese contenido."""
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return ruta


def _bloques(ruta_cifrada: Path, tamano_bloque: int) -> tuple[bytes, list[bytes]]:
    """Separa un .enc en su cabecera y la lista de bloques cifrados."""
    datos = ruta_cifrada.read_bytes()
    cabecera, cuerpo = datos[:TAMANO_CABECERA], datos[TAMANO_CABECERA:]
    tamano = tamano_bloque + TAMANO_ETIQUETA_GCM
    return cabecera, [cuerpo[i : i + tamano] for i in range(0, len(cuerpo), tamano)]


def _ficheros_en(carpeta: Path) -> set[str]:
    """Nombres de lo que hay en la carpeta, ocultos incluidos."""
    return {f.name for f in carpeta.iterdir()}


class TestIdaYVuelta:
    """Lo que se cifra se descifra igual, byte a byte."""

    @pytest.mark.parametrize(
        "tamano",
        [0, 1, BLOQUE_PEQUENO - 1, BLOQUE_PEQUENO, BLOQUE_PEQUENO + 1, 3 * BLOQUE_PEQUENO + 5],
    )
    def test_tamanos_en_los_limites_de_bloque(
        self, tmp_path: Path, bloques_pequenos: int, tamano: int
    ):
        """Vacío, un byte, justo un bloque, uno de más o uno de menos: todos vuelven igual."""
        original = os.urandom(tamano)
        fichero = _crear(tmp_path, "datos.bin", original)

        cifrado = Cifrador.cifrar_archivo(fichero, contrasena=CONTRASENA, borrar_original=True)
        descifrado = Cifrador.descifrar_archivo(cifrado.ruta, contrasena=CONTRASENA)

        assert descifrado.ruta.read_bytes() == original

    def test_con_archivo_de_clave(self, tmp_path: Path, bloques_pequenos: int, clave: Path):
        """Con fichero .key y varios bloques, también."""
        original = os.urandom(5 * BLOQUE_PEQUENO + 7)
        fichero = _crear(tmp_path, "datos.bin", original)

        cifrado = Cifrador.cifrar_archivo(fichero, ruta_clave=clave, borrar_original=True)
        descifrado = Cifrador.descifrar_archivo(cifrado.ruta, ruta_clave=clave)

        assert descifrado.ruta.read_bytes() == original


class TestFormato:
    """Cómo queda el ``.enc`` por dentro."""

    def test_cabecera_y_numero_de_bloques(self, tmp_path: Path, bloques_pequenos: int):
        """La cabecera dice el modo y el tamaño de bloque; cada bloque lleva su etiqueta GCM y el
        último, lo que sobra.
        """
        fichero = _crear(tmp_path, "datos.bin", os.urandom(2 * BLOQUE_PEQUENO + 10))

        cifrado = Cifrador.cifrar_archivo(fichero, contrasena=CONTRASENA)
        cabecera, bloques = _bloques(cifrado.ruta, BLOQUE_PEQUENO)

        assert cabecera[:4] == CABECERA_MAGICA
        assert cabecera[4:5] == MODO_CONTRASENA
        assert int.from_bytes(cabecera[-4:], "big") == BLOQUE_PEQUENO
        # 2 bloques completos + 1 bloque final de 10 bytes, cada uno con su etiqueta GCM.
        assert [len(b) for b in bloques] == [
            BLOQUE_PEQUENO + TAMANO_ETIQUETA_GCM,
            BLOQUE_PEQUENO + TAMANO_ETIQUETA_GCM,
            10 + TAMANO_ETIQUETA_GCM,
        ]

    def test_modo_archivo_de_clave_en_la_cabecera(self, tmp_path: Path, clave: Path):
        """La cabecera dice que se cifró con fichero .key."""
        fichero = _crear(tmp_path, "datos.bin", b"hola")
        cifrado = Cifrador.cifrar_archivo(fichero, ruta_clave=clave)
        assert cifrado.ruta.read_bytes()[4:5] == MODO_ARCHIVO_CLAVE

    def test_misma_clave_y_contenido_dan_cifrados_distintos(self, tmp_path: Path, clave: Path):
        """El salt aleatorio hace que cada cifrado sea distinto aunque todo lo demás sea igual."""
        a = Cifrador.cifrar_archivo(_crear(tmp_path, "a.txt", b"igual"), ruta_clave=clave)
        b = Cifrador.cifrar_archivo(_crear(tmp_path, "b.txt", b"igual"), ruta_clave=clave)
        assert a.ruta.read_bytes() != b.ruta.read_bytes()


class TestMemoriaConstante:
    """La memoria usada no depende del tamaño del fichero."""

    def test_no_lee_el_fichero_entero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Ni cifrar ni descifrar leen el fichero entero de una vez."""
        fichero = _crear(tmp_path, "datos.bin", os.urandom(300_000))

        def prohibido(self: Path) -> bytes:
            raise AssertionError(f"Se ha leído entero en memoria: {self}")

        monkeypatch.setattr(Path, "read_bytes", prohibido)

        cifrado = Cifrador.cifrar_archivo(fichero, contrasena=CONTRASENA, borrar_original=True)
        Cifrador.descifrar_archivo(cifrado.ruta, contrasena=CONTRASENA)

    def test_pico_de_memoria_no_depende_del_tamano(self, tmp_path: Path):
        """Con un fichero de 16 MB, el pico de memoria se queda por debajo de 2 MB."""
        tamano = 16 * 1024 * 1024
        fichero = tmp_path / "grande.bin"
        with open(fichero, "wb") as f:
            for _ in range(tamano // (1024 * 1024)):
                f.write(os.urandom(1024 * 1024))

        tracemalloc.start()
        try:
            cifrado = Cifrador.cifrar_archivo(fichero, contrasena=CONTRASENA, borrar_original=True)
            Cifrador.descifrar_archivo(cifrado.ruta, contrasena=CONTRASENA)
            _, pico = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        # Con el fichero entero en memoria el pico superaría los 16 MB.
        assert pico < 2 * 1024 * 1024, f"pico de memoria: {pico / 1024 / 1024:.1f} MB"
        assert (tmp_path / "grande.bin").stat().st_size == tamano


class TestManipulacion:
    """Cualquier cambio en un ``.enc`` se detecta al descifrar, con el bloque afectado."""

    @pytest.fixture
    def cifrado(self, tmp_path: Path, bloques_pequenos: int) -> Path:
        """Un ``.enc`` de cinco bloques pequeños, sin su original."""
        fichero = _crear(tmp_path, "datos.bin", os.urandom(4 * BLOQUE_PEQUENO + 3))
        resultado = Cifrador.cifrar_archivo(fichero, contrasena=CONTRASENA, borrar_original=True)
        return resultado.ruta

    def _reescribir(self, ruta: Path, cabecera: bytes, bloques: list[bytes]) -> None:
        """Vuelve a escribir el ``.enc`` con esa cabecera y esos bloques."""
        ruta.write_bytes(cabecera + b"".join(bloques))

    def test_truncado_en_frontera_de_bloque(self, cifrado: Path):
        """Quitar el último bloque entero se detecta: el que queda no está marcado como último."""
        cabecera, bloques = _bloques(cifrado, BLOQUE_PEQUENO)
        self._reescribir(cifrado, cabecera, bloques[:-1])
        with pytest.raises(ErrorArchivoCifradoCorrupto):
            Cifrador.descifrar_archivo(cifrado, contrasena=CONTRASENA)

    def test_truncado_a_mitad_de_bloque(self, cifrado: Path):
        """Cortar el final de un bloque se detecta."""
        datos = cifrado.read_bytes()
        cifrado.write_bytes(datos[:-5])
        with pytest.raises(ErrorArchivoCifradoCorrupto):
            Cifrador.descifrar_archivo(cifrado, contrasena=CONTRASENA)

    def test_bloques_reordenados(self, cifrado: Path):
        """Cambiar dos bloques de sitio se detecta en el primero de ellos."""
        cabecera, bloques = _bloques(cifrado, BLOQUE_PEQUENO)
        bloques[1], bloques[2] = bloques[2], bloques[1]
        self._reescribir(cifrado, cabecera, bloques)
        with pytest.raises(ErrorArchivoCifradoCorrupto, match="bloque 1"):
            Cifrador.descifrar_archivo(cifrado, contrasena=CONTRASENA)

    def test_datos_anadidos_al_final(self, cifrado: Path):
        """Añadir datos detrás del último bloque se detecta."""
        with open(cifrado, "ab") as f:
            f.write(b"basura")
        with pytest.raises(ErrorArchivoCifradoCorrupto):
            Cifrador.descifrar_archivo(cifrado, contrasena=CONTRASENA)

    def test_byte_modificado_en_un_bloque(self, cifrado: Path):
        """Cambiar un solo byte se detecta y se dice en qué bloque."""
        datos = bytearray(cifrado.read_bytes())
        datos[TAMANO_CABECERA + 2 * (BLOQUE_PEQUENO + TAMANO_ETIQUETA_GCM) + 3] ^= 0xFF
        cifrado.write_bytes(bytes(datos))
        with pytest.raises(ErrorArchivoCifradoCorrupto, match="bloque 2"):
            Cifrador.descifrar_archivo(cifrado, contrasena=CONTRASENA)

    def test_cabecera_manipulada(self, cifrado: Path):
        """Cambiar el salt da otra clave: se detecta en el primer bloque, como una contraseña
        incorrecta.
        """
        datos = bytearray(cifrado.read_bytes())
        datos[10] ^= 0xFF  # un byte del salt
        cifrado.write_bytes(bytes(datos))
        with pytest.raises(ErrorContrasenaInvalida):
            Cifrador.descifrar_archivo(cifrado, contrasena=CONTRASENA)

    def test_tamano_de_bloque_absurdo_en_la_cabecera(self, cifrado: Path):
        """Un tamaño de bloque imposible en la cabecera se rechaza antes de reservar memoria."""
        datos = bytearray(cifrado.read_bytes())
        datos[TAMANO_CABECERA - 4 : TAMANO_CABECERA] = (2**31).to_bytes(4, "big")
        cifrado.write_bytes(bytes(datos))
        with pytest.raises(ErrorArchivoCifradoCorrupto, match="cabecera no es válida"):
            Cifrador.descifrar_archivo(cifrado, contrasena=CONTRASENA)

    def test_cabecera_incompleta(self, cifrado: Path):
        """Un fichero más corto que la cabecera se rechaza."""
        cifrado.write_bytes(cifrado.read_bytes()[:10])
        with pytest.raises(ErrorArchivoCifradoCorrupto):
            Cifrador.descifrar_archivo(cifrado, contrasena=CONTRASENA)


class TestSinFicherosAMedias:
    """Si descifrar falla, no queda nada a medias."""

    def test_fallo_al_descifrar_no_deja_ficheros(self, tmp_path: Path, bloques_pequenos: int):
        """Un fallo en el último bloque no deja ni lo descifrado hasta ahí ni el temporal."""
        fichero = _crear(tmp_path, "datos.bin", os.urandom(3 * BLOQUE_PEQUENO))
        cifrado = Cifrador.cifrar_archivo(fichero, contrasena=CONTRASENA, borrar_original=True)
        datos = cifrado.ruta.read_bytes()
        cifrado.ruta.write_bytes(datos[:-5])  # el error salta en el último bloque
        antes = _ficheros_en(tmp_path)

        with pytest.raises(ErrorArchivoCifradoCorrupto):
            Cifrador.descifrar_archivo(cifrado.ruta, contrasena=CONTRASENA)

        # Ni el fichero descifrado a medias ni el temporal.
        assert _ficheros_en(tmp_path) == antes

    def test_contrasena_incorrecta_no_deja_ficheros(self, tmp_path: Path):
        """Con la contraseña mal no queda ningún fichero nuevo."""
        cifrado = Cifrador.cifrar_archivo(
            _crear(tmp_path, "a.txt", b"secreto"), contrasena=CONTRASENA, borrar_original=True
        )
        antes = _ficheros_en(tmp_path)

        with pytest.raises(ErrorContrasenaInvalida):
            Cifrador.descifrar_archivo(cifrado.ruta, contrasena="otra")

        assert _ficheros_en(tmp_path) == antes

    def test_fallo_no_borra_el_original(self, tmp_path: Path):
        """Aunque se pidiera borrar el original, si falla el ``.enc`` se queda."""
        cifrado = Cifrador.cifrar_archivo(_crear(tmp_path, "a.txt", b"x"), contrasena=CONTRASENA)
        with pytest.raises(ErrorContrasenaInvalida):
            Cifrador.descifrar_archivo(cifrado.ruta, contrasena="otra", borrar_original=True)
        assert cifrado.ruta.exists()


class TestCompatibilidadFormatoAntiguo:
    """Ficheros .enc creados por versiones anteriores (Fernet sobre el fichero entero)."""

    def test_descifra_antiguo_con_contrasena(self, tmp_path: Path):
        """Un Fernet con contraseña se descifra."""
        salt = os.urandom(16)
        clave = base64.urlsafe_b64encode(Cifrador._derivar_clave_de_contrasena(CONTRASENA, salt))
        token = Fernet(clave).encrypt(b"contenido antiguo")
        antiguo = _crear(
            tmp_path, "viejo.txt.enc", CABECERA_MAGICA + MODO_CONTRASENA_FERNET + salt + token
        )

        resultado = Cifrador.descifrar_archivo(antiguo, contrasena=CONTRASENA)

        assert resultado.ruta.read_bytes() == b"contenido antiguo"

    def test_descifra_antiguo_con_archivo_de_clave(self, tmp_path: Path, clave: Path):
        """Un Fernet con fichero .key se descifra."""
        token = Fernet(clave.read_bytes()).encrypt(b"contenido antiguo")
        antiguo = _crear(
            tmp_path, "viejo.txt.enc", CABECERA_MAGICA + MODO_ARCHIVO_CLAVE_FERNET + token
        )

        resultado = Cifrador.descifrar_archivo(antiguo, ruta_clave=clave)

        assert resultado.ruta.read_bytes() == b"contenido antiguo"

    def test_antiguo_con_contrasena_incorrecta(self, tmp_path: Path):
        """Con la contraseña mal: ErrorContrasenaInvalida, como en el formato actual."""
        salt = os.urandom(16)
        clave = base64.urlsafe_b64encode(Cifrador._derivar_clave_de_contrasena("otra", salt))
        token = Fernet(clave).encrypt(b"x")
        antiguo = _crear(
            tmp_path, "viejo.txt.enc", CABECERA_MAGICA + MODO_CONTRASENA_FERNET + salt + token
        )
        with pytest.raises(ErrorContrasenaInvalida):
            Cifrador.descifrar_archivo(antiguo, contrasena=CONTRASENA)


class TestArchivoDeClaveInvalido:
    """Un fichero .key que no contiene una clave válida."""

    @pytest.mark.parametrize("contenido", [b"esto no es base64 !!", b"", b"c2hvcnQ="])
    def test_cifrar_con_clave_mal_formada(self, tmp_path: Path, contenido: bytes):
        """Si no es base64, está vacío o es demasiado corto: ErrorArchivoClaveInvalido."""
        clave = _crear(tmp_path, "mala.key", contenido)
        with pytest.raises(ErrorArchivoClaveInvalido):
            Cifrador.cifrar_archivo(_crear(tmp_path, "a.txt", b"x"), ruta_clave=clave)

    def test_lote_con_clave_mal_formada_no_aborta(self, tmp_path: Path):
        """Al cifrar una carpeta, cada fichero falla con ese error, sin cortar a la mitad."""
        clave = _crear(tmp_path, "mala.key", b"no es una clave")
        carpeta = tmp_path / "docs"
        carpeta.mkdir()
        _crear(carpeta, "a.txt", b"a")
        _crear(carpeta, "b.txt", b"b")

        resultado = Cifrador.procesar_carpeta(carpeta, AccionCifrado.CIFRAR, ruta_clave=clave)

        assert resultado.fallidos == 2
        assert all(isinstance(e, ErrorArchivoClaveInvalido) for _, e in resultado.errores)


class TestProgresoDeUnFichero:
    """Cifrar o descifrar un fichero avisa del progreso tras cada bloque (en bytes)."""

    def test_cifrar_avisa_tras_cada_bloque(self, tmp_path: Path):
        """Con 150 KB y bloques de 64 KB: tres avisos, el último con el tamaño entero."""
        datos = os.urandom(150 * 1024)
        fichero = _crear(tmp_path, "datos.bin", datos)
        avances: list[tuple[int, int]] = []

        Cifrador.cifrar_archivo(
            fichero, contrasena=CONTRASENA, progreso=lambda a, t: avances.append((a, t))
        )

        assert avances == [(65536, len(datos)), (131072, len(datos)), (len(datos), len(datos))]

    def test_descifrar_avisa_hasta_el_final_del_fichero(self, tmp_path: Path):
        """Al descifrar, los avisos crecen y el último es el tamaño del ``.enc``."""
        fichero = _crear(tmp_path, "datos.bin", os.urandom(150 * 1024))
        cifrado = Cifrador.cifrar_archivo(fichero, contrasena=CONTRASENA).ruta
        fichero.unlink()
        avances: list[tuple[int, int]] = []

        Cifrador.procesar_archivo(
            cifrado,
            AccionCifrado.DESCIFRAR,
            contrasena=CONTRASENA,
            progreso=lambda a, t: avances.append((a, t)),
        )

        tamano = cifrado.stat().st_size
        assert len(avances) == 3
        assert avances[-1] == (tamano, tamano)
        assert [a for a, _ in avances] == sorted(a for a, _ in avances)

    def test_un_fichero_vacio_tambien_termina(self, tmp_path: Path):
        """Un fichero vacío da un aviso, para que la barra llegue al final."""
        fichero = _crear(tmp_path, "vacio.txt", b"")
        avances: list[tuple[int, int]] = []
        Cifrador.cifrar_archivo(
            fichero, contrasena=CONTRASENA, progreso=lambda a, t: avances.append((a, t))
        )
        assert avances == [(0, 0)]

    def test_formato_antiguo_avisa_al_terminar(self, tmp_path: Path):
        """El formato antiguo se descifra de una vez: un solo aviso, al terminar."""
        salt = os.urandom(16)
        clave = base64.urlsafe_b64encode(Cifrador._derivar_clave_de_contrasena(CONTRASENA, salt))
        token = Fernet(clave).encrypt(b"contenido antiguo")
        datos = CABECERA_MAGICA + MODO_CONTRASENA_FERNET + salt + token
        antiguo = _crear(tmp_path, "viejo.txt.enc", datos)
        avances: list[tuple[int, int]] = []

        Cifrador.descifrar_archivo(
            antiguo, contrasena=CONTRASENA, progreso=lambda a, t: avances.append((a, t))
        )

        assert avances == [(len(datos), len(datos))]
