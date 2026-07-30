"""Tests de la configuración: conexión a la base (`config/db.py`) y
aislamiento del storage de media durante la suite."""

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
