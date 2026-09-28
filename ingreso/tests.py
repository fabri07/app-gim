from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase

from ingreso import tokens
from ingreso.models import ConfiguracionIngreso, Dispositivo, obtener_configuracion
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
