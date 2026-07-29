"""
Tests de Fase 0: registro, login y aislamiento básico de datos entre
gimnasios. Los tests de TenantScopedMixin/TenantScopedModelForm contra un
modelo de dominio real (Alumno, etc.) se agregan en Fase 1, siguiendo el
patrón de ~/gestor-pedidos/core/tests.py — en Fase 0 todavía no existe ningún
TenantOwnedModel concreto para ejercitarlos.
"""

from datetime import date, timedelta

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from alumnos.models import Alumno
from novedades.models import Novedad, NovedadLeida
from pagos.models import MedioCobro, PagoMensual
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
from tenants.models import Gimnasio, Perfil


class RegisterViewTests(TestCase):
    def test_registro_crea_usuario_gimnasio_y_perfil_staff_y_loguea(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "dueno1",
                "password1": "una-clave-segura-123",
                "password2": "una-clave-segura-123",
                "nombre_gimnasio": "Gimnasio Central",
            },
        )
        self.assertRedirects(response, reverse("home"))

        user = User.objects.get(username="dueno1")
        gimnasio = Gimnasio.objects.get(nombre="Gimnasio Central")
        perfil = Perfil.objects.get(usuario=user)

        self.assertEqual(perfil.gimnasio, gimnasio)
        self.assertEqual(perfil.rol, Perfil.Rol.STAFF)
        self.assertEqual(gimnasio.slug, "gimnasio-central")

        # El registro deja al usuario logueado.
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

    def test_slug_no_colisiona_entre_gimnasios_con_el_mismo_nombre(self):
        Gimnasio.objects.create(nombre="Gimnasio Central", slug="gimnasio-central")

        self.client.post(
            reverse("register"),
            {
                "username": "dueno2",
                "password1": "otra-clave-segura-456",
                "password2": "otra-clave-segura-456",
                "nombre_gimnasio": "Gimnasio Central",
            },
        )

        segundo = Gimnasio.objects.exclude(slug="gimnasio-central").get(
            nombre="Gimnasio Central"
        )
        self.assertEqual(segundo.slug, "gimnasio-central-2")


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.user = User.objects.create_user("alumno1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )

    def test_home_requiere_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('home')}"
        )

    def test_login_y_home_muestra_el_gimnasio_del_perfil(self):
        self.client.login(username="alumno1", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gimnasio de Prueba")

    def test_logout_saca_la_sesion(self):
        self.client.login(username="alumno1", password="clave-123456")
        self.client.post(reverse("logout"))
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)


class TenantIsolationTests(TestCase):
    """Confirma que dos gimnasios no comparten datos ni perfiles."""

    def test_cada_perfil_pertenece_a_un_solo_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        user = User.objects.create_user("staff-a", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=user, gimnasio=gimnasio_a, rol=Perfil.Rol.STAFF
        )

        self.assertEqual(perfil.gimnasio, gimnasio_a)
        self.assertNotEqual(perfil.gimnasio, gimnasio_b)


class _VistaDeStaff(StaffRequiredMixin, TemplateView):
    """Vista mínima de prueba; no se registra en urls."""

    template_name = "tenants/home.html"


class StaffRequiredMixinTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")

    def _get(self, user):
        request = self.factory.get("/")
        request.user = user
        return _VistaDeStaff.as_view()(request)

    def test_staff_entra_sin_problema(self):
        user = User.objects.create_user("staff-1", password="x")
        Perfil.objects.create(usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        response = self._get(user)
        self.assertEqual(response.status_code, 200)

    def test_alumno_recibe_permission_denied(self):
        user = User.objects.create_user("alumno-1", password="x")
        Perfil.objects.create(usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        with self.assertRaises(PermissionDenied):
            self._get(user)

    def test_usuario_sin_perfil_recibe_permission_denied(self):
        user = User.objects.create_user("sin-perfil", password="x")
        with self.assertRaises(PermissionDenied):
            self._get(user)


class _VistaDeAlumno(AlumnoRequiredMixin, View):
    """Vista mínima de prueba; no se registra en urls."""

    def get(self, request):
        return HttpResponse("ok")


class AlumnoRequiredMixinTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")

    def _get(self, user):
        request = self.factory.get("/")
        request.user = user
        return _VistaDeAlumno.as_view()(request)

    def test_alumno_entra_sin_problema(self):
        user = User.objects.create_user("alumno-1", password="x")
        Perfil.objects.create(usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        response = self._get(user)
        self.assertEqual(response.status_code, 200)

    def test_staff_recibe_permission_denied(self):
        user = User.objects.create_user("staff-1", password="x")
        Perfil.objects.create(usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        with self.assertRaises(PermissionDenied):
            self._get(user)

    def test_usuario_sin_perfil_recibe_permission_denied(self):
        user = User.objects.create_user("sin-perfil", password="x")
        with self.assertRaises(PermissionDenied):
            self._get(user)

    def test_anonimo_es_redirigido_al_login(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()
        response = _VistaDeAlumno.as_view()(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_alumno_sin_alumno_vinculado_entra_y_alumno_es_none(self):
        user = User.objects.create_user("alumno-sin-vinculo", password="x")
        Perfil.objects.create(usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)

        # Capturar la vista para verificar que self.alumno es None
        request = self.factory.get("/")
        request.user = user
        view = _VistaDeAlumno()
        view.setup(request)
        response = view.dispatch(request)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(view.alumno)


class HomeViewAlumnoTests(TestCase):
    """Portal del alumno (Fase 3): rutina activa, cuota del mes, novedades."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio Alfa", slug="alfa")
        self.hoy = timezone.now().date()

    def _crear_alumno_con_login(self, *, username, nombre, apellido):
        user = User.objects.create_user(username, password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre=nombre,
            apellido=apellido,
            perfil=perfil,
        )
        return user, perfil, alumno

    def test_alumno_ve_rutina_pago_y_novedad(self):
        user, _perfil, alumno = self._crear_alumno_con_login(
            username="ana", nombre="Ana", apellido="Gómez"
        )
        rutina = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno,
            nombre_snapshot="Rutina Fuerza",
            objetivo_snapshot="Ganar fuerza",
            fecha_inicio=self.hoy,
            activa=True,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=rutina,
            ejercicio_nombre_snapshot="Sentadilla",
            ejercicio_video_snapshot="https://videos.example.com/sentadilla",
            dia=1,
            orden=1,
            series=4,
            repeticiones="10",
            descanso="60s",
            notas="Controlar la técnica",
        )
        PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno,
            mes=self.hoy.month,
            anio=self.hoy.year,
            monto=10000,
            estado=PagoMensual.Estado.PAGADO,
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Gimnasio cerrado el feriado",
            mensaje="Aviso",
        )

        self.client.login(username="ana", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rutina Fuerza")
        self.assertContains(response, "Sentadilla")
        self.assertContains(response, "https://videos.example.com/sentadilla")
        self.assertContains(response, "Pagado")
        self.assertContains(response, "Gimnasio cerrado el feriado")

    def test_alumno_ve_solo_los_ejercicios_de_su_semana_actual(self):
        _user, _perfil, alumno = self._crear_alumno_con_login(
            username="fede", nombre="Fede", apellido="Iglesias"
        )
        rutina = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno,
            nombre_snapshot="Rutina Progresiva",
            objetivo_snapshot="Hipertrofia",
            fecha_inicio=timezone.localdate() - timedelta(days=7),  # hoy cae en semana 2
            activa=True,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=rutina,
            ejercicio_nombre_snapshot="Sentadilla semana 1",
            semana=1,
            dia=1,
            orden=1,
            series=4,
            repeticiones="10",
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=rutina,
            ejercicio_nombre_snapshot="Peso muerto semana 2",
            semana=2,
            dia=1,
            orden=1,
            series=4,
            repeticiones="8",
        )

        self.client.login(username="fede", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertContains(response, "Peso muerto semana 2")
        self.assertNotContains(response, "Sentadilla semana 1")
        self.assertContains(response, "Semana 2 de 4")

    def test_alumno_ve_semana_1_si_semana_actual_no_tiene_items(self):
        # Rutinas de antes de la progresión semanal (o plantillas nuevas
        # todavía sin cargar más allá de semana 1) tienen TODOS sus items en
        # semana=1. Sin fallback, un alumno cuya `semana_actual` calculada
        # sea >1 vería la tabla vacía para siempre.
        _user, _perfil, alumno = self._crear_alumno_con_login(
            username="lucia", nombre="Lucía", apellido="Fernández"
        )
        rutina = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno,
            nombre_snapshot="Rutina Vieja",
            objetivo_snapshot="Fuerza",
            fecha_inicio=timezone.localdate() - timedelta(days=21),  # semana 4
            activa=True,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=rutina,
            ejercicio_nombre_snapshot="Sentadilla semana 1",
            semana=1,
            dia=1,
            orden=1,
            series=4,
            repeticiones="10",
        )

        self.client.login(username="lucia", password="clave-123456")
        response = self.client.get(reverse("home"))

        # El header debe reflejar la semana MOSTRADA (1, por el fallback),
        # no la semana_actual calculada (4) -- mostrar "Semana 4 de 4" junto
        # a ejercicios de semana 1 sería engañoso para el alumno.
        self.assertContains(response, "Semana 1 de 4")
        self.assertNotContains(response, "Semana 4 de 4")
        self.assertContains(response, "Sentadilla semana 1")

    def test_alumno_sin_rutina_ve_mensaje_no_tecnico(self):
        self._crear_alumno_con_login(
            username="beto", nombre="Beto", apellido="Ramírez"
        )
        self.client.login(username="beto", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Todavía no tenés una rutina asignada.")

    def test_alumno_sin_pago_del_mes_ve_estado_sin_informacion(self):
        self._crear_alumno_con_login(
            username="carla", nombre="Carla", apellido="Díaz"
        )
        self.client.login(username="carla", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin información de tu cuota este mes.")

    def test_alumno_no_ve_rutina_de_otro_alumno_del_mismo_gimnasio(self):
        _user_a, _perfil_a, alumno_a = self._crear_alumno_con_login(
            username="dario", nombre="Darío", apellido="López"
        )
        _user_b, _perfil_b, alumno_b = self._crear_alumno_con_login(
            username="elena", nombre="Elena", apellido="Suárez"
        )
        RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno_a,
            nombre_snapshot="Rutina de Darío",
            objetivo_snapshot="Objetivo A",
            fecha_inicio=self.hoy,
            activa=True,
        )
        RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno_b,
            nombre_snapshot="Rutina de Elena",
            objetivo_snapshot="Objetivo B",
            fecha_inicio=self.hoy,
            activa=True,
        )

        self.client.login(username="dario", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rutina de Darío")
        self.assertNotContains(response, "Rutina de Elena")

    def test_alumno_ve_su_novedad_personal_pero_no_la_de_otro(self):
        # Parte B: las novedades personales (con `alumno`) solo las ve su
        # destinatario; los broadcasts (sin `alumno`), todo el gimnasio.
        _u_a, _p_a, alumno_a = self._crear_alumno_con_login(
            username="hugo", nombre="Hugo", apellido="Vera"
        )
        _u_b, _p_b, alumno_b = self._crear_alumno_con_login(
            username="ines", nombre="Inés", apellido="Mora"
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Aviso para todo el gimnasio",
            mensaje="Broadcast.",
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Aviso solo para Hugo",
            mensaje="Personal.", alumno=alumno_a,
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Aviso solo para Ines",
            mensaje="Personal.", alumno=alumno_b,
        )

        self.client.login(username="hugo", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertContains(response, "Aviso para todo el gimnasio")
        self.assertContains(response, "Aviso solo para Hugo")
        self.assertNotContains(response, "Aviso solo para Ines")

    def test_contexto_incluye_ids_novedades_leidas(self):
        _user, _perfil, alumno = self._crear_alumno_con_login(
            username="fede", nombre="Fede", apellido="Ruiz"
        )
        novedad_leida = Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Leída", mensaje="Contenido."
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Sin leer", mensaje="Contenido."
        )
        NovedadLeida.objects.create(novedad=novedad_leida, alumno=alumno)

        self.client.login(username="fede", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["ids_novedades_leidas"], {novedad_leida.pk}
        )

    def test_badge_nueva_solo_aparece_para_novedades_no_leidas(self):
        _user, _perfil, alumno = self._crear_alumno_con_login(
            username="gaby", nombre="Gaby", apellido="Torres"
        )
        novedad_leida = Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Ya la leí", mensaje="Contenido."
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Todavía no la leí", mensaje="Contenido."
        )
        NovedadLeida.objects.create(novedad=novedad_leida, alumno=alumno)

        self.client.login(username="gaby", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertContains(response, "Nueva")
        # Ambas novedades comparten plantilla; el badge "Nueva" debe aparecer
        # una sola vez (para la no leída), no para la ya leída.
        self.assertEqual(response.content.decode().count(">Nueva<"), 1)

    def test_alumno_sin_ficha_no_ve_boton_marcar_leida(self):
        user = User.objects.create_user("sin-ficha-2", password="clave-123456")
        Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio, titulo="Aviso", mensaje="Contenido."
        )

        self.client.login(username="sin-ficha-2", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Marcar como leída")
        self.assertNotContains(response, "Nueva")

    def test_perfil_alumno_sin_alumno_vinculado_no_rompe(self):
        user = User.objects.create_user("sin-vinculo", password="clave-123456")
        Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )

        self.client.login(username="sin-vinculo", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Todavía no está vinculada tu cuenta a una ficha de alumno.",
        )

    def test_cuota_pendiente_con_monto_muestra_monto_y_alias_activos(self):
        _user, _perfil, alumno = self._crear_alumno_con_login(
            username="hugo", nombre="Hugo", apellido="Peralta"
        )
        PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno,
            mes=self.hoy.month,
            anio=self.hoy.year,
            monto=15000,
            estado=PagoMensual.Estado.PENDIENTE,
        )
        MedioCobro.objects.create(
            gimnasio=self.gimnasio,
            alias="gimnasio.alfa",
            titular="Juan Pérez",
            entidad="Mercado Pago",
            activo=True,
        )
        MedioCobro.objects.create(
            gimnasio=self.gimnasio,
            alias="alias-inactivo",
            activo=False,
        )
        otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio Beta", slug="beta")
        MedioCobro.objects.create(
            gimnasio=otro_gimnasio,
            alias="alias-otro-gimnasio",
            activo=True,
        )

        self.client.login(username="hugo", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monto: $ 15000")
        self.assertContains(response, "gimnasio.alfa")
        self.assertContains(response, "Juan Pérez")
        self.assertContains(response, "Mercado Pago")
        self.assertNotContains(response, "alias-inactivo")
        self.assertNotContains(response, "alias-otro-gimnasio")

    def test_cuota_pagada_no_muestra_lista_de_alias(self):
        _user, _perfil, alumno = self._crear_alumno_con_login(
            username="ines", nombre="Inés", apellido="Marín"
        )
        PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno,
            mes=self.hoy.month,
            anio=self.hoy.year,
            monto=15000,
            estado=PagoMensual.Estado.PAGADO,
        )
        MedioCobro.objects.create(
            gimnasio=self.gimnasio,
            alias="gimnasio.alfa",
            activo=True,
        )

        self.client.login(username="ines", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "transferir")
        self.assertNotContains(response, "gimnasio.alfa")

    def test_sin_mensualidad_actual_no_rompe_por_medios_cobro_ausente(self):
        self._crear_alumno_con_login(
            username="jorge", nombre="Jorge", apellido="Núñez"
        )
        MedioCobro.objects.create(
            gimnasio=self.gimnasio,
            alias="gimnasio.alfa",
            activo=True,
        )

        self.client.login(username="jorge", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin información de tu cuota este mes.")

    def test_alumno_ve_link_reservar_turno_con_ficha(self):
        self._crear_alumno_con_login(
            username="con-ficha-turno", nombre="Rita", apellido="Sosa"
        )

        self.client.login(username="con-ficha-turno", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("turnos:mis_turnos"))

    def test_alumno_ve_link_reservar_turno_sin_ficha(self):
        user = User.objects.create_user("sin-ficha-turno", password="clave-123456")
        Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )

        self.client.login(username="sin-ficha-turno", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("turnos:mis_turnos"))


class GimnasioLandingViewTests(TestCase):
    """Landing pública (subproyecto 5): la primera vista sin ningún mixin de
    autenticación. Foco: accesible sin login, 404 para gimnasio inactivo o
    slug inexistente (no revela cuál de los dos casos es), y que el
    contenido de contacto/login esté presente."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio Central",
            slug="central",
            texto_bienvenida="¡Sumate a entrenar con nosotros!",
            contacto="011-1234-5678",
            link_whatsapp="https://wa.me/5491112345678",
            link_instagram="https://instagram.com/gimnasiocentral",
        )
        self.gimnasio_inactivo = Gimnasio.objects.create(
            nombre="Gimnasio Cerrado", slug="cerrado", activo=False
        )

    def test_anonimo_puede_ver_la_landing(self):
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gimnasio Central")
        self.assertContains(response, "¡Sumate a entrenar con nosotros!")

    def test_muestra_los_links_de_contacto_y_el_login(self):
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertContains(response, "https://wa.me/5491112345678")
        self.assertContains(response, "https://instagram.com/gimnasiocentral")
        self.assertContains(response, reverse("login"))

    def test_gimnasio_inactivo_da_404(self):
        response = self.client.get(reverse("landing_gimnasio", args=["cerrado"]))
        self.assertEqual(response.status_code, 404)

    def test_slug_inexistente_da_404(self):
        response = self.client.get(reverse("landing_gimnasio", args=["no-existe"]))
        self.assertEqual(response.status_code, 404)

    def test_staff_logueado_tambien_puede_verla(self):
        """La landing es pública -- estar logueado como staff de OTRO
        gimnasio no debería bloquear el acceso (no es una vista de gestión)."""
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")
        staff = User.objects.create_user("dueno-otro", password="clave-123456")
        Perfil.objects.create(usuario=staff, gimnasio=otro_gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="dueno-otro", password="clave-123456")
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertEqual(response.status_code, 200)


class GimnasioUpdateViewTests(TestCase):
    """Fase 4: personalización white-label. Sin pk en la URL -- get_object
    siempre devuelve el gimnasio del Perfil logueado."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio Central", slug="central")
        self.staff = User.objects.create_user("dueno", password="clave-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

    def test_anonimo_redirige_a_login(self):
        response = self.client.get(reverse("gimnasio_editar"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_alumno_recibe_403(self):
        alumno_user = User.objects.create_user("alumno-1", password="clave-123456")
        Perfil.objects.create(usuario=alumno_user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.login(username="alumno-1", password="clave-123456")
        response = self.client.get(reverse("gimnasio_editar"))
        self.assertEqual(response.status_code, 403)

    def test_staff_actualiza_los_datos_de_su_gimnasio(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            {
                "nombre": "Gimnasio Central",
                "color_primario": "#112233",
                "color_secundario": "#445566",
                "tipografia": "sistema",
                "texto_bienvenida": "¡Bienvenido!",
                "contacto": "011-1234-5678",
                "link_instagram": "https://instagram.com/gimnasiocentral",
                "link_whatsapp": "https://wa.me/5491112345678",
            },
        )
        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.color_primario, "#112233")
        self.assertEqual(self.gimnasio.texto_bienvenida, "¡Bienvenido!")

    def test_los_colores_actualizados_se_reflejan_en_el_home(self):
        self.gimnasio.color_primario = "#abcdef"
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "#abcdef")

    def test_tipografia_default_no_carga_google_fonts(self):
        """`sistema` mapea a --font-sans y no dispara ninguna carga
        externa -- un gimnasio nuevo/existente no cambia de aspecto ni
        pierde performance hasta que el dueño elige una fuente."""
        self.assertEqual(self.gimnasio.tipografia, Gimnasio.Tipografia.SISTEMA)
        self.assertIsNone(self.gimnasio.tipografia_google_param)

    def test_staff_actualiza_la_tipografia(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            {
                "nombre": "Gimnasio Central",
                "color_primario": "",
                "color_secundario": "",
                "tipografia": "montserrat",
                "texto_bienvenida": "",
                "contacto": "",
                "link_instagram": "",
                "link_whatsapp": "",
            },
        )
        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.tipografia, "montserrat")

    def test_el_form_rechaza_una_tipografia_fuera_de_la_lista_curada(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            {
                "nombre": "Gimnasio Central",
                "color_primario": "",
                "color_secundario": "",
                "tipografia": "comic-sans-libre",
                "texto_bienvenida": "",
                "contacto": "",
                "link_instagram": "",
                "link_whatsapp": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.tipografia, Gimnasio.Tipografia.SISTEMA)

    def test_tipografia_elegida_carga_google_fonts_en_el_home(self):
        self.gimnasio.tipografia = "oswald"
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Oswald")
        self.assertContains(response, "fonts.googleapis.com")

    def test_tipografia_con_comillas_no_queda_html_escapada(self):
        """`<style>` es "raw text": el navegador no decodifica entidades ahí
        adentro, así que si Django autoescapa las comillas de una familia
        tipográfica (p.ej. 'Playfair Display') el CSS queda roto (&#x27;) en
        vez de protegido. Blinda que --font-gimnasio salga con comillas
        literales, no escapadas."""
        self.gimnasio.tipografia = "playfair"
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "--font-gimnasio: 'Playfair Display', Georgia, serif;")
        self.assertNotContains(response, "&#x27;")

    def test_tipografia_sistema_no_carga_google_fonts_en_el_home(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "fonts.googleapis.com")


class AnaliticaTests(TestCase):
    """Agregaciones del dashboard de analítica (subproyecto 4): asistencia
    por día/hora, distribución por género, RPE por ejercicio. El foco es el
    aislamiento por gimnasio -- ninguna de las 3 debe mezclar datos de otro
    gimnasio."""

    def setUp(self):
        from turnos.models import Reserva

        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")

        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Pérez",
            sexo=Alumno.Sexo.FEMENINO,
        )
        self.otro_alumno_mismo_gym = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Gómez",
            sexo=Alumno.Sexo.MASCULINO,
        )
        self.alumno_sin_sexo = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Carla", apellido="Ruiz",
        )
        self.alumno_de_otro_gym = Alumno.objects.create(
            gimnasio=self.otro_gimnasio, nombre="Dario", apellido="Sosa",
            sexo=Alumno.Sexo.MASCULINO,
        )

        # 2026-01-05 es lunes.
        Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            fecha=date(2026, 1, 5), hora_inicio="09:00",
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.otro_alumno_mismo_gym,
            fecha=date(2026, 1, 5), hora_inicio="09:00",
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            fecha=date(2026, 1, 6), hora_inicio="18:00",  # martes
        )
        Reserva.objects.create(
            gimnasio=self.otro_gimnasio, alumno=self.alumno_de_otro_gym,
            fecha=date(2026, 1, 5), hora_inicio="09:00",
        )

        self.asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            nombre_snapshot="Rutina", objetivo_snapshot="Hipertrofia",
            fecha_inicio=date(2026, 1, 1), activa=True,
        )
        self.otra_asignada = RutinaAsignada.objects.create(
            gimnasio=self.otro_gimnasio, alumno=self.alumno_de_otro_gym,
            nombre_snapshot="Rutina", objetivo_snapshot="Hipertrofia",
            fecha_inicio=date(2026, 1, 1), activa=True,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada, ejercicio_nombre_snapshot="Sentadilla",
            semana=1, dia=1, orden=1, series=4, repeticiones="8-12",
            rpe=RutinaAsignadaItem.RPE.AL_LIMITE,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada, ejercicio_nombre_snapshot="Sentadilla",
            semana=2, dia=1, orden=1, series=4, repeticiones="8-12",
            rpe=RutinaAsignadaItem.RPE.BAJAR_INTENSIDAD,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada, ejercicio_nombre_snapshot="Press banca",
            semana=1, dia=1, orden=2, series=4, repeticiones="6-10",
            rpe="",  # sin calificar -- no debe contarse
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=self.otra_asignada, ejercicio_nombre_snapshot="Sentadilla",
            semana=1, dia=1, orden=1, series=4, repeticiones="8-12",
            rpe=RutinaAsignadaItem.RPE.MAS_INTENSO,
        )

    def test_asistencia_agrupa_por_dia_y_hora_y_no_mezcla_gimnasios(self):
        from tenants.analitica import asistencia_por_dia_y_hora

        resultado = asistencia_por_dia_y_hora(self.gimnasio)
        self.assertEqual(resultado["horas"], [9, 18])
        self.assertEqual(resultado["maximo"], 2)

        por_nombre = {dia["nombre"]: dia["celdas"] for dia in resultado["dias"]}
        # Lunes 9h tiene 2 reservas (alumno + otro_alumno_mismo_gym); lunes
        # 18h ninguna (esa es un martes).
        self.assertEqual(por_nombre["Lunes"][0]["valor"], 2)
        self.assertEqual(por_nombre["Lunes"][0]["nivel"], 4)
        self.assertEqual(por_nombre["Lunes"][1]["valor"], 0)
        self.assertEqual(por_nombre["Lunes"][1]["nivel"], 0)
        # Martes 18h tiene 1 reserva (nivel intermedio, no el máximo).
        self.assertEqual(por_nombre["Martes"][1]["valor"], 1)
        self.assertEqual(por_nombre["Martes"][1]["nivel"], 2)

    def test_asistencia_sin_reservas_devuelve_estado_vacio(self):
        from tenants.analitica import asistencia_por_dia_y_hora

        gimnasio_nuevo = Gimnasio.objects.create(nombre="Nuevo", slug="nuevo")
        resultado = asistencia_por_dia_y_hora(gimnasio_nuevo)
        self.assertEqual(resultado, {"horas": [], "dias": [], "maximo": 0})

    def test_genero_incluye_no_informado_y_no_mezcla_gimnasios(self):
        from tenants.analitica import distribucion_por_genero

        resultado = {fila["etiqueta"]: fila["total"] for fila in distribucion_por_genero(self.gimnasio)}
        self.assertEqual(resultado["Femenino"], 1)
        self.assertEqual(resultado["Masculino"], 1)
        self.assertEqual(resultado["No informado"], 1)
        self.assertEqual(resultado["Prefiere no decir"], 0)

    def test_rpe_agrupa_por_nombre_ordena_por_total_y_no_mezcla_gimnasios(self):
        from tenants.analitica import rpe_por_ejercicio

        resultado = rpe_por_ejercicio(self.gimnasio)
        self.assertEqual(len(resultado), 1)  # Press banca sin calificar no aparece.
        fila = resultado[0]
        self.assertEqual(fila["ejercicio"], "Sentadilla")
        self.assertEqual(fila["total"], 2)  # no las 3 (la de otro_gimnasio no cuenta).
        self.assertEqual(fila["niveles"]["al_limite"], 1)
        self.assertEqual(fila["niveles"]["bajar_intensidad"], 1)
        self.assertEqual(fila["niveles"]["mas_intenso"], 0)
