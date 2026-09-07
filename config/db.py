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

    `disable_server_side_cursors` también es obligatorio: producción entra por
    la URL **pooled** de Neon (PgBouncer en modo transacción), y Django
    documenta que con ese tipo de pooler los cursores de servidor tienen que
    estar apagados -- el `DECLARE` de un cursor con nombre y su `FETCH` pueden
    caer en conexiones distintas del pool. No es teórico: el 2026-09-07 un
    worker de gunicorn murió por timeout (30 s) esperando en
    `psycopg/_server_cursor.py` mientras renderizaba el preview del importador;
    `ModelChoiceIterator` (todo `<select>` de un `ModelChoiceField`) usa
    `QuerySet.iterator()`, que en Postgres abre justamente uno de esos
    cursores. Ver `ISSUES.md` `[2026-09-07]`.
    """
    if database_url:
        return dj_database_url.parse(
            database_url,
            conn_max_age=CONN_MAX_AGE,
            conn_health_checks=True,
            disable_server_side_cursors=True,
            ssl_require=not debug,
        )
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": base_dir / "db.sqlite3",
    }
