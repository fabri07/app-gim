"""Pantalla «Pasar lista» del staff: GET con fecha/búsqueda/página y POST."""

from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from asistencia.models import Asistencia
from asistencia.services import MAX_DIAS_ATRAS
from ingreso.models import Dispositivo, EventoIngreso
from tenants.models import Gimnasio, Perfil
from tenants.terminos import VERSION_TERMINOS


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="A", slug="a", reconocimientos_habilitado=True
        )
        self.otro = Gimnasio.objects.create(
            nombre="B", slug="b", reconocimientos_habilitado=True
        )
        self.disp = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta", token_hash="h1"
        )
        self.user = User.objects.create_user("staff-1", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")
        self.hoy = timezone.localdate()
        self.url = reverse("asistencia:pasar_lista")
        self.ana = self._alumno("Ana", "Gómez")
        self.beto = self._alumno("Beto", "Pérez")

    def _alumno(self, nombre, apellido, gimnasio=None, **extra):
        return Alumno.objects.create(
            gimnasio=gimnasio or self.gimnasio,
            nombre=nombre,
            apellido=apellido,
            **extra,
        )

    def _marca(self, alumno, fecha, origen=Asistencia.Origen.MANUAL):
        return Asistencia.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, fecha=fecha, origen=origen
        )

    def _lector(self, alumno, fecha):
        return EventoIngreso.objects.create(
            gimnasio=alumno.gimnasio,
            dispositivo=self.disp,
            alumno=alumno,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            sentido=EventoIngreso.Sentido.ENTRADA,
            momento=timezone.make_aware(
                datetime(fecha.year, fecha.month, fecha.day, 10, 0)
            ),
        )

    def _post(self, ids, marcados, fecha=None, **extra):
        datos = {
            "fecha": (fecha or self.hoy).isoformat(),
            "ids": ",".join(str(i) for i in ids),
            "marcados": [str(m) for m in marcados],
            **extra,
        }
        return self.client.post(self.url, datos)

    def _mensajes(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class GetTests(_Base):
    def test_lista_solo_activos_del_gimnasio(self):
        self._alumno("Inac", "Tivo", estado=Alumno.Estado.INACTIVO)
        self._alumno("Carla", "Ruiz", gimnasio=self.otro)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gómez")
        self.assertContains(response, "Pérez")
        self.assertNotContains(response, "Tivo")
        self.assertNotContains(response, "Ruiz")

    def test_casilla_tildada_si_hay_marca_ese_dia(self):
        self._marca(self.ana, self.hoy)
        html = self.client.get(self.url).content.decode()
        self.assertRegex(html, rf'value="{self.ana.pk}"[^>]*checked')
        self.assertNotRegex(html, rf'value="{self.beto.pk}"[^>]*checked')

    def test_marca_de_otro_dia_no_tilda(self):
        self._marca(self.ana, self.hoy - timedelta(days=1))
        html = self.client.get(self.url).content.decode()
        self.assertNotRegex(html, rf'value="{self.ana.pk}"[^>]*checked')

    def test_lector_muestra_etiqueta_y_sin_casilla(self):
        self._lector(self.ana, self.hoy)
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertContains(response, "lector")
        self.assertNotIn(f'name="marcados" value="{self.ana.pk}"', html)
        self.assertIn(f'name="marcados" value="{self.beto.pk}"', html)

    def test_lector_mas_marca_del_staff_muestra_casilla_tildada(self):
        self._lector(self.ana, self.hoy)
        self._marca(self.ana, self.hoy)
        html = self.client.get(self.url).content.decode()
        self.assertRegex(html, rf'value="{self.ana.pk}"[^>]*checked')

    def test_filtro_q(self):
        response = self.client.get(self.url, {"q": "góm"})
        self.assertContains(response, "Gómez")
        self.assertNotContains(response, "Pérez")
        response = self.client.get(self.url, {"q": "beto"})
        self.assertContains(response, "Pérez")
        self.assertNotContains(response, "Gómez")

    def test_fecha_por_defecto_hoy(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["fecha"], self.hoy)

    def test_fecha_pedida(self):
        d = self.hoy - timedelta(days=3)
        response = self.client.get(self.url, {"fecha": d.isoformat()})
        self.assertEqual(response.context["fecha"], d)

    def test_fecha_invalida_o_fuera_de_rango_cae_a_hoy_con_mensaje(self):
        for valor in (
            "basura",
            (self.hoy + timedelta(days=1)).isoformat(),
            (self.hoy - timedelta(days=MAX_DIAS_ATRAS + 1)).isoformat(),
        ):
            response = self.client.get(self.url, {"fecha": valor})
            self.assertEqual(response.status_code, 200, valor)
            self.assertEqual(response.context["fecha"], self.hoy, valor)

    def test_fecha_invalida_deja_mensaje(self):
        response = self.client.get(self.url, {"fecha": "basura"}, follow=True)
        self.assertTrue(
            [str(m) for m in response.context["messages"]]
        )

    def test_limite_inferior_es_valido(self):
        d = self.hoy - timedelta(days=MAX_DIAS_ATRAS)
        response = self.client.get(self.url, {"fecha": d.isoformat()})
        self.assertEqual(response.context["fecha"], d)

    def test_paginado_de_200(self):
        for i in range(205):
            self._alumno(f"N{i:03d}", f"Z{i:03d}")
        r1 = self.client.get(self.url)
        self.assertEqual(len(r1.context["filas"]), 200)
        self.assertEqual(len(r1.context["ids"].split(",")), 200)
        r2 = self.client.get(self.url, {"pagina": 2})
        self.assertEqual(len(r2.context["filas"]), 7)
        self.assertEqual(len(r2.context["ids"].split(",")), 7)

    def test_pagina_invalida_cae_a_la_primera(self):
        response = self.client.get(self.url, {"pagina": "x"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["filas"]), 2)

    def test_ordena_por_apellido_y_nombre(self):
        response = self.client.get(self.url)
        apellidos = [f["alumno"].apellido for f in response.context["filas"]]
        self.assertEqual(apellidos, ["Gómez", "Pérez"])

    def _contar(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(self.url)
        return len(ctx)

    def test_queries_constantes(self):
        self.client.get(self.url)  # calentamiento (cachés de la primera request)
        for i in range(3):
            self._alumno(f"C{i}", f"Y{i}")
        base = self._contar()
        with self.assertNumQueries(base):
            self.client.get(self.url)
        for i in range(60):
            a = self._alumno(f"D{i}", f"W{i}")
            self._marca(a, self.hoy)
            self._lector(a, self.hoy)
        with self.assertNumQueries(base):
            self.client.get(self.url)


class PostTests(_Base):
    def test_crea_marcas_faltantes_manuales(self):
        response = self._post([self.ana.pk, self.beto.pk], [self.ana.pk])
        self.assertEqual(response.status_code, 302)
        marca = Asistencia.objects.get()
        self.assertEqual(marca.alumno, self.ana)
        self.assertEqual(marca.fecha, self.hoy)
        self.assertEqual(marca.origen, Asistencia.Origen.MANUAL)
        self.assertEqual(marca.registrada_por, self.perfil)
        self.assertEqual(self._mensajes(response), ["1 marcadas, 0 quitadas."])

    def test_mensaje_n_marcadas_m_quitadas(self):
        self._marca(self.beto, self.hoy)
        response = self._post([self.ana.pk, self.beto.pk], [self.ana.pk])
        self.assertEqual(self._mensajes(response), ["1 marcadas, 1 quitadas."])

    def test_ya_marcada_no_se_duplica_ni_cuenta(self):
        self._marca(self.ana, self.hoy)
        response = self._post([self.ana.pk], [self.ana.pk])
        self.assertEqual(Asistencia.objects.count(), 1)
        self.assertEqual(self._mensajes(response), ["0 marcadas, 0 quitadas."])

    def test_doble_envio_es_idempotente(self):
        self._post([self.ana.pk], [self.ana.pk])
        self._post([self.ana.pk], [self.ana.pk])
        self.assertEqual(Asistencia.objects.count(), 1)

    def test_borra_destildadas_de_cualquier_origen_solo_entre_los_ids(self):
        self._marca(self.ana, self.hoy, Asistencia.Origen.TURNO)
        self._marca(self.beto, self.hoy)
        fuera = self._alumno("Fuera", "Pagina")
        self._marca(fuera, self.hoy)
        self._post([self.ana.pk, self.beto.pk], [])
        self.assertEqual(
            list(Asistencia.objects.values_list("alumno_id", flat=True)),
            [fuera.pk],
        )

    def test_destildar_no_toca_otras_fechas(self):
        ayer = self.hoy - timedelta(days=1)
        self._marca(self.ana, ayer)
        self._post([self.ana.pk], [])
        self.assertEqual(Asistencia.objects.count(), 1)

    def test_nunca_toca_ingresos_del_lector(self):
        self._lector(self.ana, self.hoy)
        self._post([self.ana.pk], [])
        self.assertEqual(EventoIngreso.objects.count(), 1)

    def test_marcado_fuera_de_ids_se_ignora(self):
        self._post([self.ana.pk], [self.ana.pk, self.beto.pk])
        self.assertEqual(
            list(Asistencia.objects.values_list("alumno_id", flat=True)),
            [self.ana.pk],
        )

    def test_otro_gimnasio_e_inactivos_se_ignoran(self):
        ajeno = self._alumno("Carla", "Ruiz", gimnasio=self.otro)
        inactivo = self._alumno("Inac", "Tivo", estado=Alumno.Estado.INACTIVO)
        self._marca(ajeno, self.hoy)
        self._marca(inactivo, self.hoy)
        self._post(
            [ajeno.pk, inactivo.pk, self.ana.pk],
            [ajeno.pk, inactivo.pk, self.ana.pk],
        )
        self.assertEqual(Asistencia.objects.count(), 3)  # 2 previas + Ana
        self.assertEqual(Asistencia.objects.filter(alumno=ajeno).count(), 1)
        self.assertEqual(Asistencia.objects.filter(alumno=inactivo).count(), 1)
        self._post([ajeno.pk, inactivo.pk], [])
        self.assertEqual(Asistencia.objects.count(), 3)  # no se borraron

    def test_ids_basura_no_rompe(self):
        response = self.client.post(
            self.url,
            {"fecha": self.hoy.isoformat(), "ids": "x,,3.5,", "marcados": ["z"]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Asistencia.objects.count(), 0)

    def test_fecha_futura_no_guarda(self):
        futura = self.hoy + timedelta(days=1)
        response = self._post([self.ana.pk], [self.ana.pk], fecha=futura)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Asistencia.objects.exists())
        self.assertTrue(self._mensajes(response))

    def test_fecha_muy_vieja_no_guarda(self):
        vieja = self.hoy - timedelta(days=MAX_DIAS_ATRAS + 1)
        response = self._post([self.ana.pk], [self.ana.pk], fecha=vieja)
        self.assertFalse(Asistencia.objects.exists())
        self.assertTrue(self._mensajes(response))

    def test_fecha_invalida_no_guarda(self):
        response = self.client.post(
            self.url,
            {"fecha": "nada", "ids": str(self.ana.pk), "marcados": [self.ana.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Asistencia.objects.exists())

    def test_limite_viejo_valido_guarda(self):
        limite = self.hoy - timedelta(days=MAX_DIAS_ATRAS)
        self._post([self.ana.pk], [self.ana.pk], fecha=limite)
        self.assertEqual(Asistencia.objects.get().fecha, limite)

    def test_redirige_a_la_misma_fecha_q_y_pagina(self):
        d = self.hoy - timedelta(days=2)
        response = self._post([self.ana.pk], [], fecha=d, q="gom", pagina="2")
        url = response["Location"]
        self.assertTrue(url.startswith(self.url))
        self.assertIn(f"fecha={d.isoformat()}", url)
        self.assertIn("q=gom", url)
        self.assertIn("pagina=2", url)


class PermisosTests(_Base):
    def test_sin_bandera_404_en_get_y_post(self):
        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self._post([self.ana.pk], [self.ana.pk]).status_code, 404)
        self.assertFalse(Asistencia.objects.exists())

    def test_alumno_recibe_403(self):
        u = User.objects.create_user("alu", password="clave-123456")
        Perfil.objects.create(
            usuario=u,
            gimnasio=self.gimnasio,
            rol=Perfil.Rol.ALUMNO,
            terminos_version=VERSION_TERMINOS,
        )
        self.client.login(username="alu", password="clave-123456")
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self._post([self.ana.pk], [self.ana.pk]).status_code, 403)

    def test_anonimo_va_al_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class LinkEnAgendaTests(_Base):
    def test_link_solo_con_bandera(self):
        agenda = reverse("turnos:agenda")
        self.assertContains(self.client.get(agenda), self.url)
        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()
        self.assertNotContains(self.client.get(agenda), self.url)
