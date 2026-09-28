import io
import re
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
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
