"""`pagos/cobro.py` y las vistas que dejaron de pisar un cobro online (T3 de
«cuotas por Mercado Pago»).

Qué se fija:
- las carreras que pisaban un cobro de Mercado Pago: confirmar, subir el
  comprobante, anular y corregir el monto sobre una cuota que se acaba de
  pagar NO la tocan (cada test de carrera cambia la fila en la base entre la
  lectura de la vista y su escritura);
- confirmar, corregir el monto y anular cancelan los links de pago vivos;
- «Volver a impaga» sólo para una cuota cuyo cobro por MP se devolvió;
- una cuota cobrada por MP no deja cambiar ni el monto ni el medio.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.fields.files import FieldFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from pagos import cobro
from pagos.models import CobroOnlineCuota, Cuota
from pagos.testing import crear_cuota
from pagos.views import AlumnoComprobanteUpdateView, CuotaAnularView, CuotaMontoView
from tenants.models import Gimnasio, Perfil

User = get_user_model()

Estado = Cuota.Estado
EstadoCobro = CobroOnlineCuota.Estado


def _mensajes(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.hoy = timezone.localdate()
        self.cuota = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=self.hoy - timedelta(days=3), monto=Decimal("28000"),
        )

    def _intento(self, cuota=None, **extra):
        return CobroOnlineCuota.objects.create(
            gimnasio=self.gimnasio, cuota=cuota or self.cuota,
            monto=Decimal("28000"), **extra,
        )

    def _cobrar_por_mp(self, cuota=None):
        """Lo que va a hacer la acreditación de MP (T5): la cuota PAGADA por
        UPDATE y su intento PAGADO."""
        cuota = cuota or self.cuota
        Cuota.objects.filter(pk=cuota.pk).update(
            estado=Estado.PAGADO, monto=Decimal("28000"),
            fecha_pago=self.hoy, medio_pago_texto="Mercado Pago",
        )
        return self._intento(cuota, estado=EstadoCobro.PAGADO, payment_id="999")

    def _login_staff(self):
        self.client.login(username="staff-a", password="clave-123456")


# ---------------------------------------------------------------------------
# Dominio
# ---------------------------------------------------------------------------


class MarcarPagadaTests(_Base):
    def test_camino_staff_la_paga_y_cancela_los_links_vivos(self):
        vivo = self._intento()

        self.assertTrue(cobro.marcar_pagada(self.cuota))

        self.cuota.refresh_from_db()
        vivo.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)
        self.assertEqual(vivo.estado, EstadoCobro.CANCELADO)

    def test_camino_staff_no_guarda_si_ya_se_cobro_por_mp(self):
        self._cobrar_por_mp()
        en_memoria = Cuota.objects.get(pk=self.cuota.pk)
        en_memoria.monto = Decimal("1")

        self.assertFalse(cobro.marcar_pagada(en_memoria))

        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("28000"))

    def _con_comprobante(self, cuota=None):
        cuota = cuota or Cuota.objects.get(pk=self.cuota.pk)
        cuota.comprobante = SimpleUploadedFile(
            "comprobante.jpg", b"contenido", content_type="image/jpeg"
        )
        cuota.fecha_pago = self.hoy
        cuota.medio_pago_texto = "Efectivo"
        return cuota

    def test_camino_staff_sube_el_comprobante_antes_de_tomar_los_locks(self):
        """Subir a R2 tarda: no puede pasar con la cuota y sus intentos
        bloqueados (la acreditación de MP esperaría detrás)."""
        orden = []
        subir_original = FieldFile.save
        bloquear_original = cobro._bloquear

        def subir(campo, *args, **kwargs):
            orden.append("subir")
            return subir_original(campo, *args, **kwargs)

        def bloquear(cuota_pk):
            orden.append("bloquear")
            return bloquear_original(cuota_pk)

        cuota = self._con_comprobante()
        with patch.object(FieldFile, "save", autospec=True, side_effect=subir), \
                patch.object(cobro, "_bloquear", side_effect=bloquear):
            self.assertTrue(cobro.marcar_pagada(cuota))

        self.assertEqual(orden, ["subir", "bloquear"])
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)
        self.assertTrue(self.cuota.comprobante.name.startswith("comprobantes/"))
        self.assertTrue(self.cuota.comprobante.storage.exists(self.cuota.comprobante.name))

    def test_camino_staff_guarda_solo_los_campos_del_form(self):
        """`update_fields`: lo que otro cambió en la fila mientras el form
        estaba abierto (y no es del form) no se pisa."""
        cuota = self._con_comprobante()
        Cuota.objects.filter(pk=self.cuota.pk).update(observaciones="anotado en el medio")

        self.assertTrue(cobro.marcar_pagada(cuota))

        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.observaciones, "anotado en el medio")
        self.assertEqual(self.cuota.medio_pago_texto, "Efectivo")
        self.assertEqual(self.cuota.fecha_pago, self.hoy)

    def test_camino_staff_si_mp_la_cobro_borra_el_comprobante_recien_subido(self):
        cuota = self._con_comprobante()
        self._cobrar_por_mp()

        self.assertFalse(cobro.marcar_pagada(cuota))

        self.assertTrue(cuota.comprobante.name)
        self.assertFalse(cuota.comprobante.storage.exists(cuota.comprobante.name))
        self.cuota.refresh_from_db()
        self.assertFalse(self.cuota.comprobante)

    def test_camino_mp_update_condicional(self):
        vivo = self._intento()
        ahora = timezone.now()

        marcada = cobro.marcar_pagada(
            self.cuota, desde=Cuota.ESTADOS_IMPAGOS, ahora=ahora,
            fecha_pago=self.hoy, medio_pago_texto="Mercado Pago",
        )

        self.assertTrue(marcada)
        self.cuota.refresh_from_db()
        vivo.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)
        self.assertEqual(self.cuota.medio_pago_texto, "Mercado Pago")
        self.assertEqual(self.cuota.modificado, ahora)
        self.assertEqual(vivo.estado, EstadoCobro.CANCELADO)

    def test_camino_mp_no_toca_una_anulada(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=Estado.ANULADO)
        vivo = self._intento()

        self.assertFalse(
            cobro.marcar_pagada(self.cuota, desde=Cuota.ESTADOS_IMPAGOS)
        )

        self.cuota.refresh_from_db()
        vivo.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.ANULADO)
        self.assertEqual(vivo.estado, EstadoCobro.CREADO)

    def test_cancelar_links_vivos_solo_toca_los_creado_de_esa_cuota(self):
        otra = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=self.hoy - timedelta(days=31), monto=Decimal("28000"),
        )
        vivo = self._intento()
        rechazado = self._intento(estado=EstadoCobro.RECHAZADO)
        de_otra = self._intento(otra)

        self.assertEqual(cobro.cancelar_links_vivos(self.cuota.pk, timezone.now()), 1)

        for intento, esperado in (
            (vivo, EstadoCobro.CANCELADO),
            (rechazado, EstadoCobro.RECHAZADO),
            (de_otra, EstadoCobro.CREADO),
        ):
            intento.refresh_from_db()
            self.assertEqual(intento.estado, esperado)


class CorregirMontoTests(_Base):
    def test_corrige_una_impaga_y_cancela_sus_links(self):
        vivo = self._intento()

        self.assertTrue(
            cobro.corregir_monto(self.cuota.pk, self.gimnasio, Decimal("30000"))
        )

        self.cuota.refresh_from_db()
        vivo.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("30000"))
        self.assertEqual(vivo.estado, EstadoCobro.CANCELADO)

    def test_no_toca_una_pagada(self):
        self._cobrar_por_mp()

        self.assertFalse(
            cobro.corregir_monto(self.cuota.pk, self.gimnasio, Decimal("1"))
        )

        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("28000"))

    def test_no_toca_la_cuota_de_otro_gimnasio(self):
        self.assertFalse(
            cobro.corregir_monto(self.cuota.pk, self.otro_gimnasio, Decimal("1"))
        )


# ---------------------------------------------------------------------------
# Confirmar pago (staff)
# ---------------------------------------------------------------------------


class ConfirmarPagoCarreraTests(_Base):
    def _post(self, estado_al_abrir="pendiente", **datos):
        datos = {
            "monto": "25000", "fecha_pago": self.hoy.isoformat(),
            "medio_pago_texto": "Efectivo", "estado_al_abrir": estado_al_abrir,
            **datos,
        }
        return self.client.post(reverse("pagos:confirmar", args=[self.cuota.pk]), datos)

    def setUp(self):
        super().setUp()
        self._login_staff()

    def test_confirmar_cancela_los_links_vivos(self):
        vivo = self._intento()

        response = self._post()

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        vivo.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)
        self.assertEqual(vivo.estado, EstadoCobro.CANCELADO)

    def test_no_pisa_una_cuota_cobrada_por_mp_mientras_el_form_estaba_abierto(self):
        """El staff abrió «Confirmar» con la cuota pendiente; antes de que
        guarde, MP la cobró. Su efectivo no puede pisar el cobro."""
        self.client.get(reverse("pagos:confirmar", args=[self.cuota.pk]))
        self._cobrar_por_mp()

        response = self._post(estado_al_abrir="pendiente")

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("28000"))
        self.assertEqual(self.cuota.medio_pago_texto, "Mercado Pago")
        self.assertIn(
            "Esta cuota se acaba de cobrar por Mercado Pago; no se guardó tu cambio.",
            _mensajes(response),
        )

    def test_cobrada_por_mp_el_form_no_deja_cambiar_monto_ni_medio(self):
        self._cobrar_por_mp()

        response = self.client.get(reverse("pagos:confirmar", args=[self.cuota.pk]))
        form = response.context["form"]
        self.assertTrue(form.fields["monto"].disabled)
        self.assertTrue(form.fields["medio_pago_texto"].disabled)
        self.assertFalse(form.fields["fecha_pago"].disabled)

        nueva_fecha = self.hoy - timedelta(days=1)
        response = self._post(
            estado_al_abrir="pagado", monto="1", medio_pago_texto="Efectivo",
            fecha_pago=nueva_fecha.isoformat(),
        )

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("28000"))
        self.assertEqual(self.cuota.medio_pago_texto, "Mercado Pago")
        self.assertEqual(self.cuota.fecha_pago, nueva_fecha)
        self.assertEqual(self.cuota.estado, Estado.PAGADO)

    def test_pagada_a_mano_se_sigue_editando_entera(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            estado=Estado.PAGADO, fecha_pago=self.hoy, medio_pago_texto="Efectivo"
        )

        response = self._post(estado_al_abrir="pagado", monto="31000")

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("31000"))

    def test_editar_una_pagada_que_volvio_a_impaga_no_la_vuelve_a_pagar(self):
        """El staff abrió «Editar» sobre una pagada; en el medio, el pago se
        devolvió y la cuota volvió a impaga. Guardar no la marca pagada."""
        response = self._post(estado_al_abrir="pagado")

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PENDIENTE)
        self.assertTrue(any("cambió" in m for m in _mensajes(response)))


# ---------------------------------------------------------------------------
# Comprobante (alumno)
# ---------------------------------------------------------------------------


class ComprobanteCarreraTests(_Base):
    def setUp(self):
        super().setUp()
        usuario = User.objects.create_user("alumno-a", password="clave-123456")
        self.alumno.perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.save()
        self.client.login(username="alumno-a", password="clave-123456")

    def _post(self):
        return self.client.post(
            reverse("pagos:comprobante_subir", args=[self.cuota.pk]),
            {"comprobante": SimpleUploadedFile(
                "comprobante.jpg", b"contenido", content_type="image/jpeg"
            )},
        )

    @patch("notificaciones.services.notificar_comprobante_subido")
    def test_sobre_una_cuota_recien_pagada_no_la_vuelve_a_pendiente(self, notificar):
        original = AlumnoComprobanteUpdateView.get_object
        test = self

        def get_object_y_mp_la_cobra(vista, *args, **kwargs):
            objeto = original(vista, *args, **kwargs)
            test._cobrar_por_mp()
            return objeto

        with patch.object(AlumnoComprobanteUpdateView, "get_object", get_object_y_mp_la_cobra), \
                self.captureOnCommitCallbacks(execute=True):
            response = self._post()

        self.assertRedirects(response, reverse("home"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)
        self.assertFalse(self.cuota.comprobante)
        notificar.assert_not_called()

    @patch("notificaciones.services.notificar_comprobante_subido")
    def test_el_caso_normal_guarda_solo_el_comprobante_y_avisa(self, notificar):
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post()

        self.assertRedirects(response, reverse("home"))
        self.cuota.refresh_from_db()
        self.assertTrue(self.cuota.comprobante.name.startswith("comprobantes/"))
        self.assertEqual(self.cuota.estado, Estado.PENDIENTE)
        notificar.assert_called_once()


# ---------------------------------------------------------------------------
# Anular (staff)
# ---------------------------------------------------------------------------


class AnularCarreraTests(_Base):
    def setUp(self):
        super().setUp()
        self._login_staff()

    def test_anular_cancela_los_links_vivos(self):
        vivo = self._intento()

        self.client.post(reverse("pagos:anular", args=[self.cuota.pk]))

        self.cuota.refresh_from_db()
        vivo.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.ANULADO)
        self.assertEqual(vivo.estado, EstadoCobro.CANCELADO)

    def test_no_anula_una_cuota_recien_pagada(self):
        original = CuotaAnularView.get_object
        test = self

        def get_object_y_mp_la_cobra(vista, *args, **kwargs):
            objeto = original(vista, *args, **kwargs)
            test._cobrar_por_mp()
            return objeto

        with patch.object(CuotaAnularView, "get_object", get_object_y_mp_la_cobra):
            response = self.client.post(reverse("pagos:anular", args=[self.cuota.pk]))

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)
        self.assertTrue(any("no se anuló" in m.lower() for m in _mensajes(response)))

    def test_reactivar_sigue_andando(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=Estado.ANULADO)

        response = self.client.post(reverse("pagos:anular", args=[self.cuota.pk]))

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PENDIENTE)


# ---------------------------------------------------------------------------
# Corregir monto (staff)
# ---------------------------------------------------------------------------


class CuotaMontoViewTests(_Base):
    def setUp(self):
        super().setUp()
        self._login_staff()
        self.url = reverse("pagos:monto", args=[self.cuota.pk])

    def test_corrige_y_cancela_los_links_vivos(self):
        vivo = self._intento()

        response = self.client.post(self.url, {"monto": "30000"})

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        vivo.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("30000"))
        self.assertEqual(vivo.estado, EstadoCobro.CANCELADO)

    def test_monto_negativo_muestra_el_error(self):
        response = self.client.post(self.url, {"monto": "-1"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["monto"])
        self.assertContains(response, "No puede ser negativo")
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("28000"))

    def test_pagada_anulada_y_de_otro_gimnasio_dan_404(self):
        pagada = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=self.hoy - timedelta(days=31), estado=Estado.PAGADO,
        )
        anulada = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=self.hoy - timedelta(days=59), estado=Estado.ANULADO,
        )
        alumno_b = Alumno.objects.create(gimnasio=self.otro_gimnasio, nombre="B", apellido="B")
        de_otro = crear_cuota(
            gimnasio=self.otro_gimnasio, alumno=alumno_b, inicio=self.hoy,
        )
        for cuota in (pagada, anulada, de_otro):
            response = self.client.get(reverse("pagos:monto", args=[cuota.pk]))
            self.assertEqual(response.status_code, 404)

    def test_no_corrige_una_cuota_recien_pagada(self):
        original = CuotaMontoView.get_object
        test = self

        def get_object_y_mp_la_cobra(vista, *args, **kwargs):
            objeto = original(vista, *args, **kwargs)
            test._cobrar_por_mp()
            return objeto

        with patch.object(CuotaMontoView, "get_object", get_object_y_mp_la_cobra):
            response = self.client.post(self.url, {"monto": "1"})

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("28000"))
        self.assertEqual(self.cuota.estado, Estado.PAGADO)

    def test_el_listado_ofrece_corregir_monto_solo_en_impagas(self):
        pagada = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=self.hoy - timedelta(days=31), estado=Estado.PAGADO,
        )

        response = self.client.get(reverse("pagos:listado"))

        self.assertContains(response, reverse("pagos:monto", args=[self.cuota.pk]))
        self.assertNotContains(response, reverse("pagos:monto", args=[pagada.pk]))


# ---------------------------------------------------------------------------
# Volver a impaga (staff)
# ---------------------------------------------------------------------------


class VolverAImpagaTests(_Base):
    def setUp(self):
        super().setUp()
        self._login_staff()
        self.url = reverse("pagos:volver_a_impaga", args=[self.cuota.pk])

    def _devolver(self):
        intento = self._cobrar_por_mp()
        CobroOnlineCuota.objects.filter(pk=intento.pk).update(
            estado=EstadoCobro.DEVUELTO,
            motivo_revision=CobroOnlineCuota.MotivoRevision.DEVUELTO,
            revision_pendiente=True,
        )
        return intento

    def test_la_vuelve_a_pendiente_y_resuelve_la_revision(self):
        intento = self._devolver()

        response = self.client.post(self.url)

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        intento.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PENDIENTE)
        self.assertIsNone(self.cuota.fecha_pago)
        self.assertEqual(self.cuota.medio_pago_texto, "")
        self.assertFalse(intento.revision_pendiente)
        self.assertIsNotNone(intento.revision_resuelta_en)

    def test_un_pago_adicional_sin_devolver_sigue_en_revisar(self):
        """P1 pagó la cuota y P2 quedó como adicional (hay que devolverlo);
        P1 se devolvió. «Volver a impaga» resuelve el motivo propio (el
        devuelto) pero P2 sigue siendo plata a devolver: no sale de Revisar."""
        intento = self._devolver()
        CobroOnlineCuota.objects.filter(pk=intento.pk).update(
            respuesta_mp={
                cobro.CLAVE_PAGOS_ADICIONALES: [
                    {"payment_id": "502", "estado_mp": "approved"},
                    {"payment_id": "503", "estado_mp": "approved", "resuelto": "2026-10-01"},
                ]
            }
        )

        self.client.post(self.url)

        self.cuota.refresh_from_db()
        intento.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PENDIENTE)
        self.assertTrue(intento.revision_pendiente)
        # El motivo propio (el devuelto) sí quedó resuelto: cuando después
        # llegue la devolución de P2, la revisión se apaga sola.
        self.assertIsNotNone(intento.revision_resuelta_en)

    def test_adicionales_ya_resueltos_no_la_dejan_en_revisar(self):
        intento = self._devolver()
        CobroOnlineCuota.objects.filter(pk=intento.pk).update(
            respuesta_mp={
                cobro.CLAVE_PAGOS_ADICIONALES: [
                    {"payment_id": "502", "estado_mp": "approved", "resuelto": "2026-10-01"},
                ]
            }
        )

        self.client.post(self.url)

        intento.refresh_from_db()
        self.assertFalse(intento.revision_pendiente)
        self.assertIsNotNone(intento.revision_resuelta_en)

    def test_si_su_plazo_ya_paso_vuelve_vencida(self):
        viejo = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=self.hoy - timedelta(days=60), monto=Decimal("28000"),
        )
        self.cuota = viejo
        self._devolver()

        self.client.post(reverse("pagos:volver_a_impaga", args=[viejo.pk]))

        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, Estado.VENCIDO)

    def test_una_pagada_sin_devolucion_no_se_toca(self):
        self._cobrar_por_mp()

        response = self.client.post(self.url)

        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)
        self.assertTrue(_mensajes(response))

    def test_una_pagada_a_mano_no_se_toca(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=Estado.PAGADO)

        self.client.post(self.url)

        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)

    def test_get_no_cambia_nada_y_otro_gimnasio_da_404(self):
        self._devolver()
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Estado.PAGADO)

        alumno_b = Alumno.objects.create(gimnasio=self.otro_gimnasio, nombre="B", apellido="B")
        de_otro = crear_cuota(gimnasio=self.otro_gimnasio, alumno=alumno_b, inicio=self.hoy)
        response = self.client.post(reverse("pagos:volver_a_impaga", args=[de_otro.pk]))
        self.assertEqual(response.status_code, 404)
