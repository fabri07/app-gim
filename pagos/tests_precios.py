"""Precio de la cuota por gimnasio y por alumno (T1 de «cuotas por Mercado
Pago»).

Qué se fija:
- el cron emite la cuota con el precio que corresponde (alumno > gimnasio > 0);
- guardar un precio completa las cuotas impagas que siguen en $0, y nunca pisa
  un monto ya puesto ni toca una cuota pagada o anulada;
- un precio negativo se rechaza con el error a la vista.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from pagos.models import Cuota, generar_pagos_pendientes
from pagos.precios import aplicar_precio_a_impagas_sin_monto
from pagos.testing import crear_cuota
from tenants.models import Gimnasio, Perfil

User = get_user_model()


class GenerarConPrecioTests(TestCase):
    def setUp(self):
        self.hoy = date(2026, 7, 15)
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio A", slug="gimnasio-a", precio_cuota=Decimal("25000")
        )

    def _alumno(self, nombre, gimnasio=None, **extra):
        return Alumno.objects.create(
            gimnasio=gimnasio or self.gimnasio, nombre=nombre, apellido="X",
            estado=Alumno.Estado.ACTIVO, fecha_inicio_ciclo=date(2026, 7, 1),
            **extra,
        )

    def test_gana_el_precio_del_alumno(self):
        alumno = self._alumno("Propio", precio_cuota=Decimal("18000.50"))

        generar_pagos_pendientes(self.hoy)

        self.assertEqual(Cuota.objects.get(alumno=alumno).monto, Decimal("18000.50"))

    def test_sin_precio_propio_usa_el_del_gimnasio(self):
        alumno = self._alumno("Sin propio")

        generar_pagos_pendientes(self.hoy)

        self.assertEqual(Cuota.objects.get(alumno=alumno).monto, Decimal("25000"))

    def test_sin_ningun_precio_sale_en_cero(self):
        sin_precio = Gimnasio.objects.create(nombre="Sin precio", slug="sin-precio")
        alumno = self._alumno("Nadie", gimnasio=sin_precio)

        generar_pagos_pendientes(self.hoy)

        self.assertEqual(Cuota.objects.get(alumno=alumno).monto, Decimal("0"))

    def test_precio_cero_explicito_no_cae_al_del_gimnasio(self):
        """0 es un valor, no un «vacío»: el Coalesce sólo salta los NULL."""
        alumno = self._alumno("Cero", precio_cuota=Decimal("0"))

        generar_pagos_pendientes(self.hoy)

        self.assertEqual(Cuota.objects.get(alumno=alumno).monto, Decimal("0"))

    def test_el_alumno_sin_fecha_de_inicio_sigue_sin_cobrarse(self):
        alumno = self._alumno("Sin ancla", precio_cuota=Decimal("10000"))
        Alumno.objects.filter(pk=alumno.pk).update(fecha_inicio_ciclo=None)

        generar_pagos_pendientes(self.hoy)

        self.assertFalse(Cuota.objects.filter(alumno=alumno).exists())


class AplicarPrecioAImpagasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )
        self.inicio = timezone.localdate() - timedelta(days=10)

    def _cuota(self, alumno=None, inicio=None, **extra):
        alumno = alumno or self.alumno
        return crear_cuota(
            gimnasio=alumno.gimnasio, alumno=alumno, inicio=inicio or self.inicio, **extra
        )

    def test_completa_las_impagas_en_cero_del_gimnasio(self):
        pendiente = self._cuota()
        vencida = self._cuota(
            inicio=self.inicio - timedelta(days=28), estado=Cuota.Estado.VENCIDO
        )
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()

        cantidad = aplicar_precio_a_impagas_sin_monto(self.gimnasio)

        self.assertEqual(cantidad, 2)
        pendiente.refresh_from_db()
        vencida.refresh_from_db()
        self.assertEqual(pendiente.monto, Decimal("30000"))
        self.assertEqual(vencida.monto, Decimal("30000"))

    def test_no_pisa_un_monto_ya_puesto_ni_pagadas_ni_anuladas(self):
        con_monto = self._cuota(monto=Decimal("12000"))
        pagada = self._cuota(
            inicio=self.inicio - timedelta(days=28), estado=Cuota.Estado.PAGADO,
            fecha_pago=self.inicio,
        )
        anulada = self._cuota(
            inicio=self.inicio - timedelta(days=56), estado=Cuota.Estado.ANULADO
        )
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()

        self.assertEqual(aplicar_precio_a_impagas_sin_monto(self.gimnasio), 0)

        for cuota, esperado in ((con_monto, "12000"), (pagada, "0"), (anulada, "0")):
            cuota.refresh_from_db()
            self.assertEqual(cuota.monto, Decimal(esperado))

    def test_el_precio_del_gimnasio_no_pisa_al_alumno_con_precio_propio(self):
        self.alumno.precio_cuota = Decimal("9000")
        self.alumno.save()
        cuota = self._cuota()
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()

        aplicar_precio_a_impagas_sin_monto(self.gimnasio)

        cuota.refresh_from_db()
        self.assertEqual(cuota.monto, Decimal("0"))

    def test_no_toca_otro_gimnasio(self):
        otro = Gimnasio.objects.create(nombre="Otro", slug="otro")
        ajeno = Alumno.objects.create(gimnasio=otro, nombre="Ana", apellido="B")
        cuota_ajena = self._cuota(alumno=ajeno)
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()

        aplicar_precio_a_impagas_sin_monto(self.gimnasio)

        cuota_ajena.refresh_from_db()
        self.assertEqual(cuota_ajena.monto, Decimal("0"))

    def test_por_alumno_usa_su_precio_y_solo_sus_cuotas(self):
        otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="B"
        )
        mia = self._cuota()
        ajena = self._cuota(alumno=otro_alumno)
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()
        self.alumno.precio_cuota = Decimal("9000")
        self.alumno.save()

        cantidad = aplicar_precio_a_impagas_sin_monto(self.gimnasio, alumno=self.alumno)

        self.assertEqual(cantidad, 1)
        mia.refresh_from_db()
        ajena.refresh_from_db()
        self.assertEqual(mia.monto, Decimal("9000"))
        self.assertEqual(ajena.monto, Decimal("0"))

    def test_por_alumno_sin_precio_propio_cae_al_del_gimnasio(self):
        cuota = self._cuota()
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()

        aplicar_precio_a_impagas_sin_monto(self.gimnasio, alumno=self.alumno)

        cuota.refresh_from_db()
        self.assertEqual(cuota.monto, Decimal("30000"))

    def test_sin_precio_no_hace_nada(self):
        cuota = self._cuota()

        self.assertEqual(aplicar_precio_a_impagas_sin_monto(self.gimnasio), 0)
        self.assertEqual(
            aplicar_precio_a_impagas_sin_monto(self.gimnasio, alumno=self.alumno), 0
        )
        cuota.refresh_from_db()
        self.assertEqual(cuota.monto, Decimal("0"))

    def test_escribe_modificado_en_las_pendientes(self):
        cuota = self._cuota()
        Cuota.objects.filter(pk=cuota.pk).update(
            modificado=timezone.now() - timedelta(days=3)
        )
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()

        aplicar_precio_a_impagas_sin_monto(self.gimnasio)

        cuota.refresh_from_db()
        self.assertEqual(cuota.modificado.date(), timezone.now().date())

    def test_no_reestampa_modificado_en_las_vencidas(self):
        """`enviar_recordatorios` manda «tu cuota está vencida» por
        `estado=VENCIDO, modificado__date=hoy`: re-estampar `modificado` al
        cargar un precio dispararía ese push sobre vencidas viejas."""
        vencida = self._cuota(estado=Cuota.Estado.VENCIDO)
        hace_tres_dias = timezone.now() - timedelta(days=3)
        Cuota.objects.filter(pk=vencida.pk).update(modificado=hace_tres_dias)
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()

        aplicar_precio_a_impagas_sin_monto(self.gimnasio)

        vencida.refresh_from_db()
        self.assertEqual(vencida.monto, Decimal("30000"))
        self.assertEqual(vencida.modificado, hace_tres_dias)

    def test_el_costo_no_crece_con_la_cantidad_de_cuotas(self):
        self.gimnasio.precio_cuota = Decimal("30000")
        self.gimnasio.save()
        for i in range(3):
            self._cuota(inicio=self.inicio - timedelta(days=28 * i))
        with self.assertNumQueries(1):
            aplicar_precio_a_impagas_sin_monto(self.gimnasio)

        for i in range(3, 30):
            self._cuota(inicio=self.inicio - timedelta(days=28 * i))
        with self.assertNumQueries(1):
            aplicar_precio_a_impagas_sin_monto(self.gimnasio)


class PrecioDesdeLasVistasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio A", slug="gimnasio-a",
            tipo_publico=Gimnasio.TipoPublico.MIXTO,
        )
        self.staff = User.objects.create_user("dueno", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez",
            estado=Alumno.Estado.ACTIVO, sexo=Alumno.Sexo.MASCULINO,
        )
        self.inicio = timezone.localdate() - timedelta(days=5)
        Alumno.objects.filter(pk=self.alumno.pk).update(fecha_inicio_ciclo=self.inicio)
        self.alumno.refresh_from_db()
        self.cuota = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno, inicio=self.inicio
        )
        self.client.login(username="dueno", password="clave-123456")

    def _datos_gimnasio(self, **extra):
        datos = {
            "nombre": "Gimnasio A",
            "tipo_publico": "mixto",
            "paleta": self.gimnasio.paleta,
            "tipografia": self.gimnasio.tipografia,
            "fondo_tipo": "color",
            "precio_cuota": "",
        }
        datos.update(extra)
        return datos

    def _datos_alumno(self, **extra):
        datos = {
            "nombre": "Juan",
            "apellido": "Perez",
            "email": "",
            "telefono": "",
            "fecha_nacimiento": "",
            "estado": Alumno.Estado.ACTIVO,
            "fecha_inicio_ciclo": self.inicio.isoformat(),
            "sexo": Alumno.Sexo.MASCULINO,
            "observaciones": "",
            "precio_cuota": "",
        }
        datos.update(extra)
        return datos

    def test_guardar_el_precio_del_gimnasio_completa_la_impaga_en_cero(self):
        response = self.client.post(
            reverse("gimnasio_editar"), self._datos_gimnasio(precio_cuota="27500")
        )

        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.gimnasio.refresh_from_db()
        self.cuota.refresh_from_db()
        self.assertEqual(self.gimnasio.precio_cuota, Decimal("27500"))
        self.assertEqual(self.cuota.monto, Decimal("27500"))

    def test_guardar_otro_campo_del_gimnasio_no_toca_cuotas(self):
        """Sólo un CAMBIO de precio se aplica: guardar el form por otro campo
        no tiene por qué reescribir cuotas."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(precio_cuota=Decimal("100"))
        response = self.client.post(
            reverse("gimnasio_editar"),
            self._datos_gimnasio(precio_cuota="100", nombre="Otro nombre"),
        )

        self.assertRedirects(response, reverse("gimnasio_editar"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("0"))

    def test_precio_negativo_del_gimnasio_muestra_el_error(self):
        response = self.client.post(
            reverse("gimnasio_editar"), self._datos_gimnasio(precio_cuota="-1")
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("precio_cuota", response.context["form"].errors)
        self.assertContains(response, 'class="config-error"')
        self.gimnasio.refresh_from_db()
        self.assertIsNone(self.gimnasio.precio_cuota)

    def test_el_form_del_gimnasio_muestra_el_campo_en_cobros(self):
        response = self.client.get(reverse("gimnasio_editar"))

        self.assertContains(response, 'name="precio_cuota"')
        self.assertNotContains(response, 'name="cuotas_con_mercadopago"')

    def test_guardar_el_precio_del_alumno_completa_su_impaga_en_cero(self):
        response = self.client.post(
            reverse("alumnos:editar", args=[self.alumno.pk]),
            self._datos_alumno(precio_cuota="15000"),
        )

        self.assertEqual(response.status_code, 302)
        self.alumno.refresh_from_db()
        self.cuota.refresh_from_db()
        self.assertEqual(self.alumno.precio_cuota, Decimal("15000"))
        self.assertEqual(self.cuota.monto, Decimal("15000"))

    def test_precio_negativo_del_alumno_muestra_el_error(self):
        response = self.client.post(
            reverse("alumnos:editar", args=[self.alumno.pk]),
            self._datos_alumno(precio_cuota="-5"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("precio_cuota", response.context["form"].errors)
        self.assertContains(response, 'class="config-error"')

    def test_la_ficha_muestra_el_precio(self):
        Alumno.objects.filter(pk=self.alumno.pk).update(precio_cuota=Decimal("15000"))
        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno.pk]))
        self.assertContains(response, "15000")

        Alumno.objects.filter(pk=self.alumno.pk).update(precio_cuota=None)
        response = self.client.get(reverse("alumnos:detalle", args=[self.alumno.pk]))
        self.assertContains(response, "el del gimnasio")
