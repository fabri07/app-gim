"""Tests de la configuración: conexión a la base (`config/db.py`), aislamiento
del storage de media durante la suite, y que el Blueprint de Render declare
todas las variables de entorno que `settings.py` lee."""

import re
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase

from config.db import database_config

URL_POSTGRES = "postgres://u:p@h:5432/d"


class DatabaseConfigTests(SimpleTestCase):
    def test_sin_database_url_usa_sqlite(self):
        cfg = database_config("", debug=True, base_dir=Path("/tmp"))
        self.assertEqual(cfg["ENGINE"], "django.db.backends.sqlite3")

    def test_postgres_activa_health_checks(self):
        """Neon suspende el compute por inactividad (scale-to-zero). Sin health
        checks, Django reusa una conexión ya muerta del pool de CONN_MAX_AGE y
        el request falla de forma intermitente."""
        cfg = database_config(URL_POSTGRES, debug=False, base_dir=Path("/tmp"))
        self.assertIs(cfg["CONN_HEALTH_CHECKS"], True)
        self.assertEqual(cfg["CONN_MAX_AGE"], 600)

    def test_postgres_desactiva_los_cursores_de_servidor(self):
        """Producción entra a Neon por el pooler (PgBouncer en modo transacción).
        Django exige `DISABLE_SERVER_SIDE_CURSORS` con ese tipo de pooler: un
        cursor con nombre (`DECLARE`) puede caer en otra conexión del pool que
        el `FETCH`. Se descubrió el 2026-09-07 con un worker de gunicorn muerto
        por timeout dentro de `ModelChoiceIterator` (que usa `.iterator()`)
        al renderizar el preview del importador."""
        cfg = database_config(URL_POSTGRES, debug=False, base_dir=Path("/tmp"))
        self.assertIs(cfg["DISABLE_SERVER_SIDE_CURSORS"], True)

    def test_postgres_exige_ssl_fuera_de_debug(self):
        cfg = database_config(URL_POSTGRES, debug=False, base_dir=Path("/tmp"))
        self.assertEqual(cfg["OPTIONS"]["sslmode"], "require")


class StorageDeTestsAisladoTests(SimpleTestCase):
    """La suite NO debe tocar el bucket real de R2.

    El `.env` de desarrollo tiene las 4 `R2_*`, así que sin el caso especial de
    `config/settings.py` el storage por defecto es `S3Storage` también cuando
    corren los tests — e `importaciones/tests.py` sube `.xlsx` de verdad. Se
    habían acumulado 816 archivos huérfanos en `app-gim-media/importaciones/`
    antes de detectarlo (ver `ISSUES.md` 2026-07-30).
    """

    def test_el_storage_por_defecto_es_en_memoria(self):
        self.assertEqual(
            settings.STORAGES["default"]["BACKEND"],
            "django.core.files.storage.InMemoryStorage",
        )

    def test_escribir_un_archivo_no_toca_el_disco(self):
        """El archivo existe para Django pero no aparece en el filesystem.

        `InMemoryStorage.path()` igual devuelve una ruta (es `safe_join` sobre
        `location`), así que lo que hay que comprobar es que en esa ruta NO
        haya nada: si el backend fuera `FileSystemStorage` el archivo estaría
        ahí, y si fuera `S3Storage` habría salido por la red.
        """
        nombre = default_storage.save("prueba.txt", ContentFile(b"hola"))
        try:
            self.assertTrue(default_storage.exists(nombre))
            self.assertFalse(Path(default_storage.path(nombre)).exists())
        finally:
            default_storage.delete(nombre)


class BlueprintDeclaraLoQueSettingsLeeTests(SimpleTestCase):
    """`render.yaml` tiene que declarar toda variable de entorno que
    `settings.py` lee.

    No es prolijidad: es el agujero que dejó el Blueprint sin
    `GOOGLE_LOGIN_REDIRECT_URI` mientras sí declaraba los otros dos del grupo
    de login con Google. Un grupo a medias hace que
    `_bandera_todo_o_nada` lance `ImproperlyConfigured` y el servicio **no
    arranque** -- y eso se descubre recreando el servicio, que es justo el
    momento de menos ganas de depurar. Los grupos que quedan en CERO degradan
    peor todavía: en silencio (R2 al disco efímero, push apagado, "olvidé mi
    contraseña" oculto).

    Se parsea con expresiones regulares y no con un parser de YAML a propósito:
    no hay `pyyaml` en `requirements.txt` y no vale agregar una dependencia
    para un test.
    """

    #: En el Blueprint pero fuera de `settings.py`: las lee Render, no Django.
    SOLO_DE_RENDER = {"PYTHON_VERSION"}

    def _declaradas(self):
        contenido = (Path(settings.BASE_DIR) / "render.yaml").read_text()
        return set(re.findall(r"^\s*-\s*key:\s*([A-Z0-9_]+)\s*$", contenido, re.M))

    #: Las cuatro formas en que `settings.py` lee el entorno. Si aparece una
    #: quinta, el segundo test de esta clase la detecta: va a reportar la
    #: variable como "declarada y que nadie lee", que es la pista de que el
    #: lector se quedó corto y no de que la variable sobre.
    NOMBRE = r"""["']([A-Z][A-Z0-9_]*)["']"""

    def _leidas(self):
        texto = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text()
        leidas = set()
        # 1) os.environ.get("X")  2) os.environ["X"]  3) _env_bool("X", ...)
        for patron in (
            r"os\.environ(?:\.get)?\(\s*" + self.NOMBRE,
            r"os\.environ\[\s*" + self.NOMBRE,
            r"_env_bool\(\s*" + self.NOMBRE,
        ):
            leidas |= set(re.findall(patron, texto))
        # 4) los grupos todo-o-nada reciben la lista de nombres, no los leen
        #    de a uno: `_bandera_todo_o_nada(["R2_BUCKET_NAME", ...], ...)`
        for bloque in re.findall(
            r"_bandera_todo_o_nada\(\s*\[(.*?)\]", texto, re.S
        ):
            leidas |= set(re.findall(self.NOMBRE, bloque))
        return leidas

    def test_el_blueprint_declara_todo_lo_que_settings_lee(self):
        faltan = self._leidas() - self._declaradas()
        self.assertEqual(
            faltan,
            set(),
            "render.yaml no declara estas variables que config/settings.py lee: "
            f"{sorted(faltan)}. Agregalas con `sync: false` -- si pertenecen a "
            "un grupo todo-o-nada y el grupo queda incompleto, el servicio no "
            "arranca.",
        )

    def test_el_blueprint_no_declara_variables_que_nadie_lee(self):
        """Una variable de más es una que alguien va a cargar creyendo que
        hace algo."""
        sobran = self._declaradas() - self._leidas() - self.SOLO_DE_RENDER
        self.assertEqual(
            sobran, set(), f"render.yaml declara variables que nadie lee: {sorted(sobran)}"
        )
