from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from alumnos.models import Alumno
from ingreso import services, tokens
from ingreso.models import (
    ConfiguracionIngreso,
    Credencial,
    Dispositivo,
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
