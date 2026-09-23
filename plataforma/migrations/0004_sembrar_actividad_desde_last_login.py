"""
Siembra un día de actividad por usuario a partir de `User.last_login`.

`ActividadDiaria` arranca vacía, así que el día del deploy el monitor diría
«nunca» para TODOS los gimnasios — incluidos los que vienen usando la app hace
meses. Leído literalmente, «último uso staff: nunca» en la fila de un cliente
que paga es exactamente la señal que la columna existe para dar, y es falsa.

`last_login` es lo único registrado hasta ahora. No alcanza para reconstruir
el historial (es un solo instante por usuario, y ni siquiera el último uso:
es el último LOGIN, que en una app con sesiones largas puede ser de hace
meses), así que esto NO inventa una serie: siembra UN día por usuario, el que
de verdad consta. El gráfico de 30 días va a seguir casi vacío hasta que la
gente entre, y está bien; lo que se arregla es el «nunca» de la columna.

`ignore_conflicts=True` la deja idempotente: correrla dos veces, o aplicarla
sobre una base donde el middleware ya anotó el día de hoy, no duplica nada.
"""

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def sembrar_actividad_desde_last_login(ActividadDiaria, Perfil):
    """Una fila por usuario con `Perfil` y `last_login` cargado.

    `timezone.localtime(...).date()` y nunca `.date()` a secas: `last_login`
    se guarda en UTC, y con `TIME_ZONE` en UTC-3 un login de las 22:00 caería
    en el día siguiente. Es el mismo criterio que
    `facturacion.inicio_efectivo` con `Gimnasio.creado`.

    El `gimnasio` y el `rol` salen del `Perfil` de HOY, que es lo único que
    hay: si alguien cambió de gimnasio desde su último login, su día viejo
    queda acreditado al gimnasio nuevo. Es una fila, por única vez, y la
    alternativa sería no sembrar nada.

    Recibe los dos modelos por parámetro para poder llamarla desde un test con
    los modelos reales, igual que `tenants/0013`.
    """
    filas = [
        ActividadDiaria(
            usuario_id=usuario_id,
            gimnasio_id=gimnasio_id,
            rol=rol,
            fecha=timezone.localtime(last_login).date(),
        )
        for usuario_id, gimnasio_id, rol, last_login in Perfil.objects.filter(
            usuario__last_login__isnull=False
        ).values_list("usuario_id", "gimnasio_id", "rol", "usuario__last_login")
    ]
    return ActividadDiaria.objects.bulk_create(filas, ignore_conflicts=True)


def _sembrar(apps, schema_editor):
    sembrar_actividad_desde_last_login(
        apps.get_model("plataforma", "ActividadDiaria"),
        apps.get_model("tenants", "Perfil"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("plataforma", "0003_actividaddiaria"),
        ("tenants", "0014_gimnasio_estado_cuenta"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # `noop` al revés: volver atrás es revertir la 0003, que borra la tabla
        # entera. No hay nada que deshacer fila por fila, y distinguir las
        # sembradas de las reales no se puede (son idénticas a propósito).
        migrations.RunPython(_sembrar, migrations.RunPython.noop),
    ]
