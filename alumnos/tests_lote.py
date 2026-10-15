"""Tests de las acciones en lote del listado de alumnos (`alumnos:lote`):
fijar fecha de inicio, dar de baja, reactivar y generar códigos de ingreso."""

import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos import identidad
from alumnos.models import Alumno
from alumnos.services import crear_acceso
from ingreso import services as servicios_ingreso
from ingreso.models import Credencial
from pagos.models import Cuota, generar_pagos_pendientes
from tenants.models import Gimnasio, Perfil

User = get_user_model()


class LoteBase(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.otro = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.force_login(self.staff)
        self.url = reverse("alumnos:lote")
        self.listado = reverse("alumnos:listado")
        self.hoy = timezone.localdate()

    def crear(self, nombre, apellido="Pérez", gimnasio=None, ancla="hoy", **kw):
        alumno = Alumno.objects.create(
            gimnasio=gimnasio or self.gimnasio, nombre=nombre, apellido=apellido, **kw
        )
        # El pre_save del alta ancla en hoy; para otra ancla (o None, el
        # importado) se pisa con update(), que no corre señales.
        if ancla != "hoy":
            Alumno.objects.filter(pk=alumno.pk).update(fecha_inicio_ciclo=ancla)
            alumno.refresh_from_db()
        return alumno

    def con_cuota(self, alumno):
        Cuota.objects.create(
            gimnasio=alumno.gimnasio, alumno=alumno, monto=0,
            periodo_inicio=self.hoy, periodo_fin=self.hoy + datetime.timedelta(days=27),
        )

    def post(self, accion, alumnos, **extra):
        datos = {
            "accion": accion,
            "ids": ",".join(str(a.pk) for a in alumnos),
            "filtros": "",
        }
        datos.update(extra)
        return self.client.post(self.url, datos)

    def mensajes(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class FijarFechaTests(LoteBase):
    def test_primer_post_pide_confirmacion_con_hoy_por_defecto(self):
        a = self.crear("Ana", ancla=None)
        r = self.post("fijar_fecha", [a])
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "alumnos/lote_confirmar.html")
        self.assertContains(r, f'value="{self.hoy.isoformat()}"')
        self.assertContains(r, "Pérez, Ana")
        a.refresh_from_db()
        self.assertIsNone(a.fecha_inicio_ciclo)

    def test_fecha_despues_de_hoy_mas_60_es_error_sin_cambiar_nada(self):
        a = self.crear("Ana", ancla=None)
        fecha = self.hoy + datetime.timedelta(days=61)
        r = self.post("fijar_fecha", [a], fecha=fecha.isoformat(), confirmado="1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="config-error"')
        a.refresh_from_db()
        self.assertIsNone(a.fecha_inicio_ciclo)

    def test_fecha_antes_de_hoy_menos_27_es_error(self):
        a = self.crear("Ana", ancla=None)
        fecha = self.hoy - datetime.timedelta(days=28)
        r = self.post("fijar_fecha", [a], fecha=fecha.isoformat(), confirmado="1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="config-error"')
        a.refresh_from_db()
        self.assertIsNone(a.fecha_inicio_ciclo)

    def test_fecha_vacia_es_error(self):
        a = self.crear("Ana", ancla=None)
        r = self.post("fijar_fecha", [a], fecha="", confirmado="1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="config-error"')

    def test_bordes_del_rango_son_validos(self):
        for dias in (60, -27):
            a = self.crear(f"Ana{dias}", ancla=None)
            fecha = self.hoy + datetime.timedelta(days=dias)
            r = self.post("fijar_fecha", [a], fecha=fecha.isoformat(), confirmado="1")
            self.assertRedirects(r, self.listado, fetch_redirect_response=False)
            a.refresh_from_db()
            self.assertEqual(a.fecha_inicio_ciclo, fecha)

    def test_sin_confirmado_muestra_la_fecha_elegida_sin_ejecutar(self):
        a = self.crear("Ana", ancla=None)
        fecha = self.hoy + datetime.timedelta(days=5)
        r = self.post("fijar_fecha", [a], fecha=fecha.isoformat())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="confirmado"')
        self.assertContains(r, f'value="{fecha.isoformat()}"')
        a.refresh_from_db()
        self.assertIsNone(a.fecha_inicio_ciclo)

    def test_saltea_y_nombra_a_los_que_tienen_cuotas(self):
        sin = self.crear("Ana", ancla=None)
        con = self.crear("Beto", "Gómez")
        self.con_cuota(con)
        ancla_con = con.fecha_inicio_ciclo
        fecha = self.hoy + datetime.timedelta(days=3)

        r = self.post("fijar_fecha", [sin, con], fecha=fecha.isoformat())
        self.assertContains(r, "Gómez, Beto")

        r = self.post(
            "fijar_fecha", [sin, con], fecha=fecha.isoformat(), confirmado="1"
        )
        sin.refresh_from_db()
        con.refresh_from_db()
        self.assertEqual(sin.fecha_inicio_ciclo, fecha)
        self.assertEqual(con.fecha_inicio_ciclo, ancla_con)
        texto = " ".join(self.mensajes(r))
        self.assertIn("Fecha fijada a 1", texto)
        self.assertIn("Gómez, Beto", texto)

    def test_fecha_futura_no_emite_cuotas(self):
        a = self.crear("Ana", ancla=None)
        fecha = self.hoy + datetime.timedelta(days=10)
        self.post("fijar_fecha", [a], fecha=fecha.isoformat(), confirmado="1")
        generar_pagos_pendientes(self.hoy)
        self.assertFalse(Cuota.objects.filter(alumno=a).exists())

    def test_actualiza_modificado(self):
        a = self.crear("Ana", ancla=None)
        viejo = timezone.now() - datetime.timedelta(days=3)
        Alumno.objects.filter(pk=a.pk).update(modificado=viejo)
        self.post("fijar_fecha", [a], fecha=self.hoy.isoformat(), confirmado="1")
        a.refresh_from_db()
        self.assertGreater(a.modificado, viejo)

    def test_ids_de_otro_gimnasio_no_se_tocan(self):
        mio = self.crear("Ana", ancla=None)
        ajeno = self.crear("Zoe", gimnasio=self.otro, ancla=None)
        r = self.post(
            "fijar_fecha", [mio, ajeno], fecha=self.hoy.isoformat(), confirmado="1"
        )
        ajeno.refresh_from_db()
        mio.refresh_from_db()
        self.assertIsNone(ajeno.fecha_inicio_ciclo)
        self.assertEqual(mio.fecha_inicio_ciclo, self.hoy)
        self.assertIn("1 no se encontró", " ".join(self.mensajes(r)))

    def test_avisa_cuantos_activos_nacen_bloqueados(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=5,
            fecha_activacion_bloqueo=self.hoy - datetime.timedelta(days=400),
        )
        a = self.crear("Ana", ancla=None)
        b = self.crear("Beto", ancla=None)
        c = self.crear("Caro", ancla=None, estado=Alumno.Estado.INACTIVO)
        vieja = self.hoy - datetime.timedelta(days=10)
        r = self.post("fijar_fecha", [a, b, c], fecha=vieja.isoformat())
        self.assertContains(r, "2 alumnos activos nacerían con el acceso pausado")
        r = self.post("fijar_fecha", [a, b, c], fecha=self.hoy.isoformat())
        self.assertNotContains(r, "nacería")

    def test_reloj_a_las_2330_usa_la_fecha_local(self):
        # 23:30 en Buenos Aires = 02:30 UTC del día siguiente. Fecha lejana,
        # para que no coincida por casualidad con la fecha real del test. En
        # UTC, como lo devuelve `timezone.now()`: con el tzinfo de Buenos Aires
        # un `.date()` mal escrito daría igual la fecha local y el test pasaría
        # con el bug puesto (se verificó).
        momento = datetime.datetime(
            2031, 3, 15, 23, 30, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")
        ).astimezone(datetime.timezone.utc)
        hoy = datetime.date(2031, 3, 15)
        with patch("django.utils.timezone.now", return_value=momento):
            self.client.force_login(self.staff)
            a = self.crear("Ana", ancla=None)
            r = self.post("fijar_fecha", [a])
            self.assertContains(r, 'value="2031-03-15"')
            for dias, valida in ((61, False), (60, True), (-27, True), (-28, False)):
                fecha = hoy + datetime.timedelta(days=dias)
                r = self.post("fijar_fecha", [a], fecha=fecha.isoformat(), confirmado="1")
                a.refresh_from_db()
                if valida:
                    self.assertEqual(r.status_code, 302, dias)
                    self.assertEqual(a.fecha_inicio_ciclo, fecha)
                else:
                    self.assertEqual(r.status_code, 200, dias)
                    self.assertContains(r, 'class="config-error"')

    def test_tope_excedido_redirige_con_mensaje(self):
        alumnos = [self.crear(f"A{i}", ancla=None) for i in range(3)]
        ajeno = self.crear("Zoe", gimnasio=self.otro, ancla=None)
        with patch("alumnos.lote.MAX_LOTE_FECHA", 2):
            r = self.post(
                "fijar_fecha", alumnos, fecha=self.hoy.isoformat(), confirmado="1"
            )
            self.assertRedirects(r, self.listado, fetch_redirect_response=False)
            self.assertIn("máximo", " ".join(self.mensajes(r)))
            # El tope cuenta solo los del gimnasio: dos propios + uno ajeno pasa.
            r = self.post(
                "fijar_fecha", alumnos[:2] + [ajeno],
                fecha=self.hoy.isoformat(), confirmado="1",
            )
        for a in alumnos:
            a.refresh_from_db()
        self.assertEqual(
            [a.fecha_inicio_ciclo for a in alumnos], [self.hoy, self.hoy, None]
        )


class BajaTests(LoteBase):
    def setUp(self):
        super().setUp()
        self.a = self.crear("Ana")
        crear_acceso(self.a, identidad.TIPO_EMAIL, "ana@ejemplo.com")
        self.a.refresh_from_db()

    def test_pide_confirmacion_sin_cambiar_nada(self):
        r = self.post("dar_de_baja", [self.a])
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "alumnos/lote_confirmar.html")
        self.assertContains(r, "acceso")
        self.assertContains(r, "padrón")
        self.a.refresh_from_db()
        self.assertEqual(self.a.estado, Alumno.Estado.ACTIVO)

    def test_confirmado_da_de_baja_y_corta_el_acceso(self):
        r = self.post("dar_de_baja", [self.a], confirmado="1")
        self.assertRedirects(r, self.listado, fetch_redirect_response=False)
        self.a.refresh_from_db()
        self.assertEqual(self.a.estado, Alumno.Estado.INACTIVO)
        self.assertEqual(self.a.fecha_baja, self.hoy)
        self.assertFalse(self.a.perfil.usuario.is_active)
        self.assertIn("Pérez, Ana", " ".join(self.mensajes(r)))

    def test_doble_post_no_pisa_fecha_baja(self):
        self.post("dar_de_baja", [self.a], confirmado="1")
        vieja = self.hoy - datetime.timedelta(days=30)
        Alumno.objects.filter(pk=self.a.pk).update(fecha_baja=vieja)
        self.post("dar_de_baja", [self.a], confirmado="1")
        self.a.refresh_from_db()
        self.assertEqual(self.a.fecha_baja, vieja)

    def _queries_baja(self, cantidad, offset):
        alumnos = []
        for i in range(cantidad):
            a = self.crear(f"N{offset}-{i}")
            crear_acceso(a, identidad.TIPO_EMAIL, f"n{offset}-{i}@ejemplo.com")
            alumnos.append(a)
        with CaptureQueriesContext(connection) as ctx:
            r = self.post("dar_de_baja", alumnos, confirmado="1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            Alumno.objects.filter(
                pk__in=[a.pk for a in alumnos], estado=Alumno.Estado.INACTIVO
            ).count(),
            cantidad,
        )
        return len(ctx)

    def test_escala_a_lo_sumo_3_queries_por_alumno(self):
        chico = self._queries_baja(10, "c")
        grande = self._queries_baja(40, "g")
        self.assertLessEqual(grande - chico, 3 * 30)


class ReactivarTests(LoteBase):
    def test_limpia_fecha_baja_y_devuelve_el_acceso(self):
        a = self.crear("Ana")
        crear_acceso(a, identidad.TIPO_EMAIL, "ana@ejemplo.com")
        self.post("dar_de_baja", [a], confirmado="1")
        r = self.post("reactivar", [a])
        self.assertRedirects(r, self.listado, fetch_redirect_response=False)
        a.refresh_from_db()
        self.assertEqual(a.estado, Alumno.Estado.ACTIVO)
        self.assertIsNone(a.fecha_baja)
        self.assertTrue(a.perfil.usuario.is_active)

    def test_importado_sin_ancla_sigue_sin_ancla_y_se_avisa(self):
        a = self.crear("Ana", ancla=None, estado=Alumno.Estado.INACTIVO)
        r = self.post("reactivar", [a])
        a.refresh_from_db()
        self.assertEqual(a.estado, Alumno.Estado.ACTIVO)
        self.assertIsNone(a.fecha_inicio_ciclo)
        self.assertIn("sin fecha de inicio", " ".join(self.mensajes(r)))

    def test_ancla_vieja_se_re_ancla_a_hoy(self):
        a = self.crear(
            "Ana", ancla=self.hoy - datetime.timedelta(days=60),
            estado=Alumno.Estado.INACTIVO,
        )
        self.post("reactivar", [a])
        a.refresh_from_db()
        self.assertEqual(a.fecha_inicio_ciclo, self.hoy)


class GenerarCodigosTests(LoteBase):
    def test_solo_seleccionados_y_activos(self):
        a = self.crear("Ana")
        b = self.crear("Beto")
        no_elegido = self.crear("Caro")
        inactivo = self.crear("Dani", estado=Alumno.Estado.INACTIVO)
        r = self.post("generar_codigos", [a, b, inactivo])
        self.assertRedirects(r, self.listado, fetch_redirect_response=False)
        con_qr = set(
            Credencial.objects.filter(tipo=Credencial.Tipo.QR).values_list(
                "alumno_id", flat=True
            )
        )
        self.assertEqual(con_qr, {a.pk, b.pk})
        self.assertNotIn(no_elegido.pk, con_qr)
        self.assertIn("2", " ".join(self.mensajes(r)))

    def test_sin_alumno_ids_genera_para_todos_los_activos(self):
        a = self.crear("Ana")
        b = self.crear("Beto")
        self.crear("Dani", estado=Alumno.Estado.INACTIVO)
        self.assertEqual(
            servicios_ingreso.generar_credenciales_en_bloque(self.gimnasio), 2
        )
        self.assertEqual(
            set(Credencial.objects.values_list("alumno_id", flat=True)), {a.pk, b.pk}
        )


class NombresTests(LoteBase):
    def test_hasta_10_nombres_y_despues_y_n_mas(self):
        from alumnos.lote import nombres

        alumnos = [self.crear(f"A{i:02d}") for i in range(12)]
        texto = nombres(alumnos)
        self.assertIn("Pérez, A09", texto)
        self.assertNotIn("Pérez, A10", texto)
        self.assertTrue(texto.endswith(" y 2 más"))


class EntradaTests(LoteBase):
    def test_get_redirige_al_listado(self):
        r = self.client.get(self.url)
        self.assertRedirects(r, self.listado, fetch_redirect_response=False)

    def test_seleccion_vacia_redirige_con_error(self):
        r = self.client.post(self.url, {"accion": "reactivar", "ids": ""})
        self.assertRedirects(r, self.listado, fetch_redirect_response=False)
        self.assertTrue(self.mensajes(r))

    def test_ids_invalidos_redirigen_con_error(self):
        r = self.client.post(self.url, {"accion": "reactivar", "ids": "1,abc"})
        self.assertRedirects(r, self.listado, fetch_redirect_response=False)
        self.assertTrue(self.mensajes(r))

    def test_solo_ids_ajenos_redirige_con_error(self):
        ajeno = self.crear("Zoe", gimnasio=self.otro, estado=Alumno.Estado.INACTIVO)
        r = self.post("reactivar", [ajeno])
        self.assertRedirects(r, self.listado, fetch_redirect_response=False)
        ajeno.refresh_from_db()
        self.assertEqual(ajeno.estado, Alumno.Estado.INACTIVO)

    def test_accion_desconocida_redirige(self):
        a = self.crear("Ana")
        r = self.post("borrar_todo", [a])
        self.assertRedirects(r, self.listado, fetch_redirect_response=False)

    def test_filtros_se_revalidan_y_recodifican(self):
        a = self.crear("Ana")
        r = self.post(
            "generar_codigos", [a],
            filtros="//evil.com&q=//evil&estado=activo&next=http://x.com",
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, self.listado + "?q=%2F%2Fevil&estado=activo")

    def test_filtros_basura_vuelven_al_listado_pelado(self):
        a = self.crear("Ana")
        r = self.post("generar_codigos", [a], filtros="//evil.com")
        self.assertEqual(r.url, self.listado)

    def test_confirmacion_conserva_los_filtros(self):
        a = self.crear("Ana")
        r = self.post("dar_de_baja", [a], filtros="estado=activo")
        self.assertContains(r, 'name="filtros" value="estado=activo"')
