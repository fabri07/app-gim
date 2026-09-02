"""
Tests de Fase 0: registro, login y aislamiento básico de datos entre
gimnasios. Los tests de TenantScopedMixin/TenantScopedModelForm contra un
modelo de dominio real (Alumno, etc.) se agregan en Fase 1, siguiendo el
patrón de ~/gestor-pedidos/core/tests.py — en Fase 0 todavía no existe ningún
TenantOwnedModel concreto para ejercitarlos.
"""

from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
from unittest.mock import patch
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import NoReverseMatch, resolve, reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.generic import TemplateView, View
from PIL import Image

from alumnos.identidad import TIPO_EMAIL
from alumnos.models import Alumno
from alumnos.services import crear_acceso
from novedades.models import Novedad, NovedadLeida
from pagos.models import MedioCobro, PagoMensual
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants import paisaje_matching, suplantacion
from tenants.context_processors import tour_onboarding_disponible
from tenants.forms import GimnasioForm
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
from tenants.models import Gimnasio, Perfil, RegistroSuplantacion


class RegistroPublicoCerradoTests(TestCase):
    """El alta self-serve de gimnasios se cerró: cualquiera en internet podía
    crear User + Gimnasio + Perfil STAFF y quedaba logueado, sin verificación
    de nada. Ahora los gimnasios se dan de alta con `manage.py crear_gimnasio`.
    """

    def test_no_existe_la_ruta_de_registro(self):
        with self.assertRaises(NoReverseMatch):
            reverse("register")

    def test_la_url_vieja_de_registro_da_404(self):
        self.assertEqual(self.client.get("/accounts/register/").status_code, 404)


class CrearGimnasioCommandTests(TestCase):
    def test_crea_gimnasio_usuario_y_perfil_staff(self):
        call_command(
            "crear_gimnasio", nombre="Gimnasio Central", email="dueno@ejemplo.com"
        )

        gimnasio = Gimnasio.objects.get(nombre="Gimnasio Central")
        user = User.objects.get(username="dueno@ejemplo.com")
        perfil = Perfil.objects.get(usuario=user)

        self.assertEqual(gimnasio.slug, "gimnasio-central")
        self.assertEqual(perfil.gimnasio, gimnasio)
        self.assertEqual(perfil.rol, Perfil.Rol.STAFF)
        self.assertEqual(user.email, "dueno@ejemplo.com")

    def test_por_defecto_genera_una_contrasena_provisoria_que_sirve_para_entrar(self):
        """Estado transitorio: hasta que exista el login con Google, un
        gimnasio recién creado tiene que poder entrar de alguna forma."""
        salida = StringIO()
        call_command(
            "crear_gimnasio",
            nombre="Gimnasio Central",
            email="dueno@ejemplo.com",
            stdout=salida,
        )

        user = User.objects.get(username="dueno@ejemplo.com")
        self.assertTrue(user.has_usable_password())

        # La contraseña que imprime el comando es la que realmente funciona.
        password = salida.getvalue().split("Contraseña provisoria: ")[1].split("\n")[0]
        self.assertTrue(
            self.client.login(username="dueno@ejemplo.com", password=password)
        )

    def test_password_explicita_se_respeta(self):
        call_command(
            "crear_gimnasio",
            nombre="Gimnasio Central",
            email="dueno@ejemplo.com",
            password="una-clave-elegida-123",
        )

        self.assertTrue(
            self.client.login(
                username="dueno@ejemplo.com", password="una-clave-elegida-123"
            )
        )

    def test_password_debil_falla_sin_crear_nada(self):
        with self.assertRaises(CommandError):
            call_command(
                "crear_gimnasio",
                nombre="Gimnasio Central",
                email="dueno@ejemplo.com",
                password="1234",
            )

        self.assertFalse(Gimnasio.objects.exists())
        self.assertFalse(User.objects.exists())

    def test_sin_password_deja_la_cuenta_solo_para_google(self):
        """Modo definitivo: el staff entra por Google. Una contraseña
        inutilizable no se puede adivinar ni resetear por mail
        (`PasswordResetForm.get_users()` filtra por `has_usable_password()`)."""
        call_command(
            "crear_gimnasio",
            nombre="Gimnasio Central",
            email="dueno@ejemplo.com",
            sin_password=True,
        )

        user = User.objects.get(username="dueno@ejemplo.com")
        self.assertFalse(user.has_usable_password())

    def test_email_se_normaliza_a_minusculas(self):
        """`User.objects.get(username=...)` es case-sensitive en Postgres: sin
        normalizar, `Dueno@X.com` y `dueno@x.com` serían dos cuentas."""
        call_command(
            "crear_gimnasio", nombre="Gimnasio Central", email="  Dueno@Ejemplo.COM "
        )

        self.assertTrue(User.objects.filter(username="dueno@ejemplo.com").exists())

    def test_slug_no_colisiona_entre_gimnasios_con_el_mismo_nombre(self):
        Gimnasio.objects.create(nombre="Gimnasio Central", slug="gimnasio-central")

        call_command(
            "crear_gimnasio", nombre="Gimnasio Central", email="otro@ejemplo.com"
        )

        segundo = Gimnasio.objects.exclude(slug="gimnasio-central").get(
            nombre="Gimnasio Central"
        )
        self.assertEqual(segundo.slug, "gimnasio-central-2")

    def test_slug_explicito_se_respeta(self):
        call_command(
            "crear_gimnasio",
            nombre="Gimnasio Central",
            email="dueno@ejemplo.com",
            slug="central",
        )

        self.assertEqual(Gimnasio.objects.get(nombre="Gimnasio Central").slug, "central")

    def test_email_repetido_falla_sin_crear_nada(self):
        call_command(
            "crear_gimnasio", nombre="Primero", email="dueno@ejemplo.com"
        )

        with self.assertRaises(CommandError):
            call_command(
                "crear_gimnasio", nombre="Segundo", email="dueno@ejemplo.com"
            )

        # Atómico: el gimnasio del intento fallido no debe haber quedado.
        self.assertFalse(Gimnasio.objects.filter(nombre="Segundo").exists())
        self.assertEqual(User.objects.filter(username="dueno@ejemplo.com").count(), 1)

    def test_email_invalido_falla_sin_crear_nada(self):
        with self.assertRaises(CommandError):
            call_command("crear_gimnasio", nombre="Gimnasio", email="no-es-un-email")

        self.assertFalse(Gimnasio.objects.exists())
        self.assertFalse(User.objects.exists())


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

    def test_form_de_login_no_queda_boosteado(self):
        """El <style> del fondo del gimnasio vive en <head>, que hx-boost no
        swapea -- sin hx-boost="false" acá, el fondo no aparece recién
        logueado hasta un refresh manual."""
        response = self.client.get(reverse("login"))
        self.assertContains(response, 'hx-boost="false"')

    def test_form_de_logout_no_queda_boosteado(self):
        """Mismo motivo que el form de login: sin esto, el fondo del
        gimnasio anterior sigue apareciendo después de cerrar sesión."""
        self.client.login(username="alumno1", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(
            response, f'action="{reverse("logout")}" class="topbar__salir" hx-boost="false"'
        )

    def test_link_de_marca_anonimo_no_queda_boosteado(self):
        """Mismo motivo que el link "¿No es tu gimnasio?": el anónimo cae en
        el login genérico, y el extra_style de la página de la que viene
        (una landing/login con gimnasio) no se refresca sin esto."""
        response = self.client.get(reverse("landing_gimnasio", args=["gimnasio-de-prueba"]))
        self.assertContains(response, 'class="topbar__marca" hx-boost="false"')

    def test_link_de_marca_autenticado_sigue_boosteado(self):
        """A diferencia del caso anónimo, acá NO debe llevar hx-boost="false":
        no hay ningún extra_style de por medio (el destino es `home`, sin
        landing/login con gimnasio), y perderlo rompería la transición
        suave de siempre al clickear el logo para ir al dashboard."""
        self.client.login(username="alumno1", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'class="topbar__marca" >')

    def test_usuario_logueado_que_visita_login_es_redirigido_a_home(self):
        """Sin `redirect_authenticated_user`, un usuario ya logueado veía el
        form de login superpuesto a su propia nav y fondo (bug real visto en
        producción, ver CLAUDE.md)."""
        self.client.login(username="alumno1", password="clave-123456")
        response = self.client.get(reverse("login"))
        self.assertRedirects(response, reverse("home"))


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


class TourOnboardingDisponibleTests(TestCase):
    """`Perfil.creado` (auto_now_add) es la única señal server-side del tour
    de bienvenida: perfiles de staff creados antes de TOUR_ONBOARDING_DESDE
    nunca lo ven, aunque nunca hayan abierto la app (ver settings.py)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")

    def _habilitado(self, user):
        request = self.factory.get("/")
        # Refetch: `Perfil.objects.create(usuario=user, ...)` deja cacheado
        # `user.perfil` en memoria con el `creado` de la creación real -- el
        # `.update()` de `_crear_perfil` lo pisa en la base, pero no en ese
        # caché, así que sin refetch el test leería el `creado` viejo.
        request.user = (
            User.objects.get(pk=user.pk) if user.is_authenticated else user
        )
        return tour_onboarding_disponible(request)["tour_onboarding_habilitado"]

    def _crear_perfil(self, username, rol, creado):
        user = User.objects.create_user(username, password="clave-123456")
        perfil = Perfil.objects.create(usuario=user, gimnasio=self.gimnasio, rol=rol)
        # auto_now_add ignora el valor pasado a create(); update() lo esquiva.
        Perfil.objects.filter(pk=perfil.pk).update(
            creado=timezone.make_aware(datetime.combine(creado, datetime.min.time()))
        )
        return user

    def test_staff_creado_antes_del_corte_no_ve_el_tour(self):
        user = self._crear_perfil(
            "staff-viejo", Perfil.Rol.STAFF,
            settings.TOUR_ONBOARDING_DESDE - timedelta(days=1),
        )
        self.assertFalse(self._habilitado(user))

    def test_staff_creado_despues_del_corte_ve_el_tour(self):
        user = self._crear_perfil(
            "staff-nuevo", Perfil.Rol.STAFF,
            settings.TOUR_ONBOARDING_DESDE + timedelta(days=1),
        )
        self.assertTrue(self._habilitado(user))

    def test_alumno_no_ve_el_tour(self):
        user = self._crear_perfil(
            "alumno-1", Perfil.Rol.ALUMNO,
            settings.TOUR_ONBOARDING_DESDE + timedelta(days=1),
        )
        self.assertFalse(self._habilitado(user))

    def test_usuario_anonimo_no_ve_el_tour(self):
        self.assertFalse(self._habilitado(AnonymousUser()))

    def test_medianoche_utc_no_se_confunde_con_medianoche_local(self):
        # 01:00 UTC en la fecha de corte es todavía las 22:00 del día
        # anterior en Buenos Aires (UTC-3) -- sin convertir a hora local,
        # `.date()` diría que el perfil ya cruzó el corte un día antes de lo
        # que corresponde en el calendario local.
        user = User.objects.create_user("staff-limite", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        corte = settings.TOUR_ONBOARDING_DESDE
        creado_utc = datetime(corte.year, corte.month, corte.day, 1, 0, tzinfo=ZoneInfo("UTC"))
        Perfil.objects.filter(pk=perfil.pk).update(creado=creado_utc)
        self.assertFalse(self._habilitado(user))

    def test_admin_no_habilita_el_tour(self):
        user = self._crear_perfil(
            "staff-admin", Perfil.Rol.STAFF,
            settings.TOUR_ONBOARDING_DESDE + timedelta(days=1),
        )
        request = self.factory.get("/admin/")
        request.user = User.objects.get(pk=user.pk)
        request.resolver_match = resolve("/admin/")
        self.assertFalse(tour_onboarding_disponible(request)["tour_onboarding_habilitado"])


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
        self.assertContains(response, "Pagado")
        self.assertContains(response, "Gimnasio cerrado el feriado")

    def test_saluda_con_el_nombre_del_alumno_no_con_el_username(self):
        """El username de un alumno dado de alta por teléfono es el
        número normalizado (ver `alumnos/identidad.py`) -- el saludo no
        puede mostrar eso, tiene que mostrar `Alumno.nombre`."""
        self._crear_alumno_con_login(
            username="+5493572546151", nombre="Enzo", apellido="Sola"
        )

        self.client.login(username="+5493572546151", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertContains(response, "Hola, Enzo")
        self.assertNotContains(response, "+5493572546151")

    def test_saluda_sin_nombre_cuando_no_hay_alumno_vinculado(self):
        """Un `Perfil` de rol alumno sin `Alumno` asociado todavía (ver
        docstring de `HomeView._portal_alumno`) no debe romper el saludo
        ni mostrar el username crudo."""
        user = User.objects.create_user("sin_ficha", password="clave-123456")
        Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )

        self.client.login(username="sin_ficha", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertContains(response, "<h1>Hola</h1>", html=True)

    def test_dashboard_muestra_boton_por_dia_sin_ejercicios_sueltos(self):
        """El dashboard ya no lista ejercicios directamente (Fase 8: ver
        `RutinaMiDiaDetailViewTests` para el detalle semana a semana) -- solo
        un botón por día real, y el número de semana actual como
        orientación. Los ejercicios de las dos semanas viven en la pantalla
        de detalle de "Día 1", no acá."""
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

        self.assertNotContains(response, "Peso muerto semana 2")
        self.assertNotContains(response, "Sentadilla semana 1")
        self.assertContains(response, "Semana 2 de 4")
        self.assertContains(response, reverse("rutinas:mi_dia_detalle", args=[1]))

    def test_dashboard_muestra_la_semana_actual_real_aunque_no_tenga_items(self):
        # Antes había un fallback que mostraba "semana 1" si la semana
        # actual calculada no tenía items, para no dejar la tabla vacía.
        # Ya no aplica: el dashboard no muestra ejercicios (solo botones de
        # día), así que mostrar la semana REAL (aunque esa semana puntual
        # esté vacía) ya no es engañoso -- el detalle de cada semana, vacía
        # o no, se ve recién al entrar a un día.
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

        self.assertContains(response, "Semana 4 de 4")
        self.assertContains(response, reverse("rutinas:mi_dia_detalle", args=[1]))

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

    def test_alumno_ve_redes_sociales_del_gimnasio(self):
        self.gimnasio.link_instagram = "https://instagram.com/gimnasioalfa"
        self.gimnasio.link_whatsapp = "https://wa.me/5491112345678"
        self.gimnasio.link_facebook = "https://facebook.com/gimnasioalfa"
        self.gimnasio.save()
        self._crear_alumno_con_login(username="con-redes", nombre="Lu", apellido="Paz")

        self.client.login(username="con-redes", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.gimnasio.link_instagram)
        self.assertContains(response, self.gimnasio.link_whatsapp)
        self.assertContains(response, self.gimnasio.link_facebook)
        # Íconos, no botones con el nombre escrito. Antes este test fijaba
        # `<a class="boton" href=...>WhatsApp</a>`: el dueño del producto lo
        # encontró "desabrido" y pidió los logos de cada red, que la gente
        # reconoce sin leer. El nombre sobrevive en `aria-label`, que es lo
        # único que un lector de pantalla puede anunciar de un <svg>.
        self.assertContains(response, 'aria-label="WhatsApp"')
        self.assertContains(response, 'aria-label="Instagram"')
        self.assertContains(response, 'aria-label="Facebook"')
        self.assertContains(response, "redes-sociales__boton")
        self.assertNotContains(response, f'>WhatsApp</a>')

    def test_las_redes_del_alumno_van_al_pie_arriba_de_privacidad(self):
        """Pedido explícito: al final de la página, no dentro de la tarjeta
        de bienvenida. Ahí abajo cierran la pantalla, que es donde uno los
        busca."""
        self.gimnasio.link_instagram = "https://instagram.com/gimnasioalfa"
        self.gimnasio.save()
        self._crear_alumno_con_login(username="pie-redes", nombre="Sol", apellido="Rey")

        self.client.login(username="pie-redes", password="clave-123456")
        contenido = self.client.get(reverse("home")).content.decode()

        posicion_redes = contenido.index("redes-sociales__boton")
        posicion_privacidad = contenido.index(reverse("politica_privacidad"))
        posicion_bienvenida = contenido.index("Hola")
        self.assertLess(posicion_redes, posicion_privacidad)
        self.assertLess(posicion_bienvenida, posicion_redes)

    def test_solo_se_muestran_las_redes_que_el_gimnasio_cargo(self):
        """Un gimnasio con Instagram pero sin Facebook no debe mostrar un
        botón que no lleva a ningún lado."""
        self.gimnasio.link_instagram = "https://instagram.com/gimnasioalfa"
        self.gimnasio.link_whatsapp = ""
        self.gimnasio.link_facebook = ""
        self.gimnasio.save()
        self._crear_alumno_con_login(username="una-red", nombre="Ale", apellido="Gil")

        self.client.login(username="una-red", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertContains(response, 'aria-label="Instagram"')
        self.assertNotContains(response, 'aria-label="Facebook"')
        self.assertNotContains(response, 'aria-label="WhatsApp"')

    def test_alumno_ve_link_a_politica_de_privacidad(self):
        self._crear_alumno_con_login(
            username="con-privacidad", nombre="Mora", apellido="Diaz"
        )

        self.client.login(username="con-privacidad", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertContains(response, reverse("politica_privacidad"))


class PoliticaPrivacidadViewTests(TestCase):
    def test_anonimo_puede_verla(self):
        response = self.client.get(reverse("politica_privacidad"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Política de privacidad")

    def test_staff_ve_el_link_desde_mi_gimnasio(self):
        gimnasio = Gimnasio.objects.create(nombre="Gimnasio Beta", slug="beta")
        staff = User.objects.create_user("dueno-beta", password="clave-123456")
        Perfil.objects.create(usuario=staff, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="dueno-beta", password="clave-123456")

        response = self.client.get(reverse("gimnasio_editar"))

        self.assertContains(response, reverse("politica_privacidad"))


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
        self.assertContains(response, reverse("login_gimnasio", args=["central"]))

    def test_las_redes_de_la_landing_tambien_son_iconos(self):
        """Mismo tratamiento que el portal del alumno: los dos consumen el
        partial `partials/redes_sociales.html`, así que no pueden divergir."""
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertContains(response, 'aria-label="WhatsApp"')
        self.assertContains(response, 'aria-label="Instagram"')
        self.assertContains(response, "redes-sociales__boton")

    def test_el_cta_del_hero_sigue_siendo_texto(self):
        """El hero es la superficie de persuasión de la landing: su CTA
        primario dice qué hacer con palabras ("Escribinos por WhatsApp") y
        NO se reemplaza por un ícono. Los íconos son el cierre de la página,
        no la llamada a la acción."""
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertContains(response, "Escribinos por WhatsApp")

    def test_link_de_login_no_queda_boosteado(self):
        """El <style> de extra_style (fondo imagen/doodle del gimnasio
        destino) vive en <head>, que hx-boost no swapea -- sin
        hx-boost="false" acá, un click boosteado no se ve con el fondo
        correcto hasta un refresh manual (mismo motivo que login/logout)."""
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertContains(
            response,
            f'href="{reverse("login_gimnasio", args=["central"])}" hx-boost="false"',
        )

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

    def test_muestra_los_horarios_agrupados_por_dia(self):
        from turnos.models import DiaSemana, HorarioAtencion

        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio, dia_semana=DiaSemana.LUNES,
            hora_desde="08:00", hora_hasta="12:00",
        )
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio, dia_semana=DiaSemana.LUNES,
            hora_desde="17:00", hora_hasta="21:00",
        )
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertContains(response, "Lunes")
        self.assertContains(response, "08:00")
        self.assertContains(response, "17:00")

    def test_no_muestra_horarios_de_otro_gimnasio(self):
        from turnos.models import DiaSemana, HorarioAtencion

        otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro-horarios")
        HorarioAtencion.objects.create(
            gimnasio=otro_gimnasio, dia_semana=DiaSemana.MARTES,
            hora_desde="09:00", hora_hasta="10:00",
        )
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertNotContains(response, "Martes")

    def test_gimnasio_sin_horarios_no_rompe(self):
        response = self.client.get(reverse("landing_gimnasio", args=["central"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Horarios de atención")


class GimnasioLoginViewTests(TestCase):
    """Login con la estética de un gimnasio específico (`g/<slug>/login/`),
    calcada de GimnasioLandingViewTests. Foco: estética presente solo con
    slug válido, el login genérico no la filtra, 404 igual que la landing,
    y que el slug sea puramente cosmético (no una barrera de auth)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio Central",
            slug="central",
            paleta=Gimnasio.Paleta.OCEANO,
            texto_bienvenida="¡Sumate a entrenar con nosotros!",
        )
        self.gimnasio_inactivo = Gimnasio.objects.create(
            nombre="Gimnasio Cerrado", slug="cerrado", activo=False
        )
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")
        self.alumno_otro = User.objects.create_user("alumno-otro", password="clave-123456")
        Perfil.objects.create(
            usuario=self.alumno_otro, gimnasio=self.otro_gimnasio, rol=Perfil.Rol.ALUMNO
        )

    def test_anonimo_puede_ver_login_con_estetica_del_gimnasio(self):
        response = self.client.get(reverse("login_gimnasio", args=["central"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gimnasio Central")
        self.assertContains(response, "¡Sumate a entrenar con nosotros!")
        self.assertContains(response, self.gimnasio.color_primario_css)

    def test_login_generico_no_muestra_estetica_de_gimnasio(self):
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, "Gimnasio Central")
        self.assertNotContains(response, self.gimnasio.color_primario_css)

    def test_gimnasio_inactivo_da_404(self):
        response = self.client.get(reverse("login_gimnasio", args=["cerrado"]))
        self.assertEqual(response.status_code, 404)

    def test_slug_inexistente_da_404(self):
        response = self.client.get(reverse("login_gimnasio", args=["no-existe"]))
        self.assertEqual(response.status_code, 404)

    def test_login_exitoso_redirige_a_home(self):
        response = self.client.post(
            reverse("login_gimnasio", args=["central"]),
            {"username": "alumno-otro", "password": "clave-123456"},
        )
        self.assertRedirects(response, reverse("home"))

    def test_usuario_logueado_es_redirigido_sin_ver_el_form(self):
        self.client.login(username="alumno-otro", password="clave-123456")
        response = self.client.get(reverse("login_gimnasio", args=["central"]))
        self.assertRedirects(response, reverse("home"))

    def test_alumno_de_otro_gimnasio_puede_loguearse_igual(self):
        """El slug es puramente estético -- el proyecto no tiene subdominios
        por gimnasio, así que loguearse desde el login de OTRO gimnasio
        funciona igual y termina en el propio home del alumno."""
        response = self.client.post(
            reverse("login_gimnasio", args=["central"]),
            {"username": "alumno-otro", "password": "clave-123456"},
        )
        self.assertRedirects(response, reverse("home"))
        home = self.client.get(reverse("home"))
        self.assertContains(home, "Otro")

    def test_link_no_es_tu_gimnasio_aparece_y_no_queda_boosteado(self):
        response = self.client.get(reverse("login_gimnasio", args=["central"]))
        self.assertContains(response, "otro_gimnasio=1")
        self.assertContains(response, 'hx-boost="false"')


class GimnasioPreferidoCookieTests(TestCase):
    """Cookie `gimnasio_preferido`: recordar el gimnasio entre logins.

    Usa `self.client.post(reverse("login"), ...)` para loguearse, NO
    `self.client.login(...)` -- ese atajo crea la sesión directo sin pasar
    por `LoginView.post`/`form_valid`, así que nunca ejecutaría
    `setear_cookie_gimnasio`."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.gimnasio_inactivo = Gimnasio.objects.create(
            nombre="Cerrado", slug="cerrado", activo=False
        )
        self.alumno_a = User.objects.create_user("alumno-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.alumno_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )
        self.alumno_b = User.objects.create_user("alumno-b", password="clave-123456")
        Perfil.objects.create(
            usuario=self.alumno_b, gimnasio=self.gimnasio_b, rol=Perfil.Rol.ALUMNO
        )

    def test_login_generico_exitoso_setea_la_cookie(self):
        response = self.client.post(
            reverse("login"), {"username": "alumno-a", "password": "clave-123456"}
        )
        self.assertEqual(response.cookies["gimnasio_preferido"].value, "gimnasio-a")

    def test_login_por_slug_exitoso_tambien_setea_la_cookie(self):
        """Confirma que GimnasioLoginView hereda form_valid sin duplicarlo."""
        response = self.client.post(
            reverse("login_gimnasio", args=["gimnasio-b"]),
            {"username": "alumno-a", "password": "clave-123456"},
        )
        self.assertEqual(response.cookies["gimnasio_preferido"].value, "gimnasio-a")

    def test_dos_logins_sucesivos_de_gimnasios_distintos_pisan_la_cookie(self):
        self.client.post(
            reverse("login"), {"username": "alumno-a", "password": "clave-123456"}
        )
        self.assertEqual(self.client.cookies["gimnasio_preferido"].value, "gimnasio-a")
        self.client.post(reverse("logout"))
        self.client.post(
            reverse("login"), {"username": "alumno-b", "password": "clave-123456"}
        )
        self.assertEqual(self.client.cookies["gimnasio_preferido"].value, "gimnasio-b")

    def test_anonimo_con_cookie_valida_es_redirigido_preservando_next(self):
        self.client.post(
            reverse("login"), {"username": "alumno-a", "password": "clave-123456"}
        )
        self.client.post(reverse("logout"))
        response = self.client.get(reverse("home"), follow=True)
        esperado = (
            reverse("login_gimnasio", args=["gimnasio-a"])
            + f"?{urlencode({'next': reverse('home')})}"
        )
        self.assertIn((esperado, 302), response.redirect_chain)
        self.assertContains(response, "Gimnasio A")

    def test_cookie_de_gimnasio_inactivo_se_ignora_y_se_borra(self):
        self.client.cookies["gimnasio_preferido"] = "cerrado"
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Gimnasio A")
        self.assertEqual(response.cookies["gimnasio_preferido"].value, "")
        self.assertEqual(response.cookies["gimnasio_preferido"]["max-age"], 0)

    def test_cookie_con_slug_inexistente_se_ignora_sin_romper(self):
        self.client.cookies["gimnasio_preferido"] = "no-existe"
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)

    def test_link_otro_gimnasio_evita_el_loop_y_borra_la_cookie(self):
        self.client.cookies["gimnasio_preferido"] = "gimnasio-a"
        response = self.client.get(reverse("login") + "?otro_gimnasio=1")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Gimnasio A")
        self.assertEqual(response.cookies["gimnasio_preferido"].value, "")

    def test_gimnasio_login_view_ignora_la_cookie_de_otro_gimnasio(self):
        """El slug de la URL nunca debe ser pisado por la cookie."""
        self.client.cookies["gimnasio_preferido"] = "gimnasio-a"
        response = self.client.get(reverse("login_gimnasio", args=["gimnasio-b"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gimnasio B")


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
                "paleta": "oceano",
                "tipografia": "plus_jakarta",
                "fondo_tipo": "color",
                "texto_bienvenida": "¡Bienvenido!",
                "contacto": "011-1234-5678",
                "link_instagram": "https://instagram.com/gimnasiocentral",
                "link_whatsapp": "https://wa.me/5491112345678",
                "dia_vencimiento_pago": 10,
            },
        )
        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.paleta, "oceano")
        self.assertEqual(self.gimnasio.texto_bienvenida, "¡Bienvenido!")

    def test_un_link_de_red_mal_escrito_muestra_el_error_en_pantalla(self):
        """Los tres campos de redes eran los únicos del form que no
        renderizaban sus errores: un "@usuario" o un teléfono suelto no
        validan como URL, así que el form volvía sin guardar NADA (ni las
        redes ni el fondo ni el logo) y sin decir por qué. Reportado por un
        cliente real: cargó las redes, no le aparecieron en el portal del
        alumno, y no vio ningún mensaje.
        """
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            {
                "nombre": "Gimnasio Central",
                "paleta": "bosque",
                "tipografia": "plus_jakarta",
                "fondo_tipo": "color",
                "texto_bienvenida": "",
                "contacto": "",
                "link_instagram": "@gimnasiocentral",
                "link_whatsapp": "",
                "link_facebook": "",
                "dia_vencimiento_pago": 10,
            },
        )
        # Sin redirect: el form es inválido y se vuelve a renderizar.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Introduzca una URL válida")
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.link_instagram, "")

    def test_el_form_explica_el_formato_de_los_links_antes_de_escribirlos(self):
        """El aviso y los placeholders son la mitad preventiva del fix de
        arriba: el dueño de un gimnasio no tiene por qué saber qué es una
        URL, y descubrirlo recién por un error es tarde."""
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("gimnasio_editar"))
        self.assertContains(response, "https://wa.me/5491123456789")
        self.assertContains(response, "https://www.instagram.com/migimnasio")
        self.assertContains(response, "dirección completa")

    def test_los_colores_actualizados_se_reflejan_en_el_home(self):
        self.gimnasio.paleta = Gimnasio.Paleta.OCEANO
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "#1e3a5f")

    def test_tipografia_default_no_carga_google_fonts(self):
        """Plus Jakarta Sans (el default) está auto-hospedada -- a diferencia
        de las otras 4 opciones, no dispara ninguna carga externa a Google."""
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "fonts.googleapis.com")
        self.assertContains(response, "Plus Jakarta Sans")

    def test_staff_actualiza_la_tipografia(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            {
                "nombre": "Gimnasio Central",
                "paleta": "bosque",
                "tipografia": "sora",
                "fondo_tipo": "color",
                "texto_bienvenida": "",
                "contacto": "",
                "link_instagram": "",
                "link_whatsapp": "",
                "dia_vencimiento_pago": 10,
            },
        )
        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.tipografia, "sora")

    def test_el_form_rechaza_una_tipografia_fuera_de_la_lista_curada(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            {
                "nombre": "Gimnasio Central",
                "paleta": "bosque",
                "tipografia": "comic-sans-libre",
                "fondo_tipo": "color",
                "texto_bienvenida": "",
                "contacto": "",
                "link_instagram": "",
                "link_whatsapp": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.tipografia, Gimnasio.Tipografia.PLUS_JAKARTA)

    def test_el_form_rechaza_una_paleta_fuera_del_catalogo(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            {
                "nombre": "Gimnasio Central",
                "paleta": "azul-libre-inventado",
                "tipografia": "plus_jakarta",
                "fondo_tipo": "color",
                "texto_bienvenida": "",
                "contacto": "",
                "link_instagram": "",
                "link_whatsapp": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.paleta, Gimnasio.Paleta.BOSQUE)

    def test_tipografia_elegida_carga_google_fonts_en_el_home(self):
        self.gimnasio.tipografia = "sora"
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Sora")
        self.assertContains(response, "fonts.googleapis.com")

    def test_tipografia_con_comillas_no_queda_html_escapada(self):
        """`<style>` es "raw text": el navegador no decodifica entidades ahí
        adentro, así que si Django autoescapa las comillas de una familia
        tipográfica (p.ej. 'Sora') el CSS queda roto (&#x27;) en vez de
        protegido. Blinda que --font-gimnasio salga con comillas literales,
        no escapadas."""
        self.gimnasio.tipografia = "sora"
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "--font-gimnasio: 'Sora', var(--font-sans);")
        self.assertNotContains(response, "&#x27;")

    def _datos_base_fondo(self, **overrides):
        datos = {
            "nombre": "Gimnasio Central",
            "paleta": "bosque",
            "tipografia": "plus_jakarta",
            "fondo_tipo": "color",
            "texto_bienvenida": "",
            "contacto": "",
            "link_instagram": "",
            "link_whatsapp": "",
            "dia_vencimiento_pago": 10,
        }
        datos.update(overrides)
        return datos

    def test_staff_guarda_fondo_tipo_color(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(reverse("gimnasio_editar"), self._datos_base_fondo())
        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.fondo_tipo, "color")

    def test_staff_guarda_fondo_tipo_doodle(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            self._datos_base_fondo(fondo_tipo="doodle", fondo_doodle="kettlebell"),
        )
        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.fondo_doodle, "kettlebell")

    def test_staff_guarda_fondo_tipo_imagen(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("gimnasio_editar"),
            self._datos_base_fondo(
                fondo_tipo="imagen", fondo_imagen=_imagen_subida((0x1D, 0x6F, 0x56))
            ),
        )
        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.gimnasio.refresh_from_db()
        self.assertTrue(self.gimnasio.fondo_imagen)

    def test_imagen_que_excede_el_limite_no_guarda_y_muestra_error(self):
        self.client.login(username="dueno", password="clave-123456")
        contenido = _png((0, 0, 0)).read() + b"\x00" * (6 * 1024 * 1024)
        archivo = SimpleUploadedFile("fondo.png", contenido, content_type="image/png")
        response = self.client.post(
            reverse("gimnasio_editar"),
            self._datos_base_fondo(fondo_tipo="imagen", fondo_imagen=archivo),
        )
        self.assertEqual(response.status_code, 200)
        self.gimnasio.refresh_from_db()
        self.assertFalse(self.gimnasio.fondo_imagen)

    def test_smoke_render_fondo_color_no_agrega_mask_ni_url_fondos(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "mask-image")
        self.assertNotContains(response, 'url("/media/fondos/')

    def test_smoke_render_fondo_doodle_agrega_mask_image(self):
        self.gimnasio.fondo_tipo = Gimnasio.FondoTipo.DOODLE
        self.gimnasio.fondo_doodle = Gimnasio.Doodle.MANCUERNAS
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "mask-image")
        self.assertContains(response, "mancuernas.svg")

    def test_smoke_render_fondo_imagen_agrega_background_image_url(self):
        self.gimnasio.fondo_tipo = Gimnasio.FondoTipo.IMAGEN
        self.gimnasio.fondo_imagen = _imagen_subida((0x1D, 0x6F, 0x56))
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'url("/media/fondos/')

    def test_url_firmada_de_r2_no_sale_html_escapada(self):
        """En producción R2 va con AWS_QUERYSTRING_AUTH, así que `.url` trae
        query string firmada. Dentro de <style> (raw text) el navegador no
        decodifica entidades, así que un "&" autoescapado a "&amp;" rompe la
        firma y R2 responde 403 -- el fondo simplemente no carga. La suite usa
        InMemoryStorage (URLs sin query string), por eso hace falta simular la
        URL firmada a mano acá."""
        firmada = "https://r2.example/fondos/x.png?X-Amz-Expires=3600&X-Amz-Signature=abc"
        self.gimnasio.fondo_tipo = Gimnasio.FondoTipo.IMAGEN
        self.gimnasio.fondo_imagen = _imagen_subida((0x1D, 0x6F, 0x56))
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        with patch(
            "django.core.files.storage.memory.InMemoryStorage.url",
            return_value=firmada,
        ):
            response = self.client.get(reverse("home"))
        self.assertContains(response, f'url("{firmada}")')
        self.assertNotContains(response, "X-Amz-Expires=3600&amp;")

    def test_doodle_sin_archivo_estatico_no_rompe_la_pantalla_de_configuracion(self):
        """Fuera de DEBUG, `{% static %}` sobre un archivo ausente del manifest
        levanta ValueError. `gimnasio_editar` arma las URLs de los 4 doodles en
        Python (context["doodle_svgs"]), así que sin guard un doodle sin archivo
        da 500 justo en la ÚNICA pantalla donde el dueño podría cambiar el fondo:
        queda encerrado afuera.

        (En base.html la condición `{% if ... and gimnasio.fondo_doodle_url %}`
        ya no rompía, pero solo de casualidad: los operadores de smartif.py
        atrapan cualquier excepción y devuelven False. No es algo en lo que
        convenga apoyarse.)"""
        self.gimnasio.fondo_tipo = Gimnasio.FondoTipo.DOODLE
        self.gimnasio.fondo_doodle = Gimnasio.Doodle.MANCUERNAS
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        with patch(
            "tenants.models.static", side_effect=ValueError("no está en el manifest")
        ):
            response = self.client.get(reverse("gimnasio_editar"))
        self.assertEqual(response.status_code, 200)

    def test_preview_arranca_con_la_imagen_ya_guardada(self):
        """Al abrir la pantalla con un fondo de imagen ya guardado, el preview
        tiene que mostrarlo; si no, miente sobre lo guardado hasta que el dueño
        vuelve a elegir un archivo."""
        self.gimnasio.fondo_tipo = Gimnasio.FondoTipo.IMAGEN
        self.gimnasio.fondo_imagen = _imagen_subida((0x1D, 0x6F, 0x56))
        self.gimnasio.save()
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.get(reverse("gimnasio_editar"))
        # La aserción va sobre el bloque json_script puntual, no sobre
        # "/media/fondos/" suelto: esa ruta ya aparece en el <img> de "imagen
        # actual", así que un assert laxo pasaría igual sin el fix.
        self.assertContains(
            response,
            f'id="fondo-imagen-url-data" type="application/json">'
            f'"{self.gimnasio.fondo_imagen.url}"',
        )

    def test_imagen_rechazada_no_suma_un_error_contradictorio(self):
        """clean_fondo_imagen() ya explicó por qué se rechaza; clean() no debe
        agregar además "subí una imagen" a alguien que subió una."""
        contenido = _png((0, 0, 0)).read() + b"\x00" * (6 * 1024 * 1024)
        archivo = SimpleUploadedFile("fondo.png", contenido, content_type="image/png")
        form = GimnasioForm(
            data=self._datos_base_fondo(fondo_tipo="imagen"),
            files={"fondo_imagen": archivo},
            instance=self.gimnasio,
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(len(form.errors["fondo_imagen"]), 1)
        self.assertIn("5 MB", form.errors["fondo_imagen"][0])


def _png(color, size=(20, 20), mode="RGB"):
    """Arma un PNG en memoria de un solo color, para los tests de
    `paisaje_matching` -- evita depender de un archivo de logo real."""
    buffer = BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _imagen_subida(color, size=(1280, 720), formato="PNG", content_type="image/png", nombre="fondo.png"):
    """Como `_png`, pero envuelta en `SimpleUploadedFile` -- lo que
    `GimnasioForm`/`request.FILES` necesitan para `fondo_imagen`."""
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=formato)
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type=content_type)


class DoodlesAssetsTests(SimpleTestCase):
    """Los 4 SVG de doodle son assets del repo, no datos: si uno falta o está
    malformado nadie se entera hasta que un gimnasio lo elige, porque una
    máscara rota no tira error -- el navegador simplemente no dibuja nada."""

    def test_cada_doodle_del_catalogo_tiene_su_archivo(self):
        for valor, _ in Gimnasio.Doodle.choices:
            ruta = settings.BASE_DIR / "static" / "img" / "doodles" / f"{valor}.svg"
            self.assertTrue(ruta.exists(), f"falta el SVG del doodle {valor}")

    def test_los_svg_son_xml_bien_formado(self):
        """Un guion doble dentro de un comentario XML (fácil de escribir sin
        querer) invalida el archivo entero y la máscara deja de cargar, sin
        ningún síntoma en consola ni en los logs."""
        for valor, _ in Gimnasio.Doodle.choices:
            ruta = settings.BASE_DIR / "static" / "img" / "doodles" / f"{valor}.svg"
            with self.subTest(doodle=valor):
                try:
                    ElementTree.parse(ruta)
                except ElementTree.ParseError as error:
                    self.fail(f"{valor}.svg no es XML válido: {error}")


class GimnasioFormFondoImagenTests(SimpleTestCase):
    """Validación de `fondo_imagen` (tamaño, resolución mínima, formato) y
    de la relación cruzada entre `fondo_tipo` y el campo que corresponde --
    Django-free en el sentido de que `GimnasioForm().is_valid()` acá no
    dispara ninguna query (ningún campo incluido tiene validador `unique`),
    mismo criterio que `PaisajeMatchingTests`."""

    def _datos_base(self, **overrides):
        datos = {
            "nombre": "Gimnasio Test",
            "paleta": "bosque",
            "tipografia": "plus_jakarta",
            "fondo_tipo": "imagen",
            "texto_bienvenida": "",
            "contacto": "",
            "link_instagram": "",
            "link_whatsapp": "",
            "dia_vencimiento_pago": 10,
        }
        datos.update(overrides)
        return datos

    def test_imagen_valida_pasa(self):
        archivos = {"fondo_imagen": _imagen_subida((0x1D, 0x6F, 0x56))}
        form = GimnasioForm(data=self._datos_base(), files=archivos)
        self.assertTrue(form.is_valid(), form.errors)

    def test_imagen_muy_pesada_se_rechaza(self):
        contenido = _png((0x1D, 0x6F, 0x56)).read() + b"\x00" * (6 * 1024 * 1024)
        archivo = SimpleUploadedFile("fondo.png", contenido, content_type="image/png")
        form = GimnasioForm(data=self._datos_base(), files={"fondo_imagen": archivo})
        self.assertFalse(form.is_valid())
        self.assertIn("fondo_imagen", form.errors)

    def test_imagen_debajo_de_la_resolucion_minima_se_rechaza(self):
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(640, 360))
        form = GimnasioForm(data=self._datos_base(), files={"fondo_imagen": archivo})
        self.assertFalse(form.is_valid())
        self.assertIn("fondo_imagen", form.errors)

    def test_imagen_cuadrada_grande_pasa(self):
        """Un cliente real quiso usar su logo (1080x1075) de fondo y la regla
        vieja (`ancho >= 1280 Y alto >= 720`) lo rechazaba, aunque tiene MÁS
        píxeles que el mínimo. El fondo se pinta con `background-size: cover`,
        así que el navegador ya recorta centrado a la pantalla de cada
        dispositivo: la forma de la imagen no importa, su resolución sí."""
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(1080, 1075))
        form = GimnasioForm(data=self._datos_base(), files={"fondo_imagen": archivo})
        self.assertTrue(form.is_valid(), form.errors)

    def test_imagen_vertical_grande_pasa(self):
        """La misma foto de 1280x720 rotada (una foto de celular en vertical)
        se rechazaba solo por la orientación. La regla nueva mira píxeles y
        lado más corto, no ancho y alto por separado."""
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(720, 1280))
        form = GimnasioForm(data=self._datos_base(), files={"fondo_imagen": archivo})
        self.assertTrue(form.is_valid(), form.errors)

    def test_imagen_apaisada_en_el_minimo_de_siempre_sigue_pasando(self):
        """1280x720 es el piso histórico: la regla nueva no puede excluir
        nada de lo que hoy se acepta."""
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(1280, 720))
        form = GimnasioForm(data=self._datos_base(), files={"fondo_imagen": archivo})
        self.assertTrue(form.is_valid(), form.errors)

    def test_imagen_cuadrada_chica_se_rechaza(self):
        """800x800 = 640.000 px, por debajo de los 921.600 de una 1280x720:
        estirada a pantalla completa se ve pixelada. "Cuadrada" no es un pase
        libre, lo que importa es la resolución."""
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(800, 800))
        form = GimnasioForm(data=self._datos_base(), files={"fondo_imagen": archivo})
        self.assertFalse(form.is_valid())
        self.assertIn("fondo_imagen", form.errors)

    def test_imagen_panoramica_con_un_lado_finito_se_rechaza(self):
        """4000x250 supera el mínimo de píxeles totales pero con `cover` en
        una pantalla normal hay que estirar 250px de alto a 900: el piso por
        lado existe justamente para este caso."""
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(4000, 250))
        form = GimnasioForm(data=self._datos_base(), files={"fondo_imagen": archivo})
        self.assertFalse(form.is_valid())
        self.assertIn("fondo_imagen", form.errors)

    def test_el_logo_conserva_su_propio_piso_de_resolucion(self):
        """El fondo se afloja, el logo NO: 200x200 le alcanza porque va chico
        en la barra, pero como fondo esa misma imagen se vería pixelada. Los
        dos umbrales son independientes y este test lo fija."""
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(200, 200), nombre="logo.png")
        form = GimnasioForm(
            data=self._datos_base(fondo_tipo="color"), files={"logo": archivo}
        )
        self.assertTrue(form.is_valid(), form.errors)

        chico = _imagen_subida((0x1D, 0x6F, 0x56), size=(150, 150), nombre="logo.png")
        form = GimnasioForm(
            data=self._datos_base(fondo_tipo="color"), files={"logo": chico}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)

    def test_formato_no_soportado_se_rechaza(self):
        archivo = _imagen_subida(
            (0x1D, 0x6F, 0x56), formato="GIF", content_type="image/gif", nombre="fondo.gif"
        )
        form = GimnasioForm(data=self._datos_base(), files={"fondo_imagen": archivo})
        self.assertFalse(form.is_valid())
        self.assertIn("fondo_imagen", form.errors)

    def test_fondo_tipo_doodle_sin_doodle_elegido_se_rechaza(self):
        form = GimnasioForm(data=self._datos_base(fondo_tipo="doodle"))
        self.assertFalse(form.is_valid())
        self.assertIn("fondo_doodle", form.errors)

    def test_fondo_tipo_doodle_con_doodle_elegido_pasa(self):
        form = GimnasioForm(data=self._datos_base(fondo_tipo="doodle", fondo_doodle="kettlebell"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_fondo_tipo_imagen_sin_archivo_ni_imagen_previa_se_rechaza(self):
        form = GimnasioForm(data=self._datos_base(fondo_tipo="imagen"))
        self.assertFalse(form.is_valid())
        self.assertIn("fondo_imagen", form.errors)

    def test_fondo_tipo_color_no_exige_nada_mas(self):
        form = GimnasioForm(data=self._datos_base(fondo_tipo="color"))
        self.assertTrue(form.is_valid(), form.errors)


class GimnasioFormLogoTests(SimpleTestCase):
    """Validación de `logo` (tamaño, resolución mínima, formato) -- espejo
    de `GimnasioFormFondoImagenTests`, pero con los umbrales propios del
    logo (2 MB, mínimo 200x200px): es un asset más chico que el fondo. La
    lógica de validación en sí se comparte vía `_validar_imagen()`
    (`tenants/forms.py`), estos tests solo fijan que `clean_logo` la invoca
    con los umbrales correctos."""

    def _datos_base(self, **overrides):
        datos = {
            "nombre": "Gimnasio Test",
            "paleta": "bosque",
            "tipografia": "plus_jakarta",
            "fondo_tipo": "color",
            "texto_bienvenida": "",
            "contacto": "",
            "link_instagram": "",
            "link_whatsapp": "",
            "dia_vencimiento_pago": 10,
        }
        datos.update(overrides)
        return datos

    def test_logo_valido_pasa(self):
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(200, 200), nombre="logo.png")
        form = GimnasioForm(data=self._datos_base(), files={"logo": archivo})
        self.assertTrue(form.is_valid(), form.errors)

    def test_logo_muy_pesado_se_rechaza(self):
        contenido = _png((0x1D, 0x6F, 0x56), size=(200, 200)).read() + b"\x00" * (3 * 1024 * 1024)
        archivo = SimpleUploadedFile("logo.png", contenido, content_type="image/png")
        form = GimnasioForm(data=self._datos_base(), files={"logo": archivo})
        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)

    def test_logo_debajo_de_la_resolucion_minima_se_rechaza(self):
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), size=(100, 100), nombre="logo.png")
        form = GimnasioForm(data=self._datos_base(), files={"logo": archivo})
        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)

    def test_logo_formato_no_soportado_se_rechaza(self):
        archivo = _imagen_subida(
            (0x1D, 0x6F, 0x56),
            size=(200, 200),
            formato="GIF",
            content_type="image/gif",
            nombre="logo.gif",
        )
        form = GimnasioForm(data=self._datos_base(), files={"logo": archivo})
        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)

    def test_sin_archivo_el_form_sigue_siendo_valido(self):
        form = GimnasioForm(data=self._datos_base())
        self.assertTrue(form.is_valid(), form.errors)


class GimnasioFormArchivoYEliminarContradiccionTests(SimpleTestCase):
    """Documenta el comportamiento de `ClearableFileInput` (logo y
    fondo_imagen) que motivó el JS de `gimnasio_form.html` que evita que
    esta combinación llegue nunca al server: si el dueño tilda "Eliminar"
    Y ADEMÁS elige un archivo nuevo en el mismo envío, Django lo trata
    como una contradicción y rechaza el form entero -- no borra el viejo
    ni guarda el nuevo. El fix real es de UI (el checkbox se destilda
    solo al elegir un archivo, ver el script del template) -- esto no
    prueba el JS (el test client no ejecuta JS), prueba que el
    comportamiento de Django que lo motiva sigue siendo real."""

    def _datos_base(self, **overrides):
        datos = {
            "nombre": "Gimnasio Test",
            "paleta": "bosque",
            "tipografia": "plus_jakarta",
            "fondo_tipo": "color",
            "texto_bienvenida": "",
            "contacto": "",
            "link_instagram": "",
            "link_whatsapp": "",
            "dia_vencimiento_pago": 10,
        }
        datos.update(overrides)
        return datos

    def test_logo_nuevo_mas_eliminar_tildado_rechaza_el_form(self):
        archivo = _imagen_subida((0x1D, 0x6F, 0x56), nombre="logo.png")
        form = GimnasioForm(
            data=self._datos_base(**{"logo-clear": "on"}),
            files={"logo": archivo},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)


class PaisajeMatchingTests(SimpleTestCase):
    """`tenants/paisaje_matching.py` es Django-free a propósito (no toca la
    base de datos, solo lee `Gimnasio.PALETAS`) -- mismo criterio que
    `alumnos/identidad.py`. Distancia RGB simple (no Lab/CIEDE2000) porque
    con solo 4 paisajes candidatos alcanza, y evita sumar una dependencia
    nueva (colormath/scikit-image) solo para esto."""

    def test_color_solido_de_bosque_sugiere_bosque(self):
        imagen = _png((0x1D, 0x6F, 0x56))
        self.assertEqual(paisaje_matching.sugerir_paisaje(imagen), Gimnasio.Paleta.BOSQUE)

    def test_color_solido_de_oceano_sugiere_oceano(self):
        imagen = _png((0x1E, 0x3A, 0x5F))
        self.assertEqual(paisaje_matching.sugerir_paisaje(imagen), Gimnasio.Paleta.OCEANO)

    def test_color_solido_de_arena_sugiere_arena(self):
        imagen = _png((0xB4, 0x53, 0x2A))
        self.assertEqual(paisaje_matching.sugerir_paisaje(imagen), Gimnasio.Paleta.ARENA)

    def test_color_solido_de_pizarra_sugiere_pizarra(self):
        imagen = _png((0x33, 0x47, 0x5B))
        self.assertEqual(paisaje_matching.sugerir_paisaje(imagen), Gimnasio.Paleta.PIZARRA)

    def test_ignora_fondo_blanco_dominante_y_usa_el_color_del_logo(self):
        """La mayoría de los píxeles son blancos (fondo típico de un PNG de
        logo), pero el color que importa es el del isotipo en el centro."""
        imagen = Image.new("RGB", (40, 40), (255, 255, 255))
        centro = Image.new("RGB", (10, 10), (0x1E, 0x3A, 0x5F))  # azul de Océano
        imagen.paste(centro, (15, 15))
        buffer = BytesIO()
        imagen.save(buffer, format="PNG")
        buffer.seek(0)
        self.assertEqual(paisaje_matching.sugerir_paisaje(buffer), Gimnasio.Paleta.OCEANO)

    def test_ignora_pixeles_transparentes(self):
        """Un logo PNG con fondo transparente no debe confundir el canal
        alfa con un color real -- solo los píxeles opacos cuentan."""
        imagen = Image.new("RGBA", (40, 40), (255, 255, 255, 0))
        centro = Image.new("RGBA", (10, 10), (0xB4, 0x53, 0x2A, 255))  # terracota de Arena
        imagen.paste(centro, (15, 15))
        buffer = BytesIO()
        imagen.save(buffer, format="PNG")
        buffer.seek(0)
        self.assertEqual(paisaje_matching.sugerir_paisaje(buffer), Gimnasio.Paleta.ARENA)


class LogoSugerirPaisajeViewTests(TestCase):
    """Vista de sugerencia (Frente 2): staff-only, no persiste nada -- el
    dueño confirma con "Guardar cambios" como siempre, igual que ya puede
    cambiar a mano el paisaje sugerido antes de guardar."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio Central", slug="central")
        self.staff = User.objects.create_user("dueno", password="clave-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

    def _archivo(self, color):
        return SimpleUploadedFile("logo.png", _png(color).read(), content_type="image/png")

    def test_anonimo_redirige_a_login(self):
        response = self.client.post(
            reverse("logo_sugerir_paisaje"), {"logo": self._archivo((0x1E, 0x3A, 0x5F))}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_alumno_recibe_403(self):
        alumno_user = User.objects.create_user("alumno-1", password="clave-123456")
        Perfil.objects.create(usuario=alumno_user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.login(username="alumno-1", password="clave-123456")
        response = self.client.post(
            reverse("logo_sugerir_paisaje"), {"logo": self._archivo((0x1E, 0x3A, 0x5F))}
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_recibe_el_paisaje_sugerido(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(
            reverse("logo_sugerir_paisaje"), {"logo": self._archivo((0x1E, 0x3A, 0x5F))}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"paisaje": "oceano"})

    def test_no_persiste_nada_en_el_gimnasio(self):
        """Es sugerencia pura -- el dueño sigue confirmando con 'Guardar
        cambios'; esta vista no debe tocar la base."""
        self.client.login(username="dueno", password="clave-123456")
        self.client.post(
            reverse("logo_sugerir_paisaje"), {"logo": self._archivo((0x1E, 0x3A, 0x5F))}
        )
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.paleta, Gimnasio.Paleta.BOSQUE)

    def test_sin_archivo_devuelve_400(self):
        self.client.login(username="dueno", password="clave-123456")
        response = self.client.post(reverse("logo_sugerir_paisaje"), {})
        self.assertEqual(response.status_code, 400)


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
        self.asignada_bruno = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.otro_alumno_mismo_gym,
            nombre_snapshot="Rutina", objetivo_snapshot="Hipertrofia",
            fecha_inicio=date(2026, 1, 1), activa=True,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=self.asignada_bruno, ejercicio_nombre_snapshot="Sentadilla",
            semana=1, dia=1, orden=1, series=4, repeticiones="8-12",
            rpe="",  # sin calificar -- debe contar para "más asignados" igual,
                     # a diferencia de rpe_por_ejercicio.
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

    def test_ejercicios_mas_asignados_cuenta_no_calificados_y_no_mezcla_gimnasios(self):
        from tenants.analitica import ejercicios_mas_asignados

        resultado = ejercicios_mas_asignados(self.gimnasio)
        self.assertEqual(
            [(f["ejercicio"], f["total"]) for f in resultado],
            [("Sentadilla", 3), ("Press banca", 1)],
        )

    def test_ejercicios_mas_asignados_respeta_limite(self):
        from tenants.analitica import ejercicios_mas_asignados

        resultado = ejercicios_mas_asignados(self.gimnasio, limite=1)
        self.assertEqual([f["ejercicio"] for f in resultado], ["Sentadilla"])

    def test_ejercicios_mas_asignados_sin_datos_devuelve_lista_vacia(self):
        from tenants.analitica import ejercicios_mas_asignados

        gimnasio_nuevo = Gimnasio.objects.create(nombre="Nuevo2", slug="nuevo2")
        self.assertEqual(ejercicios_mas_asignados(gimnasio_nuevo), [])

    def test_ejercicios_mas_asignados_por_genero_mismo_orden_y_desglosa_sexo(self):
        from tenants.analitica import ejercicios_mas_asignados_por_genero

        resultado = ejercicios_mas_asignados_por_genero(self.gimnasio)
        self.assertEqual(
            [f["ejercicio"] for f in resultado], ["Sentadilla", "Press banca"],
        )
        sentadilla = resultado[0]
        self.assertEqual(sentadilla["generos"]["femenino"], 2)  # Ana
        self.assertEqual(sentadilla["generos"]["masculino"], 1)  # Bruno
        self.assertEqual(sentadilla["generos"]["no_decir"], 0)
        self.assertEqual(sentadilla["generos"]["no_informado"], 0)
        self.assertEqual(sentadilla["total"], 3)

        press_banca = resultado[1]
        self.assertEqual(press_banca["generos"]["femenino"], 1)  # Ana
        self.assertEqual(press_banca["generos"]["masculino"], 0)
        self.assertEqual(press_banca["total"], 1)

    def test_ejercicios_mas_asignados_por_genero_sin_datos_devuelve_lista_vacia(self):
        from tenants.analitica import ejercicios_mas_asignados_por_genero

        gimnasio_nuevo = Gimnasio.objects.create(nombre="Nuevo3", slug="nuevo3")
        self.assertEqual(ejercicios_mas_asignados_por_genero(gimnasio_nuevo), [])

    def test_ejercicios_mas_asignados_por_genero_no_revienta_con_sexo_fuera_de_catalogo(self):
        from tenants.analitica import ejercicios_mas_asignados_por_genero

        # `sexo` es un CharField con choices, no un enum forzado por la DB:
        # un valor fuera de catálogo es posible (dato viejo, choice
        # eliminada) y no debe tirar un KeyError que voltee todo el
        # dashboard de staff -- .update() evita las validaciones de
        # Alumno.full_clean() para simular exactamente ese caso.
        Alumno.objects.filter(pk=self.otro_alumno_mismo_gym.pk).update(sexo="otro")

        resultado = ejercicios_mas_asignados_por_genero(self.gimnasio)
        sentadilla = next(f for f in resultado if f["ejercicio"] == "Sentadilla")
        self.assertEqual(sentadilla["generos"]["no_informado"], 1)  # Bruno cae acá
        self.assertEqual(sentadilla["generos"]["masculino"], 0)  # ya no es "masculino"
        self.assertEqual(sentadilla["total"], 3)


class SuplantacionServicioTests(TestCase):
    """Servicio de "entrar como este alumno" (`tenants/suplantacion.py`).

    Existe para que el staff pueda resolver "no puedo entrar" y ver la app
    como la ve su alumno, SIN que el sistema guarde ninguna contraseña
    legible. Los dos primeros tests cubren las trampas de
    `django.contrib.auth.login()`, que son la razón por la que este servicio
    no es tres líneas.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        crear_acceso(self.alumno, TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()

    def _request_de(self, usuario):
        request = RequestFactory().post("/")
        request.user = usuario
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        return request

    # --- Las dos trampas de login() ---

    def test_no_estampa_fecha_activacion(self):
        """El alumno NUNCA entró: que el staff lo suplante no puede marcar que
        sí. `login()` emite `user_logged_in`, y el receiver de
        `alumnos/signals.py` estamparía `fecha_activacion` corrompiendo la
        métrica de adopción."""
        self.assertIsNone(self.alumno.fecha_activacion)
        suplantacion.iniciar(self._request_de(self.staff), self.alumno)
        self.alumno.refresh_from_db()
        self.assertIsNone(self.alumno.fecha_activacion)

    def test_no_cambia_last_login_del_alumno(self):
        """`update_last_login` (conectado por django.contrib.auth) pisaría el
        'último ingreso' que muestra el panel de accesos."""
        usuario = self.alumno.perfil.usuario
        self.assertIsNone(usuario.last_login)
        suplantacion.iniciar(self._request_de(self.staff), self.alumno)
        usuario.refresh_from_db()
        self.assertIsNone(usuario.last_login)

    def test_no_cambia_last_login_ya_existente(self):
        usuario = self.alumno.perfil.usuario
        momento = timezone.now() - timedelta(days=3)
        User.objects.filter(pk=usuario.pk).update(last_login=momento)

        suplantacion.iniciar(self._request_de(self.staff), self.alumno)

        usuario.refresh_from_db()
        self.assertEqual(usuario.last_login, momento)

    def test_la_clave_de_sesion_sobrevive_al_flush_de_login(self):
        """`login()` hace session.flush() al cambiar de usuario: si la clave se
        escribiera antes, se borraría y no habría forma de volver."""
        request = self._request_de(self.staff)
        suplantacion.iniciar(request, self.alumno)

        self.assertIn(suplantacion.CLAVE_SESION, request.session)
        datos = request.session[suplantacion.CLAVE_SESION]
        self.assertEqual(datos["original_pk"], self.staff.pk)
        self.assertEqual(datos["alumno_nombre"], str(self.alumno))

    # --- Auditoría ---

    def test_registra_la_auditoria_con_el_gimnasio_correcto(self):
        suplantacion.iniciar(self._request_de(self.staff), self.alumno)

        registro = RegistroSuplantacion.objects.get()
        self.assertEqual(registro.gimnasio, self.gimnasio)
        self.assertEqual(registro.staff_usuario, self.staff)
        self.assertEqual(registro.alumno, self.alumno)
        self.assertIsNone(registro.finalizada_en)

    def test_aislamiento_el_registro_no_se_ve_desde_otro_gimnasio(self):
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        suplantacion.iniciar(self._request_de(self.staff), self.alumno)

        self.assertEqual(
            RegistroSuplantacion.objects.for_gimnasio(self.gimnasio).count(), 1
        )
        self.assertEqual(
            RegistroSuplantacion.objects.for_gimnasio(otro_gim).count(), 0
        )

    # --- Reglas duras ---

    def test_no_se_puede_suplantar_a_un_staff(self):
        otro_staff = User.objects.create_user("staff2", password="clave-larga-456")
        perfil = Perfil.objects.create(
            usuario=otro_staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        falso_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="X", apellido="Y", perfil=perfil
        )
        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(self._request_de(self.staff), falso_alumno)
        self.assertFalse(RegistroSuplantacion.objects.exists())

    def test_no_se_puede_suplantar_a_un_superusuario(self):
        usuario = self.alumno.perfil.usuario
        usuario.is_superuser = True
        usuario.save(update_fields=["is_superuser"])
        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(self._request_de(self.staff), self.alumno)

    def test_no_se_puede_suplantar_a_un_alumno_dado_de_baja(self):
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save(update_fields=["estado"])
        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(self._request_de(self.staff), self.alumno)

    def test_no_se_puede_suplantar_a_un_alumno_sin_acceso(self):
        sin_acceso = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(self._request_de(self.staff), sin_acceso)

    def test_no_es_anidable(self):
        """Encadenar suplantaciones haría imposible saber a qué cuenta volver."""
        request = self._request_de(self.staff)
        suplantacion.iniciar(request, self.alumno)

        otro = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        crear_acceso(otro, TIPO_EMAIL, "ana@ejemplo.com")
        otro.refresh_from_db()

        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(request, otro)

    # --- Volver ---

    def test_volver_restaura_al_staff_y_cierra_el_registro(self):
        request = self._request_de(self.staff)
        suplantacion.iniciar(request, self.alumno)
        request.user = self.alumno.perfil.usuario

        suplantacion.volver(request)

        self.assertNotIn(suplantacion.CLAVE_SESION, request.session)
        self.assertEqual(
            int(request.session["_auth_user_id"]), self.staff.pk
        )
        self.assertIsNotNone(RegistroSuplantacion.objects.get().finalizada_en)

    def test_volver_sin_suplantacion_activa_falla(self):
        with self.assertRaises(PermissionDenied):
            suplantacion.volver(self._request_de(self.staff))

    def test_volver_a_un_staff_que_perdio_el_rol_falla_y_limpia(self):
        """Fail-closed: que la sesión diga que sos staff no alcanza, se
        revalida contra la base."""
        request = self._request_de(self.staff)
        suplantacion.iniciar(request, self.alumno)
        request.user = self.alumno.perfil.usuario

        perfil_staff = Perfil.objects.get(usuario=self.staff)
        perfil_staff.rol = Perfil.Rol.ALUMNO
        perfil_staff.save(update_fields=["rol"])

        with self.assertRaises(PermissionDenied):
            suplantacion.volver(request)
        self.assertNotIn(suplantacion.CLAVE_SESION, request.session)

    def test_volver_a_un_staff_desactivado_falla(self):
        request = self._request_de(self.staff)
        suplantacion.iniciar(request, self.alumno)
        request.user = self.alumno.perfil.usuario

        User.objects.filter(pk=self.staff.pk).update(is_active=False)

        with self.assertRaises(PermissionDenied):
            suplantacion.volver(request)

    def test_esta_activa_refleja_el_estado(self):
        request = self._request_de(self.staff)
        self.assertFalse(suplantacion.esta_activa(request))
        suplantacion.iniciar(request, self.alumno)
        self.assertTrue(suplantacion.esta_activa(request))


class SuplantacionVistasTests(TestCase):
    """Rutas, banner y bloqueos de "entrar como este alumno".

    Las rutas van SIN namespace: `tenants/urls.py` no define `app_name`
    (agregárselo rompería `{% url 'home' %}` y `{% url 'login' %}` en todo el
    proyecto).
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        crear_acceso(self.alumno, TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        self.client.force_login(self.staff)

    def _url_suplantar(self, alumno=None):
        return reverse("suplantar", args=[(alumno or self.alumno).pk])

    def test_suplantar_y_volver(self):
        response = self.client.post(self._url_suplantar(), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Volver a mi cuenta")
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            self.alumno.perfil.usuario.pk,
        )

        response = self.client.post(reverse("suplantacion_volver"), follow=True)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)
        self.assertNotContains(response, "Volver a mi cuenta")

    def test_el_banner_nombra_al_alumno(self):
        response = self.client.post(self._url_suplantar(), follow=True)
        self.assertContains(response, str(self.alumno))

    def test_mientras_suplanta_no_entra_a_vistas_de_staff(self):
        self.client.post(self._url_suplantar())
        self.assertEqual(self.client.get(reverse("alumnos:listado")).status_code, 403)

    def test_aislamiento_alumno_de_otro_gimnasio_da_404(self):
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        ajeno = Alumno.objects.create(
            gimnasio=otro_gim, nombre="Ana", apellido="Gómez"
        )
        crear_acceso(ajeno, TIPO_EMAIL, "ana@ejemplo.com")
        ajeno.refresh_from_db()

        self.assertEqual(self.client.post(self._url_suplantar(ajeno)).status_code, 404)
        self.assertFalse(RegistroSuplantacion.objects.exists())

    def test_get_no_esta_permitido(self):
        self.assertEqual(self.client.get(self._url_suplantar()).status_code, 405)
        self.assertEqual(self.client.get(reverse("suplantacion_volver")).status_code, 405)

    def test_un_alumno_no_puede_suplantar(self):
        self.client.logout()
        self.client.force_login(self.alumno.perfil.usuario)
        self.assertEqual(self.client.post(self._url_suplantar()).status_code, 403)

    def test_sesion_fabricada_hacia_staff_de_otro_gimnasio_es_rechazada(self):
        """Alguien que manipula la sesión no debe poder saltar de tenant."""
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        staff_ajeno = User.objects.create_user("staff-b", password="clave-larga-789")
        Perfil.objects.create(
            usuario=staff_ajeno, gimnasio=otro_gim, rol=Perfil.Rol.STAFF
        )
        self.client.post(self._url_suplantar())

        sesion = self.client.session
        datos = sesion[suplantacion.CLAVE_SESION]
        datos["original_pk"] = staff_ajeno.pk
        sesion[suplantacion.CLAVE_SESION] = datos
        sesion.save()

        response = self.client.post(reverse("suplantacion_volver"))

        # 403 explícito, no "cualquier cosa menos el staff ajeno": sin esto el
        # test pasaría igual con un 500 o si la sesión quedara en el alumno.
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(
            int(self.client.session.get("_auth_user_id", 0)), staff_ajeno.pk
        )
        # Fail-closed: la sesión se descarta entera.
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn(suplantacion.CLAVE_SESION, self.client.session)

    def test_el_panel_de_accesos_ofrece_entrar_como(self):
        response = self.client.get(reverse("alumnos:accesos"))
        self.assertContains(response, self._url_suplantar())

    def test_el_panel_no_ofrece_entrar_como_a_un_alumno_dado_de_baja(self):
        self.client.post(reverse("alumnos:activar", args=[self.alumno.pk]))
        response = self.client.get(reverse("alumnos:accesos"))
        self.assertNotContains(response, self._url_suplantar())

    def test_no_se_puede_conectar_calendar_mientras_suplanta(self):
        """Si no, el staff vincularía SU cuenta de Google al calendario del
        alumno: fuga de privacidad real."""
        self.client.post(self._url_suplantar())
        self.assertEqual(
            self.client.get(reverse("calendario:conectar")).status_code, 403
        )

    def test_no_se_puede_desconectar_calendar_mientras_suplanta(self):
        self.client.post(self._url_suplantar())
        self.assertEqual(
            self.client.post(reverse("calendario:desconectar")).status_code, 403
        )


class SuplantacionExpiracionTests(TestCase):
    """`MAX_DURACION` era código muerto: `vencida()` no se llamaba desde
    ningún lado, pero CLAUDE.md e ISSUES.md afirmaban un límite de 2 h.

    Ahora lo aplica `tenants.middleware.ExpirarSuplantacionMiddleware`.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        crear_acceso(self.alumno, TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        self.client.force_login(self.staff)

    def _envejecer(self, horas):
        sesion = self.client.session
        datos = sesion[suplantacion.CLAVE_SESION]
        datos["inicio"] = (timezone.now() - timedelta(hours=horas)).isoformat()
        sesion[suplantacion.CLAVE_SESION] = datos
        sesion.save()

    def test_dentro_de_las_dos_horas_sigue_suplantando(self):
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))
        self._envejecer(1)

        self.client.get(reverse("home"))
        self.assertIn(suplantacion.CLAVE_SESION, self.client.session)

    def test_pasadas_las_dos_horas_vuelve_solo_a_la_cuenta_del_staff(self):
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))
        self._envejecer(3)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(suplantacion.CLAVE_SESION, self.client.session)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)
        self.assertIsNotNone(RegistroSuplantacion.objects.get().finalizada_en)

    def test_si_no_se_puede_volver_la_sesion_se_descarta(self):
        """Fail-closed: ante cualquier error, no dejar al staff dentro de la
        cuenta del alumno.

        Se corrompe la clave de sesión para que `volver()` levante algo que NO
        maneja (un KeyError, no un PermissionDenied) y así ejercitar el
        `except` del middleware. Borrar al staff no sirve como escenario: el
        `PROTECT` de `RegistroSuplantacion` lo impide, que es justo lo que se
        buscaba al diseñarlo.
        """
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))
        self._envejecer(3)

        sesion = self.client.session
        datos = sesion[suplantacion.CLAVE_SESION]
        del datos["original_pk"]
        sesion[suplantacion.CLAVE_SESION] = datos
        sesion.save()

        self.client.get(reverse("home"))

        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn(suplantacion.CLAVE_SESION, self.client.session)


class SuplantacionAccesoInactivoTests(TestCase):
    """`login()` NO valida `is_active`: sin este guard la suplantación
    "funcionaba" y el staff perdía su sesión en el request siguiente, sin
    poder siquiera usar "Volver a mi cuenta"."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        crear_acceso(self.alumno, TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        self.client.force_login(self.staff)

    def test_no_se_puede_suplantar_a_un_usuario_desactivado(self):
        # Se desactiva el User sin tocar `Alumno.estado`, que es el caso que
        # el guard de `estado` no cubre (p.ej. desde /admin/).
        User.objects.filter(pk=self.alumno.perfil.usuario.pk).update(is_active=False)

        response = self.client.post(reverse("suplantar", args=[self.alumno.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)
        self.assertFalse(RegistroSuplantacion.objects.exists())


class SuplantacionMiddlewareRobustezTests(TestCase):
    """Regresiones que la re-revisión encontró en el propio middleware."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        crear_acceso(self.alumno, TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        self.client.force_login(self.staff)

    def test_una_sesion_corrupta_no_deja_al_usuario_sin_salida(self):
        """`vencida()` parsea `datos["inicio"]`. Si esa clave falta o tiene una
        fecha inválida y la llamada quedara fuera del `try`, cada request
        —incluido el logout— sería un 500 y el usuario no tendría forma de
        salir salvo borrar cookies a mano."""
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))

        sesion = self.client.session
        sesion[suplantacion.CLAVE_SESION] = {"inicio": "no-es-una-fecha"}
        sesion.save()

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_si_falla_el_retorno_no_se_renderiza_el_portal_del_alumno(self):
        """Fail-closed de verdad: cuando `volver()` falla DESPUÉS de haber
        resuelto `request.user` como el alumno, seguir con la vista devolvería
        un 200 con su portal — fail-open por un request."""
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        staff_ajeno = User.objects.create_user("staff-b", password="clave-larga-789")
        Perfil.objects.create(
            usuario=staff_ajeno, gimnasio=otro_gim, rol=Perfil.Rol.STAFF
        )
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))

        sesion = self.client.session
        datos = sesion[suplantacion.CLAVE_SESION]
        datos["original_pk"] = staff_ajeno.pk
        datos["inicio"] = (timezone.now() - timedelta(hours=3)).isoformat()
        sesion[suplantacion.CLAVE_SESION] = datos
        sesion.save()

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.assertNotIn("_auth_user_id", self.client.session)


class StaffPasswordChangeViewTests(TestCase):
    """Task 6: el staff pueda cambiar su propia contraseña, estando ya
    logueado -- distinto de "olvidé mi contraseña" (StaffPasswordResetConfirmView)
    y de la regeneración staff->alumno (`alumnos/views.py`). Debe seguir
    siendo inaccesible para un alumno: `StaffRequiredMixin` es lo que lo
    garantiza (403), no solo la ausencia del link en la nav."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio Central", slug="central")
        self.staff = User.objects.create_user("dueno-central", password="clave-vieja-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno_user = User.objects.create_user(
            "alumno-central", password="clave-alumno-123"
        )
        Perfil.objects.create(
            usuario=self.alumno_user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )

    def test_anonimo_redirige_a_login(self):
        response = self.client.get(reverse("password_change"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_alumno_recibe_403(self):
        self.client.login(username="alumno-central", password="clave-alumno-123")

        response = self.client.get(reverse("password_change"))

        self.assertEqual(response.status_code, 403)

    def test_staff_ve_el_formulario(self):
        self.client.login(username="dueno-central", password="clave-vieja-123")

        response = self.client.get(reverse("password_change"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cambiar contraseña")

    def test_link_solo_visible_para_staff(self):
        # El link vive en "Mi gimnasio" (`tenants:gimnasio_editar`), no en
        # el topbar global (`base.html`) -- se sacó de ahí para no sumar un
        # ítem más al topbar en mobile (ver ISSUES.md). La página ya es
        # staff-only por su propio `StaffRequiredMixin` (403 para un
        # alumno), así que la aserción real es "el staff lo ve al entrar
        # a esa pantalla".
        self.client.login(username="dueno-central", password="clave-vieja-123")
        response = self.client.get(reverse("gimnasio_editar"))
        self.assertContains(response, reverse("password_change"))

        self.client.logout()
        self.client.login(username="alumno-central", password="clave-alumno-123")
        response = self.client.get(reverse("gimnasio_editar"))
        self.assertEqual(response.status_code, 403)

    def test_staff_cambia_su_contraseña_con_exito_y_sigue_logueado(self):
        self.client.login(username="dueno-central", password="clave-vieja-123")

        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "clave-vieja-123",
                "new_password1": "clave-nueva-456!",
                "new_password2": "clave-nueva-456!",
            },
        )

        self.assertRedirects(response, reverse("password_change_done"))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password("clave-nueva-456!"))

        # `update_session_auth_hash` mantuvo la sesión activa: un request
        # posterior sigue autenticado como el mismo usuario, sin redirigir a
        # login.
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)

    def test_contraseña_actual_incorrecta_se_rechaza(self):
        self.client.login(username="dueno-central", password="clave-vieja-123")

        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "esta-no-es-la-actual",
                "new_password1": "clave-nueva-456!",
                "new_password2": "clave-nueva-456!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("old_password"))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password("clave-vieja-123"))
