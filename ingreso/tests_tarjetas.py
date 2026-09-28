"""Tests de las tarjetas imprimibles con el QR de ingreso.

Dos capas, como `rutinas/pdf.py` ↔ `RutinaAsignadaPdfView`:

- `ingreso/tarjetas.py` es Django-free (`SimpleTestCase`): el PDF se abre con
  `pdfplumber` (ya es dependencia del importador) y se miden las posiciones
  REALES de lo que quedó dibujado, no las constantes del módulo.
- Las vistas (`TestCase`): cada tarjeta es una llave al portador, así que se
  fija quién puede pedirlas, cuáles entran y que la respuesta no quede en
  ningún caché.
"""

import io
import re
from unittest.mock import patch

import pdfplumber
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from alumnos.models import Alumno
from ingreso import qr, services, tarjetas, tokens
from ingreso.models import Credencial
from ingreso.tests_vistas import _BaseVistas
from tenants.models import Gimnasio

MM_POR_PUNTO = 25.4 / 72
LARGO_MAXIMO_VALOR = Credencial._meta.get_field("valor").max_length


def _pdf(pdf_bytes):
    return pdfplumber.open(io.BytesIO(pdf_bytes))


def _tarjetas(cantidad, nombre="Juan P."):
    return [tarjetas.Tarjeta(nombre=nombre, valor=tokens.generar()) for _ in range(cantidad)]


def _mm(puntos):
    return puntos * MM_POR_PUNTO


class GenerarPdfCredencialesTests(SimpleTestCase):
    def test_devuelve_un_pdf(self):
        pdf = tarjetas.generar_pdf_credenciales("Gimnasio A", _tarjetas(1))

        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_diez_tarjetas_por_hoja(self):
        for cantidad, hojas in ((1, 1), (10, 1), (11, 2), (25, 3)):
            with self.subTest(cantidad=cantidad), _pdf(
                tarjetas.generar_pdf_credenciales("G", _tarjetas(cantidad))
            ) as documento:
                self.assertEqual(len(documento.pages), hojas)
                self.assertEqual(
                    sum(len(p.images) for p in documento.pages), cantidad
                )

    def test_sin_tarjetas_da_un_pdf_valido_y_no_revienta(self):
        with _pdf(tarjetas.generar_pdf_credenciales("G", [])) as documento:
            self.assertEqual(len(documento.pages), 1)
            self.assertIn("No hay", documento.pages[0].extract_text())

    def test_cada_tarjeta_lleva_el_qr_de_SU_valor(self):
        lista = _tarjetas(3)
        with patch.object(tarjetas.qr, "png_de", wraps=qr.png_de) as espia:
            tarjetas.generar_pdf_credenciales("G", lista)

        self.assertEqual(
            [llamada.args[0] for llamada in espia.call_args_list],
            [t.valor for t in lista],
        )

    def test_el_qr_impreso_no_baja_de_dos_centimetros_y_medio(self):
        """Medido sobre el PDF: el lado de la imagen menos la zona silenciosa.
        Más chico, la cámara del lector tiene que acercarse tanto que pierde
        el foco. Con el `valor` más largo que admite el modelo, que es el que
        da más módulos y por lo tanto el código más apretado."""
        valor = "x" * LARGO_MAXIMO_VALOR
        pdf = tarjetas.generar_pdf_credenciales(
            "G", [tarjetas.Tarjeta(nombre="Ana", valor=valor)]
        )
        modulos = qr.modulos_de(valor)
        with _pdf(pdf) as documento:
            imagen = documento.pages[0].images[0]
        lado_mm = _mm(imagen["x1"] - imagen["x0"])
        alto_mm = _mm(imagen["bottom"] - imagen["top"])
        codigo_mm = lado_mm * modulos / (modulos + 2 * qr.BORDE)

        self.assertAlmostEqual(lado_mm, alto_mm, places=1)  # cuadrado, sin deformar
        self.assertGreaterEqual(codigo_mm, 25)
        self.assertAlmostEqual(codigo_mm, tarjetas.lado_del_codigo_mm(valor), places=1)
        # La cuenta del módulo y el mínimo que declara, para el caso normal.
        self.assertGreaterEqual(
            tarjetas.lado_del_codigo_mm(tokens.generar()), tarjetas.MINIMO_CODIGO_MM
        )
        self.assertGreaterEqual(tarjetas.MINIMO_CODIGO_MM, 25)

    def test_ningun_qr_queda_partido_ni_sale_de_su_tarjeta(self):
        """Cada QR entra entero en su hoja y en el rectángulo de SU tarjeta:
        el corte por la línea punteada no lo parte."""
        cantidad = 23
        with _pdf(tarjetas.generar_pdf_credenciales("G", _tarjetas(cantidad))) as documento:
            indice = 0
            for pagina in documento.pages:
                ancho_hoja, alto_hoja = _mm(pagina.width), _mm(pagina.height)
                for imagen in sorted(pagina.images, key=lambda i: (round(i["top"]), i["x0"])):
                    x_tarjeta, y_tarjeta = tarjetas.posicion_de(indice)
                    x0, x1 = _mm(imagen["x0"]), _mm(imagen["x1"])
                    y0, y1 = _mm(imagen["top"]), _mm(imagen["bottom"])
                    with self.subTest(tarjeta=indice):
                        self.assertGreaterEqual(x0, 0)
                        self.assertGreaterEqual(y0, 0)
                        self.assertLessEqual(x1, ancho_hoja)
                        self.assertLessEqual(y1, alto_hoja)
                        self.assertGreaterEqual(x0, x_tarjeta)
                        self.assertGreaterEqual(y0, y_tarjeta)
                        self.assertLessEqual(x1, x_tarjeta + tarjetas.ANCHO_TARJETA_MM)
                        self.assertLessEqual(y1, y_tarjeta + tarjetas.ALTO_TARJETA_MM)
                    indice += 1
        self.assertEqual(indice, cantidad)

    def test_las_tarjetas_entran_en_la_hoja(self):
        x, y = tarjetas.posicion_de(tarjetas.TARJETAS_POR_HOJA - 1)
        self.assertLessEqual(x + tarjetas.ANCHO_TARJETA_MM, 210)
        self.assertLessEqual(y + tarjetas.ALTO_TARJETA_MM, 297)

    def test_nombres_con_tilde_y_enie_salen_bien(self):
        lista = [
            tarjetas.Tarjeta(nombre="Belén M.", valor=tokens.generar()),
            tarjetas.Tarjeta(nombre="Lucía Muñoz", valor=tokens.generar()),
        ]
        pdf = tarjetas.generar_pdf_credenciales("Gimnasio Ñandú Güemes", lista)
        with _pdf(pdf) as documento:
            texto = documento.pages[0].extract_text()

        self.assertIn("Belén M.", texto)
        self.assertIn("Lucía Muñoz", texto)
        self.assertIn("Gimnasio Ñandú Güemes", texto)

    def test_lo_que_cp1252_no_tiene_se_degrada_sin_reventar(self):
        """Un solo nombre raro no puede tumbar las tarjetas de todo el
        gimnasio."""
        lista = [
            tarjetas.Tarjeta(nombre="Łukasz Ș.", valor=tokens.generar()),
            tarjetas.Tarjeta(nombre="Ana 💪", valor=tokens.generar()),
        ]
        pdf = tarjetas.generar_pdf_credenciales("Gym 🏋 Ţara", lista)
        with _pdf(pdf) as documento:
            texto = documento.pages[0].extract_text()

        self.assertIn("?ukasz S.", texto)
        self.assertIn("Ana ?", texto)
        self.assertIn("Gym ? Tara", texto)

    def test_el_valor_no_se_imprime_en_texto(self):
        """Tipeado en un lector, abre la puerta sin la tarjeta."""
        lista = _tarjetas(2)
        with _pdf(tarjetas.generar_pdf_credenciales("G", lista)) as documento:
            texto = documento.pages[0].extract_text()
        for tarjeta in lista:
            self.assertNotIn(tarjeta.valor, texto)
            self.assertNotIn(tarjeta.valor[:12], texto)

    def test_un_nombre_de_gimnasio_largo_no_pisa_el_qr(self):
        largo = "Centro de Entrenamiento Funcional y Musculación Integral del Oeste"
        with _pdf(tarjetas.generar_pdf_credenciales(largo, _tarjetas(1, "Maximiliano Alejandro R."))) as documento:
            pagina = documento.pages[0]
            borde_qr = pagina.images[0]["x0"]
            palabras = pagina.extract_words()

        self.assertTrue(palabras)
        for palabra in palabras:
            if palabra["top"] < pagina.images[0]["bottom"]:
                self.assertLessEqual(palabra["x1"], borde_qr, palabra["text"])


# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------


def _forms_hacia(html, url):
    """Los `<form ...>` (solo el tag de apertura) cuyo `action` es `url`."""
    return [
        tag for tag in re.findall(r"<form\b[^>]*>", html)
        if f'action="{url}"' in tag
    ]


class _BaseTarjetas(_BaseVistas):
    def setUp(self):
        super().setUp()
        self.credencial = services.generar_credencial_qr(self.alumno)
        self.credencial_ajena = services.generar_credencial_qr(self.alumno_ajeno)

    def _assert_pdf_privado(self, respuesta):
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "application/pdf")
        self.assertTrue(respuesta.content.startswith(b"%PDF"))
        cache = respuesta["Cache-Control"]
        self.assertIn("no-store", cache)
        self.assertIn("private", cache)
        disposicion = respuesta["Content-Disposition"]
        self.assertTrue(disposicion.startswith("attachment;"), disposicion)
        return disposicion

    def _texto(self, respuesta):
        with _pdf(respuesta.content) as documento:
            return "\n".join(p.extract_text() for p in documento.pages)


class TarjetaDeUnAlumnoTests(_BaseTarjetas):
    def _url(self, alumno=None):
        return reverse("ingreso:tarjeta_alumno", args=[(alumno or self.alumno).pk])

    def test_anonimo_es_redirigido_al_login(self):
        url = self._url()
        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}")

    def test_alumno_recibe_403(self):
        self._login("alumno-a")
        self.assertEqual(self.client.post(self._url()).status_code, 403)

    def test_solo_por_post(self):
        self._login()
        self.assertEqual(self.client.get(self._url()).status_code, 405)

    def test_staff_descarga_la_tarjeta_de_su_alumno(self):
        self._login()
        with patch("ingreso.tarjetas.qr.png_de", wraps=qr.png_de) as espia:
            respuesta = self.client.post(self._url())

        disposicion = self._assert_pdf_privado(respuesta)
        self.assertEqual([c.args[0] for c in espia.call_args_list], [self.credencial.valor])
        texto = self._texto(respuesta)
        self.assertIn(services.nombre_corto(self.alumno), texto)
        self.assertIn(self.gimnasio.nombre, texto)
        # Ni el apellido completo ni el valor, y un nombre de archivo sin datos
        # del alumno.
        self.assertNotIn("Perez", texto)
        self.assertNotIn(self.credencial.valor, texto)
        self.assertNotIn("Perez", disposicion)
        self.assertNotIn("Juan", disposicion)
        self.assertNotIn(self.credencial.valor, disposicion)

    def test_alumno_de_otro_gimnasio_da_404(self):
        self._login()
        respuesta = self.client.post(self._url(self.alumno_ajeno))
        self.assertEqual(respuesta.status_code, 404)

    def test_credencial_dada_de_baja_no_se_imprime(self):
        services.dar_de_baja_credencial(self.credencial)
        self._login()
        with patch("ingreso.tarjetas.qr.png_de", wraps=qr.png_de) as espia:
            respuesta = self.client.post(self._url(), follow=True)

        self.assertRedirects(respuesta, reverse("alumnos:detalle", args=[self.alumno.pk]))
        self.assertFalse(espia.called)
        self.assertContains(respuesta, "no tiene un código que abra la puerta")

    def test_alumno_inactivo_no_se_imprime(self):
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save()
        self._login()
        with patch("ingreso.tarjetas.qr.png_de", wraps=qr.png_de) as espia:
            respuesta = self.client.post(self._url())

        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(espia.called)

    def test_alumno_sin_credencial_no_revienta(self):
        sin_codigo = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Sin", apellido="Codigo")
        self._login()
        respuesta = self.client.post(self._url(sin_codigo))
        self.assertEqual(respuesta.status_code, 302)

    def test_la_ficha_ofrece_imprimir_fuera_del_boost(self):
        """Bajo el `hx-boost` global, htmx se traga la descarga."""
        self._login()
        respuesta = self.client.get(reverse("alumnos:detalle", args=[self.alumno.pk]))

        forms = _forms_hacia(respuesta.content.decode(), self._url())
        self.assertEqual(len(forms), 1)
        self.assertIn('method="post"', forms[0])
        self.assertIn('hx-boost="false"', forms[0])

    def test_la_ficha_no_ofrece_imprimir_un_codigo_dado_de_baja(self):
        services.dar_de_baja_credencial(self.credencial)
        self._login()
        respuesta = self.client.get(reverse("alumnos:detalle", args=[self.alumno.pk]))
        self.assertEqual(_forms_hacia(respuesta.content.decode(), self._url()), [])


class TarjetasDelGimnasioTests(_BaseTarjetas):
    def _url_pagina(self):
        return reverse("ingreso:tarjetas")

    def _url_pdf(self):
        return reverse("ingreso:tarjetas_pdf")

    def _alumno_con_codigo(self, nombre, apellido, **extra):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre=nombre, apellido=apellido, **extra
        )
        return alumno, services.generar_credencial_qr(alumno)

    def test_guardias(self):
        for url, metodo in ((self._url_pagina(), "get"), (self._url_pdf(), "post")):
            with self.subTest(url=url):
                self.client.logout()
                pedir = getattr(self.client, metodo)
                self.assertRedirects(pedir(url), f"{reverse('login')}?next={url}")
                self._login("alumno-a")
                self.assertEqual(pedir(url).status_code, 403)

    def test_el_pdf_solo_por_post(self):
        self._login()
        self.assertEqual(self.client.get(self._url_pdf()).status_code, 405)

    def test_imprime_solo_las_vigentes_de_su_gimnasio(self):
        """Ni la del otro gimnasio, ni una dada de baja, ni la de un alumno
        inactivo, ni la de un alumno sin código (que no rompe nada)."""
        _, baja = self._alumno_con_codigo("Dado", "DeBaja")
        services.dar_de_baja_credencial(baja)
        _, de_inactivo = self._alumno_con_codigo(
            "Ido", "Inactivo", estado=Alumno.Estado.INACTIVO
        )
        Alumno.objects.create(gimnasio=self.gimnasio, nombre="Sin", apellido="Codigo")
        belen, de_belen = self._alumno_con_codigo("Belén", "Muñoz")
        self._login()

        with patch("ingreso.tarjetas.qr.png_de", wraps=qr.png_de) as espia:
            respuesta = self.client.post(self._url_pdf(), {"tanda": "1"})

        disposicion = self._assert_pdf_privado(respuesta)
        impresos = {c.args[0] for c in espia.call_args_list}
        self.assertEqual(impresos, {self.credencial.valor, de_belen.valor})
        self.assertNotIn(self.credencial_ajena.valor, impresos)
        self.assertNotIn(baja.valor, impresos)
        self.assertNotIn(de_inactivo.valor, impresos)
        texto = self._texto(respuesta)
        self.assertIn("Belén M.", texto)
        self.assertNotIn("Ajeno", texto)
        self.assertNotIn("Muñoz", disposicion)

    def test_sin_codigos_vigentes_avisa_y_no_genera(self):
        services.dar_de_baja_credencial(self.credencial)
        self._login()
        respuesta = self.client.get(self._url_pagina())
        self.assertContains(respuesta, "Generar códigos de ingreso")
        self.assertEqual(_forms_hacia(respuesta.content.decode(), self._url_pdf()), [])

        respuesta = self.client.post(self._url_pdf(), {"tanda": "1"})
        self.assertRedirects(respuesta, self._url_pagina())

    def test_tanda_invalida_vuelve_a_la_pagina(self):
        self._login()
        for tanda in ("0", "2", "x", ""):
            with self.subTest(tanda=tanda):
                respuesta = self.client.post(self._url_pdf(), {"tanda": tanda})
                self.assertRedirects(respuesta, self._url_pagina())

    def test_se_imprime_por_tandas_para_no_colgar_el_worker(self):
        for i in range(5):
            self._alumno_con_codigo(f"N{i}", f"A{i}")
        self._login()
        with patch("ingreso.views.TARJETAS_POR_TANDA", 2):
            pagina = self.client.get(self._url_pagina())
            forms = _forms_hacia(pagina.content.decode(), self._url_pdf())
            with patch("ingreso.tarjetas.qr.png_de", wraps=qr.png_de) as espia:
                respuestas = [
                    self.client.post(self._url_pdf(), {"tanda": str(t)}) for t in (1, 2, 3)
                ]
            por_tanda = [2, 2, 2]

        # 6 vigentes (Juan + 5) en tandas de 2: tres botones, tres PDF, sin
        # repetir ni perder ninguna tarjeta.
        self.assertEqual(len(forms), 3)
        for respuesta in respuestas:
            self._assert_pdf_privado(respuesta)
        valores = [c.args[0] for c in espia.call_args_list]
        self.assertEqual(len(valores), sum(por_tanda))
        self.assertEqual(len(set(valores)), sum(por_tanda))
        self.assertIn("-3.pdf", respuestas[2]["Content-Disposition"])

    def test_la_pagina_ofrece_cada_tanda_fuera_del_boost(self):
        self._login()
        forms = _forms_hacia(
            self.client.get(self._url_pagina()).content.decode(), self._url_pdf()
        )
        self.assertEqual(len(forms), 1)
        for form in forms:
            self.assertIn('method="post"', form)
            self.assertIn('hx-boost="false"', form)

    def test_el_listado_de_alumnos_lleva_a_imprimir(self):
        self._login()
        respuesta = self.client.get(reverse("alumnos:listado"))
        self.assertContains(respuesta, f'href="{self._url_pagina()}"')

    def test_costo_fijo_en_queries(self):
        """Una query trae credencial y alumno juntos: el costo no crece con
        la cantidad de tarjetas. Y el PDF no sale vacío."""
        self._login()

        def medir():
            with CaptureQueriesContext(connection) as consultas:
                respuesta = self.client.post(self._url_pdf(), {"tanda": "1"})
            self._assert_pdf_privado(respuesta)
            with _pdf(respuesta.content) as documento:
                imagenes = sum(len(p.images) for p in documento.pages)
            return len(consultas), imagenes

        # El primer request del día escribe la actividad (`PlataformaMiddleware`):
        # se descarta para comparar solo lo que hace la vista.
        medir()
        pocas, imagenes_pocas = medir()
        for i in range(12):
            self._alumno_con_codigo(f"N{i}", f"A{i}")
        muchas, imagenes_muchas = medir()

        self.assertEqual(imagenes_pocas, 1)
        self.assertEqual(imagenes_muchas, 13)
        self.assertEqual(pocas, muchas)

    def test_con_la_cuenta_suspendida_no_se_imprime(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA
        )
        self._login()
        with patch("ingreso.tarjetas.qr.png_de", wraps=qr.png_de) as espia:
            self.client.post(self._url_pdf(), {"tanda": "1"})
        self.assertFalse(espia.called)
