"""El modelo `pagos.CobroOnlineCuota` (T2 de «cuotas por Mercado Pago»).

Qué se fija acá: las restricciones que sostienen la idempotencia del cobro
(un solo link vivo por cuota, un `payment_id` acredita un solo intento), la
referencia que viaja a MP y el PROTECT hacia la cuota. El aislamiento por
gimnasio vive en `tenants.tests.CobroOnlineCuotaAislamientoTests`, y los tres
lugares de todo `TenantOwnedModel` en `tenants.tests.VaciarGimnasioTests` y
`tenants.tests_exportacion`.
"""

import uuid
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from alumnos.models import Alumno
from pagos.models import CobroOnlineCuota
from pagos.testing import crear_cuota
from tenants.models import Gimnasio


class CobroOnlineCuotaModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="A")
        self.cuota = crear_cuota(
            gimnasio=self.gimnasio, alumno=alumno, inicio=date(2026, 3, 1),
            monto=Decimal("28000"),
        )
        self.otra_cuota = crear_cuota(
            gimnasio=self.gimnasio, alumno=alumno, inicio=date(2026, 3, 29),
            monto=Decimal("28000"),
        )

    def _cobro(self, cuota=None, **extra):
        return CobroOnlineCuota.objects.create(
            gimnasio=self.gimnasio, cuota=cuota or self.cuota,
            monto=Decimal("28000"), **extra,
        )

    def test_nace_creado_con_referencia_unica_y_prefijo_para_mp(self):
        cobro = self._cobro()

        self.assertEqual(cobro.estado, CobroOnlineCuota.Estado.CREADO)
        self.assertIsInstance(cobro.referencia_externa, uuid.UUID)
        self.assertEqual(cobro.referencia_mp, f"cuota-{cobro.referencia_externa}")
        self.assertEqual(cobro.respuesta_mp, {})
        self.assertFalse(cobro.revision_pendiente)
        self.assertEqual(list(self.cuota.cobros_online.all()), [cobro])

    def test_dos_links_vivos_de_la_misma_cuota_no_pueden_existir(self):
        self._cobro()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._cobro()

    def test_un_link_cancelado_no_impide_uno_nuevo(self):
        self._cobro(estado=CobroOnlineCuota.Estado.CANCELADO)
        self._cobro(estado=CobroOnlineCuota.Estado.VENCIDO)

        self._cobro()  # no levanta

        self.assertEqual(self.cuota.cobros_online.count(), 3)

    def test_un_payment_id_acredita_un_solo_intento(self):
        self._cobro(payment_id="123", estado=CobroOnlineCuota.Estado.PAGADO)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._cobro(cuota=self.otra_cuota, payment_id="123")

    def test_varios_intentos_sin_pago_conviven(self):
        self._cobro(estado=CobroOnlineCuota.Estado.CANCELADO)
        self._cobro(cuota=self.otra_cuota)

        self.assertEqual(CobroOnlineCuota.objects.filter(payment_id="").count(), 2)

    def test_la_cuota_con_cobros_online_no_se_borra_en_cascada(self):
        self._cobro()

        with self.assertRaises(ProtectedError):
            self.cuota.delete()

    def test_mp_user_id_largo_se_rechaza_en_python(self):
        """SQLite no valida el largo de un `varchar`; Postgres sí."""
        with self.assertRaises(ValueError):
            self._cobro(mp_user_id="9" * 41)
