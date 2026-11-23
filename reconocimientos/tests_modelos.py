"""Pieza 5, tarea 2: `PremioOtorgado` y `PremioAplicacion`."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from alumnos.models import Alumno
from core.borrado import arrastres_de_borrado
from pagos.models import Cuota
from reconocimientos.models import Premio, PremioAplicacion, PremioOtorgado
from tenants.models import Gimnasio, Perfil
from tenants.terminos import VERSION_TERMINOS


def _otorgado(gimnasio, alumno, inicio=date(2026, 1, 1), **extra):
    datos = dict(
        gimnasio=gimnasio, alumno=alumno, nombre="Un mes", tipo=Premio.Tipo.PORCENTAJE,
        valor=Decimal("10"), cuotas=2, periodo_inicio=inicio,
        periodo_fin=date(2026, 1, 28), meta=12, asistidas=12,
    )
    datos.update(extra)
    return PremioOtorgado.objects.create(**datos)


def _cuota(gimnasio, alumno, dia=1):
    return Cuota.objects.create(
        gimnasio=gimnasio, alumno=alumno, periodo_inicio=date(2026, 2, dia),
        periodo_fin=date(2026, 2, dia + 3), monto=Decimal("10000"),
    )


class PremioOtorgadoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="A", apellido="B")

    def test_unicidad_parcial_por_ciclo(self):
        anulado = _otorgado(self.gimnasio, self.alumno)
        PremioOtorgado.objects.filter(pk=anulado.pk).update(
            estado=PremioOtorgado.Estado.ANULADO
        )
        _otorgado(self.gimnasio, self.alumno)  # ANULADO + nuevo OTORGADO: OK
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _otorgado(self.gimnasio, self.alumno)

    def test_otro_ciclo_no_choca(self):
        _otorgado(self.gimnasio, self.alumno)
        _otorgado(self.gimnasio, self.alumno, inicio=date(2026, 2, 1))

    def test_una_cuota_no_admite_dos_aplicaciones(self):
        otorgado = _otorgado(self.gimnasio, self.alumno)
        otro = _otorgado(self.gimnasio, self.alumno, inicio=date(2026, 2, 1))
        cuota = _cuota(self.gimnasio, self.alumno)
        PremioAplicacion.objects.create(otorgado=otorgado, cuota=cuota, descuento=Decimal("1"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PremioAplicacion.objects.create(
                    otorgado=otro, cuota=cuota, descuento=Decimal("1")
                )

    def test_borrar_el_alumno_borra_premios_y_aplicaciones(self):
        # Sin cuotas el alumno es borrable; la aplicación cuelga del otorgado.
        otorgado = _otorgado(self.gimnasio, self.alumno)
        self.assertEqual(arrastres_de_borrado(self.alumno), [("premio otorgado", 1)])
        self.alumno.delete()
        self.assertFalse(PremioOtorgado.objects.filter(pk=otorgado.pk).exists())

    def test_borrar_el_otorgado_borra_sus_aplicaciones(self):
        otorgado = _otorgado(self.gimnasio, self.alumno)
        PremioAplicacion.objects.create(
            otorgado=otorgado, cuota=_cuota(self.gimnasio, self.alumno),
            descuento=Decimal("1"),
        )
        otorgado.delete()
        self.assertEqual(PremioAplicacion.objects.count(), 0)

    def test_borrar_la_cuota_borra_la_aplicacion(self):
        otorgado = _otorgado(self.gimnasio, self.alumno)
        cuota = _cuota(self.gimnasio, self.alumno)
        PremioAplicacion.objects.create(otorgado=otorgado, cuota=cuota, descuento=Decimal("1"))
        cuota.delete()
        self.assertEqual(PremioAplicacion.objects.count(), 0)
        self.assertTrue(PremioOtorgado.objects.filter(pk=otorgado.pk).exists())

    def test_alumno_borrable_por_la_vista_real(self):
        usuario = User.objects.create_user("staff", password="clave-123456")
        Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF,
            terminos_version=VERSION_TERMINOS,
        )
        self.client.force_login(usuario)
        otorgado = _otorgado(self.gimnasio, self.alumno)
        respuesta = self.client.post(reverse("alumnos:eliminar", args=[self.alumno.pk]))
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(Alumno.objects.filter(pk=self.alumno.pk).exists())
        self.assertFalse(PremioOtorgado.objects.filter(pk=otorgado.pk).exists())

    def test_borrar_el_perfil_del_otorgante_deja_none(self):
        usuario = User.objects.create_user("dueno", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF,
            terminos_version=VERSION_TERMINOS,
        )
        otorgado = _otorgado(self.gimnasio, self.alumno, otorgado_por=perfil)
        perfil.delete()
        otorgado.refresh_from_db()
        self.assertIsNone(otorgado.otorgado_por)

    def test_borrar_el_premio_del_catalogo_deja_el_otorgado(self):
        premio = Premio.objects.create(gimnasio=self.gimnasio, nombre="P", valor=10)
        otorgado = _otorgado(self.gimnasio, self.alumno, premio=premio)
        premio.delete()
        otorgado.refresh_from_db()
        self.assertIsNone(otorgado.premio)
        self.assertEqual(otorgado.nombre, "Un mes")

    def test_restantes(self):
        otorgado = _otorgado(self.gimnasio, self.alumno, cuotas=3)
        self.assertEqual(otorgado.restantes, 3)
        PremioAplicacion.objects.create(
            otorgado=otorgado, cuota=_cuota(self.gimnasio, self.alumno),
            descuento=Decimal("1"),
        )
        self.assertEqual(otorgado.restantes, 2)

    def test_restantes_usa_el_conteo_anotado(self):
        otorgado = _otorgado(self.gimnasio, self.alumno, cuotas=3)
        otorgado.n_aplicaciones = 5
        with self.assertNumQueries(0):
            self.assertEqual(otorgado.restantes, 0)  # nunca negativo

    def test_restantes_del_obsequio_es_cero(self):
        otorgado = _otorgado(
            self.gimnasio, self.alumno, tipo=Premio.Tipo.OBSEQUIO, valor=None,
            cuotas=1, texto="Una remera",
        )
        self.assertEqual(otorgado.restantes, 0)
