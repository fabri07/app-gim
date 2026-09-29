"""`IntentoDeCobro`: las tres restricciones de la base y su convivencia con la
cuenta demo."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import TenantOwnedModel
from suscripcion.models import IntentoDeCobro
from tenants.models import Gimnasio, Perfil

Estado = IntentoDeCobro.Estado


def _intento(gimnasio, desde=date(2026, 6, 1), hasta=date(2026, 6, 30), **extra):
    datos = {
        "plan": IntentoDeCobro.Plan.MENSUAL,
        "periodo_desde": desde,
        "periodo_hasta": hasta,
        "monto_usd": Decimal("10.00"),
        "monto_ars": Decimal("12000.00"),
        "tipo_cambio": Decimal("1200.00"),
        "alumnos_activos": 5,
        "expira": timezone.now() + timedelta(days=1),
    }
    datos.update(extra)
    return IntentoDeCobro.objects.create(gimnasio=gimnasio, **datos)


class RestriccionesTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Vida Plena", slug="vida-plena")

    def _choca(self, **kwargs):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _intento(self.gimnasio, **kwargs)

    def test_dos_creados_del_mismo_periodo_chocan(self):
        _intento(self.gimnasio)

        self._choca()

    def test_un_creado_y_un_vencido_del_mismo_periodo_conviven(self):
        _intento(self.gimnasio, estado=Estado.VENCIDO)
        _intento(self.gimnasio)

        self.assertEqual(IntentoDeCobro.objects.count(), 2)

    def test_creados_de_periodos_distintos_conviven(self):
        _intento(self.gimnasio)
        _intento(self.gimnasio, desde=date(2026, 7, 1), hasta=date(2026, 7, 30))

        self.assertEqual(IntentoDeCobro.objects.count(), 2)

    def test_dos_sin_payment_id_conviven(self):
        _intento(self.gimnasio, estado=Estado.VENCIDO, payment_id="")
        _intento(self.gimnasio, estado=Estado.CANCELADO, payment_id="")

        self.assertEqual(IntentoDeCobro.objects.count(), 2)

    def test_dos_con_el_mismo_payment_id_chocan(self):
        _intento(self.gimnasio, estado=Estado.PAGADO, payment_id="123")

        self._choca(estado=Estado.DUPLICADO, payment_id="123")

    def test_periodo_invertido_choca(self):
        self._choca(desde=date(2026, 6, 30), hasta=date(2026, 6, 1))

    def test_periodo_de_un_dia_es_valido(self):
        _intento(self.gimnasio, desde=date(2026, 6, 1), hasta=date(2026, 6, 1))

    def test_str_nombra_gimnasio_plan_periodo_y_estado(self):
        texto = str(_intento(self.gimnasio))

        for parte in ("Vida Plena", "Mensual", "01/06/2026", "30/06/2026", "Link generado"):
            self.assertIn(parte, texto)


class NoEsDelGimnasioTests(TestCase):
    def test_no_es_tenant_owned(self):
        """Dato de la plataforma sobre el gimnasio: queda fuera de los tres
        lugares de cobertura de los modelos tenant-owned."""
        self.assertFalse(issubclass(IntentoDeCobro, TenantOwnedModel))


class ConLaCuentaDemoTests(TestCase):
    def test_vaciar_la_demo_con_un_intento_colgado_no_levanta(self):
        """`vaciar_gimnasio` borra los `User` de los alumnos cada 6 h: el
        intento no cuelga de ningún `User`, así que no puede trabarlo."""
        from tenants.demo import vaciar_gimnasio

        demo = Gimnasio.objects.create(nombre="Gimnasio Demo", slug="demo", es_demo=True)
        staff = User.objects.create_user("staff-demo", password="c-123456")
        Perfil.objects.create(usuario=staff, gimnasio=demo, rol=Perfil.Rol.STAFF)
        intento = _intento(demo, email_de_quien_pago="staff@ejemplo.com")

        vaciar_gimnasio(gimnasio=demo)

        self.assertTrue(IntentoDeCobro.objects.filter(pk=intento.pk).exists())
