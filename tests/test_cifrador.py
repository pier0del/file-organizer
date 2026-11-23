"""Tests del cifrador: generar claves, cifrar y descifrar ficheros con contraseña o con fichero
.key, los errores de cada caso, los permisos de lo que se escribe y las operaciones sobre carpetas
enteras.
"""

import stat
import sys
from pathlib import Path

import pytest

from file_organizer.cifrador import SUFIJO_CIFRADO, AccionCifrado, Cifrador
from file_organizer.excepciones import (
    ErrorArchivoCifradoCorrupto,
    ErrorArchivoClaveNoEncontrado,
    ErrorCarpetaInvalida,
    ErrorClaveAmbigua,
    ErrorContrasenaInvalida,
    ErrorFaltaClave,
    ErrorNoCifrado,
    ErrorYaCifrado,
)


@pytest.fixture
def cifrador() -> Cifrador:
    """Un cifrador (no guarda estado: sus métodos son estáticos)."""
    return Cifrador()


@pytest.fixture
def fichero_texto(tmp_path: Path) -> Path:
    """Un fichero de texto con contenido «confidencial»."""
    f = tmp_path / "secreto.txt"
    f.write_text("Esta es una información confidencial", encoding="utf-8")
    return f


@pytest.fixture
def fichero_binario(tmp_path: Path) -> Path:
    """Un fichero binario con todos los valores de byte."""
    f = tmp_path / "imagen.bin"
    f.write_bytes(bytes(range(256)) * 4)
    return f


@pytest.fixture
def clave(tmp_path: Path, cifrador: Cifrador) -> Path:
    """Un fichero .key recién generado."""
    return cifrador.generar_clave(tmp_path / "proyecto.key")


class TestGenerarClave:
    """Generar un fichero de clave."""

    def test_crea_el_fichero(self, tmp_path: Path, cifrador: Cifrador):
        """Se crea el fichero en la ruta pedida."""
        ruta_clave = cifrador.generar_clave(tmp_path / "mi.key")
        assert ruta_clave.exists()

    def test_el_fichero_no_esta_vacio(self, tmp_path: Path, cifrador: Cifrador):
        """El fichero lleva la clave dentro."""
        ruta_clave = cifrador.generar_clave(tmp_path / "mi.key")
        assert ruta_clave.stat().st_size > 0

    def test_crea_carpeta_padre_si_no_existen(self, tmp_path: Path, cifrador: Cifrador):
        """Se crean las carpetas que falten."""
        ruta_clave = cifrador.generar_clave(tmp_path / "sub" / "carpeta" / "mi.key")
        assert ruta_clave.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX no aplican en windows")
    def test_permisos_son_0600(self, tmp_path: Path, cifrador: Cifrador):
        """Solo su dueño puede leer la clave."""
        ruta_clave = cifrador.generar_clave(tmp_path / "mi.key")
        modo = stat.S_IMODE(ruta_clave.stat().st_mode)
        assert modo == 0o600

    def test_dos_claves_generadas_son_distintas(self, tmp_path: Path, cifrador: Cifrador):
        """Cada clave es aleatoria."""
        k1 = cifrador.generar_clave(tmp_path / "k1.key")
        k2 = cifrador.generar_clave(tmp_path / "k2.key")
        assert k1.read_bytes() != k2.read_bytes()


class TestModoContrasena:
    """Cifrar y descifrar con una contraseña."""

    def test_cifrar_archivo_devuelve_la_ruta_y_un_mensaje(
        self, cifrador: Cifrador, fichero_texto: Path
    ):
        """Devuelve la ruta del ``.enc`` y un mensaje con su nombre."""
        r = cifrador.cifrar_archivo(fichero_texto, contrasena="ClaveSegura123")
        assert r.ruta == fichero_texto.with_name(fichero_texto.name + ".enc")
        assert r.mensaje == f"Cifrado correctamente: {r.ruta.name}"

    def test_cifrar_archivo_crea_fichero_enc(self, cifrador: Cifrador, fichero_texto: Path):
        """El ``.enc`` se crea junto al original."""
        r = cifrador.cifrar_archivo(fichero_texto, contrasena="ClaveSegura123")
        assert r.ruta is not None
        assert r.ruta.exists()
        assert r.ruta.suffix == SUFIJO_CIFRADO

    def test_ida_y_vuelta_recupera_contenido_exacto(self, cifrador: Cifrador, fichero_texto: Path):
        """Cifrar y descifrar devuelve el mismo texto."""
        original = fichero_texto.read_text(encoding="utf-8")
        r1 = cifrador.cifrar_archivo(fichero_texto, contrasena="ClaveSegura123")
        r2 = cifrador.descifrar_archivo(r1.ruta, contrasena="ClaveSegura123")
        assert r2.ruta.read_text(encoding="utf-8") == original

    def test_ida_y_vuelta_con_contenido_binario_exacto(
        self, cifrador: Cifrador, fichero_binario: Path
    ):
        """Cifrar y descifrar devuelve los mismos bytes."""
        original = fichero_binario.read_bytes()
        r1 = cifrador.cifrar_archivo(fichero_binario, contrasena="ClaveSegura123")
        r2 = cifrador.descifrar_archivo(r1.ruta, contrasena="ClaveSegura123")
        assert r2.ruta.read_bytes() == original

    def test_contrasena_incorrecta_lanza_error_contrasena_invalida(
        self, cifrador: Cifrador, fichero_texto: Path
    ):
        """Con otra contraseña no se descifra: ErrorContrasenaInvalida."""
        r1 = cifrador.cifrar_archivo(fichero_texto, contrasena="ClaveCorrecta")
        with pytest.raises(ErrorContrasenaInvalida):
            cifrador.descifrar_archivo(r1.ruta, contrasena="ClaveIncorrecta")

    def test_dos_ficheros_con_misma_contrasena_dan_tokens_distintos(
        self, cifrador: Cifrador, tmp_path: Path
    ):
        """El mismo contenido con la misma contraseña da cifrados distintos (salt aleatorio)."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("mismo contenido")
        f2.write_text("mismo contenido")

        r1 = cifrador.cifrar_archivo(f1, contrasena="MismaClave")
        r2 = cifrador.cifrar_archivo(f2, contrasena="MismaClave")

        assert r1.ruta.read_bytes() != r2.ruta.read_bytes()

    def test_borrar_original_true_elimina_el_fichero_fuente(
        self, cifrador: Cifrador, fichero_texto: Path
    ):
        """Con ``borrar_original`` solo queda el cifrado."""
        cifrador.cifrar_archivo(fichero_texto, contrasena="Clave123", borrar_original=True)
        assert not fichero_texto.exists()

    def test_borrar_original_false_conserva_el_fichero_fuente(
        self, cifrador: Cifrador, fichero_texto: Path
    ):
        """Sin ``borrar_original`` el original se queda."""
        cifrador.cifrar_archivo(fichero_texto, contrasena="Clave123", borrar_original=False)
        assert fichero_texto.exists()


class TestModoArchivoClave:
    """Cifrar y descifrar con un fichero .key."""

    def test_ida_y_vuelta_con_archivo_clave(
        self, cifrador: Cifrador, fichero_texto: Path, clave: Path
    ):
        """Cifrar y descifrar con la misma clave devuelve el mismo contenido."""
        original = fichero_texto.read_text(encoding="utf-8")
        r1 = cifrador.cifrar_archivo(fichero_texto, ruta_clave=clave)
        r2 = cifrador.descifrar_archivo(r1.ruta, ruta_clave=clave)
        assert r2.ruta.read_text(encoding="utf-8") == original

    def test_archivo_clave_inexistente_lanza_error_archivo_clave_no_encontrado(
        self, cifrador: Cifrador, fichero_texto: Path, tmp_path: Path
    ):
        """Si el .key no existe: ErrorArchivoClaveNoEncontrado."""
        clave_falsa = tmp_path / "no_existe.key"
        with pytest.raises(ErrorArchivoClaveNoEncontrado):
            cifrador.cifrar_archivo(fichero_texto, ruta_clave=clave_falsa)

    def test_descifrar_con_archivo_clave_incorrecto_lanza_error_contrasena_invalida(
        self, cifrador: Cifrador, fichero_texto: Path, clave: Path, tmp_path: Path
    ):
        """Con otra clave no se descifra: ErrorContrasenaInvalida."""
        otra_clave = cifrador.generar_clave(tmp_path / "otra.key")
        r1 = cifrador.cifrar_archivo(fichero_texto, ruta_clave=clave)
        with pytest.raises(ErrorContrasenaInvalida):
            cifrador.descifrar_archivo(r1.ruta, ruta_clave=otra_clave)


class TestValidacionMaterialClave:
    """Hace falta una contraseña o una clave, y la correcta."""

    def test_cifrar_sin_contrasena_ni_archivo_clave_lanza_error_falta_clave(
        self, cifrador: Cifrador, fichero_texto: Path
    ):
        """Sin ninguna de las dos: ErrorFaltaClave."""
        with pytest.raises(ErrorFaltaClave):
            cifrador.cifrar_archivo(fichero_texto)

    def test_cifrar_con_contrasena_y_archivo_clave_a_la_vez_lanza_error_clave_ambigua(
        self, cifrador: Cifrador, fichero_texto: Path, clave: Path
    ):
        """Con las dos a la vez: ErrorClaveAmbigua."""
        with pytest.raises(ErrorClaveAmbigua):
            cifrador.cifrar_archivo(fichero_texto, contrasena="Clave123", ruta_clave=clave)

    def test_descifrar_con_contrasena_un_fichero_cifrado_con_archivo_clave(
        self, cifrador: Cifrador, fichero_texto: Path, clave: Path
    ):
        """Lo cifrado con .key no se intenta con contraseña: ErrorFaltaClave lo explica."""
        r1 = cifrador.cifrar_archivo(fichero_texto, ruta_clave=clave)
        with pytest.raises(ErrorFaltaClave):
            cifrador.descifrar_archivo(r1.ruta, contrasena="loque sea")

    def test_descifrar_con_archivo_clave_un_fichero_cifrado_con_contrasena(
        self, cifrador: Cifrador, fichero_texto: Path, clave: Path
    ):
        """Lo cifrado con contraseña no se intenta con .key: ErrorFaltaClave lo explica."""
        r1 = cifrador.cifrar_archivo(fichero_texto, contrasena="Clave123")
        with pytest.raises(ErrorFaltaClave):
            cifrador.descifrar_archivo(r1.ruta, ruta_clave=clave)


class TestErroresDeEstado:
    """Ficheros que no existen, ya cifrados o que no son de este programa."""

    def test_cifrar_fichero_inexistente_lanza_file_not_found_error(
        self, cifrador: Cifrador, tmp_path: Path
    ):
        """Cifrar algo que no existe: FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            cifrador.cifrar_archivo(tmp_path / "no_existe.txt", contrasena="x")

    def test_cifrar_fichero_ya_cifrado_lanza_error_ya_cifrado(
        self, cifrador: Cifrador, fichero_texto: Path
    ):
        """Un ``.enc`` no se vuelve a cifrar: ErrorYaCifrado."""
        r1 = cifrador.cifrar_archivo(fichero_texto, contrasena="Clave123")
        with pytest.raises(ErrorYaCifrado):
            cifrador.cifrar_archivo(r1.ruta, contrasena="OtraClave")

    def test_descifrar_fichero_inexistente_lanza_file_not_found_error(
        self, cifrador: Cifrador, tmp_path: Path
    ):
        """Descifrar algo que no existe: FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            cifrador.descifrar_archivo(tmp_path / "no_existe.txt.enc", contrasena="x")

    def test_descifrar_fichero_sin_extension_enc_lanza_error_no_cifrado(
        self, cifrador: Cifrador, fichero_texto: Path
    ):
        """Un fichero que no acaba en ``.enc`` no se descifra: ErrorNoCifrado."""
        with pytest.raises(ErrorNoCifrado):
            cifrador.descifrar_archivo(fichero_texto, contrasena="x")

    def test_descifrar_fichero_con_cabecera_invalida_lanza_error_corrupto(
        self, cifrador: Cifrador, tmp_path: Path
    ):
        """Un ``.enc`` sin la cabecera de este programa: ErrorArchivoCifradoCorrupto."""
        falso = tmp_path / "falso.txt.enc"
        falso.write_bytes(b"esto no tiene la cabecera magica esperada")
        with pytest.raises(ErrorArchivoCifradoCorrupto):
            cifrador.descifrar_archivo(falso, contrasena="cualquiera")


class TestResolucionDeConflictos:
    """Cifrar no pisa un ``.enc`` que ya existe."""

    def test_no_sobrescribe_enc_existente(self, cifrador: Cifrador, tmp_path: Path):
        """Cifrar otra vez el mismo fichero crea otro ``.enc`` y deja intacto el primero."""
        f1 = tmp_path / "doc.txt"
        f1.write_text("version original")

        r1 = cifrador.cifrar_archivo(f1, contrasena="Clave123")
        contenido_enc_original = r1.ruta.read_bytes()
        f1.write_text("version nueva")
        r2 = cifrador.cifrar_archivo(f1, contrasena="OtraClave")

        assert r2.ruta != r1.ruta
        assert r1.ruta.read_bytes() == contenido_enc_original


@pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX no aplican en windows")
class TestPermisos:
    """Lo que se cifra o descifra no queda a la vista de otros usuarios del equipo."""

    @pytest.mark.parametrize("operacion", ["cifrar", "descifrar"])
    def test_el_temporal_solo_lo_puede_leer_su_dueno(
        self, cifrador: Cifrador, tmp_path: Path, operacion: str
    ):
        """A mitad de escribir (tras cada bloque), el temporal tiene permisos 0o600, aunque el
        original sea legible por todos.
        """
        original = tmp_path / "grande.bin"
        original.write_bytes(b"x" * 300_000)  # varios bloques: se mira a mitad de escribir
        original.chmod(0o644)
        modos: list[int] = []

        def mirar_temporal(_hechos: int, _total: int) -> None:
            modos.extend(stat.S_IMODE(t.stat().st_mode) for t in tmp_path.glob(".*.tmp"))

        if operacion == "cifrar":
            cifrador.cifrar_archivo(original, contrasena="clave", progreso=mirar_temporal)
        else:
            cifrado = cifrador.cifrar_archivo(original, contrasena="clave", borrar_original=True)
            cifrador.descifrar_archivo(cifrado.ruta, contrasena="clave", progreso=mirar_temporal)

        assert modos and set(modos) == {0o600}

    @pytest.mark.parametrize("permisos", [0o600, 0o640], ids=["600", "640"])
    def test_cifrar_y_descifrar_conservan_los_permisos_del_original(
        self, cifrador: Cifrador, fichero_texto: Path, permisos: int
    ):
        """El ``.enc`` tiene los permisos del original y lo descifrado, los del ``.enc``."""
        fichero_texto.chmod(permisos)

        cifrado = cifrador.cifrar_archivo(fichero_texto, contrasena="clave", borrar_original=True)
        assert stat.S_IMODE(cifrado.ruta.stat().st_mode) == permisos

        descifrado = cifrador.descifrar_archivo(cifrado.ruta, contrasena="clave")
        assert stat.S_IMODE(descifrado.ruta.stat().st_mode) == permisos


class TestCifrarCarpeta:
    """Cifrar todos los ficheros de una carpeta."""

    def test_cifra_todos_los_ficheros_de_la_carpeta(self, cifrador: Cifrador, tmp_path: Path):
        """Cifra cada fichero y cuenta los hechos, los fallidos y el total."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")

        resultado = cifrador.procesar_carpeta(tmp_path, AccionCifrado.CIFRAR, contrasena="Clave123")

        assert resultado.exitosos == 2
        assert resultado.fallidos == 0
        assert resultado.total == 2

    def test_omite_ficheros_key_y_enc_existentes(self, cifrador: Cifrador, tmp_path: Path):
        """No cifra las claves ni lo que ya está cifrado."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "clave.key").write_bytes(b"no es un fichero a cifrar")
        (tmp_path / "ya_cifrado.enc").write_bytes(b"tampoco este")

        resultado = cifrador.procesar_carpeta(tmp_path, AccionCifrado.CIFRAR, contrasena="Clave123")

        assert resultado.exitosos == 1

    def test_omite_ficheros_ocultos(self, cifrador: Cifrador, tmp_path: Path):
        """No cifra los ficheros ocultos."""
        (tmp_path / "visible.txt").write_text("x")
        (tmp_path / ".oculto.txt").write_text("y")

        resultado = cifrador.procesar_carpeta(tmp_path, AccionCifrado.CIFRAR, contrasena="Clave123")

        assert resultado.exitosos == 1

    def test_carpeta_inexistente_lanza_error_carpeta_invalida(
        self, cifrador: Cifrador, tmp_path: Path
    ):
        """Si la carpeta no existe: ErrorCarpetaInvalida."""
        with pytest.raises(ErrorCarpetaInvalida):
            cifrador.procesar_carpeta(tmp_path / "no_existe", AccionCifrado.CIFRAR, contrasena="x")

    def test_recursivo_true_incluye_subcarpetas(self, cifrador: Cifrador, tmp_path: Path):
        """Con ``recursivo`` cifra también lo de las subcarpetas."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "raiz.txt").write_text("raiz")
        (sub / "anidado.txt").write_text("anidado")

        resultado = cifrador.procesar_carpeta(
            tmp_path, AccionCifrado.CIFRAR, contrasena="Clave123", recursivo=True
        )

        assert resultado.exitosos == 2

    def test_recursivo_false_ignora_subcarpetas(self, cifrador: Cifrador, tmp_path: Path):
        """Sin ``recursivo`` solo la carpeta."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "raiz.txt").write_text("raiz")
        (sub / "anidado.txt").write_text("anidado")

        resultado = cifrador.procesar_carpeta(
            tmp_path, AccionCifrado.CIFRAR, contrasena="Clave123", recursivo=False
        )

        assert resultado.exitosos == 1

    def test_falta_de_clave_lanza_antes_de_procesar_nada(self, cifrador: Cifrador, tmp_path: Path):
        """Sin contraseña ni clave falla antes de empezar, no con cada fichero."""
        (tmp_path / "a.txt").write_text("a")
        with pytest.raises(ErrorFaltaClave):
            cifrador.procesar_carpeta(tmp_path, AccionCifrado.CIFRAR)


class TestDescifrarCarpeta:
    """Descifrar todos los ``.enc`` de una carpeta."""

    def test_descifra_solo_los_ficheros_enc(self, cifrador: Cifrador, tmp_path: Path):
        """Descifra los ``.enc`` y deja los demás ficheros como están."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        cifrador.procesar_carpeta(tmp_path, AccionCifrado.CIFRAR, contrasena="Clave123")

        (tmp_path / "normal.txt").write_text("no cifrado")

        resultado = cifrador.procesar_carpeta(
            tmp_path, AccionCifrado.DESCIFRAR, contrasena="Clave123"
        )

        assert resultado.exitosos == 2
        assert (tmp_path / "normal.txt").read_text() == "no cifrado"

    def test_contrasena_incorrecta_falla_todos_sin_interrumpir(
        self, cifrador: Cifrador, tmp_path: Path
    ):
        """Con otra contraseña fallan todos, cada uno con su error, sin cortar a la mitad."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        cifrador.procesar_carpeta(tmp_path, AccionCifrado.CIFRAR, contrasena="ClaveBuena")

        resultado = cifrador.procesar_carpeta(
            tmp_path, AccionCifrado.DESCIFRAR, contrasena="ClaveMala"
        )

        assert resultado.exitosos == 0
        assert resultado.fallidos == 2
        assert len(resultado.errores) == 2
        for _fichero, error in resultado.errores:
            assert isinstance(error, ErrorContrasenaInvalida)

    def test_carpeta_con_ficheros_mixtos_solo_falla_los_incorrectos(
        self, cifrador: Cifrador, tmp_path: Path
    ):
        """Un ``.enc`` dañado falla con su error y los demás se descifran."""
        bueno = tmp_path / "bueno.txt"
        bueno.write_text("contenido válido")
        cifrador.cifrar_archivo(bueno, contrasena="Clave123")

        corrupto = tmp_path / "corrupto.txt.enc"
        corrupto.write_bytes(b"esto no tiene cabecera valida")

        resultado = cifrador.procesar_carpeta(
            tmp_path, AccionCifrado.DESCIFRAR, contrasena="Clave123"
        )

        assert resultado.exitosos == 1
        assert resultado.fallidos == 1
        assert len(resultado.errores) == 1
        fichero_fallido, error = resultado.errores[0]
        assert fichero_fallido.name == "corrupto.txt.enc"
        assert isinstance(error, ErrorArchivoCifradoCorrupto)
