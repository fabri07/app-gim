"""Tests del anti-spam en capas."""

from datetime import timedelta

from django.core import signing
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from solicitudes import antispam


def _token_de_hace(segundos):
    """Token de tiempo firmado con una marca de hace `segundos`."""
    marca = (timezone.now() - timedelta(seconds=segundos)).timestamp()
    return signing.dumps(marca, salt=antispam._SIGNER_SALT, compress=True)


class AntispamTests(TestCase):
    def setUp(self):
        cache.clear()  # el LocMemCache es global entre tests

    def test_honeypot(self):
        self.assertTrue(antispam.honeypot_lleno("http://spam"))
        self.assertFalse(antispam.honeypot_lleno(""))
        self.assertFalse(antispam.honeypot_lleno("   "))

    def test_demasiado_rapido(self):
        self.assertTrue(antispam.demasiado_rapido(""))  # sin token
        self.assertTrue(antispam.demasiado_rapido("token-adulterado"))
        self.assertTrue(antispam.demasiado_rapido(_token_de_hace(0.2)))  # muy rápido
        self.assertFalse(antispam.demasiado_rapido(_token_de_hace(5)))  # humano

    def test_rate_limit_por_ip(self):
        ip = antispam.hash_de("1.2.3.4")
        for _ in range(antispam.MAX_POR_HORA):
            self.assertFalse(antispam.rate_limit_excedido(ip))
        self.assertTrue(antispam.rate_limit_excedido(ip))

    def test_dedup_por_email(self):
        h = antispam.hash_de("ana@box.com")
        self.assertFalse(antispam.duplicado_reciente(h))
        self.assertTrue(antispam.duplicado_reciente(h))

    def test_hash_no_guarda_el_dato_en_claro(self):
        h = antispam.hash_de("1.2.3.4")
        self.assertNotIn("1.2.3.4", h)
        self.assertEqual(len(h), 64)
