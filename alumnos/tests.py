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
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
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
    """Vistas de acceso del alumno.

    El staff ya NO inventa usuario ni contraseña: elige si el alumno entra con
    su email o su teléfono, y la app genera la contraseña y la muestra una sola
    vez (ver el spec del portal de cuentas). Antes se pedían los dos campos a
    mano y la contraseña viajaba por `messages`.
    """

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

    def _url_regenerar(self, alumno):
        return reverse("alumnos:acceso_regenerar", args=[alumno.pk])

    def _datos(self, identificador="juan@ejemplo.com", tipo=identidad.TIPO_EMAIL):
        return {"tipo": tipo, "identificador": identificador}

    # 1. Anónimo -> login; rol ALUMNO -> 403.
    def test_anonimo_redirige_a_login_y_alumno_recibe_403(self):
        response = self.client.get(self._url_crear(self.alumno_a))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

        response = self.client.post(self._url_regenerar(self.alumno_a))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

        self.client.login(username="usuario_alumno", password="clave12345")
        self.assertEqual(self.client.get(self._url_crear(self.alumno_a)).status_code, 403)
        self.assertEqual(
            self.client.post(self._url_regenerar(self.alumno_a)).status_code, 403
        )

    # 2. Staff crea acceso: User+Perfil creados y el alumno puede entrar.
    def test_staff_crea_acceso_para_su_alumno(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(self._url_crear(self.alumno_a), self._datos())

        # No redirige: renderiza la credencial en un 200 (ver test siguiente).
        self.assertEqual(response.status_code, 200)
        password = response.context["password"]

        self.alumno_a.refresh_from_db()
        self.assertIsNotNone(self.alumno_a.perfil)
        self.assertEqual(self.alumno_a.perfil.rol, Perfil.Rol.ALUMNO)
        self.assertEqual(self.alumno_a.perfil.gimnasio, self.gimnasio_a)
        self.assertEqual(self.alumno_a.perfil.usuario.username, "juan@ejemplo.com")

        self.client.logout()
        self.assertTrue(
            self.client.login(username="juan@ejemplo.com", password=password)
        )

    # 3. La contraseña se muestra, pero NO pasa por `messages`.
    def test_la_password_no_viaja_por_messages(self):
        """`messages` se serializa en la sesión, que vive en la base de datos.
        Renderizar un 200 directo deja la contraseña solo en esa respuesta."""
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(self._url_crear(self.alumno_a), self._datos())

        self.assertContains(response, response.context["password"])
        self.assertContains(response, "juan@ejemplo.com")
        self.assertEqual(list(response.context["messages"]), [])

    # 4. Ya tiene acceso -> redirect con error, no crea un segundo User/Perfil.
    def test_crear_acceso_si_ya_tiene_uno_no_duplica(self):
        self.client.login(username="staff_a", password="clave12345")
        self.client.post(self._url_crear(self.alumno_a), self._datos())
        self.alumno_a.refresh_from_db()
        perfil_original = self.alumno_a.perfil

        usuarios_antes = User.objects.count()
        perfiles_antes = Perfil.objects.count()

        response = self.client.post(
            self._url_crear(self.alumno_a), self._datos("otro@ejemplo.com")
        )
        self.assertEqual(response.status_code, 302)

        self.alumno_a.refresh_from_db()
        self.assertEqual(self.alumno_a.perfil, perfil_original)
        self.assertEqual(User.objects.count(), usuarios_antes)
        self.assertEqual(Perfil.objects.count(), perfiles_antes)

    # 5. Aislamiento de tenant: staff A no puede tocar alumno de B (404).
    def test_aislamiento_de_tenant_devuelve_404(self):
        self.client.login(username="staff_a", password="clave12345")

        self.assertEqual(self.client.get(self._url_crear(self.alumno_b)).status_code, 404)
        response = self.client.post(self._url_crear(self.alumno_b), self._datos())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.post(self._url_regenerar(self.alumno_b)).status_code, 404
        )

        self.alumno_b.refresh_from_db()
        self.assertIsNone(self.alumno_b.perfil)

    # 6. Colisión de identificador -> error de form, no 500, sin crear nada.
    def test_identificador_duplicado_es_error_de_form(self):
        User.objects.create_user(username="juan@ejemplo.com", password="clave12345")
        usuarios_antes = User.objects.count()

        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(self._url_crear(self.alumno_a), self._datos())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())
        self.alumno_a.refresh_from_db()
        self.assertIsNone(self.alumno_a.perfil)
        self.assertEqual(User.objects.count(), usuarios_antes)

    # 7. Ese error no puede confirmar si el email existe en la plataforma.
    def test_el_error_de_colision_no_revela_si_el_email_existe(self):
        """Con emails reales como usuario, un mensaje específico convertiría
        este form en un enumerador de usuarios de toda la plataforma."""
        User.objects.create_user(username="juan@ejemplo.com", password="clave12345")
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(self._url_crear(self.alumno_a), self._datos())

        texto = response.content.decode()
        self.assertIn("No se puede usar ese dato", texto)
        self.assertNotIn("ya está en uso", texto)
        self.assertNotIn("en toda la plataforma", texto)

    # 8. Identificador mal escrito -> error de campo, no 500.
    def test_identificador_invalido_es_error_de_form(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            self._url_crear(self.alumno_a), self._datos("no-es-un-email")
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("identificador", response.context["form"].errors)

    # 9. El form precarga el dato de contacto que ya tiene la ficha.
    def test_el_form_precarga_el_email_del_alumno(self):
        self.alumno_a.email = "juan@ejemplo.com"
        self.alumno_a.save(update_fields=["email"])
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.get(self._url_crear(self.alumno_a))
        self.assertEqual(
            response.context["form"].initial["identificador"], "juan@ejemplo.com"
        )

    # 10. Regenerar contraseña: cambia la credencial y muestra la nueva.
    def test_regenerar_password_muestra_la_nueva_y_deja_entrar(self):
        self.client.login(username="staff_a", password="clave12345")
        creacion = self.client.post(self._url_crear(self.alumno_a), self._datos())
        vieja = creacion.context["password"]

        response = self.client.post(self._url_regenerar(self.alumno_a))
        self.assertEqual(response.status_code, 200)
        nueva = response.context["password"]
        self.assertNotEqual(vieja, nueva)
        self.assertContains(response, nueva)

        self.client.logout()
        self.assertFalse(
            self.client.login(username="juan@ejemplo.com", password=vieja)
        )
        self.assertTrue(
            self.client.login(username="juan@ejemplo.com", password=nueva)
        )

    # 11. Regenerar sobre un alumno sin acceso: redirige, no rompe.
    def test_regenerar_sin_acceso_previo_redirige(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(self._url_regenerar(self.alumno_a))
        self.assertEqual(response.status_code, 302)

    # 12. Regenerar es POST-only: un GET no puede mutar credenciales.
    def test_regenerar_no_acepta_get(self):
        self.client.login(username="staff_a", password="clave12345")
        self.client.post(self._url_crear(self.alumno_a), self._datos())
        response = self.client.get(self._url_regenerar(self.alumno_a))
        self.assertEqual(response.status_code, 405)


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


class RevocacionAccesoTests(TestCase):
    """Dar de baja a un alumno tiene que apagarle el login.

    Antes `AlumnoToggleEstadoView` cambiaba `Alumno.estado` y nunca tocaba
    `User.is_active`: un alumno dado de baja seguía entrando al portal como si
    nada. El acceso es un ESPEJO del estado del alumno, no un interruptor
    aparte (decisión del dueño del producto).
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
        self.password = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com"
        )
        self.alumno.refresh_from_db()

    def _toggle(self, alumno=None, cliente=None):
        cliente = cliente or self.client
        alumno = alumno or self.alumno
        return cliente.post(reverse("alumnos:activar", args=[alumno.pk]))

    def test_dar_de_baja_impide_entrar(self):
        self.client.force_login(self.staff)
        self._toggle()

        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.estado, Alumno.Estado.INACTIVO)
        self.assertFalse(self.alumno.perfil.usuario.is_active)

        self.client.logout()
        self.assertFalse(
            self.client.login(username="juan@ejemplo.com", password=self.password)
        )

    def test_reactivar_devuelve_el_acceso(self):
        self.client.force_login(self.staff)
        self._toggle()
        self._toggle()

        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.estado, Alumno.Estado.ACTIVO)
        self.assertTrue(self.alumno.perfil.usuario.is_active)

        self.client.logout()
        self.assertTrue(
            self.client.login(username="juan@ejemplo.com", password=self.password)
        )

    def test_dar_de_baja_corta_la_sesion_ya_abierta(self):
        """No hace falta invalidar sesiones a mano: `ModelBackend.get_user()`
        revalida `is_active` en CADA request, así que la sesión viva muere en
        el request siguiente."""
        cliente_alumno = Client()
        cliente_alumno.login(username="juan@ejemplo.com", password=self.password)
        self.assertEqual(cliente_alumno.get(reverse("home")).status_code, 200)

        cliente_staff = Client()
        cliente_staff.force_login(self.staff)
        self._toggle(cliente=cliente_staff)

        self.assertEqual(cliente_alumno.get(reverse("home")).status_code, 302)

    def test_alumno_sin_acceso_no_rompe(self):
        sin_acceso = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.client.force_login(self.staff)
        response = self._toggle(alumno=sin_acceso)
        self.assertEqual(response.status_code, 302)
        sin_acceso.refresh_from_db()
        self.assertEqual(sin_acceso.estado, Alumno.Estado.INACTIVO)

    def test_no_toca_el_acceso_de_otros_alumnos(self):
        otro = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        servicios.crear_acceso(otro, identidad.TIPO_EMAIL, "ana@ejemplo.com")
        otro.refresh_from_db()

        self.client.force_login(self.staff)
        self._toggle()

        otro.refresh_from_db()
        self.assertTrue(otro.perfil.usuario.is_active)

    def test_la_ficha_avisa_que_el_acceso_quedo_desactivado(self):
        """Si no, la ficha mostraría el usuario y el botón de regenerar
        contraseña para alguien que no puede entrar de ninguna forma."""
        self.client.force_login(self.staff)
        url = reverse("alumnos:detalle", args=[self.alumno.pk])

        self.assertNotContains(self.client.get(url), "Acceso desactivado")
        self._toggle()
        self.assertContains(self.client.get(url), "Acceso desactivado")

    def test_aislamiento_no_se_puede_togglear_alumno_de_otro_gimnasio(self):
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        ajeno = Alumno.objects.create(
            gimnasio=otro_gim, nombre="Ana", apellido="Gómez"
        )
        servicios.crear_acceso(ajeno, identidad.TIPO_EMAIL, "ana@ejemplo.com")
        ajeno.refresh_from_db()

        self.client.force_login(self.staff)
        response = self._toggle(alumno=ajeno)
        self.assertEqual(response.status_code, 404)

        ajeno.refresh_from_db()
        self.assertEqual(ajeno.estado, Alumno.Estado.ACTIVO)
        self.assertTrue(ajeno.perfil.usuario.is_active)


class PanelAccesosTests(TestCase):
    """Vista de conjunto de los accesos del gimnasio.

    Hasta ahora el staff solo veía el acceso de un alumno entrando a su ficha,
    de a uno.
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
        servicios.crear_acceso(self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        self.client.force_login(self.staff)

    def test_lista_el_usuario_exacto(self):
        """El username tal cual quedó guardado: es lo que el staff tiene que
        dictarle al alumno, y la mitigación de un error de normalización."""
        response = self.client.get(reverse("alumnos:accesos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "juan@ejemplo.com")

    def test_marca_los_alumnos_sin_acceso(self):
        Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        response = self.client.get(reverse("alumnos:accesos"))
        self.assertContains(response, "Sin acceso")

    def test_muestra_que_nunca_entro(self):
        response = self.client.get(reverse("alumnos:accesos"))
        self.assertContains(response, "Nunca entró")

    def test_refleja_el_acceso_dado_de_baja(self):
        self.client.post(reverse("alumnos:activar", args=[self.alumno.pk]))
        response = self.client.get(reverse("alumnos:accesos"))
        self.assertContains(response, "Dado de baja")

    def test_aislamiento_no_muestra_alumnos_de_otro_gimnasio(self):
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        ajeno = Alumno.objects.create(
            gimnasio=otro_gim, nombre="Ana", apellido="Gómez"
        )
        servicios.crear_acceso(ajeno, identidad.TIPO_EMAIL, "ana@ejemplo.com")

        response = self.client.get(reverse("alumnos:accesos"))
        self.assertNotContains(response, "ana@ejemplo.com")
        self.assertNotContains(response, "Gómez")

    def test_un_alumno_no_puede_ver_el_panel(self):
        self.client.logout()
        self.client.force_login(self.alumno.perfil.usuario)
        self.assertEqual(self.client.get(reverse("alumnos:accesos")).status_code, 403)

    def test_anonimo_redirige_a_login(self):
        self.client.logout()
        response = self.client.get(reverse("alumnos:accesos"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_no_hace_una_query_por_alumno(self):
        """Cada fila muestra el username y el último ingreso, que viven dos
        saltos más allá (`alumno.perfil.usuario`). Sin `select_related` esto
        crece con la cantidad de alumnos.

        Se comparan dos tamaños en vez de fijar un número exacto: un
        `assertNumQueries(7)` se rompe con cualquier cambio interno de Django
        sin que haya un N+1 de verdad.
        """
        url = reverse("alumnos:accesos")
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(url)

        for indice in range(5):
            otro = Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"Alumno{indice}", apellido="Test"
            )
            servicios.crear_acceso(
                otro, identidad.TIPO_EMAIL, f"alumno{indice}@ejemplo.com"
            )

        with CaptureQueriesContext(connection) as muchas:
            self.client.get(url)

        self.assertEqual(len(muchas), len(pocas))


class EspejoEstadoAccesoTests(TestCase):
    """El espejo `Alumno.estado` <-> `User.is_active` tiene que valer por
    CUALQUIER camino que escriba el estado, no solo por el botón de baja.

    Hallazgos de la revisión del Frente B: `crear_acceso()` dejaba
    `is_active=True` para un alumno ya dado de baja, y editar la ficha (donde
    `estado` es un campo del form) cambiaba el estado sin tocar el acceso —
    dejando un alumno que figura activo y no puede entrar, sin forma de
    diagnosticarlo.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.force_login(self.staff)

    def test_crear_acceso_a_un_alumno_dado_de_baja_no_lo_deja_entrar(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Baja",
            apellido="Test",
            estado=Alumno.Estado.INACTIVO,
        )
        password = servicios.crear_acceso(
            alumno, identidad.TIPO_EMAIL, "baja@ejemplo.com"
        )
        alumno.refresh_from_db()

        self.assertFalse(alumno.perfil.usuario.is_active)
        self.assertFalse(
            Client().login(username="baja@ejemplo.com", password=password)
        )

    def test_editar_la_ficha_sincroniza_el_acceso(self):
        """`estado` es un campo de `AlumnoForm`: cambiarlo desde la ficha tiene
        que mover `is_active` igual que el botón de baja."""
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        servicios.crear_acceso(alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")
        alumno.refresh_from_db()

        self.client.post(
            reverse("alumnos:editar", args=[alumno.pk]),
            {
                "nombre": "Juan",
                "apellido": "Pérez",
                "estado": Alumno.Estado.INACTIVO,
                "actividad_fisica_previa": False,
                "tiene_discapacidad": False,
                "tiene_enfermedad_cronica": False,
            },
        )

        alumno.refresh_from_db()
        self.assertEqual(alumno.estado, Alumno.Estado.INACTIVO)
        self.assertFalse(alumno.perfil.usuario.is_active)

    def test_reactivar_desde_la_ficha_devuelve_el_acceso(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Juan",
            apellido="Pérez",
            estado=Alumno.Estado.INACTIVO,
        )
        servicios.crear_acceso(alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")
        alumno.refresh_from_db()
        self.assertFalse(alumno.perfil.usuario.is_active)

        self.client.post(
            reverse("alumnos:editar", args=[alumno.pk]),
            {
                "nombre": "Juan",
                "apellido": "Pérez",
                "estado": Alumno.Estado.ACTIVO,
                "actividad_fisica_previa": False,
                "tiene_discapacidad": False,
                "tiene_enfermedad_cronica": False,
            },
        )

        alumno.refresh_from_db()
        self.assertTrue(alumno.perfil.usuario.is_active)

    def test_un_alumno_sin_acceso_no_rompe_al_guardarse(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Sin", apellido="Acceso"
        )
        alumno.estado = Alumno.Estado.INACTIVO
        alumno.save()  # no debe explotar


class CrearAccesoConcurrenciaTests(TestCase):
    """`crear_acceso` hacía `exists()` y después `create_user()` sin lock.

    Un doble submit del form (que está boosteado por htmx y no deshabilita el
    botón) llegaba al `UNIQUE` de `auth_user.username` como IntegrityError, o
    sea un 500 en vez de un error de campo.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )

    def test_un_integrityerror_de_username_se_traduce_a_identificador_en_uso(self):
        """Se simula la carrera creando el `User` entre el chequeo y el insert."""
        original = servicios.get_user_model().objects.create_user

        def crear_pisando(*args, **kwargs):
            # Emula al request concurrente que se adelantó.
            servicios.get_user_model().objects.filter(
                username=kwargs["username"]
            ).delete()
            original(username=kwargs["username"], password="otra-clave-larga-99")
            return original(*args, **kwargs)

        with patch.object(
            servicios.get_user_model().objects, "create_user", crear_pisando
        ):
            with self.assertRaises(servicios.IdentificadorEnUso):
                servicios.crear_acceso(
                    self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com"
                )

        self.alumno.refresh_from_db()
        self.assertIsNone(self.alumno.perfil)

    def test_regenerar_password_rechaza_un_perfil_que_no_sea_alumno(self):
        """Defensa en profundidad: un `Alumno.perfil` apuntando a un Perfil
        STAFF (construible desde /admin/) dejaría a cualquier staff resetear
        la contraseña de otro staff y entrar con ella."""
        usuario_staff = User.objects.create_user("otro-staff", password="clave-larga-1")
        perfil_staff = Perfil.objects.create(
            usuario=usuario_staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno.perfil = perfil_staff
        self.alumno.save(update_fields=["perfil"])

        with self.assertRaises(PermissionDenied):
            servicios.regenerar_password(self.alumno)
