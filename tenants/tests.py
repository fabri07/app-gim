"""
Tests de Fase 0: registro, login y aislamiento básico de datos entre
gimnasios. Los tests de TenantScopedMixin/TenantScopedModelForm contra un
modelo de dominio real (Alumno, etc.) se agregan en Fase 1, siguiendo el
patrón de ~/gestor-pedidos/core/tests.py — en Fase 0 todavía no existe ningún
TenantOwnedModel concreto para ejercitarlos.
"""

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from alumnos.models import Alumno
from novedades.models import Novedad, NovedadLeida
from pagos.models import PagoMensual
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
