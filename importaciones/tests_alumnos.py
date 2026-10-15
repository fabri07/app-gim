"""Tests del servicio de previsualización de ALUMNOS (`importaciones/alumnos.py`).

El lector tiene sus propios tests; acá se prueba lo que agrega el servicio:
email/teléfono/largos, la marca de demo, los duplicados contra la base y dentro
del archivo, `nacen_vencidas`, el JSON y el costo en queries.
"""

import io
from datetime import timedelta

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from importaciones.alumnos import (
    CAMPOS_IMPORTABLES,
    claves_de,
    previsualizar_importacion_alumnos,
)
from importaciones.models import Importacion
from importaciones.services import ImportacionInvalida
from tenants.models import Gimnasio

ENCABEZADOS = ("Nombre", "Apellido", "Email", "Celular")


def _archivo(filas, encabezados=ENCABEZADOS):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(encabezados))
    for fila in filas:
        ws.append(list(fila))
    buffer = io.BytesIO()
    wb.save(buffer)
    return SimpleUploadedFile(
        "alumnos.xlsx", buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class BaseAlumnosTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user(username="staff", password="clave12345")

    def _previsualizar(self, filas, encabezados=ENCABEZADOS):
        importacion = previsualizar_importacion_alumnos(
            gimnasio=self.gimnasio,
            archivo=_archivo(filas, encabezados),
            usuario=self.usuario,
        )
        return importacion.resultado

    def _alumno(self, nombre, apellido, email="", telefono="", **extra):
        return Alumno.objects.create(
            gimnasio=self.gimnasio, nombre=nombre, apellido=apellido,
            email=email, telefono=telefono, **extra,
        )


class CreacionTests(BaseAlumnosTests):
    def test_crea_la_importacion_en_revision_con_el_archivo(self):
        importacion = previsualizar_importacion_alumnos(
            gimnasio=self.gimnasio,
            archivo=_archivo([("Juan", "Pérez", "", "")]),
            usuario=self.usuario,
        )
        self.assertEqual(importacion.tipo, Importacion.Tipo.ALUMNOS)
        self.assertEqual(importacion.estado, Importacion.Estado.EN_REVISION)
        self.assertEqual(importacion.creado_por, self.usuario)
        self.assertEqual(importacion.gimnasio, self.gimnasio)
        self.assertTrue(importacion.archivo.name)

    def test_forma_del_json(self):
        r = self._previsualizar([("Juan", "Pérez", "JUAN@x.com", "11 5555-1234")])
        self.assertEqual(r["version"], 1)
        for clave in (
            "fila_encabezado", "columnas_detectadas", "columnas_ignoradas",
            "advertencias_columnas", "avisos_lectura", "a_crear", "ya_existen",
            "filas_invalidas", "nacen_vencidas", "total_filas_leidas",
        ):
            self.assertIn(clave, r)
        fila = r["a_crear"][0]
        self.assertEqual(fila["fila_excel"], 2)
        self.assertEqual(fila["datos"]["email"], "juan@x.com")
        self.assertEqual(fila["claves"]["email"], "juan@x.com")
        self.assertEqual(fila["claves"]["nombre"], "juan perez")
        self.assertEqual(fila["claves"], claves_de(fila["datos"]))
        self.assertTrue(set(fila["datos"]) <= CAMPOS_IMPORTABLES)

    def test_fechas_van_como_iso_o_none(self):
        hoy = timezone.localdate()
        r = self._previsualizar(
            [("Juan", "Pérez", "", "", hoy - timedelta(days=3), None)],
            encabezados=ENCABEZADOS + ("Inicio del ciclo", "Fecha de nacimiento"),
        )
        datos = r["a_crear"][0]["datos"]
        self.assertEqual(datos["fecha_inicio_ciclo"], (hoy - timedelta(days=3)).isoformat())
        self.assertIsNone(datos["fecha_nacimiento"])

    def test_avisos_de_lectura_llegan_al_json(self):
        filas = [("Juan", "Pérez", "", "")] + [(None,) * 4] * 205
        filas.append(("Ana", "Gómez", "", ""))
        r = self._previsualizar(filas)
        self.assertTrue(r["avisos_lectura"])
        self.assertTrue(any("Dejé de leer" in a for a in r["avisos_lectura"]))

    def test_nombre_de_121_caracteres_se_descarta(self):
        r = self._previsualizar([("J" * 121, "Pérez", "", ""), ("Ana", "A" * 121, "", "")])
        self.assertEqual(r["a_crear"], [])
        self.assertEqual(len(r["filas_invalidas"]), 2)
        self.assertIn("120", r["filas_invalidas"][0]["motivo"])

    def test_nombre_de_120_caracteres_entra(self):
        r = self._previsualizar([("J" * 120, "Pérez", "", "")])
        self.assertEqual(len(r["a_crear"]), 1)


class ValoresTests(BaseAlumnosTests):
    def test_email_invalido_queda_vacio_con_aviso_y_observaciones(self):
        r = self._previsualizar([("Juan", "Pérez", "no-es-mail", "")])
        fila = r["a_crear"][0]
        self.assertEqual(fila["datos"]["email"], "")
        self.assertTrue(any("email" in a.lower() for a in fila["avisos"]))
        self.assertEqual(
            fila["datos"]["observaciones"],
            "Datos del archivo que no se pudieron cargar: Email «no-es-mail».",
        )

    def test_dos_emails_en_una_celda_es_invalido(self):
        r = self._previsualizar([("Juan", "Pérez", "a@x.com, b@x.com", "")])
        self.assertEqual(r["a_crear"][0]["datos"]["email"], "")

    def test_telefono_de_31_caracteres_queda_vacio(self):
        tel = "1" * 31
        r = self._previsualizar([("Juan", "Pérez", "", tel)])
        fila = r["a_crear"][0]
        self.assertEqual(fila["datos"]["telefono"], "")
        self.assertTrue(fila["avisos"])
        self.assertIn(f"Teléfono «{tel}»", fila["datos"]["observaciones"])
        self.assertIsNone(fila["claves"]["telefono"])

    def test_telefono_de_30_caracteres_entra(self):
        r = self._previsualizar([("Juan", "Pérez", "", "1" * 30)])
        self.assertEqual(r["a_crear"][0]["datos"]["telefono"], "1" * 30)

    def test_largo_de_opcional_desbordado_va_a_observaciones(self):
        largo = "x" * 201
        r = self._previsualizar(
            [("Juan", "Pérez", "", "", largo)],
            encabezados=ENCABEZADOS + ("Deportes",),
        )
        fila = r["a_crear"][0]
        self.assertEqual(fila["datos"]["deportes_practica"], "")
        self.assertIn(largo, fila["datos"]["observaciones"])

    def test_observaciones_existentes_y_linea_van_separadas_por_salto(self):
        r = self._previsualizar(
            [("Juan", "Pérez", "malo", "", "Lesión de rodilla")],
            encabezados=ENCABEZADOS + ("Observaciones",),
        )
        self.assertEqual(
            r["a_crear"][0]["datos"]["observaciones"],
            "Lesión de rodilla\nDatos del archivo que no se pudieron cargar: Email «malo».",
        )

    def test_junta_lo_del_lector_y_lo_propio_en_una_linea(self):
        r = self._previsualizar(
            [("Juan", "Pérez", "malo", "", "X")],
            encabezados=ENCABEZADOS + ("Sexo",),
        )
        obs = r["a_crear"][0]["datos"]["observaciones"]
        self.assertEqual(obs.count("Datos del archivo"), 1)
        self.assertIn("Sexo «X»", obs)
        self.assertIn("Email «malo»", obs)
        self.assertTrue(obs.endswith("."))

    def test_marca_demo_se_reescribe(self):
        r = self._previsualizar(
            [("Juan", "Pérez", "", "", "[demo]")],
            encabezados=ENCABEZADOS + ("Observaciones",),
        )
        fila = r["a_crear"][0]
        self.assertEqual(fila["datos"]["observaciones"], "[demo] (importado)")
        self.assertTrue(fila["avisos"])

    def test_fila_de_ejemplo_se_descarta(self):
        from importaciones.parsing import MARCA_FILA_EJEMPLO

        r = self._previsualizar(
            [("Juan", "Pérez", "", "", MARCA_FILA_EJEMPLO)],
            encabezados=ENCABEZADOS + ("Observaciones",),
        )
        self.assertEqual(r["a_crear"], [])
        self.assertEqual(len(r["filas_invalidas"]), 1)


class ErroresDeArchivoTests(BaseAlumnosTests):
    def test_cuenta_demo_se_rechaza_antes_de_leer(self):
        self.gimnasio.es_demo = True
        self.gimnasio.save(update_fields=["es_demo"])
        basura = SimpleUploadedFile("x.xlsx", b"no es un excel")
        with self.assertRaises(ImportacionInvalida) as ctx:
            previsualizar_importacion_alumnos(
                gimnasio=self.gimnasio, archivo=basura, usuario=self.usuario
            )
        self.assertIn("prueba", str(ctx.exception))
        self.assertEqual(Importacion.objects.count(), 0)

    def test_archivo_corrupto(self):
        with self.assertRaises(ImportacionInvalida):
            previsualizar_importacion_alumnos(
                gimnasio=self.gimnasio,
                archivo=SimpleUploadedFile("x.xlsx", b"no es un excel"),
                usuario=self.usuario,
            )

    def test_sin_columna_de_nombre(self):
        with self.assertRaises(ImportacionInvalida) as ctx:
            self._previsualizar([("a", "b")], encabezados=("Plan", "Monto"))
        self.assertIn("nombre", str(ctx.exception).lower())

    def test_csv_en_utf16_pide_guardarlo_de_otra_forma(self):
        archivo = SimpleUploadedFile("x.csv", "Nombre;Apellido\nJuan;Pérez".encode("utf-16"))
        with self.assertRaises(ImportacionInvalida) as ctx:
            previsualizar_importacion_alumnos(
                gimnasio=self.gimnasio, archivo=archivo, usuario=self.usuario
            )
        self.assertIn("UTF-8", str(ctx.exception))

    def test_demasiadas_filas(self):
        filas = [(f"N{i}", f"A{i}", "", "") for i in range(1001)]
        with self.assertRaises(ImportacionInvalida) as ctx:
            self._previsualizar(filas)
        self.assertIn("partes", str(ctx.exception))

    def test_si_no_se_puede_rebobinar_el_archivo_levanta(self):
        from unittest import mock

        hoja = previsualizar_importacion_alumnos(
            gimnasio=self.gimnasio, archivo=_archivo([("Juan", "Pérez", "", "")]),
            usuario=self.usuario,
        )
        self.assertTrue(hoja)

        class SinSeek(SimpleUploadedFile):
            def seek(self, *a, **k):
                raise OSError("no se puede")

        archivo = SinSeek("x.xlsx", _archivo([("Ana", "Gómez", "", "")]).read())
        from importaciones import alumnos as modulo

        real = modulo.parsear_archivo_alumnos
        with mock.patch.object(
            modulo, "parsear_archivo_alumnos",
            side_effect=lambda a, hoy: real(io.BytesIO(archivo.read()), hoy=hoy),
        ), self.assertRaises(ImportacionInvalida):
            previsualizar_importacion_alumnos(
                gimnasio=self.gimnasio, archivo=archivo, usuario=self.usuario
            )

    def test_csv_valido(self):
        archivo = SimpleUploadedFile(
            "x.csv", "Nombre;Apellido\nJuan;Pérez\n".encode("utf-8")
        )
        importacion = previsualizar_importacion_alumnos(
            gimnasio=self.gimnasio, archivo=archivo, usuario=self.usuario
        )
        self.assertEqual(len(importacion.resultado["a_crear"]), 1)


class DuplicadosTests(BaseAlumnosTests):
    def test_homonimo_con_email_invalido_se_crea_con_aviso(self):
        self._alumno("Juan", "Pérez", email="juan@x.com")
        r = self._previsualizar([("Juan", "Pérez", "no-es-mail", "")])
        self.assertEqual(r["ya_existen"], [])
        fila = r["a_crear"][0]
        self.assertTrue(any("mismo nombre" in a for a in fila["avisos"]))

    def test_mismo_email_en_mayusculas_y_mismo_nombre(self):
        existente = self._alumno("Juan", "Pérez", email="juan@x.com")
        r = self._previsualizar([("Juan", "Pérez", "JUAN@X.COM", "")])
        self.assertEqual(r["a_crear"], [])
        ya = r["ya_existen"][0]
        self.assertEqual(ya["origen"], "base")
        self.assertEqual(ya["alumno_id"], existente.pk)
        self.assertEqual((ya["nombre"], ya["apellido"]), ("Juan", "Pérez"))
        self.assertTrue(ya["motivo"])

    def test_telefono_con_y_sin_9(self):
        self._alumno("Juan", "Pérez", telefono="+54 9 11 5555-1234")
        r = self._previsualizar([("Juan", "Pérez", "", "011 5555-1234")])
        self.assertEqual(len(r["ya_existen"]), 1)

    def test_alumno_inactivo_tambien_cuenta(self):
        self._alumno("Juan", "Pérez", email="juan@x.com", estado=Alumno.Estado.INACTIVO)
        r = self._previsualizar([("Juan", "Pérez", "juan@x.com", "")])
        self.assertEqual(len(r["ya_existen"]), 1)

    def test_sin_contacto_el_nombre_basta(self):
        self._alumno("Juan", "Pérez", email="juan@x.com")
        r = self._previsualizar([("Pérez", "Juan", "", "")])  # nombre en otro orden
        self.assertEqual(len(r["ya_existen"]), 1)
        self.assertEqual(r["a_crear"], [])

    def test_hermano_con_mismo_telefono_y_otro_nombre_se_crea_con_aviso(self):
        self._alumno("Juan", "Pérez", telefono="1155551234")
        r = self._previsualizar([("Pedro", "Pérez", "", "1155551234")])
        self.assertEqual(r["ya_existen"], [])
        self.assertEqual(
            r["a_crear"][0]["avisos"], ["Comparte teléfono con Pérez, Juan"]
        )

    def test_mismo_email_otro_nombre_avisa_email(self):
        self._alumno("Juan", "Pérez", email="f@x.com")
        r = self._previsualizar([("Pedro", "Pérez", "f@x.com", "")])
        self.assertEqual(r["a_crear"][0]["avisos"], ["Comparte email con Pérez, Juan"])

    def test_mismo_nombre_otro_contacto_se_crea_con_aviso(self):
        self._alumno("Juan", "Pérez", email="juan@x.com")
        r = self._previsualizar([("Juan", "Pérez", "otro@x.com", "")])
        self.assertEqual(r["ya_existen"], [])
        self.assertEqual(
            r["a_crear"][0]["avisos"],
            ["Hay un alumno con el mismo nombre (Pérez, Juan): revisá que no sea el mismo"],
        )

    def test_aislamiento_otro_gimnasio_no_marca_duplicado(self):
        otro = Gimnasio.objects.create(nombre="Otro", slug="otro")
        Alumno.objects.create(
            gimnasio=otro, nombre="Juan", apellido="Pérez", email="juan@x.com"
        )
        r = self._previsualizar([("Juan", "Pérez", "juan@x.com", "")])
        self.assertEqual(r["ya_existen"], [])
        self.assertEqual(len(r["a_crear"]), 1)
        self.assertEqual(r["a_crear"][0]["avisos"], [])

    def test_duplicado_dentro_del_archivo_gana_la_primera(self):
        r = self._previsualizar([
            ("Juan", "Pérez", "juan@x.com", ""),
            ("Juan", "Pérez", "JUAN@x.com", ""),
        ])
        self.assertEqual(len(r["a_crear"]), 1)
        ya = r["ya_existen"][0]
        self.assertEqual(ya["origen"], "archivo")
        self.assertEqual(ya["fila_excel"], 3)
        self.assertIsNone(ya["alumno_id"])
        self.assertIn("fila 2", ya["motivo"])

    def test_dentro_del_archivo_nombre_sin_contacto(self):
        r = self._previsualizar([("Juan", "Pérez", "", ""), ("Juan", "Pérez", "", "")])
        self.assertEqual(len(r["a_crear"]), 1)
        self.assertEqual(len(r["ya_existen"]), 1)

    def test_dentro_del_archivo_hermano_se_crea_con_aviso(self):
        r = self._previsualizar([
            ("Juan", "Pérez", "", "1155551234"),
            ("Pedro", "Pérez", "", "1155551234"),
        ])
        self.assertEqual(len(r["a_crear"]), 2)
        self.assertEqual(r["a_crear"][1]["avisos"], ["Comparte teléfono con Pérez, Juan"])


class NacenVencidasTests(BaseAlumnosTests):
    def _con_inicio(self, dias_atras):
        hoy = timezone.localdate()
        return self._previsualizar(
            [("Juan", "Pérez", "", "", hoy - timedelta(days=dias_atras)),
             ("Ana", "Gómez", "", "", None)],
            encabezados=ENCABEZADOS + ("Inicio del ciclo",),
        )

    def _tolerancia(self, dias, activacion="ayer"):
        # `update()` y no `save()`: la señal estampa la activación en hoy al
        # prender la tolerancia, y acá hace falta fijarla a mano.
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=dias,
            fecha_activacion_bloqueo=(
                timezone.localdate() - timedelta(days=400)
                if activacion == "ayer" else activacion
            ),
        )
        self.gimnasio.refresh_from_db()

    def test_sin_tolerancia_es_cero(self):
        self.assertEqual(self._con_inicio(20)["nacen_vencidas"], 0)

    def test_borde_exacto_cuenta(self):
        # `pagos.acceso` bloquea con periodo_inicio <= hoy - tolerancia.
        self._tolerancia(10)
        self.assertEqual(self._con_inicio(20)["nacen_vencidas"], 1)
        self.assertEqual(self._con_inicio(10)["nacen_vencidas"], 1)
        self.assertEqual(self._con_inicio(9)["nacen_vencidas"], 0)

    def test_tolerancia_cero_con_inicio_hoy_cuenta(self):
        self._tolerancia(0)
        self.assertEqual(self._con_inicio(0)["nacen_vencidas"], 1)

    def test_sin_fecha_de_activacion_no_cuenta(self):
        self._tolerancia(10, activacion=None)
        self.assertEqual(self._con_inicio(20)["nacen_vencidas"], 0)

    def test_activacion_posterior_al_periodo_no_cuenta(self):
        self._tolerancia(10, activacion=timezone.localdate() - timedelta(days=5))
        self.assertEqual(self._con_inicio(20)["nacen_vencidas"], 0)

    def test_inicio_futuro_no_cuenta(self):
        self._tolerancia(0)
        self.assertEqual(self._con_inicio(-5)["nacen_vencidas"], 0)


class NaceBloqueadaTests(TestCase):
    def test_usa_el_periodo_real_del_cron_no_el_ancla(self):
        from pagos.acceso import nace_bloqueada

        hoy = timezone.localdate()
        gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        Gimnasio.objects.filter(pk=gimnasio.pk).update(
            dias_tolerancia_pago=10,
            fecha_activacion_bloqueo=hoy - timedelta(days=400),
        )
        gimnasio.refresh_from_db()
        # Ancla de hace 30 días: el ciclo vigente arrancó hace 2, no bloquea.
        self.assertFalse(nace_bloqueada(gimnasio, hoy - timedelta(days=30), hoy))
        self.assertTrue(nace_bloqueada(gimnasio, hoy - timedelta(days=10), hoy))
        self.assertFalse(nace_bloqueada(gimnasio, None, hoy))


class EscalaTests(BaseAlumnosTests):
    def _queries(self, filas):
        with CaptureQueriesContext(connection) as consultas:
            previsualizar_importacion_alumnos(
                gimnasio=self.gimnasio, archivo=_archivo(filas), usuario=self.usuario
            )
        return len(consultas)

    def test_costo_no_crece_con_las_filas(self):
        def filas(n, desde=0):
            return [
                (f"Nom{i}", f"Ape{i}", f"a{i}@x.com", f"11555{i:05d}")
                for i in range(desde, desde + n)
            ]

        chico = self._queries(filas(20))
        grande = self._queries(filas(200, 1000))
        self.assertLessEqual(abs(grande - chico), 2)

    def test_costo_no_crece_con_los_alumnos_existentes(self):
        filas = [(f"Nom{i}", f"Ape{i}", f"a{i}@x.com", "") for i in range(20)]
        for i in range(5):
            self._alumno(f"Ex{i}", f"Ist{i}", email=f"e{i}@x.com")
        pocos = self._queries(filas)
        for i in range(5, 50):
            self._alumno(f"Ex{i}", f"Ist{i}", email=f"e{i}@x.com")
        muchos = self._queries(filas)
        self.assertLessEqual(abs(muchos - pocos), 2)
