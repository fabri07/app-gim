import ast
import importlib
import importlib.util
import inspect
import io
import json
import re
import socket
import sys
import tempfile
import urllib.error
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from django.utils import timezone
from PIL import Image
import segno

from alumnos.models import Alumno
from ingreso import qr, services, tokens
from ingreso.management.commands.purgar_ingresos import (
    MESES_MINIMOS,
    restar_meses,
)
from ingreso.models import (
    ConfiguracionIngreso,
    Credencial,
    Dispositivo,
    EventoIngreso,
    obtener_configuracion,
)
from tenants.models import Gimnasio


class ConfiguracionIngresoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

    def test_obtener_configuracion_es_idempotente(self):
        """Única vía de acceso, y no puede crear una segunda fila: hay una
        `UniqueConstraint` por gimnasio y el resto del código toma esta fila
        como la configuración del gimnasio."""
        primera = obtener_configuracion(self.gimnasio)
        segunda = obtener_configuracion(self.gimnasio)

        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(ConfiguracionIngreso.objects.count(), 1)

    def test_los_defaults_no_bloquean_a_nadie(self):
        """Prender el control de ingreso no puede cambiarle el comportamiento a
        un gimnasio que no configuró nada, igual que `dias_tolerancia_pago`."""
        config = obtener_configuracion(self.gimnasio)

        self.assertFalse(config.bloquea_por_deuda)
        self.assertFalse(config.exige_reserva)

    def test_la_configuracion_de_un_gimnasio_no_se_ve_desde_el_otro(self):
        obtener_configuracion(self.gimnasio)

        self.assertFalse(
            ConfiguracionIngreso.objects.for_gimnasio(self.otro).exists()
        )


class TokenDeDispositivoTests(SimpleTestCase):
    """Django-free: la propiedad que importa es criptográfica, no de ORM."""

    def test_cada_token_es_distinto(self):
        self.assertNotEqual(tokens.generar(), tokens.generar())

    def test_el_token_tiene_entropia_suficiente(self):
        """32 bytes de `secrets.token_urlsafe`. Es lo que hace que un `sha256`
        pelado no sea atacable por diccionario y no haga falta PBKDF2, que
        costaría ~100 ms del único worker de gunicorn por request."""
        self.assertGreaterEqual(len(tokens.generar()), 40)

    def test_el_hash_es_estable_y_de_64_caracteres(self):
        token = tokens.generar()

        self.assertEqual(tokens.hash_de(token), tokens.hash_de(token))
        self.assertEqual(len(tokens.hash_de(token)), 64)

    def test_un_token_alterado_da_otro_hash(self):
        token = tokens.generar()

        self.assertNotEqual(tokens.hash_de(token), tokens.hash_de(token + "x"))


class DispositivoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")

    def test_el_token_no_queda_en_claro_en_ninguna_columna(self):
        """Un volcado de la base no puede contener una llave usable de la
        puerta. Se buscan los valores de TODAS las columnas, no solo de la que
        se supone que lo guarda."""
        token = tokens.generar()
        dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta principal",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de(token),
        )

        fila = Dispositivo.objects.filter(pk=dispositivo.pk).values().get()

        self.assertNotIn(token, [str(valor) for valor in fila.values()])

    def test_dos_dispositivos_no_pueden_compartir_token(self):
        token = tokens.generar()
        Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta A",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de(token),
        )

        with self.assertRaises(IntegrityError):
            Dispositivo.objects.create(
                gimnasio=self.gimnasio, nombre="Puerta B",
                tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de(token),
            )


class CredencialTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def test_generar_credencial_qr_es_idempotente_y_regenera_el_valor(self):
        """Regenerar es un UPDATE en la misma fila: hay una unicidad
        (gimnasio, alumno, tipo), así que un alumno no puede tener dos QR."""
        primera = services.generar_credencial_qr(self.alumno)
        valor_viejo = primera.valor

        segunda = services.generar_credencial_qr(self.alumno, regenerar=True)

        self.assertEqual(primera.pk, segunda.pk)
        self.assertNotEqual(segunda.valor, valor_viejo)
        self.assertEqual(Credencial.objects.count(), 1)

    def test_el_valor_se_guarda_sin_espacios_ni_salto_de_linea(self):
        """La puerta busca por el valor normalizado: un id facial copiado con
        " 42\r\n" no matchearía nunca si se guardara tal cual."""
        credencial = Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="  42\r\n",
        )

        credencial.refresh_from_db()
        self.assertEqual(credencial.valor, "42")

    def test_un_alumno_no_puede_tener_dos_credenciales_del_mismo_tipo(self):
        services.generar_credencial_qr(self.alumno)

        with self.assertRaises(IntegrityError):
            Credencial.objects.create(
                gimnasio=self.gimnasio, alumno=self.alumno,
                tipo=Credencial.Tipo.QR, valor="otro-valor",
            )

    def test_el_mismo_id_facial_puede_repetirse_entre_gimnasios(self):
        """Los lectores numeran sus usuarios desde 1, así que el id "42" va a
        existir en los dos gimnasios. La unicidad es por gimnasio, no global."""
        ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Ana", apellido="Gomez"
        )
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )

        Credencial.objects.create(
            gimnasio=self.otro, alumno=ajeno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )

        self.assertEqual(Credencial.objects.filter(valor="42").count(), 2)

    def test_dar_de_baja_libera_el_valor_para_otro_alumno(self):
        """El equipo le reasigna el número 42 de un ex-alumno a uno nuevo: la
        unicidad `(gimnasio, tipo, valor)` es solo sobre las ACTIVAS."""
        viejo = Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )
        services.dar_de_baja_credencial(viejo)
        nuevo = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez"
        )

        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=nuevo,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )

        self.assertEqual(Credencial.objects.filter(valor="42").count(), 2)

    def test_dos_activas_con_el_mismo_valor_siguen_chocando(self):
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )
        nuevo = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez"
        )

        with self.assertRaises(IntegrityError):
            Credencial.objects.create(
                gimnasio=self.gimnasio, alumno=nuevo,
                tipo=Credencial.Tipo.FACIAL, valor="42",
            )

    def test_el_padron_lleva_solo_al_dueño_activo_de_un_valor_repetido(self):
        viejo = Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )
        services.dar_de_baja_credencial(viejo)
        nuevo = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez"
        )
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=nuevo,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )

        filas = services.padron_de(self.gimnasio)["alumnos"]

        self.assertEqual(
            [(f["id"], f["id_facial"]) for f in filas], [(nuevo.pk, "42")]
        )

    def test_no_se_puede_colgar_una_credencial_de_un_alumno_de_otro_gimnasio(self):
        ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Ana", apellido="Gomez"
        )
        credencial = Credencial(
            gimnasio=self.gimnasio, alumno=ajeno,
            tipo=Credencial.Tipo.QR, valor="x",
        )

        with self.assertRaises(ValidationError):
            credencial.clean()

    def test_el_alta_en_bloque_saltea_a_los_que_ya_tienen_y_no_crece_en_queries(self):
        """Un gimnasio con 200 alumnos no puede clickear 200 veces, y el alta
        no puede ser una query por alumno.

        Se mide de verdad, comparando dos tamaños de conjunto con diferencia
        real (no un `assertNumQueries` fijo, que se rompe con cambios internos
        de Django) -- mismo molde que
        `importaciones.tests.PreviewPlantillasEscalaTests`.
        """
        services.generar_credencial_qr(self.alumno)
        for i in range(4):
            Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"A{i}", apellido="X"
            )

        with CaptureQueriesContext(connection) as ctx:
            creadas = services.generar_credenciales_en_bloque(self.gimnasio)

        self.assertEqual(creadas, 4)
        self.assertEqual(Credencial.objects.count(), 5)
        pocas = len(ctx)

        gimnasio_grande = Gimnasio.objects.create(
            nombre="Gimnasio Grande", slug="gimnasio-grande"
        )
        Alumno.objects.bulk_create(
            Alumno(gimnasio=gimnasio_grande, nombre=f"B{i}", apellido="X")
            for i in range(40)
        )

        with CaptureQueriesContext(connection) as ctx:
            creadas_en_el_grande = services.generar_credenciales_en_bloque(
                gimnasio_grande
            )

        self.assertEqual(creadas_en_el_grande, 40)
        # `bulk_create` agrega un par de statements por lote, así que se pide
        # O(lotes) y no igualdad exacta -- mismo criterio que
        # `PreviewPlantillasEscalaTests.test_el_post_del_preview_no_hace_una_
        # query_por_ejercicio_nuevo`.
        self.assertLess(len(ctx), pocas + 5)

    def test_el_alta_en_bloque_no_toca_a_los_alumnos_inactivos(self):
        Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Baja", apellido="X",
            estado=Alumno.Estado.INACTIVO,
        )

        services.generar_credenciales_en_bloque(self.gimnasio)

        self.assertEqual(Credencial.objects.count(), 1)


class EventoIngresoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de("t"),
        )

    def test_el_momento_no_es_auto_now_add(self):
        """Un lote en diferido trae el momento del equipo: si el campo fuera
        `auto_now_add`, todos los eventos que subió un lector que estuvo sin
        internet quedarían fechados en el instante en que volvió."""
        ayer = timezone.now() - timedelta(days=1)

        evento = EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            resultado=EventoIngreso.Resultado.PERMITIDO, momento=ayer,
        )

        self.assertEqual(evento.momento, ayer)

    def test_el_mismo_id_de_evento_del_equipo_no_entra_dos_veces(self):
        """Es la idempotencia del lote: el lector reintenta y no puede duplicar
        el ingreso."""
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=timezone.now(), id_evento_equipo="8821",
        )

        with self.assertRaises(IntegrityError):
            EventoIngreso.objects.create(
                gimnasio=self.gimnasio, dispositivo=self.dispositivo,
                resultado=EventoIngreso.Resultado.PERMITIDO,
                momento=timezone.now(), id_evento_equipo="8821",
            )

    def test_los_eventos_sin_id_del_equipo_no_chocan_entre_si(self):
        """La unicidad es PARCIAL: si no lo fuera, el segundo evento de un
        lector que no manda id reventaría contra el primero."""
        for _ in range(3):
            EventoIngreso.objects.create(
                gimnasio=self.gimnasio, dispositivo=self.dispositivo,
                resultado=EventoIngreso.Resultado.PERMITIDO,
                momento=timezone.now(), id_evento_equipo="",
            )

        self.assertEqual(EventoIngreso.objects.count(), 3)

    def test_borrar_el_lector_no_puede_borrar_el_historial(self):
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            resultado=EventoIngreso.Resultado.PERMITIDO, momento=timezone.now(),
        )

        with self.assertRaises(ProtectedError):
            self.dispositivo.delete()


def _matriz_esperada(valor):
    """Lo que tiene que codificar el QR: EXACTAMENTE `valor`, con el nivel de
    corrección del módulo. Si `qr.py` le agregara algo al contenido (una URL,
    un prefijo, el nombre del alumno), la matriz sería otra."""
    codigo = segno.make_qr(valor, error=qr.NIVEL_ERROR)
    return [[bool(modulo) for modulo in fila] for fila in codigo.matrix]


def _matriz_del_svg(svg):
    """Reconstruye la matriz de módulos desde el `path` que dibuja segno.

    No hay decodificador de QR en las dependencias y no se agrega uno para un
    test: el path son trazos horizontales (`M x y`, `m dx dy`, `h n`) sobre
    filas a media altura, así que se puede leer tal cual.
    """
    lado = int(re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).group(1))
    trazo = re.search(r'<path class="qrline"[^>]* d="([^"]+)"', svg).group(1)
    oscuro = [[False] * lado for _ in range(lado)]
    x = y = 0.0
    for comando, numeros in re.findall(r"([MmHh])([-\d. ]+)", trazo):
        valores = [float(n) for n in numeros.split()]
        if comando == "M":
            x, y = valores
        elif comando == "m":
            x, y = x + valores[0], y + valores[1]
        else:
            largo = valores[0] if comando == "h" else valores[0] - x
            for columna in range(int(x), int(x + largo)):
                oscuro[int(y)][columna] = True
            x += largo
    return oscuro


def _sin_borde(matriz, borde):
    return [fila[borde:len(fila) - borde] for fila in matriz[borde:len(matriz) - borde]]


class QrTests(SimpleTestCase):
    """El QR es una llave al portador que tiene que servir IMPRESO en una
    tarjeta (el alumno sin smartphone) y en la pantalla del celular."""

    VALOR = tokens.generar()

    def test_devuelve_un_svg_inline(self):
        svg = qr.svg_de("abc123")

        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("</svg>", svg)
        # Inline en el HTML: sin declaración XML ni tamaño fijo, escala con
        # el contenedor sin pixelarse.
        self.assertNotIn("<?xml", svg)
        self.assertNotIn('width="', svg.split(">", 1)[0])

    def test_el_mismo_valor_da_el_mismo_svg(self):
        self.assertEqual(qr.svg_de("abc"), qr.svg_de("abc"))

    def test_valores_distintos_dan_svg_distintos(self):
        self.assertNotEqual(qr.svg_de("abc"), qr.svg_de("abd"))

    def test_el_svg_codifica_exactamente_el_valor(self):
        matriz = _matriz_del_svg(qr.svg_de(self.VALOR))

        self.assertEqual(
            _sin_borde(matriz, qr.BORDE), _matriz_esperada(self.VALOR)
        )

    def test_el_svg_tiene_zona_silenciosa_y_fondo_blanco(self):
        """Sin los 4 módulos claros alrededor, un lector no encuentra el
        código. Y el fondo va pintado: sobre el color del paisaje del
        gimnasio, un fondo transparente le baja el contraste."""
        svg = qr.svg_de(self.VALOR)
        matriz = _matriz_del_svg(svg)
        borde = qr.BORDE

        self.assertGreaterEqual(borde, 4)
        self.assertFalse(any(any(fila) for fila in matriz[:borde]))
        self.assertFalse(any(any(fila) for fila in matriz[-borde:]))
        self.assertFalse(any(any(fila[:borde]) or any(fila[-borde:]) for fila in matriz))
        self.assertIn('fill="#fff"', svg)

    def test_es_un_qr_comun_y_no_un_micro_qr(self):
        """`segno.make` elige Micro QR para valores cortos, y la cámara del
        celular no lo lee. Tiene que ser `make_qr` siempre."""
        matriz = _sin_borde(_matriz_del_svg(qr.svg_de("abc123")), qr.BORDE)

        self.assertGreaterEqual(len(matriz), 21)

    def test_corrige_errores_al_menos_nivel_q(self):
        """Una tarjeta impresa se raya, se dobla y se gasta en el llavero. Con
        un token de 43 caracteres Q entra en la misma versión que M."""
        self.assertIn(qr.NIVEL_ERROR.upper(), ("Q", "H"))

    def test_tambien_puede_dar_png_para_el_pdf(self):
        png = qr.png_de("abc123")

        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_el_png_codifica_exactamente_el_valor_con_su_borde(self):
        imagen = Image.open(io.BytesIO(qr.png_de(self.VALOR))).convert("L")
        escala = qr.ESCALA_PNG
        lado = imagen.width // escala
        matriz = [
            [
                imagen.getpixel((col * escala + escala // 2, fila * escala + escala // 2)) < 128
                for col in range(lado)
            ]
            for fila in range(lado)
        ]

        self.assertEqual(
            _sin_borde(matriz, qr.BORDE), _matriz_esperada(self.VALOR)
        )
        self.assertFalse(any(any(fila) for fila in matriz[: qr.BORDE]))


class PurgarIngresosTests(TestCase):
    """`manage.py purgar_ingresos`: existe por el techo del exportador
    (`tenants/exportacion.py::MAX_FILAS_WEB`), no "por si alguna vez molesta".

    El reloj se congela a las 22:30 de Buenos Aires, cuando la fecha UTC ya es
    la de mañana: un corte calculado con `timezone.now().date()` caería un día
    más tarde y se llevaría el evento del borde. Y la fecha es LEJANA a hoy,
    para que el test no pase por coincidir con la fecha real.
    """

    # 15/05/2030 22:30 en Buenos Aires = 16/05/2030 01:30 UTC.
    AHORA = datetime(2030, 5, 16, 1, 30, tzinfo=dt_timezone.utc)

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de("a"),
        )
        self.dispositivo_otro = Dispositivo.objects.create(
            gimnasio=self.otro, nombre="Puerta",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de("b"),
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )
        reloj = patch("django.utils.timezone.now", return_value=self.AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        # Con --meses 24 el corte es la medianoche LOCAL del 15/05/2028.
        self.corte = timezone.make_aware(datetime(2028, 5, 15))

    def _evento(self, momento, *, dispositivo=None, alumno=None):
        dispositivo = dispositivo or self.dispositivo
        return EventoIngreso.objects.create(
            gimnasio=dispositivo.gimnasio, dispositivo=dispositivo, alumno=alumno,
            resultado=EventoIngreso.Resultado.PERMITIDO, momento=momento,
        )

    def _purgar(self, *args):
        salida = io.StringIO()
        call_command("purgar_ingresos", "--gimnasio", "gimnasio-a", *args, stdout=salida)
        return salida.getvalue()

    def test_slug_inexistente_es_un_error(self):
        with self.assertRaises(CommandError):
            call_command("purgar_ingresos", "--gimnasio", "no-existe", stdout=io.StringIO())

    def test_meses_por_debajo_del_minimo_es_un_error(self):
        """Un `--meses 0` o un `1` tipeado de apuro se llevaría los ingresos
        recientes, que son los que explican un reclamo de cuota o de acceso."""
        vieja = self._evento(self.corte - timedelta(days=1))
        for meses in ("0", "-3", str(MESES_MINIMOS - 1)):
            with self.subTest(meses=meses), self.assertRaises(CommandError):
                self._purgar("--meses", meses)
        self.assertTrue(EventoIngreso.objects.filter(pk=vieja.pk).exists())

    def test_el_minimo_se_acepta(self):
        self._purgar("--meses", str(MESES_MINIMOS))

    def test_borde_exacto_del_corte(self):
        """El evento justo en el corte se conserva; el inmediatamente anterior
        se borra. Y el corte es la medianoche LOCAL, no la de UTC."""
        en_el_corte = self._evento(self.corte)
        justo_antes = self._evento(self.corte - timedelta(microseconds=1))
        # Borrado por un corte UTC (un día después): tiene que sobrevivir.
        tarde_del_dia = self._evento(self.corte + timedelta(hours=22))

        self._purgar()

        self.assertTrue(EventoIngreso.objects.filter(pk=en_el_corte.pk).exists())
        self.assertTrue(EventoIngreso.objects.filter(pk=tarde_del_dia.pk).exists())
        self.assertFalse(EventoIngreso.objects.filter(pk=justo_antes.pk).exists())

    def test_meses_de_calendario(self):
        self._evento(self.corte + timedelta(days=1))
        viejo = self._evento(timezone.make_aware(datetime(2030, 2, 14, 23, 59)))
        nuevo = self._evento(timezone.make_aware(datetime(2030, 2, 15)))

        self._purgar("--meses", "3")

        self.assertFalse(EventoIngreso.objects.filter(pk=viejo.pk).exists())
        self.assertTrue(EventoIngreso.objects.filter(pk=nuevo.pk).exists())

    def test_no_toca_los_eventos_de_otro_gimnasio(self):
        """Incluidos los que no tienen alumno (credencial desconocida)."""
        viejo = self.corte - timedelta(days=30)
        ajenos = [
            self._evento(viejo, dispositivo=self.dispositivo_otro),
            self._evento(viejo, dispositivo=self.dispositivo_otro, alumno=None),
        ]
        propio = self._evento(viejo, alumno=self.alumno)
        propio_sin_alumno = self._evento(viejo)

        self._purgar()

        self.assertEqual(
            set(EventoIngreso.objects.values_list("pk", flat=True)),
            {e.pk for e in ajenos},
        )
        self.assertFalse(
            EventoIngreso.objects.filter(pk__in=[propio.pk, propio_sin_alumno.pk]).exists()
        )

    def test_dry_run_no_borra_y_reporta_el_numero_exacto(self):
        viejo = self.corte - timedelta(days=30)
        for _ in range(3):
            self._evento(viejo)
        self._evento(self.corte)
        self._evento(viejo, dispositivo=self.dispositivo_otro)

        salida = self._purgar("--dry-run")

        self.assertEqual(EventoIngreso.objects.count(), 5)
        self.assertIn(" 3 ", salida)
        self.assertIn("gimnasio-a", salida)
        self.assertIn("15/05/2028", salida)

    def test_corre_varias_tandas_y_borra_todo_lo_que_corresponde(self):
        viejo = self.corte - timedelta(days=30)
        for _ in range(5):
            self._evento(viejo)
        conservado = self._evento(self.corte)

        with patch("ingreso.management.commands.purgar_ingresos.TAMANIO_TANDA", 2), \
                CaptureQueriesContext(connection) as queries:
            salida = self._purgar()

        borrados = [q for q in queries.captured_queries if q["sql"].startswith("DELETE")]
        self.assertEqual(len(borrados), 3)  # 2 + 2 + 1
        self.assertEqual(
            list(EventoIngreso.objects.values_list("pk", flat=True)), [conservado.pk]
        )
        self.assertIn(" 5 ", salida)

    def test_solo_toca_eventos_de_ingreso(self):
        """Ni lectores (ni sus contadores), ni credenciales, ni alumnos."""
        credencial = services.generar_credencial_qr(self.alumno)
        Dispositivo.objects.filter(pk=self.dispositivo.pk).update(
            eventos_hoy=7, eventos_hoy_fecha=date(2030, 5, 15),
            ultimo_contacto=self.AHORA,
        )
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo, alumno=self.alumno,
            credencial=credencial, resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=self.corte - timedelta(days=1),
        )

        self._purgar()

        self.assertEqual(EventoIngreso.objects.count(), 0)
        self.assertTrue(Credencial.objects.filter(pk=credencial.pk).exists())
        self.assertTrue(Alumno.objects.filter(pk=self.alumno.pk).exists())
        dispositivo = Dispositivo.objects.get(pk=self.dispositivo.pk)
        self.assertEqual(dispositivo.eventos_hoy, 7)
        self.assertEqual(dispositivo.eventos_hoy_fecha, date(2030, 5, 15))
        self.assertEqual(dispositivo.ultimo_contacto, self.AHORA)


class RestarMesesTests(SimpleTestCase):
    def test_mismo_dia_del_mes(self):
        self.assertEqual(restar_meses(date(2030, 5, 15), 24), date(2028, 5, 15))
        self.assertEqual(restar_meses(date(2030, 1, 10), 1), date(2029, 12, 10))

    def test_un_dia_que_no_existe_cae_en_el_ultimo_del_mes(self):
        self.assertEqual(restar_meses(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(restar_meses(date(2028, 2, 29), 12), date(2027, 2, 28))


# ---------------------------------------------------------------------------
# Documento de integración y puente de ejemplo (Tarea 17)
# ---------------------------------------------------------------------------

RAIZ_DEL_REPO = Path(settings.BASE_DIR)
DOCUMENTO_DE_INTEGRACION = RAIZ_DEL_REPO / "docs" / "integracion-control-de-ingreso.md"
PUENTE_DE_EJEMPLO = RAIZ_DEL_REPO / "scripts" / "puente_ingreso_ejemplo.py"
SCRIPT_DE_PRUEBA_LOCAL = RAIZ_DEL_REPO / "scripts" / "probar_ingreso_local.sh"


def _cargar_puente():
    """El puente es un script suelto (tiene que correr en la PC del gimnasio
    sin el repo), no un módulo del proyecto: se carga por ruta. Va a
    `sys.modules` antes de ejecutarlo porque `dataclasses` busca ahí el módulo
    de la clase."""
    nombre = "puente_ingreso_ejemplo"
    spec = importlib.util.spec_from_file_location(nombre, PUENTE_DE_EJEMPLO)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


puente_mod = _cargar_puente()

PADRON_DE_PRUEBA = {
    "version": "v1",
    "reglas_no_incluidas": ["reserva", "antipassback"],
    "mensajes": {
        "permitido": "¡Hola, {nombre}! Pasá.",
        "deuda": "Tenés una cuota sin pagar. Pasá por el mostrador.",
        "sin_credencial": "Credencial no reconocida. Pasá por el mostrador.",
    },
    "alumnos": [
        {"id": 1, "nombre": "Juan P.", "habilitado": True, "motivo": None,
         "qr": "qr-juan", "id_facial": "42"},
        {"id": 2, "nombre": "Ana G.", "habilitado": False, "motivo": "deuda",
         "qr": "qr-ana", "id_facial": None},
    ],
}


class _TransporteFalso:
    """Reemplaza la red: cada llamada consume la próxima respuesta de la lista
    (una tupla `(status, encabezados, cuerpo)` o una excepción a levantar), y
    anota lo que se pidió."""

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def __call__(self, metodo, url, *, cuerpo=None, encabezados=None, timeout):
        self.llamadas.append({
            "metodo": metodo, "url": url, "encabezados": encabezados or {},
            "cuerpo": json.loads(cuerpo) if cuerpo else None, "timeout": timeout,
        })
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


def _ok(resultados):
    return 200, {}, json.dumps({"resultados": resultados}).encode()


class _Reloj:
    def __init__(self):
        self.ahora = 1000.0

    def __call__(self):
        return self.ahora


class PuenteDeEjemploTests(SimpleTestCase):
    """El puente de ejemplo implementa el contrato del documento. Lo que se
    fija es la LÓGICA (decidir con el padrón, el fallback, la cola, los
    frenos); la red se reemplaza por `_TransporteFalso`."""

    def _puente(self, *respuestas, padron=PADRON_DE_PRUEBA):
        self.transporte = _TransporteFalso(*respuestas)
        self.reloj = _Reloj()
        self.logs = []
        puente = puente_mod.Puente(
            "http://gimnasio.test/", "clave", transporte=self.transporte,
            reloj=self.reloj, log=self.logs.append,
        )
        puente._padron.valor = {"etag": '"v1"', "padron": padron}
        return puente

    # -- decidir con el padrón (puro) --------------------------------------

    def test_decide_con_el_padron_local(self):
        decidir = puente_mod.decidir_con_padron

        juan = decidir(PADRON_DE_PRUEBA, "qr", "qr-juan\r\n")
        self.assertTrue(juan.permitido)
        self.assertEqual(juan.mensaje, "¡Hola, Juan P.! Pasá.")
        self.assertTrue(juan.offline)

        ana = decidir(PADRON_DE_PRUEBA, "qr", "qr-ana")
        self.assertFalse(ana.permitido)
        self.assertEqual(ana.resultado, "deuda")
        self.assertEqual(ana.mensaje, PADRON_DE_PRUEBA["mensajes"]["deuda"])

        desconocido = decidir(PADRON_DE_PRUEBA, "qr", "otra-cosa")
        self.assertFalse(desconocido.permitido)
        self.assertEqual(desconocido.resultado, "sin_credencial")

    def test_el_qr_y_el_id_facial_no_se_mezclan(self):
        """En la app la unicidad es `(tipo, valor)`: un QR «42» no es el
        usuario facial 42."""
        self.assertTrue(puente_mod.decidir_con_padron(PADRON_DE_PRUEBA, "facial", "42").permitido)
        self.assertFalse(puente_mod.decidir_con_padron(PADRON_DE_PRUEBA, "qr", "42").permitido)

    def test_sin_padron_no_abre(self):
        self.assertFalse(puente_mod.decidir_con_padron(None, "qr", "qr-juan").permitido)

    def test_cambios_para_el_equipo_bloquea_al_deudor_y_borra_al_que_desaparece(self):
        nuevo = {**PADRON_DE_PRUEBA, "alumnos": [
            {**PADRON_DE_PRUEBA["alumnos"][0], "habilitado": False, "motivo": "deuda"},
            {"id": 3, "nombre": "Leo M.", "habilitado": True, "motivo": None,
             "qr": "qr-leo", "id_facial": None},
        ]}

        cambios = puente_mod.cambios_para_el_equipo(PADRON_DE_PRUEBA, nuevo)

        self.assertEqual([f["id"] for f in cambios["bloqueos"]], [1])
        self.assertEqual([f["id"] for f in cambios["bajas"]], [2])
        self.assertEqual([f["id"] for f in cambios["altas"]], [3])

    # -- lectura online y fallback ------------------------------------------

    def test_con_respuesta_online_manda_el_veredicto_de_la_app(self):
        puente = self._puente(_ok([{
            "id_evento_equipo": "x", "permitido": False, "resultado": "duplicado",
            "mensaje": "Ya registramos tu ingreso hace un momento.", "alumno": "Juan P.",
        }]))

        veredicto = puente.leer("qr", "qr-juan")

        self.assertFalse(veredicto.permitido)
        self.assertFalse(veredicto.offline)
        self.assertEqual(puente.cola, [])
        enviado = self.transporte.llamadas[0]
        self.assertEqual(enviado["encabezados"]["Authorization"], "Bearer clave")
        self.assertNotIn("abierto_por_el_equipo", enviado["cuerpo"]["eventos"][0])
        self.assertEqual(enviado["timeout"], puente_mod.TIMEOUT_ONLINE)

    def test_un_timeout_online_decide_con_el_padron_y_encola_en_diferido_con_el_mismo_id(self):
        """El corazón del contrato (riesgos 11 y 15): si la app no contesta a
        tiempo, el equipo decide con el padrón y el evento se sube después EN
        DIFERIDO con el MISMO id -- si la app llegó a registrar el intento
        online, el reintento devuelve ese veredicto en vez de un duplicado."""
        puente = self._puente(puente_mod.ErrorDeRed("timed out"))

        veredicto = puente.leer("qr", "qr-juan")

        self.assertTrue(veredicto.permitido)
        self.assertTrue(veredicto.offline)
        id_online = self.transporte.llamadas[0]["cuerpo"]["eventos"][0]["id_evento_equipo"]
        self.assertEqual(len(puente.cola), 1)
        encolado = puente.cola[0]
        self.assertEqual(encolado["id_evento_equipo"], id_online)
        self.assertIs(encolado["abierto_por_el_equipo"], True)
        self.assertIn("T", encolado["momento"])  # con hora, como exige el diferido

        self.transporte.respuestas.append(_ok([{
            "id_evento_equipo": id_online, "permitido": True, "resultado": "permitido",
            "mensaje": "¡Hola, Juan! Pasá.", "alumno": "Juan P.",
        }]))
        self.assertEqual(puente.subir_diferidos(), 1)
        subido = self.transporte.llamadas[1]["cuerpo"]["eventos"][0]
        self.assertEqual(subido["id_evento_equipo"], id_online)
        self.assertIs(subido["abierto_por_el_equipo"], True)
        self.assertEqual(puente.cola, [])

    def test_un_429_online_decide_con_el_padron_y_respeta_retry_after(self):
        puente = self._puente(
            (429, {"Retry-After": "30"}, b'{"error": "x", "motivo": "tope_diario"}')
        )

        self.assertTrue(puente.leer("qr", "qr-juan").offline)
        self.assertEqual(len(puente.cola), 1)

        # Dentro de la espera: ni se sube la cola ni se pregunta online.
        self.assertEqual(puente.subir_diferidos(), 0)
        self.assertTrue(puente.leer("facial", "42").offline)
        self.assertEqual(len(self.transporte.llamadas), 1)
        self.assertEqual(len(puente.cola), 2)

        self.reloj.ahora += 31
        self.transporte.respuestas.append(_ok([
            {"id_evento_equipo": e["id_evento_equipo"], "permitido": True,
             "resultado": "permitido", "mensaje": "", "alumno": None}
            for e in puente.cola
        ]))
        self.assertEqual(puente.subir_diferidos(), 2)

    def test_con_401_o_403_no_abre_con_el_padron(self):
        """La app dijo que este lector no tiene permiso: abrir con el padrón
        sería saltearse justo esa decisión."""
        for status in (401, 403):
            with self.subTest(status=status):
                puente = self._puente((status, {}, b'{"error": "x"}'))

                veredicto = puente.leer("qr", "qr-juan")

                self.assertFalse(veredicto.permitido)
                self.assertEqual(puente.cola, [])

    def test_un_rechazo_offline_no_se_sube(self):
        """`abierto_por_el_equipo` solo sabe decir «abrió»: con `false` la app
        lo tomaría como una lectura online de ahora."""
        puente = self._puente(puente_mod.ErrorDeRed("sin red"))

        veredicto = puente.leer("qr", "qr-ana")

        self.assertFalse(veredicto.permitido)
        self.assertEqual(puente.cola, [])

    # -- la cola ----------------------------------------------------------------

    def _cola(self, puente, cantidad):
        for i in range(cantidad):
            puente.cola.append({
                "tipo": "qr", "valor": "qr-juan", "sentido": "entrada",
                "momento": "2026-09-28T10:00:00-03:00",
                "id_evento_equipo": f"e{i}", "abierto_por_el_equipo": True,
            })

    def test_sube_en_lotes_de_a_lo_sumo_100(self):
        puente = self._puente()
        self._cola(puente, 150)
        for tamano in (100, 50):
            self.transporte.respuestas.append(_ok([
                {"resultado": "permitido"} for _ in range(tamano)
            ]))

        self.assertEqual(puente.subir_diferidos(), 150)

        self.assertEqual(
            [len(l["cuerpo"]["eventos"]) for l in self.transporte.llamadas], [100, 50]
        )

    def test_un_evento_invalido_se_descarta_y_no_se_reintenta(self):
        puente = self._puente(_ok([{
            "id_evento_equipo": "e0", "permitido": False, "resultado": "invalido",
            "mensaje": "", "alumno": None, "error": "momento demasiado viejo",
        }]))
        self._cola(puente, 1)

        puente.subir_diferidos()

        self.assertEqual(puente.cola, [])
        self.assertEqual(len(self.transporte.llamadas), 1)
        self.assertTrue(any("momento demasiado viejo" in l for l in self.logs))

    def test_sin_red_la_cola_queda_y_no_hay_bucle_de_reintentos(self):
        puente = self._puente(puente_mod.ErrorDeRed("sin red"))
        self._cola(puente, 3)

        self.assertEqual(puente.subir_diferidos(), 0)
        self.assertEqual(puente.subir_diferidos(), 0)  # todavía en la espera

        self.assertEqual(len(self.transporte.llamadas), 1)
        self.assertEqual(len(puente.cola), 3)

    # -- padrón ---------------------------------------------------------------

    def test_el_padron_manda_if_none_match_y_un_304_conserva_la_copia(self):
        puente = self._puente((304, {}, b""))

        self.assertEqual(puente.sincronizar_padron(), 304)

        self.assertEqual(
            self.transporte.llamadas[0]["encabezados"]["If-None-Match"], '"v1"'
        )
        self.assertEqual(puente.padron, PADRON_DE_PRUEBA)

    def test_un_padron_nuevo_reemplaza_la_copia_y_se_aplica_al_equipo(self):
        nuevo = {**PADRON_DE_PRUEBA, "version": "v2", "alumnos": []}
        puente = self._puente((200, {"ETag": '"v2"'}, json.dumps(nuevo).encode()))

        puente.sincronizar_padron()

        self.assertEqual(puente.padron["version"], "v2")
        self.assertEqual(puente._padron.valor["etag"], '"v2"')
        self.assertEqual(sum("BORRAR" in l for l in self.logs), 2)

    def test_la_cola_y_el_padron_sobreviven_a_un_reinicio(self):
        with tempfile.TemporaryDirectory() as carpeta:
            primero = puente_mod.Puente(
                "http://x", "clave", estado_dir=carpeta,
                transporte=_TransporteFalso(puente_mod.ErrorDeRed("sin red")),
                log=lambda *_: None,
            )
            primero._padron.valor = {"etag": '"v1"', "padron": PADRON_DE_PRUEBA}
            primero._padron.guardar()
            primero.leer("qr", "qr-juan")

            segundo = puente_mod.Puente(
                "http://x", "clave", estado_dir=carpeta, log=lambda *_: None
            )

            self.assertEqual(len(segundo.cola), 1)
            self.assertEqual(segundo.padron, PADRON_DE_PRUEBA)

    # -- transporte real y dependencias ---------------------------------------

    def test_el_transporte_real_traduce_el_timeout_a_error_de_red(self):
        with patch.object(
            puente_mod.urllib.request, "urlopen", side_effect=socket.timeout("timed out")
        ):
            with self.assertRaises(puente_mod.ErrorDeRed):
                puente_mod.transporte_urllib("GET", "http://x", timeout=1)

    def test_el_transporte_real_devuelve_un_4xx_como_respuesta(self):
        error = urllib.error.HTTPError(
            "http://x", 429, "Too Many", {"Retry-After": "5"}, io.BytesIO(b"{}")
        )
        with patch.object(puente_mod.urllib.request, "urlopen", side_effect=error):
            status, encabezados, _ = puente_mod.transporte_urllib("GET", "http://x", timeout=1)

        self.assertEqual(status, 429)
        self.assertEqual(encabezados["Retry-After"], "5")

    def test_solo_importa_la_biblioteca_estandar(self):
        """Tiene que correr en la PC de un gimnasio sin `pip install`."""
        arbol = ast.parse(PUENTE_DE_EJEMPLO.read_text(encoding="utf-8"))
        modulos = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                modulos.update(a.name.split(".")[0] for a in nodo.names)
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                modulos.add(nodo.module.split(".")[0])

        self.assertTrue(modulos)
        self.assertEqual(modulos - set(sys.stdlib_module_names), set())

    def test_no_hay_claves_escritas_en_los_scripts(self):
        """La clave entra por argumento o por variable de entorno del script."""
        for ruta in (PUENTE_DE_EJEMPLO, SCRIPT_DE_PRUEBA_LOCAL):
            with self.subTest(ruta=ruta.name):
                texto = ruta.read_text(encoding="utf-8")
                self.assertIn("PUENTE_INGRESO_TOKEN", texto)
                self.assertNotRegex(texto, r"Bearer [A-Za-z0-9_-]{20,}")


def _claves_de(valor):
    """Todas las claves de diccionario de un JSON, a cualquier profundidad."""
    if isinstance(valor, dict):
        claves = set(valor)
        for hijo in valor.values():
            claves |= _claves_de(hijo)
        return claves
    if isinstance(valor, list):
        return set().union(*(_claves_de(v) for v in valor)) if valor else set()
    return set()


def _objetos_de(valor):
    if isinstance(valor, dict):
        yield valor
        for hijo in valor.values():
            yield from _objetos_de(hijo)
    elif isinstance(valor, list):
        for hijo in valor:
            yield from _objetos_de(hijo)


class DocumentoDeIntegracionTests(TestCase):
    """Alambre trampa: el documento no puede quedar desfasado del código en
    silencio. Mismo criterio que `config.tests.BlueprintDeclaraLoQueSettingsLeeTests`.

    Compara contra lo que la API DEVUELVE de verdad (no contra una lista
    escrita a mano, que sería comparar el documento consigo mismo)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.texto = DOCUMENTO_DE_INTEGRACION.read_text(encoding="utf-8")
        cls.bloques_json = [
            json.loads(bloque)
            for bloque in re.findall(r"```json\n(.*?)```", cls.texto, flags=re.S)
        ]

    def test_hay_ejemplos_json(self):
        self.assertGreaterEqual(len(self.bloques_json), 6)

    def test_las_rutas_del_documento_y_de_los_scripts_existen(self):
        nombres = set()
        for ruta in (DOCUMENTO_DE_INTEGRACION, PUENTE_DE_EJEMPLO, SCRIPT_DE_PRUEBA_LOCAL):
            rutas = set(re.findall(r"/ingreso/v1/[\w/.-]*", ruta.read_text(encoding="utf-8")))
            self.assertTrue(rutas, ruta.name)
            for url in rutas:
                with self.subTest(archivo=ruta.name, url=url):
                    coincidencia = resolve(url)
                    self.assertEqual(coincidencia.namespace, "ingreso")
                    nombres.add(coincidencia.url_name)

        # Los dos endpoints de la API, documentados y ejercitados.
        self.assertEqual(nombres, {"api_eventos", "api_padron"})

    def test_la_tabla_de_numeros_coincide_con_el_codigo(self):
        filas = re.findall(
            r"^\| `(ingreso\.[\w.]+)` \| ([\d.]+) \|", self.texto, flags=re.M
        )
        self.assertGreaterEqual(len(filas), 8)
        for nombre, valor in filas:
            with self.subTest(constante=nombre):
                modulo, _, atributo = nombre.rpartition(".")
                real = getattr(importlib.import_module(modulo), atributo)
                self.assertEqual(int(valor.replace(".", "")), real)

    def _respuestas_reales(self):
        """Los JSON que la API devuelve de verdad, en todos sus caminos."""
        from django.core.cache import cache

        from ingreso import views as vistas

        cache.clear()
        self.addCleanup(cache.clear)
        gimnasio = Gimnasio.objects.create(nombre="Doc", slug="doc")
        _, token = services.crear_dispositivo(gimnasio, nombre="Puerta", tipo="qr")
        apagado, token_apagado = services.crear_dispositivo(
            gimnasio, nombre="Vieja", tipo="qr"
        )
        Dispositivo.objects.filter(pk=apagado.pk).update(activo=False)
        alumno = Alumno.objects.create(gimnasio=gimnasio, nombre="Juan", apellido="Perez")
        qr_juan = services.generar_credencial_qr(alumno)
        services.guardar_credencial_facial(alumno, "42")

        def post(cuerpo, clave=token):
            return self.client.post(
                "/ingreso/v1/eventos/", data=json.dumps(cuerpo),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {clave}",
            )

        respuestas = [
            post({"eventos": [
                {"tipo": "qr", "valor": qr_juan.valor, "id_evento_equipo": 8821},
                {"tipo": "rfid", "valor": "x", "id_evento_equipo": 8823},
            ]}),
            post({"lecturas": []}),
            post({"eventos": []}, clave="no-es-una-clave"),
            post({"eventos": []}, clave=token_apagado),
            self.client.get("/ingreso/v1/padron/", HTTP_AUTHORIZATION=f"Bearer {token}"),
            vistas._demasiado("tope_diario", "x", 1),
        ]
        self.assertEqual(
            [r.status_code for r in respuestas], [200, 400, 401, 403, 200, 429]
        )
        return [json.loads(r.content) for r in respuestas]

    def test_los_campos_de_los_ejemplos_son_los_del_codigo_y_estan_todos(self):
        from ingreso import views as vistas

        reales = set().union(*(_claves_de(r) for r in self._respuestas_reales()))
        fuente = inspect.getsource(vistas)
        del_request = set(re.findall(r'evento\.get\("(\w+)"', fuente)) | set(
            re.findall(r'datos\["(\w+)"\]', fuente)
        )
        self.assertIn("abierto_por_el_equipo", del_request)
        del_documento = set().union(*(_claves_de(b) for b in self.bloques_json))

        self.assertEqual(del_documento - (reales | del_request), set(),
                         "el documento muestra campos que la API no tiene")
        self.assertEqual((reales | del_request) - del_documento, set(),
                         "la API tiene campos que ningún ejemplo del documento muestra")

    def test_los_resultados_y_motivos_existen(self):
        from ingreso import views as vistas

        resultados = set(EventoIngreso.Resultado.values) | {services.RESULTADO_INVALIDO}
        for valor in resultados:
            with self.subTest(resultado=valor):
                self.assertIn(f"`{valor}`", self.texto)
        motivos = set(
            re.findall(r'_demasiado\(\s*"(\w+)"', inspect.getsource(vistas))
        ) | {str(EventoIngreso.Resultado.DEUDA)}
        self.assertEqual(motivos, {"intentos_fallidos", "tope_diario", "deuda"})

        for objeto in (o for b in self.bloques_json for o in _objetos_de(b)):
            if "resultado" in objeto:
                self.assertIn(objeto["resultado"], resultados)
            if objeto.get("motivo") is not None:
                self.assertIn(objeto["motivo"], motivos)

    def test_los_mensajes_del_padron_de_ejemplo_son_los_de_la_puerta(self):
        from ingreso.decision import MENSAJES, SALUDO

        ejemplos = [b["mensajes"] for b in self.bloques_json if "mensajes" in b]
        self.assertEqual(len(ejemplos), 1)
        self.assertEqual(ejemplos[0], {
            "permitido": SALUDO,
            "deuda": MENSAJES[EventoIngreso.Resultado.DEUDA],
            "sin_credencial": MENSAJES[EventoIngreso.Resultado.SIN_CREDENCIAL],
        })
