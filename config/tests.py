"""Tests de la configuración de conexión a la base (`config/db.py`)."""

from pathlib import Path

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
