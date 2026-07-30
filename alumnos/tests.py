"""
Tests de `Alumno`: creación básica, valores por defecto y aislamiento de
tenant (mismo criterio que `tenants/tests.py::TenantIsolationTests`).

La segunda mitad del archivo (Fase 2) cubre las vistas de gestión: acceso
por rol (anónimo -> login, alumno -> 403), scoping de tenant en las vistas
(alumno de otro gimnasio -> 404, no 403 ni leak), el toggle activar/inactivar
(POST-only) y que la ficha muestre pagos/rutina propios sin filtrar de otro
alumno.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from alumnos import identidad
from alumnos import services as servicios
from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil

User = get_user_model()


class AlumnoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_basica_y_str(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        self.assertEqual(str(alumno), "Pérez, Juan")

    def test_estado_por_defecto_activo_y_fecha_activacion_none(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.assertEqual(alumno.estado, Alumno.Estado.ACTIVO)
        self.assertIsNone(alumno.fecha_activacion)

    def test_ficha_ampliada_por_defecto_vacia(self):
        """Un alumno nuevo (o uno ya existente antes de esta feature) no
        tiene ningún dato de la ficha ampliada cargado todavía."""
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.assertEqual(alumno.sexo, "")
        self.assertFalse(alumno.actividad_fisica_previa)
        self.assertEqual(alumno.frecuencia_actividad_previa, "")
        self.assertEqual(alumno.deportes_practica, "")
        self.assertFalse(alumno.tiene_discapacidad)
        self.assertEqual(alumno.discapacidad_detalle, "")
        self.assertFalse(alumno.tiene_enfermedad_cronica)
        self.assertEqual(alumno.enfermedad_cronica_detalle, "")


class FechaActivacionSignalTests(TestCase):
    """Fase 3: `fecha_activacion` se registra en el primer login exitoso del
    alumno, vía la señal `user_logged_in` (`alumnos/signals.py`)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.user = User.objects.create_user("ana", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = self.perfil
        self.alumno.save()

    def test_primer_login_registra_fecha_activacion(self):
        self.assertIsNone(self.alumno.fecha_activacion)
        self.client.login(username="ana", password="clave-123456")
        self.alumno.refresh_from_db()
        self.assertIsNotNone(self.alumno.fecha_activacion)

    def test_segundo_login_no_pisa_la_fecha_original(self):
        self.client.login(username="ana", password="clave-123456")
        self.alumno.refresh_from_db()
        primera_fecha = self.alumno.fecha_activacion

        self.client.logout()
        self.client.login(username="ana", password="clave-123456")
        self.alumno.refresh_from_db()

        self.assertEqual(self.alumno.fecha_activacion, primera_fecha)

    def test_login_de_staff_no_toca_fecha_activacion_de_ningun_alumno(self):
        staff_user = User.objects.create_user("dueno", password="clave-123456")
        Perfil.objects.create(
            usuario=staff_user, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="dueno", password="clave-123456")
        self.alumno.refresh_from_db()
        self.assertIsNone(self.alumno.fecha_activacion)

    def test_alumno_sin_perfil_vinculado_no_rompe_el_login(self):
        huerfano = User.objects.create_user("huerfano", password="clave-123456")
        Perfil.objects.create(
            usuario=huerfano, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        # No hay Alumno.perfil apuntando a este Perfil -- no debe explotar.
        self.client.login(username="huerfano", password="clave-123456")


class TenantIsolationTests(TestCase):
    """Confirma que dos gimnasios no comparten alumnos."""

    def test_for_gimnasio_devuelve_solo_los_alumnos_de_ese_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        alumno_a = Alumno.objects.create(
            gimnasio=gimnasio_a, nombre="Alumno", apellido="A"
        )
        alumno_b = Alumno.objects.create(
            gimnasio=gimnasio_b, nombre="Alumno", apellido="B"
        )

        resultado = Alumno.objects.for_gimnasio(gimnasio_a)

        self.assertIn(alumno_a, resultado)
        self.assertNotIn(alumno_b, resultado)


class AlumnoViewsTests(TestCase):
    """Vistas de gestión de alumnos (Fase 2): acceso por rol, aislamiento
    de tenant, el toggle activar/inactivar y el contenido de la ficha."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

        self.staff_a = User.objects.create_user(username="staff_a", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )

        self.staff_b = User.objects.create_user(username="staff_b", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff_b, gimnasio=self.gimnasio_b, rol=Perfil.Rol.STAFF
        )

        self.usuario_alumno = User.objects.create_user(
            username="usuario_alumno", password="clave12345"
        )
        Perfil.objects.create(
            usuario=self.usuario_alumno, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )

        self.alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Juan", apellido="Pérez"
        )
        self.alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Ana", apellido="García"
        )

    def _urls_get(self):
        return [
            reverse("alumnos:listado"),
            reverse("alumnos:crear"),
            reverse("alumnos:detalle", args=[self.alumno_a.pk]),
            reverse("alumnos:editar", args=[self.alumno_a.pk]),
        ]

    # 1. Anónimo -> redirect a login en toda vista, incluido el toggle.
    def test_anonimo_redirige_a_login_en_todas_las_vistas(self):
        for url in self._urls_get():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response.url)

        response = self.client.get(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    # 2. Perfil con rol ALUMNO -> 403 en toda vista.
    def test_perfil_alumno_recibe_403_en_todas_las_vistas(self):
        self.client.login(username="usuario_alumno", password="clave12345")
        for url in self._urls_get():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)

        response = self.client.post(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.assertEqual(response.status_code, 403)

    # 3. Staff puede listar/crear/editar alumnos de su propio gimnasio.
    def test_staff_puede_listar_crear_y_editar_alumnos_de_su_gimnasio(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(reverse("alumnos:listado"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pérez")

        datos = {
            "nombre": "Nuevo",
            "apellido": "Alumno",
            "email": "",
            "telefono": "",
            "fecha_nacimiento": "",
            "estado": Alumno.Estado.ACTIVO,
            "observaciones": "",
        }
        response = self.client.post(reverse("alumnos:crear"), datos)
        self.assertEqual(response.status_code, 302)
        nuevo = Alumno.objects.get(apellido="Alumno", gimnasio=self.gimnasio_a)

        datos["apellido"] = "Alumno Editado"
        response = self.client.post(reverse("alumnos:editar", args=[nuevo.pk]), datos)
        self.assertEqual(response.status_code, 302)
        nuevo.refresh_from_db()
        self.assertEqual(nuevo.apellido, "Alumno Editado")

    def test_staff_carga_la_ficha_de_inscripcion_ampliada(self):
        self.client.login(username="staff_a", password="clave12345")
        datos = {
            "nombre": "Nuevo",
            "apellido": "Alumno",
            "email": "",
            "telefono": "",
            "fecha_nacimiento": "",
            "estado": Alumno.Estado.ACTIVO,
            "sexo": Alumno.Sexo.FEMENINO,
            "actividad_fisica_previa": "on",
            "frecuencia_actividad_previa": Alumno.FrecuenciaActividad.VARIAS_POR_SEMANA,
            "deportes_practica": "Running",
            "tiene_discapacidad": "",
            "discapacidad_detalle": "",
            "tiene_enfermedad_cronica": "on",
            "enfermedad_cronica_detalle": "Asma",
            "observaciones": "",
        }
        response = self.client.post(reverse("alumnos:crear"), datos)
        self.assertEqual(response.status_code, 302)
        nuevo = Alumno.objects.get(apellido="Alumno", gimnasio=self.gimnasio_a)
        self.assertEqual(nuevo.sexo, Alumno.Sexo.FEMENINO)
        self.assertTrue(nuevo.actividad_fisica_previa)
        self.assertEqual(
            nuevo.frecuencia_actividad_previa,
            Alumno.FrecuenciaActividad.VARIAS_POR_SEMANA,
        )
        self.assertEqual(nuevo.deportes_practica, "Running")
        self.assertFalse(nuevo.tiene_discapacidad)
        self.assertTrue(nuevo.tiene_enfermedad_cronica)
        self.assertEqual(nuevo.enfermedad_cronica_detalle, "Asma")

    def test_sexo_fuera_de_catalogo_es_rechazado(self):
        self.client.login(username="staff_a", password="clave12345")
        datos = {
            "nombre": "Nuevo",
            "apellido": "Alumno",
            "email": "",
            "telefono": "",
            "fecha_nacimiento": "",
            "estado": Alumno.Estado.ACTIVO,
            "sexo": "no-es-una-opcion",
            "observaciones": "",
        }
        response = self.client.post(reverse("alumnos:crear"), datos)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Alumno.objects.filter(apellido="Alumno", gimnasio=self.gimnasio_a).exists()
        )

    # 4. Aislamiento de tenant: alumno de otro gimnasio -> 404, no 403 ni leak.
    def test_aislamiento_de_tenant_devuelve_404_no_403(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno_b.pk]))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse("alumnos:editar", args=[self.alumno_b.pk]))
        self.assertEqual(response.status_code, 404)

        response = self.client.post(reverse("alumnos:activar", args=[self.alumno_b.pk]))
        self.assertEqual(response.status_code, 404)

    # 5. El toggle flipea estado y rechaza GET.
    def test_activar_inactivar_flipea_estado_y_rechaza_get(self):
        self.client.login(username="staff_a", password="clave12345")
        self.assertEqual(self.alumno_a.estado, Alumno.Estado.ACTIVO)

        response = self.client.get(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.assertEqual(response.status_code, 405)

        response = self.client.post(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.assertEqual(response.status_code, 302)
        self.alumno_a.refresh_from_db()
        self.assertEqual(self.alumno_a.estado, Alumno.Estado.INACTIVO)

        self.client.post(reverse("alumnos:activar", args=[self.alumno_a.pk]))
        self.alumno_a.refresh_from_db()
        self.assertEqual(self.alumno_a.estado, Alumno.Estado.ACTIVO)

    # 6. La ficha muestra los pagos y la rutina propios, no los de otro alumno.
    def test_ficha_muestra_pagos_y_rutina_propios_sin_filtrar_de_otro_alumno(self):
        from pagos.models import PagoMensual
        from rutinas.models import RutinaAsignada

        pago_propio = PagoMensual.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=1,
            anio=2026,
            monto=1000,
            estado=PagoMensual.Estado.PAGADO,
        )
        otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Otro", apellido="Alumno"
        )
        pago_ajeno = PagoMensual.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=otro_alumno,
            mes=2,
            anio=2026,
            monto=2000,
            estado=PagoMensual.Estado.PENDIENTE,
        )
        rutina_propia = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            nombre_snapshot="Full body",
            objetivo_snapshot="Hipertrofia",
            fecha_inicio=datetime.date(2026, 1, 1),
            activa=True,
        )

        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno_a.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn(pago_propio, response.context["pagos"])
        self.assertNotIn(pago_ajeno, response.context["pagos"])
        self.assertEqual(response.context["rutina_actual"], rutina_propia)
        self.assertContains(response, "Full body")

    def test_ficha_sin_rutina_asignada_muestra_mensaje_plano(self):
        self.client.login(username="staff_b", password="clave12345")
        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno_b.pk]))
        self.assertContains(response, "Sin rutina asignada todavía")


class AccesoAlumnoViewsTests(TestCase):
    """Fase 3: `CrearAccesoView`/`CambiarPasswordAlumnoView` — el staff
    asigna usuario/contraseña a mano (ver ISSUES.md 2026-07-01, ya no hay
    magic-link)."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

        self.staff_a = User.objects.create_user(username="staff_a", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )

        self.staff_b = User.objects.create_user(username="staff_b", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff_b, gimnasio=self.gimnasio_b, rol=Perfil.Rol.STAFF
        )

        self.usuario_alumno = User.objects.create_user(
            username="usuario_alumno", password="clave12345"
        )
        Perfil.objects.create(
            usuario=self.usuario_alumno, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )

        self.alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Juan", apellido="Pérez"
        )
        self.alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Ana", apellido="García"
        )

    def _url_crear(self, alumno):
        return reverse("alumnos:acceso_crear", args=[alumno.pk])

    def _url_cambiar(self, alumno):
        return reverse("alumnos:acceso_cambiar_password", args=[alumno.pk])

    # 1. Anónimo -> login; rol ALUMNO -> 403.
    def test_anonimo_redirige_a_login_y_alumno_recibe_403(self):
        for url in (self._url_crear(self.alumno_a), self._url_cambiar(self.alumno_a)):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response.url)

        self.client.login(username="usuario_alumno", password="clave12345")
        for url in (self._url_crear(self.alumno_a), self._url_cambiar(self.alumno_a)):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)

    # 2. Staff crea acceso: User+Perfil creados, alumno.perfil linkeado,
    #    rol ALUMNO, y el alumno puede loguearse con la contraseña enviada.
    def test_staff_crea_acceso_para_su_alumno(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            self._url_crear(self.alumno_a),
            {"username": "juanperez", "password": "clave-super-segura-99"},
        )
        self.assertEqual(response.status_code, 302)

        self.alumno_a.refresh_from_db()
        self.assertIsNotNone(self.alumno_a.perfil)
        self.assertEqual(self.alumno_a.perfil.rol, Perfil.Rol.ALUMNO)
        self.assertEqual(self.alumno_a.perfil.gimnasio, self.gimnasio_a)
        self.assertEqual(self.alumno_a.perfil.usuario.username, "juanperez")

        self.client.logout()
        puede_entrar = self.client.login(
            username="juanperez", password="clave-super-segura-99"
        )
        self.assertTrue(puede_entrar)

    # 3. Ya tiene acceso -> redirect con error, no crea un segundo User/Perfil.
    def test_crear_acceso_si_ya_tiene_uno_no_duplica(self):
        self.client.login(username="staff_a", password="clave12345")
        self.client.post(
            self._url_crear(self.alumno_a),
            {"username": "juanperez", "password": "clave-super-segura-99"},
        )
        self.alumno_a.refresh_from_db()
        perfil_original = self.alumno_a.perfil

        usuarios_antes = User.objects.count()
        perfiles_antes = Perfil.objects.count()

        response = self.client.post(
            self._url_crear(self.alumno_a),
            {"username": "otro-usuario", "password": "otra-clave-segura-99"},
        )
        self.assertEqual(response.status_code, 302)

        self.alumno_a.refresh_from_db()
        self.assertEqual(self.alumno_a.perfil, perfil_original)
        self.assertEqual(User.objects.count(), usuarios_antes)
        self.assertEqual(Perfil.objects.count(), perfiles_antes)

    # 4. Aislamiento de tenant: staff A no puede tocar alumno de B (404).
    def test_aislamiento_de_tenant_devuelve_404(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(self._url_crear(self.alumno_b))
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            self._url_crear(self.alumno_b),
            {"username": "intruso", "password": "clave-super-segura-99"},
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.get(self._url_cambiar(self.alumno_b))
        self.assertEqual(response.status_code, 404)

    # 5. Colisión de username (global, no por gimnasio) -> form invalido, sin User nuevo.
    def test_username_duplicado_es_rechazado_por_el_form(self):
        from alumnos.forms import CrearAccesoForm

        User.objects.create_user(username="repetido", password="clave12345")
        usuarios_antes = User.objects.count()

        form = CrearAccesoForm(
            data={"username": "repetido", "password": "clave-super-segura-99"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)
        self.assertEqual(User.objects.count(), usuarios_antes)

    # 6. Contraseña débil rechazada por los validadores de Django.
    def test_password_debil_es_rechazada(self):
        from alumnos.forms import CambiarPasswordAlumnoForm, CrearAccesoForm

        for password in ("1234", "password"):
            form = CrearAccesoForm(data={"username": "nuevo-user", "password": password})
            self.assertFalse(form.is_valid())
            self.assertIn("password", form.errors)

            form2 = CambiarPasswordAlumnoForm(data={"password": password})
            self.assertFalse(form2.is_valid())
            self.assertIn("password", form2.errors)

    # 7. CambiarPasswordAlumnoView cambia la contraseña; solo alcanzable con perfil ya creado.
    def test_cambiar_password_actualiza_credenciales_y_requiere_perfil_existente(self):
        self.client.login(username="staff_a", password="clave12345")

        # Sin perfil todavía: redirige, no rompe.
        response = self.client.get(self._url_cambiar(self.alumno_a))
        self.assertEqual(response.status_code, 302)

        self.client.post(
            self._url_crear(self.alumno_a),
            {"username": "juanperez", "password": "clave-original-99"},
        )

        response = self.client.post(
            self._url_cambiar(self.alumno_a),
            {"password": "clave-nueva-100"},
        )
        self.assertEqual(response.status_code, 302)

        self.client.logout()
        self.assertFalse(
            self.client.login(username="juanperez", password="clave-original-99")
        )
        self.assertTrue(
            self.client.login(username="juanperez", password="clave-nueva-100")
        )


class IdentidadTests(SimpleTestCase):
    """Normalización del dato con el que entra un alumno.

    La tabla de casos es exhaustiva a propósito: si la normalización difiere
    entre el alta y el login, el alumno no entra nunca y no tiene forma de
    darse cuenta solo. `SimpleTestCase` porque `alumnos/identidad.py` no toca
    la base (mismo criterio que `importaciones/parsing.py`).
    """

    def test_email_se_normaliza(self):
        for entrada, esperado in [
            ("Juan@Ejemplo.com", "juan@ejemplo.com"),
            ("  juan@ejemplo.com  ", "juan@ejemplo.com"),
            ("JUAN.PEREZ@EJEMPLO.COM.AR", "juan.perez@ejemplo.com.ar"),
        ]:
            with self.subTest(entrada=entrada):
                self.assertEqual(identidad.normalizar_email(entrada), esperado)

    def test_email_invalido_levanta(self):
        for entrada in ["", "no-es-un-email", "juan@", "@ejemplo.com", "a b@c.com"]:
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValidationError):
                    identidad.normalizar_email(entrada)

    def test_telefono_argentino_se_normaliza(self):
        for entrada, esperado in [
            ("1122334455", "+541122334455"),
            ("11 2233 4455", "+541122334455"),
            ("11-2233-4455", "+541122334455"),
            ("(011) 2233-4455", "+541122334455"),
            ("011 15 2233 4455", "+541122334455"),
            ("+54 11 2233 4455", "+541122334455"),
            ("+5491122334455", "+5491122334455"),
            ("0351 15 555 6677", "+543515556677"),
        ]:
            with self.subTest(entrada=entrada):
                self.assertEqual(identidad.normalizar_telefono(entrada), esperado)

    def test_telefono_invalido_levanta(self):
        for entrada in ["", "123", "no-es-un-telefono", "+"]:
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValidationError):
                    identidad.normalizar_telefono(entrada)

    def test_normalizar_identificador_despacha_por_tipo(self):
        self.assertEqual(
            identidad.normalizar_identificador(identidad.TIPO_EMAIL, "A@B.com"),
            "a@b.com",
        )
        self.assertEqual(
            identidad.normalizar_identificador(identidad.TIPO_TELEFONO, "1122334455"),
            "+541122334455",
        )

    def test_tipo_desconocido_levanta(self):
        with self.assertRaises(ValidationError):
            identidad.normalizar_identificador("carta-documento", "lo que sea")

    def test_el_identificador_entra_en_username(self):
        """`UnicodeUsernameValidator` acepta `@` y `+` (regex `^[\\w.@+-]+\\Z`).

        Este test es el que justifica NO haber hecho un `User` custom: si algún
        día dejara de ser cierto, hay que enterarse acá y no en producción.
        """
        validador = UnicodeUsernameValidator()
        validador("juan@ejemplo.com")
        validador("+541122334455")


class ServiciosAccesoTests(TestCase):
    """Alta y regeneración del acceso de un alumno (`alumnos/services.py`).

    La contraseña NUNCA la elige el staff: la genera la app. Un dueño de
    gimnasio no va a inventar cincuenta contraseñas razonables, y las que
    inventaría serían peores que las generadas.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )

    def test_crear_acceso_devuelve_la_password_y_deja_entrar(self):
        password = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "Juan@Ejemplo.com"
        )
        self.alumno.refresh_from_db()

        self.assertIsNotNone(self.alumno.perfil)
        self.assertEqual(self.alumno.perfil.rol, Perfil.Rol.ALUMNO)
        self.assertEqual(self.alumno.perfil.gimnasio, self.gimnasio)
        self.assertEqual(self.alumno.perfil.usuario.username, "juan@ejemplo.com")
        self.assertTrue(
            self.client.login(username="juan@ejemplo.com", password=password)
        )

    def test_crear_acceso_con_telefono_normaliza_el_username(self):
        servicios.crear_acceso(
            self.alumno, identidad.TIPO_TELEFONO, "011 15 2233 4455"
        )
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.perfil.usuario.username, "+541122334455")

    def test_crear_acceso_guarda_el_email_en_el_user(self):
        """Lo necesita el password reset del Frente C:
        `PasswordResetForm.get_users()` busca por `User.email`."""
        servicios.crear_acceso(self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.perfil.usuario.email, "juan@ejemplo.com")

    def test_crear_acceso_con_telefono_deja_el_email_vacio(self):
        """Un teléfono no es un email: poblarlo rompería el password reset,
        que busca por `User.email`."""
        servicios.crear_acceso(self.alumno, identidad.TIPO_TELEFONO, "1122334455")
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.perfil.usuario.email, "")

    def test_identificador_repetido_no_crea_nada(self):
        otro = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        servicios.crear_acceso(self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")

        with self.assertRaises(servicios.IdentificadorEnUso):
            servicios.crear_acceso(otro, identidad.TIPO_EMAIL, "juan@ejemplo.com")

        otro.refresh_from_db()
        self.assertIsNone(otro.perfil)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Perfil.objects.count(), 1)

    def test_identificador_invalido_no_crea_nada(self):
        with self.assertRaises(ValidationError):
            servicios.crear_acceso(
                self.alumno, identidad.TIPO_EMAIL, "no-es-un-email"
            )
        self.alumno.refresh_from_db()
        self.assertIsNone(self.alumno.perfil)
        self.assertEqual(User.objects.count(), 0)

    def test_regenerar_password_cambia_la_vieja(self):
        vieja = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com"
        )
        self.alumno.refresh_from_db()
        nueva = servicios.regenerar_password(self.alumno)

        self.assertNotEqual(vieja, nueva)
        self.assertFalse(
            self.client.login(username="juan@ejemplo.com", password=vieja)
        )
        self.assertTrue(
            self.client.login(username="juan@ejemplo.com", password=nueva)
        )

    def test_regenerar_password_expulsa_la_sesion_viva(self):
        """Sale gratis: `auth.get_user()` compara `HASH_SESSION_KEY` contra
        `get_session_auth_hash()`, que deriva del hash de la contraseña."""
        password = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com"
        )
        self.alumno.refresh_from_db()
        self.client.login(username="juan@ejemplo.com", password=password)
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

        servicios.regenerar_password(self.alumno)

        self.assertEqual(self.client.get(reverse("home")).status_code, 302)

    def test_la_password_generada_pasa_los_validadores(self):
        password = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com"
        )
        validate_password(password)  # no debe levantar
