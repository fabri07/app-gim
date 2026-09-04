"""Tests de la app `notificaciones` (PWA + Web Push).

Sigue el mismo criterio que el resto del proyecto: `django.test.TestCase`
plano, sin pytest ni factories. Todo lo que manda un push real se mockea
(`notificaciones.services._enviar` o `pywebpush.webpush`) -- `PUSH_ENABLED`
además está forzado a `False` bajo `TESTING`, así que ningún test debería
poder salir a la red aunque algún mock se olvide."""

from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from novedades.models import Novedad
from pagos.models import Cuota
from pagos.testing import crear_cuota, crear_cuota_mensual
from rutinas.models import RutinaAsignada, RutinaPlantilla
from tenants.models import Gimnasio, Perfil
from turnos.models import Reserva

from notificaciones.models import RecordatorioEnviado, SuscripcionPush

User = get_user_model()


def _crear_gimnasio(slug="gimnasio-a", nombre="Gimnasio A"):
    return Gimnasio.objects.create(nombre=nombre, slug=slug)


def _crear_alumno_con_perfil(gimnasio, username, nombre="Ana", apellido="Gómez"):
    alumno = Alumno.objects.create(gimnasio=gimnasio, nombre=nombre, apellido=apellido)
    user = User.objects.create_user(username, password="clave-123456")
    perfil = Perfil.objects.create(usuario=user, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO)
    alumno.perfil = perfil
    alumno.save()
    return alumno, user


def _crear_staff(gimnasio, username):
    user = User.objects.create_user(username, password="clave-123456")
    Perfil.objects.create(usuario=user, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
    return user


class SuscripcionPushModelTests(TestCase):
    def test_for_gimnasio_aisla_por_tenant(self):
        gimnasio_a = _crear_gimnasio("gimnasio-a", "Gimnasio A")
        gimnasio_b = _crear_gimnasio("gimnasio-b", "Gimnasio B")
        _, usuario_a = _crear_alumno_con_perfil(gimnasio_a, "alumno-a")
        _, usuario_b = _crear_alumno_con_perfil(gimnasio_b, "alumno-b")

        SuscripcionPush.objects.create(
            gimnasio=gimnasio_a,
            usuario=usuario_a,
            endpoint="https://push.example.com/a",
            p256dh="clave-p256dh-a",
            auth="clave-auth-a",
        )
        SuscripcionPush.objects.create(
            gimnasio=gimnasio_b,
            usuario=usuario_b,
            endpoint="https://push.example.com/b",
            p256dh="clave-p256dh-b",
            auth="clave-auth-b",
        )

        del_gimnasio_a = SuscripcionPush.objects.for_gimnasio(gimnasio_a)
        self.assertEqual(del_gimnasio_a.count(), 1)
        self.assertEqual(del_gimnasio_a.first().usuario, usuario_a)


class RecordatorioEnviadoModelTests(TestCase):
    def test_for_gimnasio_aisla_por_tenant(self):
        gimnasio_a = _crear_gimnasio("gimnasio-a", "Gimnasio A")
        gimnasio_b = _crear_gimnasio("gimnasio-b", "Gimnasio B")

        RecordatorioEnviado.objects.create(
            gimnasio=gimnasio_a, tipo=RecordatorioEnviado.Tipo.PAGO_VENCIDO, objeto_id=1
        )
        RecordatorioEnviado.objects.create(
            gimnasio=gimnasio_b, tipo=RecordatorioEnviado.Tipo.PAGO_VENCIDO, objeto_id=2
        )

        self.assertEqual(
            RecordatorioEnviado.objects.for_gimnasio(gimnasio_a).count(), 1
        )

    def test_mismo_tipo_y_objeto_id_en_gimnasios_distintos_no_colisiona(self):
        gimnasio_a = _crear_gimnasio("gimnasio-a", "Gimnasio A")
        gimnasio_b = _crear_gimnasio("gimnasio-b", "Gimnasio B")

        RecordatorioEnviado.objects.create(
            gimnasio=gimnasio_a, tipo=RecordatorioEnviado.Tipo.PAGO_VENCIDO, objeto_id=1
        )
        # No debe tirar IntegrityError: el unique constraint es por
        # (gimnasio, tipo, objeto_id), no solo (tipo, objeto_id).
        RecordatorioEnviado.objects.create(
            gimnasio=gimnasio_b, tipo=RecordatorioEnviado.Tipo.PAGO_VENCIDO, objeto_id=1
        )
        self.assertEqual(RecordatorioEnviado.objects.count(), 2)


class SuscripcionPushCreateViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno, self.usuario = _crear_alumno_con_perfil(self.gimnasio, "alumno-1")

    def _payload(self, endpoint="https://push.example.com/x"):
        return {
            "endpoint": endpoint,
            "keys": {"p256dh": "clave-p256dh", "auth": "clave-auth"},
        }

    def test_post_valido_crea_suscripcion_con_gimnasio_correcto(self):
        self.client.login(username="alumno-1", password="clave-123456")
        response = self.client.post(
            reverse("notificaciones:push_suscribir"),
            data=self._payload(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        suscripcion = SuscripcionPush.objects.get(endpoint="https://push.example.com/x")
        self.assertEqual(suscripcion.gimnasio, self.gimnasio)
        self.assertEqual(suscripcion.usuario, self.usuario)

    def test_403_durante_suplantacion_no_crea_fila(self):
        staff = _crear_staff(self.gimnasio, "staff-1")
        self.client.login(username="staff-1", password="clave-123456")
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))

        response = self.client.post(
            reverse("notificaciones:push_suscribir"),
            data=self._payload(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SuscripcionPush.objects.count(), 0)

    def test_403_sin_perfil(self):
        User.objects.create_user("sin-perfil", password="clave-123456")
        self.client.login(username="sin-perfil", password="clave-123456")
        response = self.client.post(
            reverse("notificaciones:push_suscribir"),
            data=self._payload(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SuscripcionPush.objects.count(), 0)

    def test_anonimo_403(self):
        response = self.client.post(
            reverse("notificaciones:push_suscribir"),
            data=self._payload(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)


class SignalsTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.alumno, self.usuario_alumno = _crear_alumno_con_perfil(
            self.gimnasio, "alumno-1"
        )
        self.alumno_otro, _ = _crear_alumno_con_perfil(
            self.otro_gimnasio, "alumno-otro-gimnasio"
        )
        SuscripcionPush.objects.create(
            gimnasio=self.gimnasio,
            usuario=self.usuario_alumno,
            endpoint="https://push.example.com/alumno",
            p256dh="p",
            auth="a",
        )

    @patch("notificaciones.services._enviar")
    def test_novedad_broadcast_hoy_notifica_solo_al_gimnasio_correcto(self, mock_enviar):
        with self.captureOnCommitCallbacks(execute=True):
            Novedad.objects.create(
                gimnasio=self.gimnasio,
                titulo="Aviso",
                mensaje="Mensaje de prueba",
                fecha_publicacion=timezone.localdate(),
            )
        mock_enviar.assert_called_once()
        (suscripcion_llamada, _payload), _ = mock_enviar.call_args
        self.assertEqual(suscripcion_llamada.gimnasio, self.gimnasio)

    @patch("notificaciones.services._enviar")
    def test_novedad_creada_inactiva_no_notifica(self, mock_enviar):
        """`NovedadForm` incluye `activa` como campo editable en el alta --
        el staff puede crear una novedad ya oculta desde el vamos, y eso no
        debe disparar push (mismo criterio que `NovedadQuerySet.visibles()`
        y el filtro `activa=True` de `enviar_recordatorios`)."""
        with self.captureOnCommitCallbacks(execute=True):
            Novedad.objects.create(
                gimnasio=self.gimnasio,
                titulo="Aviso oculto",
                mensaje="Mensaje de prueba",
                fecha_publicacion=timezone.localdate(),
                activa=False,
            )
        mock_enviar.assert_not_called()

    @patch("notificaciones.services._enviar")
    def test_novedad_con_fecha_futura_no_notifica(self, mock_enviar):
        Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Aviso futuro",
            mensaje="Mensaje",
            fecha_publicacion=timezone.localdate() + timedelta(days=5),
        )
        mock_enviar.assert_not_called()

    @patch("notificaciones.services._enviar")
    def test_rutina_asignada_notifica_al_alumno_correcto(self, mock_enviar):
        plantilla = RutinaPlantilla.objects.create(
            gimnasio=self.gimnasio,
            nombre="Full body",
            objetivo="Hipertrofia",
            nivel=RutinaPlantilla.Nivel.PRINCIPIANTE,
            dias_por_semana=3,
        )
        with self.captureOnCommitCallbacks(execute=True):
            RutinaAsignada.crear_desde_plantilla(
                gimnasio=self.gimnasio,
                alumno=self.alumno,
                plantilla=plantilla,
                fecha_inicio=date(2026, 1, 1),
            )
        mock_enviar.assert_called_once()
        (suscripcion_llamada, _payload), _ = mock_enviar.call_args
        self.assertEqual(suscripcion_llamada.usuario, self.usuario_alumno)

    @patch("notificaciones.services._enviar")
    def test_nueva_reserva_notifica_al_staff_del_gimnasio_correcto(self, mock_enviar):
        staff = _crear_staff(self.gimnasio, "staff-1")
        staff_otro = _crear_staff(self.otro_gimnasio, "staff-otro")
        SuscripcionPush.objects.create(
            gimnasio=self.gimnasio,
            usuario=staff,
            endpoint="https://push.example.com/staff",
            p256dh="p",
            auth="a",
        )
        SuscripcionPush.objects.create(
            gimnasio=self.otro_gimnasio,
            usuario=staff_otro,
            endpoint="https://push.example.com/staff-otro",
            p256dh="p",
            auth="a",
        )

        with self.captureOnCommitCallbacks(execute=True):
            Reserva.objects.create(
                gimnasio=self.gimnasio,
                alumno=self.alumno,
                fecha=timezone.localdate() + timedelta(days=1),
                hora_inicio=time(10, 0),
            )

        mock_enviar.assert_called_once()
        (suscripcion_llamada, _payload), _ = mock_enviar.call_args
        self.assertEqual(suscripcion_llamada.usuario, staff)


class EnviarRecordatoriosCommandTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(
            nombre="Gimnasio A", slug="gimnasio-a", dia_vencimiento_pago=10
        )
        self.gimnasio_b = Gimnasio.objects.create(
            nombre="Gimnasio B", slug="gimnasio-b", dia_vencimiento_pago=28
        )
        self.alumno_a, self.usuario_a = _crear_alumno_con_perfil(
            self.gimnasio_a, "alumno-a"
        )
        self.alumno_b, self.usuario_b = _crear_alumno_con_perfil(
            self.gimnasio_b, "alumno-b"
        )
        SuscripcionPush.objects.create(
            gimnasio=self.gimnasio_a,
            usuario=self.usuario_a,
            endpoint="https://push.example.com/a",
            p256dh="p",
            auth="a",
        )
        SuscripcionPush.objects.create(
            gimnasio=self.gimnasio_b,
            usuario=self.usuario_b,
            endpoint="https://push.example.com/b",
            p256dh="p",
            auth="a",
        )

    @patch("notificaciones.services._enviar")
    def test_pago_por_vencer_solo_del_gimnasio_correcto(self, mock_enviar):
        # La ventana de aviso ya no depende de `dia_vencimiento_pago` (que
        # murió con la migración a ciclos) sino de cuándo ARRANCÓ el ciclo de
        # cada alumno: se avisa dentro de los `DIAS_AVISO_PAGO` días
        # siguientes. La fecha se fija igual, para que el test no dependa del
        # día en que corre.
        hoy = date(2026, 3, 10)

        crear_cuota(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            inicio=hoy - timedelta(days=2),  # dentro de la ventana: notifica
            monto=10000,
        )
        crear_cuota(
            gimnasio=self.gimnasio_b,
            alumno=self.alumno_b,
            inicio=hoy - timedelta(days=10),  # fuera de la ventana: no notifica
            monto=10000,
        )

        from django.core.management import call_command

        with patch("django.utils.timezone.localdate", return_value=hoy):
            call_command("enviar_recordatorios")

        mock_enviar.assert_called_once()
        (suscripcion_llamada, _payload), _ = mock_enviar.call_args
        self.assertEqual(suscripcion_llamada.usuario, self.usuario_a)

    @patch("notificaciones.services._enviar")
    def test_correrlo_dos_veces_el_mismo_dia_no_duplica(self, mock_enviar):
        # Fecha FIJA para que el test no dependa del día en que corre.
        hoy = date(2026, 3, 10)

        crear_cuota(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            inicio=hoy - timedelta(days=2),
            monto=10000,
        )

        from django.core.management import call_command

        with patch("django.utils.timezone.localdate", return_value=hoy):
            call_command("enviar_recordatorios")
            call_command("enviar_recordatorios")

        mock_enviar.assert_called_once()
        self.assertEqual(
            RecordatorioEnviado.objects.filter(
                tipo=RecordatorioEnviado.Tipo.PAGO_POR_VENCER
            ).count(),
            1,
        )

    def test_alumno_sin_perfil_no_queda_bloqueado_para_siempre(self):
        """Si el dedup se marcara ANTES de confirmar que se puede entregar
        el push, un alumno sin Perfil vinculado en el momento del barrido
        quedaría sin este aviso para siempre, incluso después de que el
        staff le cree acceso más adelante. `notificar_pago_por_vencer`
        chequea el Perfil primero -- ver notificaciones/services.py."""
        # Fecha FIJA por el mismo motivo que el test de arriba.
        hoy = date(2026, 3, 10)

        alumno_sin_perfil = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Sin", apellido="Perfil"
        )
        crear_cuota(
            gimnasio=self.gimnasio_a,
            alumno=alumno_sin_perfil,
            inicio=hoy - timedelta(days=2),
            monto=10000,
        )

        from django.core.management import call_command

        with patch("notificaciones.services._enviar") as mock_enviar, patch(
            "django.utils.timezone.localdate", return_value=hoy
        ):
            call_command("enviar_recordatorios")
        mock_enviar.assert_not_called()
        self.assertEqual(RecordatorioEnviado.objects.count(), 0)

        # El staff le crea acceso al alumno recién ahora.
        usuario = User.objects.create_user("alumno-luego", password="clave-123456")
        perfil = Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )
        alumno_sin_perfil.perfil = perfil
        alumno_sin_perfil.save()
        SuscripcionPush.objects.create(
            gimnasio=self.gimnasio_a,
            usuario=usuario,
            endpoint="https://push.example.com/alumno-luego",
            p256dh="p",
            auth="a",
        )

        with patch("notificaciones.services._enviar") as mock_enviar, patch(
            "django.utils.timezone.localdate", return_value=hoy
        ):
            call_command("enviar_recordatorios")
        mock_enviar.assert_called_once()
        self.assertEqual(RecordatorioEnviado.objects.count(), 1)


class EnviarRecordatoriosMedianocheTests(TestCase):
    """La ventana de 60' de `notificar_turno_proximo` puede cruzar
    medianoche si el cron corre cerca de las 23:xx -- ver
    `enviar_recordatorios.py`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno, self.usuario = _crear_alumno_con_perfil(self.gimnasio, "alumno-1")
        SuscripcionPush.objects.create(
            gimnasio=self.gimnasio,
            usuario=self.usuario,
            endpoint="https://push.example.com/alumno",
            p256dh="p",
            auth="a",
        )

    @patch("notificaciones.services._enviar")
    def test_turno_de_los_ultimos_minutos_del_dia_notifica(self, mock_enviar):
        hoy = date(2026, 3, 10)
        ahora = datetime.combine(hoy, time(23, 50))
        Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=hoy, hora_inicio=time(23, 55)
        )

        from django.core.management import call_command

        with patch("django.utils.timezone.localdate", return_value=hoy), patch(
            "django.utils.timezone.localtime", return_value=ahora
        ):
            call_command("enviar_recordatorios")

        mock_enviar.assert_called_once()


class ManifestViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")

    def test_manifest_no_expone_datos_de_otro_gimnasio(self):
        otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        response = self.client.get(
            reverse("notificaciones:pwa_manifest", args=[self.gimnasio.slug])
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["name"], "Gimnasio A")
        self.assertNotEqual(data["name"], otro.nombre)

    def test_gimnasio_inactivo_da_404(self):
        self.gimnasio.activo = False
        self.gimnasio.save(update_fields=["activo"])
        response = self.client.get(
            reverse("notificaciones:pwa_manifest", args=[self.gimnasio.slug])
        )
        self.assertEqual(response.status_code, 404)


class IconoGimnasioViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")

    def test_tamano_no_permitido_da_404(self):
        response = self.client.get(
            reverse("notificaciones:pwa_icono", args=[self.gimnasio.slug, 999])
        )
        self.assertEqual(response.status_code, 404)

    def test_tamano_permitido_sin_logo_devuelve_placeholder(self):
        response = self.client.get(
            reverse("notificaciones:pwa_icono", args=[self.gimnasio.slug, 192])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")


def _logo_png(fondo, contenido=None, size=(400, 400), mode="RGB"):
    """PNG cuadrado de color `fondo` con, opcionalmente, un bloque de color
    `contenido` de 100x100 px en el centro. Devuelve los bytes."""
    import io

    from PIL import Image, ImageDraw

    imagen = Image.new(mode, size, fondo)
    if contenido is not None:
        dibujo = ImageDraw.Draw(imagen)
        cx, cy = size[0] // 2, size[1] // 2
        dibujo.rectangle((cx - 50, cy - 50, cx + 49, cy + 49), fill=contenido)
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


def _asignar_logo(gimnasio, contenido, nombre="logo.png"):
    from django.core.files.base import ContentFile

    gimnasio.logo.save(nombre, ContentFile(contenido), save=True)
    return gimnasio


def _pixel(png_bytes, xy):
    import io

    from PIL import Image

    return Image.open(io.BytesIO(png_bytes)).convert("RGB").getpixel(xy)


AZUL = (10, 40, 200)
BLANCO = (255, 255, 255)


class IconoLienzoTests(TestCase):
    """El ícono se pinta sobre el color de fondo del PROPIO logo, no sobre el
    fondo de la paleta: en el splash de Android el ícono flota sobre
    `background_color`, y si el logo trae su propio fondo opaco de otro color
    se ve un cuadrado que desentona (reporte real, 2026-09-03)."""

    def setUp(self):
        self.gimnasio = _crear_gimnasio()

    def test_logo_opaco_el_lienzo_es_su_color_de_borde(self):
        from notificaciones.icons import color_lienzo

        _asignar_logo(self.gimnasio, _logo_png(AZUL, BLANCO))
        self.assertEqual(color_lienzo(self.gimnasio), "#0a28c8")

    def test_logo_transparente_el_lienzo_es_el_fondo_de_la_paleta(self):
        from notificaciones.icons import color_lienzo

        _asignar_logo(
            self.gimnasio, _logo_png((0, 0, 0, 0), (200, 30, 30, 255), mode="RGBA")
        )
        self.assertEqual(color_lienzo(self.gimnasio), self.gimnasio.color_fondo_css)

    def test_sin_logo_el_lienzo_es_el_primario_del_placeholder(self):
        from notificaciones.icons import color_lienzo

        self.assertEqual(color_lienzo(self.gimnasio), self.gimnasio.color_primario_css)

    def test_el_icono_rellena_con_el_color_del_logo_y_recorta_los_margenes(self):
        from notificaciones.icons import generar_icono

        _asignar_logo(self.gimnasio, _logo_png(AZUL, BLANCO))
        png = generar_icono(self.gimnasio, 192)
        # Esquina: el relleno es el azul del logo, no el crema de la paleta.
        self.assertEqual(_pixel(png, (2, 2)), AZUL)
        # El bloque blanco (100 px de un logo de 400) sin recorte ocuparía 48 px
        # al centro; con los márgenes recortados ocupa casi todo el ícono.
        self.assertEqual(_pixel(png, (96, 96)), BLANCO)
        self.assertEqual(_pixel(png, (15, 96)), BLANCO)

    def test_la_variante_maskable_deja_la_zona_segura_del_20_por_ciento(self):
        from notificaciones.icons import generar_icono

        _asignar_logo(self.gimnasio, _logo_png(AZUL, BLANCO))
        png = generar_icono(self.gimnasio, 192, maskable=True)
        self.assertEqual(_pixel(png, (96, 96)), BLANCO)
        self.assertEqual(_pixel(png, (15, 96)), AZUL)
        self.assertEqual(_pixel(png, (25, 96)), BLANCO)

    def test_logo_transparente_se_pinta_sobre_el_fondo_de_la_paleta(self):
        from notificaciones.icons import generar_icono

        _asignar_logo(
            self.gimnasio, _logo_png((0, 0, 0, 0), (200, 30, 30, 255), mode="RGBA")
        )
        png = generar_icono(self.gimnasio, 192)
        fondo = self.gimnasio.color_fondo_css.lstrip("#")
        esperado = tuple(int(fondo[i : i + 2], 16) for i in (0, 2, 4))
        self.assertEqual(_pixel(png, (2, 2)), esperado)
        self.assertEqual(_pixel(png, (96, 96)), (200, 30, 30))


class IconoVersionadoTests(TestCase):
    """El navegador guarda el ícono al instalar y solo lo vuelve a pedir si
    la URL del manifest cambia: con una URL fija, cambiar el logo del gimnasio
    no cambiaba el ícono instalado (reporte real, 2026-09-03)."""

    def setUp(self):
        self.gimnasio = _crear_gimnasio()

    def _manifest(self):
        return self.client.get(
            reverse("notificaciones:pwa_manifest", args=[self.gimnasio.slug])
        )

    def test_la_url_del_icono_cambia_cuando_cambia_el_gimnasio(self):
        antes = self._manifest().json()["icons"][0]["src"]
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            modificado=timezone.now() + timedelta(days=1)
        )
        despues = self._manifest().json()["icons"][0]["src"]
        self.assertNotEqual(antes, despues)
        self.assertIn("?v=", antes)

    def test_el_manifest_no_se_cachea_y_el_icono_versionado_si(self):
        manifest = self._manifest()
        self.assertEqual(manifest["Cache-Control"], "no-cache")
        icono = self.client.get(manifest.json()["icons"][0]["src"])
        self.assertEqual(icono.status_code, 200)
        self.assertIn("immutable", icono["Cache-Control"])

    def test_el_manifest_declara_iconos_maskable_y_any_por_separado(self):
        iconos = self._manifest().json()["icons"]
        propositos = {i["purpose"] for i in iconos}
        self.assertEqual(propositos, {"any", "maskable"})
        for icono in iconos:
            self.assertEqual(self.client.get(icono["src"]).status_code, 200)

    def test_background_color_es_el_lienzo_del_icono(self):
        # Sin logo: el placeholder es una baldosa del color primario, así que
        # el splash tiene que ser de ese mismo color.
        self.assertEqual(
            self._manifest().json()["background_color"], self.gimnasio.color_primario_css
        )
        _asignar_logo(self.gimnasio, _logo_png(AZUL, BLANCO))
        self.assertEqual(self._manifest().json()["background_color"], "#0a28c8")

    def test_el_icono_de_apple_va_en_el_head_versionado(self):
        _, user = _crear_alumno_con_perfil(self.gimnasio, "ana")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'rel="apple-touch-icon"')
        self.assertContains(response, "icono-192.png?v=")

    def test_el_icono_del_push_va_versionado(self):
        from notificaciones.services import _icono_url

        self.assertIn("?v=", _icono_url(self.gimnasio))
