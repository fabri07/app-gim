"""
Congelar una cuenta: `Gimnasio.estado_cuenta`, el middleware que corta el
acceso y las dos acciones del panel.

Lo que se cubre acá es el COMPORTAMIENTO visible: qué ve un alumno de un
gimnasio con los alumnos bloqueados, qué ve su staff, qué pasa con un POST, y
qué sigue funcionando igual (salir, volver de una suplantación, la PWA, el
panel del superadmin). Los tests de push viven en `notificaciones/tests.py` y
los de los crons de cuotas en `pagos/tests.py`, cada uno al lado de sus
hermanos.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants.models import (
    ESTADOS_SIN_ACCESO_ALUMNO,
    ESTADOS_SIN_ACCESO_STAFF,
    Gimnasio,
    Perfil,
)

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


class EstadoCuentaDelModeloTests(TestCase):
    def test_un_gimnasio_nuevo_nace_normal_y_sin_fecha(self):
        """El default tiene que preservar exactamente el comportamiento de
        todos los gimnasios que ya existían: nadie queda bloqueado por el
        deploy."""
        gimnasio = _gimnasio()

        self.assertEqual(gimnasio.estado_cuenta, Gimnasio.EstadoCuenta.NORMAL)
        self.assertIsNone(gimnasio.estado_cuenta_desde)

    def test_las_constantes_dicen_a_quien_le_corta_cada_estado(self):
        """Son dos conjuntos distintos a propósito: «Bloquear alumnos» deja
        al dueño entrar a ver su deuda y a pagarla, «Suspender» no."""
        self.assertEqual(
            ESTADOS_SIN_ACCESO_ALUMNO,
            frozenset(
                {
                    Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
                    Gimnasio.EstadoCuenta.SUSPENDIDA,
                }
            ),
        )
        self.assertEqual(
            ESTADOS_SIN_ACCESO_STAFF,
            frozenset({Gimnasio.EstadoCuenta.SUSPENDIDA}),
        )


class AlumnoConLosAlumnosBloqueadosTests(TestCase):
    """El alumno de un gimnasio en `ALUMNOS_BLOQUEADOS` no entra a ningún
    lado, pero se lo dice una pantalla, no un error."""

    def setUp(self):
        self.gimnasio = _gimnasio(
            estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
            estado_cuenta_desde=timezone.now(),
        )
        self.alumno, self.usuario = _alumno_con_acceso(self.gimnasio)
        self.asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            nombre_snapshot="Full Body",
            fecha_inicio=timezone.localdate() - timedelta(days=7),
            activa=True,
        )
        self.item = RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada,
            ejercicio_nombre_snapshot="Sentadilla",
            semana=1,
            dia=1,
            orden=1,
            series=3,
            repeticiones="10",
        )
        self.client.force_login(self.usuario)

    def test_el_portal_muestra_el_cartel_con_200(self):
        """200 y no 403/404: bajo el `hx-boost` global un 4xx es un click
        muerto, sin ningún mensaje para el alumno."""
        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")

    def test_el_dia_de_la_rutina_tambien_muestra_el_cartel(self):
        respuesta = self.client.get(reverse("rutinas:mi_dia_detalle", args=[1]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")

    def test_calificar_un_ejercicio_redirige_y_no_escribe_nada(self):
        respuesta = self.client.post(
            reverse("rutinas:item_calificar", args=[self.item.pk]),
            {"rpe": RutinaAsignadaItem.RPE.AL_LIMITE},
        )

        self.assertRedirects(respuesta, reverse("home"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.rpe, "")

    def test_el_cartel_nombra_al_gimnasio_y_ofrece_contactarlo(self):
        self.gimnasio.link_whatsapp = "https://wa.me/5491100000000"
        self.gimnasio.save()

        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "Vida Plena")
        self.assertContains(respuesta, "https://wa.me/5491100000000")

    def test_el_cartel_no_ofrece_ninguna_seccion_de_la_app(self):
        """Sin links a secciones: todas terminan en el mismo cartel, así que
        ofrecerlas es prometer algo que no va a pasar."""
        respuesta = self.client.get(reverse("home"))
        contenido = respuesta.content.decode()

        self.assertNotIn(reverse("turnos:mis_turnos"), contenido)
        self.assertNotIn(reverse("rutinas:mi_dia_detalle", args=[1]), contenido)


class StaffConLosAlumnosBloqueadosTests(TestCase):
    """Al dueño no se le corta nada: es el que tiene que poder pagar. Pero se
    entera apenas abre la app."""

    def setUp(self):
        self.gimnasio = _gimnasio(
            estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
            estado_cuenta_desde=timezone.now(),
        )
        self.usuario = _staff(self.gimnasio)
        self.client.force_login(self.usuario)

    def test_el_panel_sigue_entrando(self):
        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "tenants/home.html")

    def test_ve_el_banner_ambar(self):
        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "banner-atraso")

    def test_la_nav_de_staff_sigue_completa(self):
        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, 'class="nav-staff"')

    def test_puede_seguir_dando_de_alta_alumnos(self):
        respuesta = self.client.post(
            reverse("alumnos:crear"),
            {
                "nombre": "Nuevo",
                "apellido": "Alumno",
                "estado": Alumno.Estado.ACTIVO,
                "fecha_inicio_ciclo": timezone.localdate(),
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(Alumno.objects.filter(nombre="Nuevo").exists())


class CuentaSuspendidaTests(TestCase):
    """Suspendida corta a todos, staff incluido."""

    def setUp(self):
        self.gimnasio = _gimnasio(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA,
            estado_cuenta_desde=timezone.now(),
        )
        self.usuario = _staff(self.gimnasio)
        self.client.force_login(self.usuario)

    def test_el_staff_ve_el_cartel_con_200(self):
        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")

    def test_el_cartel_del_staff_no_trae_la_nav(self):
        """Una nav de 9 secciones que van todas al mismo cartel es ruido. El
        ☰ del celular se va con ella: sin nav, es un botón que no hace nada."""
        respuesta = self.client.get(reverse("home"))

        self.assertNotContains(respuesta, 'class="nav-staff"')
        self.assertNotContains(respuesta, "Abrir menú")

    def test_el_cartel_del_staff_dice_a_quien_escribirle(self):
        with override_settings(SOPORTE_CONTACTO="soporte@tugimapp.com"):
            respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "soporte@tugimapp.com")

    def test_un_alta_de_alumno_redirige_y_no_crea_nada(self):
        respuesta = self.client.post(
            reverse("alumnos:crear"),
            {
                "nombre": "Nuevo",
                "apellido": "Alumno",
                "estado": Alumno.Estado.ACTIVO,
                "fecha_inicio_ciclo": timezone.localdate(),
            },
        )

        self.assertRedirects(respuesta, reverse("home"))
        self.assertFalse(Alumno.objects.filter(nombre="Nuevo").exists())

    def test_el_alumno_tambien_queda_afuera(self):
        _, usuario_alumno = _alumno_con_acceso(self.gimnasio)
        self.client.force_login(usuario_alumno)

        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")

    def test_el_staff_no_puede_exportar_sus_datos_desde_la_app(self):
        """Decisión de producto: la cuenta suspendida no exporta desde la app.
        El superadmin la baja con `manage.py exportar_gimnasio`."""
        self.gimnasio.exportacion_habilitada = True
        self.gimnasio.save()

        respuesta = self.client.post(reverse("gimnasio_exportar"))

        self.assertRedirects(respuesta, reverse("home"))


class OtroGimnasioNoSeContagiaTests(TestCase):
    def test_el_alumno_de_un_gimnasio_normal_entra_igual(self):
        _gimnasio(estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA)
        sano = _gimnasio("Sano", "sano")
        _, usuario = _alumno_con_acceso(sano, username="alumno-sano")
        self.client.force_login(usuario)

        respuesta = self.client.get(reverse("home"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "tenants/home.html")


class AllowlistDelBloqueoTests(TestCase):
    """Lo que tiene que seguir funcionando aunque la cuenta esté suspendida.

    Cada uno de estos es una forma de dejar a alguien encerrado: sin
    `logout` no puede ni salir, sin `suplantacion_volver` el staff se queda
    atrapado dentro de la cuenta del alumno, y sin la PWA el service worker
    y el ícono empiezan a dar carteles HTML donde el navegador espera un
    `.js` o un `.png`.
    """

    def setUp(self):
        self.gimnasio = _gimnasio(estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA)
        self.usuario = _staff(self.gimnasio)
        self.client.force_login(self.usuario)

    def test_puede_cerrar_sesion(self):
        respuesta = self.client.post(reverse("logout"))

        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_el_service_worker_se_sirve_igual(self):
        respuesta = self.client.get(reverse("notificaciones:pwa_service_worker"))

        self.assertEqual(respuesta.status_code, 200)

    def test_el_manifest_y_el_icono_se_sirven_igual(self):
        manifest = self.client.get(
            reverse("notificaciones:pwa_manifest", args=[self.gimnasio.slug])
        )
        icono = self.client.get(
            reverse("notificaciones:pwa_icono", args=[self.gimnasio.slug, 192])
        )

        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(icono.status_code, 200)

    def test_la_landing_publica_sigue_en_pie(self):
        respuesta = self.client.get(
            reverse("landing_gimnasio", args=[self.gimnasio.slug])
        )

        self.assertEqual(respuesta.status_code, 200)

    def test_la_politica_de_privacidad_sigue_en_pie(self):
        respuesta = self.client.get(reverse("politica_privacidad"))

        self.assertEqual(respuesta.status_code, 200)

    def test_el_staff_puede_volver_de_una_suplantacion(self):
        gimnasio = _gimnasio("Con Deuda", "con-deuda")
        Gimnasio.objects.filter(pk=gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS
        )
        staff = _staff(gimnasio, "duenio-deuda")
        alumno, _ = _alumno_con_acceso(gimnasio, username="alumno-deuda")
        cliente = Client()
        cliente.force_login(staff)
        cliente.post(reverse("suplantar", args=[alumno.pk]))
        self.assertIn("suplantacion", cliente.session)

        respuesta = cliente.post(reverse("suplantacion_volver"))

        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn("suplantacion", cliente.session)
        self.assertEqual(int(cliente.session["_auth_user_id"]), staff.pk)


class ElSuperadminNoSeBloqueaTests(TestCase):
    """El dueño del producto entra a su panel aunque el gimnasio al que está
    enganchado su Perfil esté suspendido."""

    def setUp(self):
        self.gimnasio = _gimnasio(estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA)
        self.superadmin = User.objects.create_superuser(
            "dueno-producto", "dueno@example.com", CLAVE
        )
        Perfil.objects.create(
            usuario=self.superadmin, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.force_login(self.superadmin)

    def test_el_panel_de_plataforma_entra(self):
        respuesta = self.client.get(reverse("plataforma:inicio"))

        self.assertEqual(respuesta.status_code, 200)

    def test_el_admin_de_django_entra(self):
        respuesta = self.client.get(reverse("admin:index"))

        self.assertEqual(respuesta.status_code, 200)

    def test_el_dashboard_del_gimnasio_tampoco_se_le_bloquea(self):
        respuesta = self.client.get(reverse("home"))

        self.assertTemplateUsed(respuesta, "tenants/home.html")


class CostoDelMiddlewareTests(TestCase):
    """El bloqueo no puede costar una query más en el camino feliz.

    `base.html` y las vistas ya resuelven `user.perfil.gimnasio` en cada
    request, y el ORM cachea las dos relaciones en la instancia: el middleware
    lee lo mismo un poco antes. Un `select_related` de más acá sería una query
    extra en TODAS las páginas de TODOS los gimnasios sanos.
    """

    def setUp(self):
        self.gimnasio = _gimnasio()
        self.usuario = _staff(self.gimnasio)

    def _queries_de_home(self, middleware=None):
        # Client nuevo DENTRO del override: el handler arma la cadena de
        # middleware en el primer request y después la guarda.
        cliente = Client()
        cliente.force_login(self.usuario)
        # El login ya es un request: se hace antes de medir a propósito.
        with CaptureQueriesContext(connection) as capturadas:
            respuesta = cliente.get(reverse("home"))
        self.assertEqual(respuesta.status_code, 200)
        return len(capturadas)

    def test_el_middleware_no_agrega_ninguna_query(self):
        sin_el_middleware = [
            m for m in settings.MIDDLEWARE if "PlataformaMiddleware" not in m
        ]
        self.assertEqual(len(sin_el_middleware), len(settings.MIDDLEWARE) - 1)

        with override_settings(MIDDLEWARE=sin_el_middleware):
            sin = self._queries_de_home()
        con = self._queries_de_home()

        self.assertEqual(con, sin)


class EstadoCuentaViewTests(TestCase):
    """Las dos palancas del panel: bloquear/suspender (con confirmación) y
    restaurar (POST directo)."""

    def setUp(self):
        self.gimnasio = _gimnasio()
        self.superadmin = User.objects.create_superuser(
            "dueno-producto", "dueno@example.com", CLAVE
        )
        self.client.force_login(self.superadmin)

    def _url(self, estado):
        return reverse(
            "plataforma:estado_cuenta", args=[self.gimnasio.pk, estado]
        )

    def test_el_get_de_suspender_pide_confirmacion_y_no_cambia_nada(self):
        respuesta = self.client.get(self._url(Gimnasio.EstadoCuenta.SUSPENDIDA))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/confirmar_estado.html")
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.estado_cuenta, Gimnasio.EstadoCuenta.NORMAL)

    def test_el_post_bloquea_a_los_alumnos_y_estampa_la_fecha(self):
        antes = timezone.now()

        respuesta = self.client.post(
            self._url(Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS)
        )

        self.assertRedirects(
            respuesta,
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk]),
        )
        self.gimnasio.refresh_from_db()
        self.assertEqual(
            self.gimnasio.estado_cuenta, Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS
        )
        self.assertGreaterEqual(self.gimnasio.estado_cuenta_desde, antes)

    def test_restaurar_limpia_la_fecha(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA,
            estado_cuenta_desde=timezone.now(),
        )

        self.client.post(self._url(Gimnasio.EstadoCuenta.NORMAL))

        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.estado_cuenta, Gimnasio.EstadoCuenta.NORMAL)
        self.assertIsNone(self.gimnasio.estado_cuenta_desde)

    def test_restaurar_no_tiene_pantalla_de_confirmacion(self):
        """Restaurar no destruye nada: pedir confirmación para devolverle el
        acceso a un gimnasio que acaba de pagar es fricción pura."""
        respuesta = self.client.get(self._url(Gimnasio.EstadoCuenta.NORMAL))

        self.assertEqual(respuesta.status_code, 405)

    def test_un_estado_inventado_da_404(self):
        respuesta = self.client.post(self._url("congelada"))

        self.assertEqual(respuesta.status_code, 404)

    def test_no_toca_modificado(self):
        """`modificado` versiona el logo y el ícono de la PWA: escribirlo acá
        le invalidaría la caché del ícono a todos los celulares del gimnasio
        por un cambio que no tiene nada que ver."""
        modificado_antes = Gimnasio.objects.get(pk=self.gimnasio.pk).modificado

        self.client.post(self._url(Gimnasio.EstadoCuenta.SUSPENDIDA))

        self.assertEqual(
            Gimnasio.objects.get(pk=self.gimnasio.pk).modificado, modificado_antes
        )

    def test_un_staff_de_gimnasio_recibe_403(self):
        staff = _staff(self.gimnasio, "duenio-gim")
        self.client.force_login(staff)

        respuesta = self.client.post(self._url(Gimnasio.EstadoCuenta.SUSPENDIDA))

        self.assertEqual(respuesta.status_code, 403)


class ExportacionToggleViewTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        self.superadmin = User.objects.create_superuser(
            "dueno-producto", "dueno@example.com", CLAVE
        )
        self.client.force_login(self.superadmin)
        self.url = reverse(
            "plataforma:exportacion_toggle", args=[self.gimnasio.pk]
        )

    def test_el_post_invierte_la_casilla(self):
        respuesta = self.client.post(self.url)

        self.assertRedirects(
            respuesta,
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk]),
        )
        self.gimnasio.refresh_from_db()
        self.assertTrue(self.gimnasio.exportacion_habilitada)

        self.client.post(self.url)
        self.gimnasio.refresh_from_db()
        self.assertFalse(self.gimnasio.exportacion_habilitada)

    def test_no_toca_modificado(self):
        modificado_antes = Gimnasio.objects.get(pk=self.gimnasio.pk).modificado

        self.client.post(self.url)

        self.assertEqual(
            Gimnasio.objects.get(pk=self.gimnasio.pk).modificado, modificado_antes
        )

    def test_un_staff_de_gimnasio_recibe_403(self):
        staff = _staff(self.gimnasio, "duenio-gim")
        self.client.force_login(staff)

        respuesta = self.client.post(self.url)

        self.assertEqual(respuesta.status_code, 403)


class FichaDelGimnasioConEstadoTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        self.superadmin = User.objects.create_superuser(
            "dueno-producto", "dueno@example.com", CLAVE
        )
        self.client.force_login(self.superadmin)
        self.url = reverse(
            "plataforma:gimnasio_detalle", args=[self.gimnasio.pk]
        )

    def test_un_gimnasio_normal_ofrece_bloquear_a_los_alumnos(self):
        respuesta = self.client.get(self.url)

        self.assertContains(
            respuesta,
            reverse(
                "plataforma:estado_cuenta",
                args=[self.gimnasio.pk, Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS],
            ),
        )
        self.assertNotContains(
            respuesta,
            reverse(
                "plataforma:estado_cuenta",
                args=[self.gimnasio.pk, Gimnasio.EstadoCuenta.NORMAL],
            ),
        )

    def test_con_los_alumnos_bloqueados_ofrece_suspender_y_restaurar(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
            estado_cuenta_desde=timezone.now(),
        )

        respuesta = self.client.get(self.url)

        self.assertContains(
            respuesta,
            reverse(
                "plataforma:estado_cuenta",
                args=[self.gimnasio.pk, Gimnasio.EstadoCuenta.SUSPENDIDA],
            ),
        )
        self.assertContains(
            respuesta,
            reverse(
                "plataforma:estado_cuenta",
                args=[self.gimnasio.pk, Gimnasio.EstadoCuenta.NORMAL],
            ),
        )
        self.assertContains(respuesta, "Alumnos bloqueados desde")

    def test_suspendida_dice_desde_cuando_y_solo_ofrece_restaurar(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA,
            estado_cuenta_desde=timezone.now(),
        )

        respuesta = self.client.get(self.url)

        self.assertContains(respuesta, "Suspendida desde")
        self.assertNotContains(
            respuesta,
            reverse(
                "plataforma:estado_cuenta",
                args=[self.gimnasio.pk, Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS],
            ),
        )

    def test_la_ficha_ofrece_prender_la_exportacion(self):
        respuesta = self.client.get(self.url)

        self.assertContains(respuesta, "Habilitar exportación")

    def test_la_ficha_ofrece_apagar_la_exportacion_ya_prendida(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            exportacion_habilitada=True
        )

        respuesta = self.client.get(self.url)

        self.assertContains(respuesta, "Deshabilitar exportación")


class MonitorConEstadoDeCuentaTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            "dueno-producto", "dueno@example.com", CLAVE
        )
        self.client.force_login(self.superadmin)

    def test_la_tabla_marca_al_gimnasio_suspendido(self):
        _gimnasio("Congelado", "congelado", estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA)
        _gimnasio("Sano", "sano")

        respuesta = self.client.get(reverse("plataforma:inicio"))

        self.assertContains(respuesta, "Suspendida")
