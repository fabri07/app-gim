"""
Actividad por gimnasio: el registro del día activo y lo que el panel lee de él.

Lo que se cubre es el COMPORTAMIENTO: que entrar a la app deje exactamente una
fila por usuario y por día, que el segundo request del día no escriba nada,
que el cambio de día se note, quién NO se registra (el superadmin, el staff
mientras suplanta, un anónimo, un usuario sin `Perfil`) y quién SÍ aunque no
pueda ver nada (un alumno con la cuenta congelada).

Los tests de `vaciar_gimnasio` sobre estas filas viven en `tenants/tests.py`,
al lado de sus hermanos.
"""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from plataforma import actividad, facturacion
from plataforma.middleware import CLAVE_SESION_ACTIVIDAD
from plataforma.models import ActividadDiaria
from tenants.models import Gimnasio, Perfil

CLAVE = "clave-123456"


def _gimnasio(nombre="Vida Plena", slug="vida-plena", **extra):
    return Gimnasio.objects.create(nombre=nombre, slug=slug, **extra)


def _staff(gimnasio, username="duenio"):
    usuario = User.objects.create_user(username, password=CLAVE)
    Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
    return usuario


def _alumno_con_acceso(gimnasio, username="alumno", nombre="Ana", apellido="Pérez"):
    usuario = User.objects.create_user(username, password=CLAVE)
    perfil = Perfil.objects.create(
        usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO
    )
    alumno = Alumno.objects.create(
        gimnasio=gimnasio, nombre=nombre, apellido=apellido, perfil=perfil
    )
    return alumno, usuario


def _reloj(momento_utc):
    """Congela `timezone.now` en ese instante UTC.

    Se parchea `django.utils.timezone.now` y el código llama
    `timezone.localdate()` (que lo resuelve en cada llamada): un
    `from django.utils.timezone import now` ligaría la función al importar y
    el parche no lo alcanzaría.
    """
    return patch("django.utils.timezone.now", return_value=momento_utc)


class RegistroDelDiaActivoTests(TestCase):
    """El middleware anota UN día de uso por usuario, la primera vez que
    entra."""

    def setUp(self):
        self.gimnasio = _gimnasio()
        self.alumno, self.usuario = _alumno_con_acceso(self.gimnasio)
        self.client.force_login(self.usuario)

    def test_el_primer_get_del_dia_crea_una_fila(self):
        self.client.get(reverse("home"))

        fila = ActividadDiaria.objects.get()
        self.assertEqual(fila.usuario, self.usuario)
        self.assertEqual(fila.gimnasio, self.gimnasio)
        self.assertEqual(fila.rol, Perfil.Rol.ALUMNO)
        self.assertEqual(fila.fecha, timezone.localdate())

    def test_el_segundo_get_del_dia_no_vuelve_a_escribir(self):
        """El dedupe por sesión: sin él, cada request del día sería un INSERT
        (y una escritura de sesión) en TODAS las páginas de TODOS los
        gimnasios."""
        self.client.get(reverse("home"))

        with CaptureQueriesContext(connection) as capturadas:
            self.client.get(reverse("home"))

        self.assertEqual(ActividadDiaria.objects.count(), 1)
        escrituras = [
            q["sql"]
            for q in capturadas
            if "actividaddiaria" in q["sql"].lower()
        ]
        self.assertEqual(escrituras, [])

    def test_la_sesion_queda_marcada_con_el_dia(self):
        self.client.get(reverse("home"))

        self.assertEqual(
            self.client.session[CLAVE_SESION_ACTIVIDAD],
            timezone.localdate().isoformat(),
        )

    def test_al_cambiar_el_dia_registra_otra_fila(self):
        with _reloj(datetime(2026, 5, 10, 15, 0, tzinfo=dt_timezone.utc)):
            self.client.get(reverse("home"))
        with _reloj(datetime(2026, 5, 11, 15, 0, tzinfo=dt_timezone.utc)):
            self.client.get(reverse("home"))

        fechas = sorted(ActividadDiaria.objects.values_list("fecha", flat=True))
        self.assertEqual(fechas, [date(2026, 5, 10), date(2026, 5, 11)])

    def test_a_las_2330_locales_la_fila_queda_en_el_dia_local(self):
        """`TIME_ZONE` es UTC-3: a las 23:30 del 10 de mayo la fecha UTC ya es
        el 11. Con `timezone.now().date()` el uso de esa noche se anotaría en
        el día siguiente y el gráfico mostraría actividad en días en los que
        el gimnasio estaba cerrado."""
        with _reloj(datetime(2026, 5, 11, 2, 30, tzinfo=dt_timezone.utc)):
            self.client.get(reverse("home"))

        self.assertEqual(ActividadDiaria.objects.get().fecha, date(2026, 5, 10))

    def test_dos_sesiones_del_mismo_usuario_el_mismo_dia_no_revientan(self):
        """El caso real son dos pestañas (o el celular y la computadora)
        cruzando el primer request del día: la segunda sesión no tiene la
        marca y reintenta el INSERT contra la clave única. De ahí el
        `ignore_conflicts`."""
        self.client.get(reverse("home"))

        otra_pestania = Client()
        otra_pestania.force_login(self.usuario)
        respuesta = otra_pestania.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(ActividadDiaria.objects.count(), 1)

    def test_el_staff_queda_registrado_con_rol_staff(self):
        staff = _staff(self.gimnasio)
        cliente = Client()
        cliente.force_login(staff)

        cliente.get(reverse("home"))

        fila = ActividadDiaria.objects.get(usuario=staff)
        self.assertEqual(fila.rol, Perfil.Rol.STAFF)

    def test_el_superusuario_no_deja_rastro(self):
        """El dueño del producto entrando a mirar no es uso del gimnasio: si
        contara, el panel diría que un gimnasio dormido se usa todos los días
        -- que es justo lo que se está tratando de detectar."""
        superadmin = User.objects.create_superuser(
            "dueno-producto", "dueno@example.com", CLAVE
        )
        Perfil.objects.create(
            usuario=superadmin, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        cliente = Client()
        cliente.force_login(superadmin)

        cliente.get(reverse("home"))

        self.assertFalse(ActividadDiaria.objects.exists())

    def test_un_anonimo_no_registra_nada(self):
        respuesta = Client().get(reverse("login"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(ActividadDiaria.objects.exists())

    def test_un_usuario_sin_perfil_no_registra_nada(self):
        suelto = User.objects.create_user("suelto", password=CLAVE)
        cliente = Client()
        cliente.force_login(suelto)

        cliente.get(reverse("home"))

        self.assertFalse(ActividadDiaria.objects.exists())

    def test_mientras_se_suplanta_no_se_registra_nada(self):
        """El staff mirando la app como un alumno no es uso del alumno: si
        contara, el gráfico mostraría alumnos activos que nunca entraron."""
        staff = _staff(self.gimnasio)
        cliente = Client()
        cliente.force_login(staff)
        cliente.post(reverse("suplantar", args=[self.alumno.pk]))
        ActividadDiaria.objects.all().delete()

        cliente.get(reverse("home"))

        self.assertFalse(ActividadDiaria.objects.exists())

    def test_un_alumno_con_la_cuenta_congelada_igual_se_registra(self):
        """Intentar entrar y encontrarse el cartel también es uso: es la señal
        de que el gimnasio sigue vivo del otro lado de la deuda. Por eso el
        registro corre ANTES del bloqueo."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS
        )

        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")
        self.assertEqual(ActividadDiaria.objects.count(), 1)


class ActivosPorDiaTests(TestCase):
    HOY = date(2026, 6, 30)

    def setUp(self):
        self.gimnasio = _gimnasio()
        self.otro = _gimnasio("Otro Gim", "otro-gim")

    def _fila(self, usuario, fecha, rol, gimnasio=None):
        return ActividadDiaria.objects.create(
            usuario=usuario,
            gimnasio=gimnasio or self.gimnasio,
            rol=rol,
            fecha=fecha,
        )

    def test_devuelve_una_fila_por_dia_aunque_no_haya_actividad(self):
        """Un gráfico que se saltea los días vacíos hace que dos días
        separados por un hueco se vean contiguos: miente sobre la tendencia."""
        filas = actividad.activos_por_dia(self.gimnasio, hoy=self.HOY)

        self.assertEqual(len(filas), 30)
        self.assertEqual(filas[0]["fecha"], self.HOY - timedelta(days=29))
        self.assertEqual(filas[-1]["fecha"], self.HOY)
        self.assertEqual({f["staff"] for f in filas}, {0})
        self.assertEqual({f["alumnos"] for f in filas}, {0})

    def test_cuenta_staff_y_alumnos_por_separado(self):
        staff = _staff(self.gimnasio)
        _, alumno_uno = _alumno_con_acceso(self.gimnasio, username="al-1")
        _, alumno_dos = _alumno_con_acceso(self.gimnasio, username="al-2")
        self._fila(staff, self.HOY, Perfil.Rol.STAFF)
        self._fila(alumno_uno, self.HOY, Perfil.Rol.ALUMNO)
        self._fila(alumno_dos, self.HOY, Perfil.Rol.ALUMNO)

        hoy = actividad.activos_por_dia(self.gimnasio, hoy=self.HOY)[-1]

        self.assertEqual(hoy["staff"], 1)
        self.assertEqual(hoy["alumnos"], 2)
        self.assertEqual(hoy["etiqueta"], "30/06")

    def test_no_mezcla_gimnasios(self):
        ajeno = _staff(self.otro, "duenio-ajeno")
        self._fila(ajeno, self.HOY, Perfil.Rol.STAFF, gimnasio=self.otro)

        filas = actividad.activos_por_dia(self.gimnasio, hoy=self.HOY)

        self.assertEqual(sum(f["staff"] for f in filas), 0)

    def test_ignora_lo_anterior_a_la_ventana(self):
        staff = _staff(self.gimnasio)
        self._fila(staff, self.HOY - timedelta(days=30), Perfil.Rol.STAFF)

        filas = actividad.activos_por_dia(self.gimnasio, hoy=self.HOY)

        self.assertEqual(sum(f["staff"] for f in filas), 0)

    def test_ignora_lo_posterior_a_hoy(self):
        """El rango es cerrado en los dos extremos: una fila con fecha futura
        (un reloj mal puesto, un test) no puede entrar en el último día."""
        staff = _staff(self.gimnasio)
        self._fila(staff, self.HOY + timedelta(days=1), Perfil.Rol.STAFF)

        filas = actividad.activos_por_dia(self.gimnasio, hoy=self.HOY)

        self.assertEqual(sum(f["staff"] for f in filas), 0)

    def test_es_una_sola_query_con_3_y_con_60_filas(self):
        """El costo tiene que depender del indicador, no de cuánto se usó la
        app: es un agregado, nunca un bucle por día."""
        staff = _staff(self.gimnasio)

        for dia in range(3):
            self._fila(staff, self.HOY - timedelta(days=dia), Perfil.Rol.STAFF)
        with CaptureQueriesContext(connection) as pocas:
            actividad.activos_por_dia(self.gimnasio, hoy=self.HOY)

        for numero in range(60):
            alumno = User.objects.create_user(f"masivo-{numero}", password=CLAVE)
            Perfil.objects.create(
                usuario=alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
            )
            self._fila(
                alumno,
                self.HOY - timedelta(days=numero % 30),
                Perfil.Rol.ALUMNO,
            )
        with CaptureQueriesContext(connection) as muchas:
            actividad.activos_por_dia(self.gimnasio, hoy=self.HOY)

        self.assertEqual(len(pocas), 1)
        self.assertEqual(len(muchas), 1)


class UltimoUsoTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()

    def test_sin_actividad_los_dos_roles_dan_none(self):
        self.assertEqual(
            actividad.ultimo_uso(self.gimnasio), {"staff": None, "alumnos": None}
        )

    def test_devuelve_el_ultimo_dia_de_cada_rol(self):
        staff = _staff(self.gimnasio)
        _, alumno = _alumno_con_acceso(self.gimnasio)
        for fecha in (date(2026, 6, 1), date(2026, 6, 20)):
            ActividadDiaria.objects.create(
                usuario=staff,
                gimnasio=self.gimnasio,
                rol=Perfil.Rol.STAFF,
                fecha=fecha,
            )
        ActividadDiaria.objects.create(
            usuario=alumno,
            gimnasio=self.gimnasio,
            rol=Perfil.Rol.ALUMNO,
            fecha=date(2026, 6, 5),
        )

        self.assertEqual(
            actividad.ultimo_uso(self.gimnasio),
            {"staff": date(2026, 6, 20), "alumnos": date(2026, 6, 5)},
        )

    def test_no_mira_a_otro_gimnasio(self):
        otro = _gimnasio("Otro Gim", "otro-gim")
        ajeno = _staff(otro, "duenio-ajeno")
        ActividadDiaria.objects.create(
            usuario=ajeno, gimnasio=otro, rol=Perfil.Rol.STAFF, fecha=date(2026, 6, 1)
        )

        self.assertIsNone(actividad.ultimo_uso(self.gimnasio)["staff"])


class UltimoUsoDelMonitorTests(TestCase):
    """`FilaMonitor.ultimo_uso_staff` sale de `ActividadDiaria`, no de
    `User.last_login`.

    `last_login` dice cuándo entró alguien por última vez, no cuánto se usa la
    app: un staff que deja la sesión abierta en la computadora del gimnasio
    puede tener un `last_login` de hace meses usándola todos los días.
    """

    def setUp(self):
        self.gimnasio = _gimnasio()

    def _fila(self):
        return facturacion.gimnasios_anotados().get(pk=self.gimnasio.pk).ultimo_uso_staff

    def test_sin_actividad_es_none_aunque_haya_last_login(self):
        staff = _staff(self.gimnasio)
        User.objects.filter(pk=staff.pk).update(
            last_login=datetime(2026, 9, 1, 12, 0, tzinfo=dt_timezone.utc)
        )

        self.assertIsNone(self._fila())

    def test_es_el_ultimo_dia_de_actividad_del_staff(self):
        staff = _staff(self.gimnasio)
        _, alumno = _alumno_con_acceso(self.gimnasio)
        ActividadDiaria.objects.create(
            usuario=staff,
            gimnasio=self.gimnasio,
            rol=Perfil.Rol.STAFF,
            fecha=date(2026, 5, 1),
        )
        # El alumno entró después: el monitor tiene que seguir mostrando al
        # staff, que es quien paga y quien deja de entrar cuando se va.
        ActividadDiaria.objects.create(
            usuario=alumno,
            gimnasio=self.gimnasio,
            rol=Perfil.Rol.ALUMNO,
            fecha=date(2026, 9, 1),
        )

        self.assertEqual(self._fila(), date(2026, 5, 1))


class FichaDeActividadTests(TestCase):
    """La tarjeta «Actividad» de la ficha del gimnasio."""

    def setUp(self):
        self.gimnasio = _gimnasio()
        self.superadmin = User.objects.create_superuser(
            "dueno-producto", "dueno@example.com", CLAVE
        )
        self.client.force_login(self.superadmin)

    def _ficha(self):
        return self.client.get(
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk])
        )

    def test_trae_treinta_dias_en_el_contexto(self):
        respuesta = self._ficha()

        self.assertEqual(len(respuesta.context["actividad"]), 30)

    def test_muestra_el_grafico_con_su_dataset_y_la_tabla(self):
        respuesta = self._ficha()
        html = respuesta.content.decode()

        self.assertIn('id="actividad-data"', html)
        self.assertIn('id="grafico-actividad"', html)
        self.assertIn('<details class="tabla-detalle">', html)

    def test_el_cdn_de_chartjs_va_dentro_del_contenido_y_no_en_el_head(self):
        """`hx-boost` solo reemplaza el `<body>`: un `<script>` del `<head>`
        no llega nunca en una navegación boosteada y el gráfico queda en
        blanco al entrar desde el panel."""
        html = self._ficha().content.decode()

        cdn = html.index("cdn.jsdelivr.net/npm/chart.js")
        self.assertGreater(cdn, html.index("<body"))

    def test_sin_actividad_dice_nunca(self):
        html = self._ficha().content.decode()

        self.assertIn("Último uso", html)
        self.assertIn("nunca", html)

    def test_muestra_el_ultimo_uso_de_los_dos_roles(self):
        staff = _staff(self.gimnasio)
        _, alumno = _alumno_con_acceso(self.gimnasio)
        ActividadDiaria.objects.create(
            usuario=staff,
            gimnasio=self.gimnasio,
            rol=Perfil.Rol.STAFF,
            fecha=date(2026, 5, 1),
        )
        ActividadDiaria.objects.create(
            usuario=alumno,
            gimnasio=self.gimnasio,
            rol=Perfil.Rol.ALUMNO,
            fecha=date(2026, 5, 3),
        )

        respuesta = self._ficha()

        self.assertEqual(
            respuesta.context["ultimo_uso"],
            {"staff": date(2026, 5, 1), "alumnos": date(2026, 5, 3)},
        )
        html = respuesta.content.decode()
        self.assertIn("01/05/2026", html)
        self.assertIn("03/05/2026", html)


class PoliticaDePrivacidadTests(TestCase):
    def test_avisa_que_se_registran_los_dias_de_uso(self):
        """Registrar qué días entra cada persona es un dato nuevo sobre ella:
        tiene que estar escrito en la política, no solo en el código."""
        html = self.client.get(reverse("politica_privacidad")).content.decode()

        self.assertIn("qué días usás la app", html)
