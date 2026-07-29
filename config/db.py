"""Configuración de la conexión a la base de datos.

Vive fuera de `settings.py` para poder testear la decisión Postgres-vs-SQLite
sin recargar el módulo de settings con otro entorno: la rama de Postgres solo
se ejercita en producción, así que sin esta costura no habría forma de cubrirla
(la suite corre siempre contra SQLite, sin `DATABASE_URL`).
"""

import dj_database_url

# Reusar conexiones entre requests evita abrir una TCP+TLS nueva en cada uno,
# importante en planes free con recursos limitados.
CONN_MAX_AGE = 600


def database_config(database_url, debug, base_dir):
    """Devuelve el dict de `DATABASES["default"]`.

    Con `database_url` seteada usa Postgres (producción, Neon); sin ella,
    SQLite local (desarrollo y tests) -- mismo criterio que el resto de
    settings.

    `conn_health_checks` es obligatorio contra Neon: el compute se suspende por
    inactividad y sin el chequeo Django reusa conexiones muertas del pool,
    fallando de forma intermitente en el primer request después de una pausa.
    """
    if database_url:
        return dj_database_url.parse(
            database_url,
            conn_max_age=CONN_MAX_AGE,
            conn_health_checks=True,
            ssl_require=not debug,
        )
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": base_dir / "db.sqlite3",
    }
