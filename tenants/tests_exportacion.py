"""Exportador de datos del gimnasio (`tenants/exportacion.py` y su vista).

Lo que estos tests cuidan, en orden de gravedad:

1. Que un gimnasio NO pueda exportar sin la casilla de `/admin/`, por más que
   postee la URL a mano (el botón deshabilitado es UX, no defensa).
2. Que el ZIP de un gimnasio no traiga NADA de otro.
3. Que no salga ningún secreto (tokens de Google, credenciales push, hashes).
4. Que un modelo nuevo no quede afuera por olvido.
"""

import csv
import io
import os
import tempfile
import zipfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from calendario.models import GoogleCalendarCredential
from novedades.models import Novedad, NovedadLeida
from pagos.models import Cuota
from rutinas.models import (
    RutinaAsignada,
    RutinaAsignadaDiaCompletado,
    RutinaAsignadaItem,
)
from tenants import exportacion
from tenants.forms import GimnasioForm
from tenants.models import Gimnasio, Perfil
from tenants.tests import _ensuciar, _modelos_tenant_owned

TOKEN_SECRETO = "token-de-google-que-no-debe-salir"
PASSWORD_ALUMNO = "clave-del-alumno-1234"


def _ensuciar_para_exportar(gimnasio, staff, marca="X"):
    """`_ensuciar` más lo que ese fixture NO crea y el exportador sí tiene que
    cubrir. Se envuelve en vez de modificarlo porque lo comparten los tests de
    `vaciar_gimnasio`.

    Sin esto, «cada CSV tiene al menos una fila» falla en dos hojas, y -- peor
    -- el test de secretos pasa sin que exista ningún token que filtrar."""
    alumno = _ensuciar(gimnasio, staff, marca=marca)

    usuario = User.objects.create_user(f"alumno-{marca}", password=PASSWORD_ALUMNO)
    perfil = Perfil.objects.create(
        usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO
    )
    alumno.perfil = perfil
    alumno.telefono = "+54 9 11 5555-0000"
    alumno.observaciones = "=HYPERLINK(\"http://malo\")"
    alumno.save()

    Cuota.objects.filter(gimnasio=gimnasio).update(monto=Decimal("1500.50"))

    asignada = RutinaAsignada.objects.get(gimnasio=gimnasio, alumno=alumno)
    RutinaAsignadaItem.objects.filter(rutina_asignada=asignada).update(
        rpe=RutinaAsignadaItem.RPE.AL_LIMITE
    )
    RutinaAsignadaDiaCompletado.objects.create(
        rutina_asignada=asignada, dia=1, semana=1
    )
    NovedadLeida.objects.create(
        novedad=Novedad.objects.get(gimnasio=gimnasio), alumno=alumno
    )
    GoogleCalendarCredential.objects.create(alumno=alumno, refresh_token=TOKEN_SECRETO)
    return alumno


def _exportar(gimnasio):
    destino = io.BytesIO()
    conteos = exportacion.exportar_gimnasio(gimnasio=gimnasio, destino=destino)
    destino.seek(0)
    return zipfile.ZipFile(destino), conteos


def _textos(zf):
    """Contenido DESCOMPRIMIDO de cada archivo: buscar un secreto en los bytes
    del ZIP no prueba nada, van comprimidos."""
    return {nombre: zf.read(nombre).decode("utf-8-sig") for nombre in zf.namelist()}


def _filas(zf, ruta, delimitador):
    texto = zf.read(ruta).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(texto), delimiter=delimitador))


@override_settings(GOOGLE_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
class ExportacionBase(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio Ñandú", slug="nandu")
        self.staff = User.objects.create_user("staff-a", password="c-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.otro = Gimnasio.objects.create(nombre="Otro", slug="otro")
        self.staff_otro = User.objects.create_user("staff-b", password="c-123456")
        Perfil.objects.create(
            usuario=self.staff_otro, gimnasio=self.otro, rol=Perfil.Rol.STAFF
        )


class ContenidoDelZipTests(ExportacionBase):
    def test_todo_modelo_tenant_owned_esta_exportado_o_excluido_a_proposito(self):
        """Un `TenantOwnedModel` nuevo rompe acá: hay que decidir si va a
        `HOJAS` o a `EXCLUIDOS` (con el motivo que se le cuenta al gimnasio).
        Que quede afuera por olvido es que el cliente se lleve sus datos
        incompletos sin que nadie lo sepa."""
        exportados = {hoja.modelo for hoja in exportacion.HOJAS}
        sin_decidir = [
            m._meta.label
            for m in _modelos_tenant_owned()
            if m._meta.label not in exportados
            and m._meta.label not in exportacion.EXCLUIDOS
        ]
        self.assertEqual(sin_decidir, [])

    def test_cada_csv_de_cada_carpeta_tiene_datos(self):
        """La otra mitad del test de arriba: que un modelo figure en `HOJAS`
        no dice que su consulta traiga algo. Un filtro mal escrito en una hoja
        de un modelo Item da un CSV vacío y el de arriba pasa igual."""
        _ensuciar_para_exportar(self.gimnasio, self.staff)
        zf, conteos = _exportar(self.gimnasio)

        vacias = [nombre for nombre, cantidad in conteos.items() if cantidad == 0]
        self.assertEqual(vacias, [])
        for hoja in exportacion.HOJAS:
            for carpeta, delimitador in (("para-excel", ";"), ("para-importar", ",")):
                filas = _filas(zf, f"{carpeta}/{hoja.nombre}.csv", delimitador)
                self.assertEqual(len(filas), conteos[hoja.nombre], hoja.nombre)
                self.assertEqual(
                    list(filas[0].keys()), [e for e, _ in hoja.columnas], hoja.nombre
                )
        self.assertIn("LEEME.txt", zf.namelist())

    def test_no_trae_nada_de_otro_gimnasio(self):
        _ensuciar_para_exportar(self.gimnasio, self.staff, marca="PROPIO")
        _ensuciar_para_exportar(self.otro, self.staff_otro, marca="AJENO")

        zf, _ = _exportar(self.gimnasio)
        todo = "\n".join(_textos(zf).values())

        self.assertIn("PROPIO", todo)
        self.assertNotIn("AJENO", todo)
        # Los modelos Item no tienen `gimnasio` propio: se acotan por su padre.
        # `dias_entrenados` no lleva ningún texto donde buscar la marca, así
        # que se cuenta contra lo que el fixture crea: uno por gimnasio. Números
        # fijos a propósito -- contar con el mismo filtro que usa el código
        # sería comparar el exportador consigo mismo.
        self.assertEqual(len(_filas(zf, "para-importar/dias_entrenados.csv", ",")), 1)
        self.assertEqual(len(_filas(zf, "para-importar/novedades_lecturas.csv", ",")), 1)
        self.assertEqual(len(_filas(zf, "para-importar/plantillas_ejercicios.csv", ",")), 1)

    def test_no_sale_ningun_secreto(self):
        alumno = _ensuciar_para_exportar(self.gimnasio, self.staff)
        hash_password = alumno.perfil.usuario.password
        # El fixture tiene que tener de verdad lo que se busca, o esto pasa solo.
        self.assertTrue(
            GoogleCalendarCredential.objects.filter(alumno=alumno).exists()
        )

        zf, _ = _exportar(self.gimnasio)
        todo = "\n".join(_textos(zf).values())

        self.assertNotIn(TOKEN_SECRETO, todo)
        self.assertNotIn("push.example.com", todo)
        self.assertNotIn(hash_password, todo)
        self.assertNotIn(PASSWORD_ALUMNO, todo)
        # ...pero el identificador de acceso sí: es un dato del gimnasio.
        self.assertIn("alumno-X", todo)

    def test_las_dos_carpetas_formatean_distinto_los_mismos_datos(self):
        _ensuciar_para_exportar(self.gimnasio, self.staff)
        zf, _ = _exportar(self.gimnasio)

        excel = _filas(zf, "para-excel/cuotas.csv", ";")[0]
        importar = _filas(zf, "para-importar/cuotas.csv", ",")[0]
        self.assertEqual(excel["monto"], "1500,50")
        self.assertEqual(importar["monto"], "1500.50")
        self.assertEqual(excel["estado"], "Pendiente")

        alumno_excel = _filas(zf, "para-excel/alumnos.csv", ";")[0]
        alumno_importar = _filas(zf, "para-importar/alumnos.csv", ",")[0]
        # El teléfono empieza con "+" y NO es una fórmula.
        self.assertEqual(alumno_excel["telefono"], "+54 9 11 5555-0000")
        # La fórmula se neutraliza solo donde se abre con Excel: en el archivo
        # para importar, un apóstrofe agregado es corrupción de datos.
        self.assertTrue(alumno_excel["observaciones"].startswith("'=HYPERLINK"))
        self.assertTrue(alumno_importar["observaciones"].startswith("=HYPERLINK"))
        self.assertEqual(alumno_excel["tiene_discapacidad"], "No")
        self.assertEqual(alumno_importar["tiene_discapacidad"], "false")

    def test_los_acentos_abren_bien_en_excel(self):
        """Sin BOM, Excel lee UTF-8 como Latin-1 y «Ñandú» sale «Ã‘andÃº»."""
        zf, _ = _exportar(self.gimnasio)
        self.assertTrue(zf.read("para-excel/gimnasio.csv").startswith(b"\xef\xbb\xbf"))
        self.assertFalse(
            zf.read("para-importar/gimnasio.csv").startswith(b"\xef\xbb\xbf")
        )
        self.assertIn("Ñandú", _textos(zf)["para-excel/gimnasio.csv"])

    def test_la_calificacion_del_alumno_sale_legible(self):
        _ensuciar_para_exportar(self.gimnasio, self.staff)
        zf, _ = _exportar(self.gimnasio)
        fila = _filas(zf, "para-importar/rutinas_asignadas_ejercicios.csv", ",")[0]
        self.assertEqual(fila["calificacion_del_alumno"], "Estoy al límite")

    def test_el_costo_crece_por_tanda_y_no_por_fila(self):
        """Se pagina por pk porque producción no tiene cursores de servidor
        (ver el docstring del módulo). Lo que no puede pasar es una consulta
        por fila: con 50.000 items son 50.000 idas a la base con el único
        worker tomado."""
        alumno = _ensuciar_para_exportar(self.gimnasio, self.staff)
        asignada = RutinaAsignada.objects.get(alumno=alumno)

        def contar():
            with CaptureQueriesContext(connection) as ctx:
                _exportar(self.gimnasio)
            return len(ctx)

        base = contar()
        RutinaAsignadaItem.objects.bulk_create(
            RutinaAsignadaItem(
                rutina_asignada=asignada, ejercicio_nombre_snapshot=f"Extra {i}",
                semana=1, dia=1, orden=10 + i, series=3, repeticiones="10",
            )
            for i in range(40)
        )
        self.assertEqual(contar(), base)

        with patch.object(exportacion, "TAMANIO_TANDA", 10):
            zf, conteos = _exportar(self.gimnasio)
        # Y paginando de verdad no se pierde ni se repite ninguna fila.
        ids = [f["id"] for f in _filas(zf, "para-importar/rutinas_asignadas_ejercicios.csv", ",")]
        self.assertEqual(len(ids), conteos["rutinas_asignadas_ejercicios"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            len(ids), RutinaAsignadaItem.objects.filter(rutina_asignada=asignada).count()
        )


class SanearParaExcelTests(SimpleTestCase):
    def test_neutraliza_lo_que_excel_ejecutaria(self):
        for texto in ("=cmd|' /C calc'!A0", "@SUM(1)", "+SUM(A1)", "-@x", "\tx", "-(1+1)"):
            self.assertEqual(exportacion.sanear_para_excel(texto), "'" + texto, texto)

    def test_no_toca_lo_que_solo_parece(self):
        for texto in ("+54 9 11 5555-0000", "-5", "- Tren superior", "3x12", "", "a=b"):
            self.assertEqual(exportacion.sanear_para_excel(texto), texto, texto)


class ExportarDatosViewTests(ExportacionBase):
    def setUp(self):
        super().setUp()
        self.url = reverse("gimnasio_exportar")

    def test_sin_la_casilla_es_403_aunque_se_postee_a_mano(self):
        self.client.force_login(self.staff)
        respuesta = self.client.post(self.url)
        self.assertEqual(respuesta.status_code, 403)
        self.gimnasio.refresh_from_db()
        self.assertIsNone(self.gimnasio.exportacion_ultima_descarga)

    def test_con_la_casilla_descarga_un_zip(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        self.client.force_login(self.staff)

        respuesta = self.client.post(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "application/zip")
        self.assertIn("datos-nandu-", respuesta["Content-Disposition"])
        zf = zipfile.ZipFile(io.BytesIO(b"".join(respuesta.streaming_content)))
        self.assertIn("para-excel/alumnos.csv", zf.namelist())
        self.gimnasio.refresh_from_db()
        self.assertIsNotNone(self.gimnasio.exportacion_ultima_descarga)

    def test_la_cuenta_demo_exporta_sin_casilla(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url).status_code, 200)

    def test_exportar_no_toca_modificado(self):
        """`modificado` versiona el logo y el ícono de la PWA: si exportar lo
        moviera, cada descarga invalidaría el caché de todos los alumnos."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        antes = Gimnasio.objects.get(pk=self.gimnasio.pk).modificado
        self.client.force_login(self.staff)
        self.client.post(self.url)
        self.assertEqual(Gimnasio.objects.get(pk=self.gimnasio.pk).modificado, antes)

    def test_un_alumno_no_exporta(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        alumno = User.objects.create_user("alu", password="c-123456")
        Perfil.objects.create(
            usuario=alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.force_login(alumno)
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_anonimo_va_al_login(self):
        respuesta = self.client.post(self.url)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("/accounts/login/", respuesta["Location"])

    def test_get_no_descarga(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_el_staff_no_puede_habilitarsela_desde_su_panel(self):
        self.assertNotIn("exportacion_habilitada", GimnasioForm.Meta.fields)
        self.assertNotIn("exportacion_habilitada", GimnasioForm().fields)

    def test_el_freno_rechaza_la_segunda_descarga_seguida(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url).status_code, 200)

        segunda = self.client.post(self.url, follow=True)

        self.assertRedirects(segunda, reverse("gimnasio_editar"))
        self.assertContains(segunda, "Esperá un minuto")

    def test_pasado_el_minuto_vuelve_a_dejar(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            exportacion_habilitada=True,
            exportacion_ultima_descarga=timezone.now() - timedelta(seconds=61),
        )
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url).status_code, 200)

    @override_settings(EXPORTACION_AVISO_EMAIL="dueno@tugimapp.com")
    def test_avisa_por_mail_cuando_exporta_un_gimnasio_real(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        self.client.force_login(self.staff)
        self.client.post(self.url)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["dueno@tugimapp.com"])
        self.assertIn("Gimnasio Ñandú", mail.outbox[0].subject)
        self.assertIn("staff-a", mail.outbox[0].body)

    @override_settings(EXPORTACION_AVISO_EMAIL="dueno@tugimapp.com")
    def test_la_demo_no_avisa(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.client.force_login(self.staff)
        self.client.post(self.url)
        self.assertEqual(mail.outbox, [])

    @override_settings(EXPORTACION_AVISO_EMAIL="dueno@tugimapp.com")
    def test_un_historial_demasiado_grande_no_se_genera_por_la_web(self):
        """Intentarlo es colgar el único worker hasta el timeout de gunicorn.
        Se corta antes, y el aviso lleva el comando listo para copiar."""
        _ensuciar_para_exportar(self.gimnasio, self.staff)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        self.client.force_login(self.staff)

        with patch.object(exportacion, "MAX_FILAS_WEB", 1), patch.object(
            exportacion, "exportar_gimnasio"
        ) as generar:
            respuesta = self.client.post(self.url, follow=True)

        generar.assert_not_called()
        self.assertContains(respuesta, "te lo preparamos nosotros")
        self.assertIn("exportar_gimnasio --gimnasio nandu", mail.outbox[0].body)
        self.gimnasio.refresh_from_db()
        self.assertIsNone(self.gimnasio.exportacion_ultima_descarga)

    def test_sin_mail_configurado_exporta_igual(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.assertEqual(mail.outbox, [])


class BotonEnMiGimnasioTests(ExportacionBase):
    def test_sin_la_casilla_el_boton_existe_pero_deshabilitado(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("gimnasio_editar")).content.decode()
        self.assertIn("Exportar mis datos", html)
        self.assertIn("disabled", html.split('id="tus-datos"')[1])
        self.assertNotIn(reverse("gimnasio_exportar"), html)

    def test_con_la_casilla_el_form_no_va_boosteado(self):
        """`hx-boost` global intercepta el POST y se traga la descarga."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(exportacion_habilitada=True)
        self.client.force_login(self.staff)
        html = self.client.get(reverse("gimnasio_editar")).content.decode()
        seccion = html.split('id="tus-datos"')[1]
        self.assertIn(f'action="{reverse("gimnasio_exportar")}" hx-boost="false"', seccion)
        self.assertNotIn("disabled", seccion.split("</section>")[0])

    @override_settings(SOPORTE_CONTACTO="escribinos al 11-5555-0000")
    def test_el_cartel_dice_a_quien_pedirla_si_esta_configurado(self):
        self.client.force_login(self.staff)
        self.assertContains(
            self.client.get(reverse("gimnasio_editar")), "escribinos al 11-5555-0000"
        )


class ComandoExportarGimnasioTests(ExportacionBase):
    def test_escribe_un_zip_valido_sin_mirar_la_casilla(self):
        _ensuciar_para_exportar(self.gimnasio, self.staff)
        with tempfile.TemporaryDirectory() as carpeta:
            salida = os.path.join(carpeta, "datos.zip")
            call_command(
                "exportar_gimnasio", gimnasio="nandu", salida=salida,
                stdout=io.StringIO(),
            )
            with zipfile.ZipFile(salida) as zf:
                self.assertEqual(len(_filas(zf, "para-importar/alumnos.csv", ",")), 1)

    def test_slug_inexistente_es_error_de_uso(self):
        with self.assertRaises(CommandError):
            call_command("exportar_gimnasio", gimnasio="no-existe", salida="/dev/null")
