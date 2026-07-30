"""
Tests de Fase 0: registro, login y aislamiento básico de datos entre
gimnasios. Los tests de TenantScopedMixin/TenantScopedModelForm contra un
modelo de dominio real (Alumno, etc.) se agregan en Fase 1, siguiendo el
patrón de ~/gestor-pedidos/core/tests.py — en Fase 0 todavía no existe ningún
TenantOwnedModel concreto para ejercitarlos.
"""

from datetime import date, timedelta
from io import StringIO

from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from alumnos.identidad import TIPO_EMAIL
from alumnos.models import Alumno
from alumnos.services import crear_acceso
from novedades.models import Novedad, NovedadLeida
from pagos.models import MedioCobro, PagoMensual
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants import suplantacion
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
