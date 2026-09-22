"""
Tests de la Fase 2 del panel: los pagos que la plataforma le cobra a cada
gimnasio.

Va en un archivo aparte de `plataforma/tests.py` (que cubre el monitor de solo
lectura de la Fase 1) porque las dos cosas se rompen por motivos distintos:
allá, que el conteo de alumnos no se contamine entre gimnasios; acá, que el
período cubierto salga bien, que registrar un pago no pueda escribirse sobre
otro gimnasio, y que la pantalla no empiece a costar una query por pago.
"""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from alumnos.models import Alumno
from plataforma import facturacion
from plataforma.forms import PagoPlataformaForm
from plataforma.models import PagoPlataforma
from plataforma.precios import EstadoPago
from tenants.models import Gimnasio

HOY = date(2026, 6, 1)


def _gimnasio(nombre, dias_desde_el_alta=0, **extra):
    """Un gimnasio dado de alta hace N días, con `creado` fechado de verdad.

    El alta se fecha con el reloj congelado (y no escribiendo `creado`, que es
    `auto_now_add`) para que `inicio_efectivo` tenga algo real que leer.
    """
    instante = datetime(
        HOY.year, HOY.month, HOY.day, 15, 0, tzinfo=dt_timezone.utc
    ) - timedelta(days=dias_desde_el_alta)
    with patch("django.utils.timezone.now", return_value=instante):
        return Gimnasio.objects.create(
            nombre=nombre, slug=nombre.lower().replace(" ", "-"), **extra
        )


def _superadmin():
    return User.objects.create_superuser("jefe", "jefe@ejemplo.com", "clave-123456")


def _pago(gimnasio, usuario, desde, hasta, **extra):
    datos = {
        "fecha_pago": desde,
        "monto_usd": Decimal("10.00"),
        "alumnos_activos": 5,
        "periodo_desde": desde,
        "periodo_hasta": hasta,
        "registrado_por": usuario,
    }
    datos.update(extra)
    return PagoPlataforma.objects.create(gimnasio=gimnasio, **datos)


class PagoPlataformaModeloTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena", 60)
        self.jefe = _superadmin()

    def test_un_periodo_que_termina_antes_de_empezar_no_entra_en_la_base(self):
        """La `CheckConstraint` es la red de último recurso: el form ya lo
        valida, pero un `create` desde la Shell o desde `/admin/` no pasa por
        el form, y un período invertido rompe `cubierto_hasta` para siempre."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            _pago(self.gimnasio, self.jefe, date(2026, 5, 1), date(2026, 4, 1))

    def test_el_periodo_hasta_es_inclusivo_y_se_muestra_como_tal(self):
        pago = _pago(self.gimnasio, self.jefe, date(2026, 5, 1), date(2026, 5, 30))

        self.assertIn("01/05/2026", str(pago))
        self.assertIn("30/05/2026", str(pago))


class CubiertoHastaTests(TestCase):
    """`gimnasios_anotados()` anota hasta qué día está pago cada gimnasio."""

    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena", 60)
        self.otro = _gimnasio("Otro Gim", 60)
        self.jefe = _superadmin()

    def _anotado(self, gimnasio):
        return facturacion.gimnasios_anotados().get(pk=gimnasio.pk)

    def test_sin_pagos_no_hay_nada_cubierto(self):
        self.assertIsNone(self._anotado(self.gimnasio).cubierto_hasta)

    def test_toma_el_periodo_mas_lejano_no_el_ultimo_cargado(self):
        """El superadmin puede registrar un pago atrasado después de uno más
        nuevo. Lo que importa es hasta cuándo está cubierto, no el orden de
        carga."""
        _pago(self.gimnasio, self.jefe, date(2026, 5, 1), date(2026, 5, 30))
        _pago(self.gimnasio, self.jefe, date(2026, 4, 1), date(2026, 4, 30))

        self.assertEqual(
            self._anotado(self.gimnasio).cubierto_hasta, date(2026, 5, 30)
        )

    def test_el_pago_de_un_gimnasio_no_cubre_al_de_al_lado(self):
        _pago(self.gimnasio, self.jefe, date(2026, 5, 1), date(2026, 5, 30))

        self.assertIsNone(self._anotado(self.otro).cubierto_hasta)

    def test_no_contamina_el_conteo_de_alumnos(self):
        for i in range(3):
            Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"A{i}", apellido="Activo"
            )
        _pago(self.gimnasio, self.jefe, date(2026, 4, 1), date(2026, 4, 30))
        _pago(self.gimnasio, self.jefe, date(2026, 5, 1), date(2026, 5, 30))

        self.assertEqual(self._anotado(self.gimnasio).alumnos_activos, 3)

    def test_el_monitor_sale_de_una_sola_tabla_sin_joins(self):
        """Las tres anotaciones multivaluadas van como `Subquery`
        correlacionada, nunca como `Max(...)`/`Count(...)` sobre un join.

        Es un test de la CONSULTA y no del resultado a propósito, y eso se
        verificó mutando el código: con `Max("pagos_plataforma__periodo_hasta")`
        el valor que sale es el mismo (el agregado dedupe las filas
        duplicadas), así que ningún test de valores lo detecta. Lo que cambia
        es que Django tiene que agrupar la consulta del monitor por las 27
        columnas de `Gimnasio`, sobre la tabla entera y en cada carga del
        panel -- y que el queryset queda agrupado, así que la próxima
        anotación multivaluada que alguien agregue (un `Count`, que sí
        multiplica) rompería en silencio. Es exactamente el bug que la Fase 1
        ya pagó entre `alumnos` y `perfiles`.

        Se mira solo la cola de la consulta EXTERNA (lo que viene después de
        su `FROM`): las subconsultas traen sus propios `GROUP BY` adentro, que
        es justamente lo que se quiere.
        """
        sql = str(facturacion.gimnasios_anotados().query).upper()

        # Guard: si el `FROM` deja de escribirse así (otro backend, otro
        # quoting), el `rpartition` devolvería la consulta ENTERA o una
        # cadena vacía y el test pasaría sin mirar nada.
        self.assertIn('FROM "TENANTS_GIMNASIO"', sql)
        cola = sql.rpartition('FROM "TENANTS_GIMNASIO"')[2]
        self.assertNotIn("JOIN", cola)
        self.assertNotIn("GROUP BY", cola)


class EstadoConPagosRegistradosTests(TestCase):
    """Un pago registrado mueve el estado y el próximo vencimiento."""

    def setUp(self):
        self.jefe = _superadmin()

    def _fila(self, gimnasio):
        anotado = facturacion.gimnasios_anotados().get(pk=gimnasio.pk)
        return facturacion.fila_de_gimnasio(anotado, hoy=HOY)

    def test_sin_pagos_y_pasada_la_prueba_esta_vencido(self):
        """Guard del test de abajo: sin él, "queda al día" pasaría igual si el
        pago no tuviera ningún efecto y el gimnasio ya estuviera al día."""
        gimnasio = _gimnasio("Atrasado", 45)

        fila = self._fila(gimnasio)

        self.assertIs(fila.estado, EstadoPago.VENCIDA)
        self.assertEqual(fila.dias_de_atraso, 15)

    def test_con_el_periodo_cubierto_queda_al_dia(self):
        gimnasio = _gimnasio("Puntual", 45)
        # Alta el 17/4 → la prueba termina el 17/5 y ese período llega al 15/6.
        _pago(gimnasio, self.jefe, date(2026, 5, 17), date(2026, 6, 15))

        fila = self._fila(gimnasio)

        self.assertIs(fila.estado, EstadoPago.AL_DIA)
        self.assertEqual(fila.cubierto_hasta, date(2026, 6, 15))
        self.assertEqual(fila.vencimiento, date(2026, 6, 16))
        self.assertEqual(fila.dias_de_atraso, 0)

    def test_el_periodo_siguiente_arranca_el_dia_despues_del_cubierto(self):
        gimnasio = _gimnasio("Puntual", 45)
        _pago(gimnasio, self.jefe, date(2026, 5, 17), date(2026, 6, 15))

        anotado = facturacion.gimnasios_anotados().get(pk=gimnasio.pk)

        self.assertEqual(
            facturacion.periodo_a_cobrar(anotado),
            (date(2026, 6, 16), date(2026, 7, 15)),
        )

    def test_un_pago_viejo_no_alcanza_y_el_gimnasio_sigue_vencido(self):
        gimnasio = _gimnasio("Dejó de pagar", 120)
        _pago(gimnasio, self.jefe, date(2026, 3, 3), date(2026, 4, 1))

        fila = self._fila(gimnasio)

        self.assertIs(fila.estado, EstadoPago.VENCIDA)
        self.assertEqual(fila.vencimiento, date(2026, 4, 2))
        self.assertEqual(fila.dias_de_atraso, 60)


class InicioEfectivoConFacturacionInicioTests(TestCase):
    def test_sin_facturacion_inicio_manda_la_fecha_de_alta(self):
        gimnasio = _gimnasio("Viejo", 100)

        self.assertEqual(facturacion.inicio_efectivo(gimnasio), date(2026, 2, 21))

    def test_facturacion_inicio_le_gana_a_la_fecha_de_alta(self):
        """Es el campo que hace que prender la facturación no sea retroactivo:
        sin él, el día que el panel se estrena todos los clientes aparecen
        vencidos con meses de atraso."""
        gimnasio = _gimnasio("Viejo", 100, facturacion_inicio=date(2026, 5, 20))

        self.assertEqual(facturacion.inicio_efectivo(gimnasio), date(2026, 5, 20))


class FacturacionAplicaTests(TestCase):
    def test_un_gimnasio_comun_se_cobra(self):
        self.assertTrue(_gimnasio("Cliente").facturacion_aplica)

    def test_la_cuenta_demo_no_se_cobra(self):
        self.assertFalse(_gimnasio("Demo", es_demo=True).facturacion_aplica)

    def test_un_gimnasio_exento_no_se_cobra(self):
        self.assertFalse(
            _gimnasio("Regalado", facturacion_exenta=True).facturacion_aplica
        )

    def test_un_gimnasio_retirado_no_se_cobra(self):
        """`activo=False` es "retirado/oculto": ya no es cliente, así que no
        tiene que sumar al ingreso esperado ni aparecer como vencido para
        siempre en «Para cobrar»."""
        self.assertFalse(_gimnasio("Retirado", activo=False).facturacion_aplica)

    def test_un_gimnasio_retirado_queda_exento_en_el_monitor(self):
        _gimnasio("Retirado", 200, activo=False)

        fila = facturacion.filas_del_monitor(hoy=HOY)[0]

        self.assertIs(fila.estado, EstadoPago.EXENTA)
        self.assertEqual(facturacion.kpis([fila])["clientes"], 0)


class KpiEnPesosTests(TestCase):
    COTIZACION = {"venta": Decimal("1500.00")}

    def test_sin_cotizacion_el_ingreso_en_pesos_es_none(self):
        _gimnasio("Cliente", 45)
        filas = facturacion.filas_del_monitor(hoy=HOY)

        self.assertIsNone(facturacion.kpis(filas)["ingreso_mensual_ars"])

    def test_con_cotizacion_pasa_el_ingreso_esperado_a_pesos(self):
        _gimnasio("Cliente", 45)
        filas = facturacion.filas_del_monitor(hoy=HOY)

        kpis = facturacion.kpis(filas, cotizacion=self.COTIZACION)

        self.assertEqual(kpis["ingreso_mensual_usd"], 10)
        self.assertEqual(kpis["ingreso_mensual_ars"], Decimal("15000.00"))


class RegistrarPagoViewTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena", 45)
        self.otro = _gimnasio("Otro Gim", 45)
        self.jefe = _superadmin()
        self.client.force_login(self.jefe)
        for i in range(120):
            Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"A{i}", apellido="Activo"
            )

    def _url(self, gimnasio=None):
        return reverse(
            "plataforma:pago_nuevo", args=[(gimnasio or self.gimnasio).pk]
        )

    def _datos(self, **extra):
        datos = {
            "fecha_pago": "2026-06-01",
            "monto_usd": "15.00",
            "monto_ars": "23047.50",
            "tipo_cambio": "1536.50",
            "alumnos_activos": "120",
            "periodo_desde": "2026-05-17",
            "periodo_hasta": "2026-06-15",
            "notas": "",
        }
        datos.update(extra)
        return datos

    def test_precarga_lo_que_el_superadmin_no_tiene_por_que_calcular(self):
        with patch(
            "plataforma.cambio.cotizacion_dolar",
            return_value={"venta": Decimal("1536.50")},
        ):
            inicial = self.client.get(self._url()).context["form"].initial

        self.assertEqual(inicial["alumnos_activos"], 120)
        self.assertEqual(inicial["monto_usd"], 15)
        self.assertEqual(inicial["tipo_cambio"], Decimal("1536.50"))
        self.assertEqual(inicial["monto_ars"], Decimal("23047.50"))
        self.assertEqual(inicial["periodo_desde"], date(2026, 5, 17))
        self.assertEqual(inicial["periodo_hasta"], date(2026, 6, 15))

    def test_sin_cotizacion_el_formulario_sigue_sirviendo_en_usd(self):
        with patch("plataforma.cambio.cotizacion_dolar", return_value=None):
            inicial = self.client.get(self._url()).context["form"].initial

        self.assertEqual(inicial["monto_usd"], 15)
        self.assertIsNone(inicial.get("tipo_cambio"))
        self.assertIsNone(inicial.get("monto_ars"))

    def test_registra_el_pago_y_deja_al_gimnasio_al_dia(self):
        respuesta = self.client.post(self._url(), self._datos())

        self.assertRedirects(
            respuesta,
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk]),
        )
        pago = PagoPlataforma.objects.get()
        self.assertEqual(pago.gimnasio, self.gimnasio)
        self.assertEqual(pago.registrado_por, self.jefe)
        self.assertEqual(pago.monto_ars, Decimal("23047.50"))

    def test_avisa_si_el_gimnasio_que_acaba_de_pagar_sigue_congelado(self):
        """Registrar el pago NO descongela la cuenta (restaurar el acceso es
        un POST aparte: un pago puede ser parcial o a cuenta de otra cosa).
        Un «Pago registrado» a secas sobre un gimnasio que sigue sin poder
        entrar es la misma mentira que `pagos.views.ConfirmarPagoView` ataja
        adentro del gimnasio, y se descubre igual: por el reclamo del cliente
        que acaba de pagar."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA
        )

        respuesta = self.client.post(self._url(), self._datos(), follow=True)

        textos = [str(m) for m in respuesta.context["messages"]]
        self.assertIn("Pago registrado para Vida Plena.", textos)
        self.assertTrue(
            any("restaurá el acceso desde la ficha" in t for t in textos), textos
        )
        self.assertTrue(any("Suspendida" in t for t in textos), textos)

    def test_no_avisa_nada_si_el_gimnasio_esta_normal(self):
        """Guard del anterior: un aviso que sale siempre deja de leerse."""
        respuesta = self.client.post(self._url(), self._datos(), follow=True)

        textos = [str(m) for m in respuesta.context["messages"]]
        self.assertEqual(textos, ["Pago registrado para Vida Plena."])

    def test_el_gimnasio_sale_de_la_url_y_no_del_formulario(self):
        """FK-injection: `gimnasio` no está en el form, así que un id ajeno en
        el POST tiene que caer al piso. Sin esto, el pago de un cliente le
        quedaría acreditado a otro."""
        self.client.post(self._url(), self._datos(gimnasio=self.otro.pk))

        self.assertEqual(PagoPlataforma.objects.get().gimnasio, self.gimnasio)

    def test_un_periodo_invertido_vuelve_con_error_y_sin_guardar_nada(self):
        """El error tiene que estar EN `periodo_hasta` y en castellano.

        Sin el `clean()` del form el POST igual se rechaza —
        `ModelForm._post_clean` llama a `full_clean()`, que desde Django 4.1
        valida las `CheckConstraint` —, pero el mensaje que sale es
        «Constraint "pago_plataforma_periodo_no_invertido" is violated.», en
        inglés, arriba de todo y sin decir cuál de las dos fechas mover. Se
        verificó mutando el código: con la aserción floja de "el form tiene
        errores" este test pasaba sin el `clean()`.
        """
        respuesta = self.client.post(
            self._url(),
            self._datos(periodo_desde="2026-06-15", periodo_hasta="2026-05-17"),
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(PagoPlataforma.objects.exists())
        self.assertEqual(
            respuesta.context["form"].errors["periodo_hasta"],
            ["El período no puede terminar antes de empezar."],
        )
        self.assertContains(respuesta, "config-error")

    def test_el_mismo_periodo_dos_veces_avisa_y_deja_una_sola_fila(self):
        """El caso real es el doble submit (el form va boosteado por htmx) y
        el «¿lo habré cargado?» del superadmin. Sin esto el segundo pago entra
        sin ruido y el gimnasio queda cobrado dos veces."""
        self.client.post(self._url(), self._datos())

        respuesta = self.client.post(self._url(), self._datos())

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(PagoPlataforma.objects.count(), 1)
        self.assertEqual(
            respuesta.context["form"].errors["periodo_desde"],
            ["Ya hay un pago registrado que arranca ese día."],
        )

    def test_el_choque_que_el_form_no_ve_tampoco_da_un_500(self):
        """El chequeo del `clean()` es check-then-insert: entre el SELECT y el
        INSERT entra el segundo submit. El caso real son las dos pestañas y el
        doble click sobre un formulario boosteado; el freno de JS del template
        cubre el segundo, pero ninguno cubre el primero.

        Se simula salteando el chequeo del form (que es exactamente lo que
        hace la race) para que el rechazo llegue de la `UniqueConstraint`.
        Sin el `try/except`, esto es un `IntegrityError` sin manejar: un 500
        mudo sobre una pantalla en la que el superadmin ya vio su pago
        guardarse una vez."""
        _pago(self.gimnasio, self.jefe, date(2026, 5, 17), date(2026, 6, 15))

        with patch.object(
            PagoPlataformaForm, "_ya_hay_un_pago_que_arranca", return_value=False
        ):
            respuesta = self.client.post(self._url(), self._datos())

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(PagoPlataforma.objects.count(), 1)
        self.assertEqual(
            respuesta.context["form"].errors["periodo_desde"],
            ["Ya hay un pago registrado que arranca ese día."],
        )

    def test_el_formulario_frena_el_doble_click(self):
        """Mismo freno que `plataforma/gimnasio_form.html`: el botón se
        deshabilita en el `submit`. No cierra la race (eso lo hace el
        `try/except` de la vista), pero es lo que ataja el caso real."""
        respuesta = self.client.get(self._url())

        self.assertContains(respuesta, 'id="registrar"')
        self.assertContains(respuesta, "boton.disabled = true")

    def test_el_mismo_periodo_en_otro_gimnasio_si_se_puede_registrar(self):
        """La clave es por gimnasio: dos clientes distintos pagan el mismo
        período todo el tiempo."""
        self.client.post(self._url(), self._datos())

        self.client.post(self._url(self.otro), self._datos())

        self.assertEqual(PagoPlataforma.objects.count(), 2)

    def test_la_base_rechaza_el_periodo_repetido_aunque_no_pase_por_el_form(self):
        """La `UniqueConstraint` es la barrera de verdad; el mensaje del form
        es solo para que se lea bien."""
        _pago(self.gimnasio, self.jefe, date(2026, 5, 17), date(2026, 6, 15))

        with self.assertRaises(IntegrityError), transaction.atomic():
            _pago(self.gimnasio, self.jefe, date(2026, 5, 17), date(2026, 6, 20))

    def test_el_post_no_sale_a_pedir_la_cotizacion(self):
        """Los montos los manda el formulario: pedirla sería esperar hasta 3 s
        contra una API externa para tirar el resultado, con un solo worker de
        gunicorn."""
        with patch("plataforma.cambio.cotizacion_dolar") as cotizacion:
            self.client.post(self._url(), self._datos())

        cotizacion.assert_not_called()

    def test_el_formulario_dice_de_cuando_es_la_cotizacion(self):
        cotizacion = {
            "venta": Decimal("1536.50"),
            "fecha": datetime(2026, 9, 21, 20, 58, tzinfo=dt_timezone.utc),
            "fuente": "dolarapi.com",
        }
        with patch("plataforma.cambio.cotizacion_dolar", return_value=cotizacion):
            respuesta = self.client.get(self._url())

        # 20:58 UTC son las 17:58 en Buenos Aires.
        self.assertContains(respuesta, "Cotización MEP del 21/09 17:58")

    def test_un_dueno_de_gimnasio_no_puede_registrar_pagos_de_la_plataforma(self):
        from tenants.models import Perfil

        staff = User.objects.create_user("dueno", password="clave-123456")
        Perfil.objects.create(
            usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.force_login(staff)

        self.assertEqual(self.client.get(self._url()).status_code, 403)


class EditarFacturacionViewTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena", 200)
        self.client.force_login(_superadmin())

    def _url(self):
        return reverse("plataforma:facturacion_editar", args=[self.gimnasio.pk])

    def test_mueve_el_arranque_de_la_facturacion(self):
        respuesta = self.client.post(
            self._url(),
            {"facturacion_inicio": "2026-05-20", "facturacion_exenta": ""},
        )

        self.assertRedirects(
            respuesta,
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk]),
        )
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.facturacion_inicio, date(2026, 5, 20))
        self.assertFalse(self.gimnasio.facturacion_exenta)

    def test_puede_dejar_un_gimnasio_exento(self):
        self.client.post(
            self._url(),
            {"facturacion_inicio": "2026-05-20", "facturacion_exenta": "on"},
        )

        self.gimnasio.refresh_from_db()
        self.assertTrue(self.gimnasio.facturacion_exenta)

    def test_no_se_puede_vaciar_el_inicio_de_facturacion(self):
        """Obligatorio en el form aunque sea `blank=True` en el modelo, mismo
        criterio que `AlumnoForm.fecha_inicio_ciclo`: vaciarlo deshace la
        migración de datos, `inicio_efectivo` vuelve a caer en la fecha de
        alta y este gimnasio (dado de alta hace 200 días) pasa a VENCIDA con
        meses de atraso, en silencio."""
        self.gimnasio.facturacion_inicio = date(2026, 5, 20)
        self.gimnasio.save()

        respuesta = self.client.post(
            self._url(),
            {"facturacion_inicio": "", "facturacion_exenta": ""},
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context["form"].errors["facturacion_inicio"])
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.facturacion_inicio, date(2026, 5, 20))

    def test_no_toca_modificado(self):
        """Mismo criterio que `EstadoCuentaView` y `ExportacionToggleView`:
        `modificado` versiona la URL del logo, la del fondo y la del ícono de
        la PWA. Cambiarle la fecha de facturación a un gimnasio no tiene nada
        que ver con sus archivos, y si la tocara, todos sus celulares y
        navegadores se volverían a bajar el ícono y el logo."""
        antes = Gimnasio.objects.get(pk=self.gimnasio.pk).modificado

        self.client.post(
            self._url(),
            {"facturacion_inicio": "2026-05-20", "facturacion_exenta": "on"},
        )

        despues = Gimnasio.objects.get(pk=self.gimnasio.pk)
        self.assertEqual(despues.modificado, antes)
        # Y lo que sí se pidió cambiar, cambió.
        self.assertEqual(despues.facturacion_inicio, date(2026, 5, 20))
        self.assertTrue(despues.facturacion_exenta)

    def test_precarga_la_fecha_que_ya_esta_en_efecto(self):
        """Un gimnasio con `facturacion_inicio` en `NULL` llegaría con el
        campo vacío a un formulario que ahora lo exige. Se precarga con la
        fecha de alta, que es la que el panel venía usando: guardar sin tocar
        nada no cambia nada."""
        self.assertIsNone(self.gimnasio.facturacion_inicio)

        inicial = self.client.get(self._url()).context["form"].initial

        self.assertEqual(
            inicial["facturacion_inicio"],
            facturacion.inicio_efectivo(self.gimnasio),
        )

    def test_un_alumno_no_entra(self):
        from tenants.models import Perfil

        alumno = User.objects.create_user("alu", password="clave-123456")
        Perfil.objects.create(
            usuario=alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.force_login(alumno)

        self.assertEqual(self.client.get(self._url()).status_code, 403)


class DetalleConFacturacionTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena", 45)
        self.jefe = _superadmin()
        self.client.force_login(self.jefe)

    def _get(self):
        return self.client.get(
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk])
        )

    def test_lista_los_pagos_registrados(self):
        _pago(
            self.gimnasio,
            self.jefe,
            date(2026, 5, 17),
            date(2026, 6, 15),
            notas="Transferencia",
        )

        respuesta = self._get()

        self.assertContains(respuesta, "17/05/2026")
        self.assertContains(respuesta, "15/06/2026")
        self.assertContains(respuesta, "Transferencia")

    def test_sin_pagos_lo_dice_en_castellano(self):
        self.assertContains(self._get(), "Todavía no se le registró ningún pago")

    def test_muestra_la_cotizacion_con_su_fuente(self):
        cotizacion = {
            "venta": Decimal("1536.50"),
            "compra": Decimal("1531.60"),
            "fecha": datetime(2026, 9, 21, 20, 58, tzinfo=dt_timezone.utc),
            "fuente": "dolarapi.com",
        }
        with patch("plataforma.cambio.cotizacion_dolar", return_value=cotizacion):
            respuesta = self._get()

        self.assertContains(respuesta, "1536,50")
        self.assertContains(respuesta, "dolarapi.com")
        # La API fecha en UTC y la pantalla tiene que mostrar hora local:
        # 20:58 en Londres son las 17:58 en Buenos Aires. El filtro `date` lo
        # hace solo (va declarado `expects_localtime=True`), pero es
        # exactamente el tipo de cosa que se rompe sin que nadie la note --
        # un cartel con la hora equivocada se lee como una cotización vieja.
        self.assertContains(respuesta, "21/09/2026 17:58")

    def test_sin_cotizacion_lo_dice_en_vez_de_mostrar_un_hueco(self):
        with patch("plataforma.cambio.cotizacion_dolar", return_value=None):
            self.assertContains(self._get(), "Sin cotización")

    def test_tiene_los_dos_botones_de_la_fase(self):
        respuesta = self._get()

        self.assertContains(
            respuesta, reverse("plataforma:pago_nuevo", args=[self.gimnasio.pk])
        )
        self.assertContains(
            respuesta,
            reverse("plataforma:facturacion_editar", args=[self.gimnasio.pk]),
        )


class CostoConPagosTests(TestCase):
    """Ni el monitor ni la ficha pueden empezar a costar una query por pago.

    Se comparan dos tamaños de conjunto, nunca un número fijo: el número
    absoluto se mueve con cualquier cambio interno de Django y el test
    empezaría a fallar sin que nada esté mal.
    """

    def setUp(self):
        self.jefe = _superadmin()
        self.client.force_login(self.jefe)

    def _poblar(self, gimnasios, pagos, prefijo):
        for g in range(gimnasios):
            gimnasio = _gimnasio(f"{prefijo} {g}", 200)
            desde = date(2024, 1, 1)
            for _ in range(pagos):
                _pago(gimnasio, self.jefe, desde, desde + timedelta(days=29))
                desde += timedelta(days=30)

    def test_el_monitor_no_crece_con_la_cantidad_de_pagos(self):
        self._poblar(2, 1, "chico")
        with CaptureQueriesContext(connection) as chico:
            facturacion.filas_del_monitor(hoy=HOY)

        self._poblar(2, 20, "grande")
        with CaptureQueriesContext(connection) as grande:
            facturacion.filas_del_monitor(hoy=HOY)

        self.assertEqual(len(grande), len(chico))

    def test_la_pantalla_del_monitor_tampoco_crece(self):
        url = reverse("plataforma:inicio")

        self._poblar(2, 1, "chico")
        with CaptureQueriesContext(connection) as chico:
            self.assertEqual(self.client.get(url).status_code, 200)

        self._poblar(12, 20, "grande")
        with CaptureQueriesContext(connection) as grande:
            self.assertEqual(self.client.get(url).status_code, 200)

        self.assertEqual(len(grande), len(chico))

    def test_la_ficha_no_crece_con_los_pagos_del_gimnasio(self):
        """Acá los pagos SÍ se listan, así que la garantía es otra: la tabla
        sale de UNA query, no de una por fila."""
        self._poblar(1, 1, "chico")
        chico_pk = Gimnasio.objects.get(nombre="chico 0").pk
        with CaptureQueriesContext(connection) as chico:
            self.client.get(reverse("plataforma:gimnasio_detalle", args=[chico_pk]))

        self._poblar(1, 20, "grande")
        grande_pk = Gimnasio.objects.get(nombre="grande 0").pk
        with CaptureQueriesContext(connection) as grande:
            self.client.get(reverse("plataforma:gimnasio_detalle", args=[grande_pk]))

        self.assertEqual(len(grande), len(chico))
