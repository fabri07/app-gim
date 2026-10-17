from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from alumnos.models import Alumno
from notificaciones.models import RecordatorioEnviado
from tenants.models import Gimnasio, Perfil
from tienda import services
from tienda.models import Pedido, Producto, Variante


class VencerPedidosComandoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        producto = Producto.objects.create(gimnasio=self.gimnasio, nombre="R", precio=Decimal("1"))
        self.variante = Variante.objects.create(producto=producto, stock=5)

    def pedido(self):
        return services.crear_pedido(gimnasio=self.gimnasio, alumno=self.alumno,
                                     carrito={self.variante.pk: 1}, suplantando=False)

    @patch("notificaciones.services.notificar_a_usuario")
    def test_vence_y_avisa_una_sola_vez_aunque_haya_vencido_perezosamente(self, push):
        p = self.pedido()
        Pedido.objects.filter(pk=p.pk).update(reservado_hasta=timezone.now() - timedelta(hours=1))
        services.vencer_pedidos()  # perezoso: sin push
        push.assert_not_called()
        call_command("vencer_pedidos", stdout=StringIO())
        call_command("vencer_pedidos", stdout=StringIO())
        self.assertEqual(push.call_count, 1)
        self.assertTrue(RecordatorioEnviado.objects.filter(
            tipo=RecordatorioEnviado.Tipo.PEDIDO_VENCIDO, objeto_id=p.pk).exists())

    @patch("notificaciones.services.notificar_a_usuario")
    def test_alumno_sin_acceso_no_queda_marcado_como_avisado(self, push):
        self.alumno.perfil = None
        self.alumno.save()
        p = self.pedido()
        Pedido.objects.filter(pk=p.pk).update(
            estado=Pedido.Estado.VENCIDO, modificado=timezone.now()
        )
        call_command("vencer_pedidos", stdout=StringIO())
        self.assertFalse(RecordatorioEnviado.objects.filter(
            tipo=RecordatorioEnviado.Tipo.PEDIDO_VENCIDO, objeto_id=p.pk).exists())

    @patch("notificaciones.services.notificar_a_gimnasio")
    def test_recordatorio_de_revision_demorada_una_vez(self, push):
        p = self.pedido()
        Pedido.objects.filter(pk=p.pk).update(
            estado=Pedido.Estado.EN_REVISION,
            comprobante_subido_en=timezone.now() - timedelta(hours=25),
        )
        call_command("vencer_pedidos", stdout=StringIO())
        call_command("vencer_pedidos", stdout=StringIO())
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.kwargs["rol"], Perfil.Rol.STAFF)


class AvisosAlStaffTests(TestCase):
    @patch("notificaciones.services.notificar_a_gimnasio")
    def test_siempre_con_rol_staff(self, push):
        from notificaciones.services import notificar_pedido_al_staff

        gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        alumno = Alumno.objects.create(gimnasio=gimnasio, nombre="Ana", apellido="G")
        pedido = Pedido.objects.create(gimnasio=gimnasio, alumno=alumno, total=1)
        for aviso in ("nuevo", "comprobante", "cancelacion", "revision_demorada"):
            notificar_pedido_al_staff(pedido, aviso)
        self.assertEqual(push.call_count, 4)
        for llamada in push.call_args_list:
            self.assertEqual(llamada.kwargs["rol"], Perfil.Rol.STAFF)


class AvisosAlAlumnoTests(TestCase):
    @patch("notificaciones.services.notificar_a_usuario")
    def test_rechazado_lleva_el_plazo_nuevo_en_hora_argentina(self, push):
        from notificaciones.services import notificar_pedido_al_alumno

        gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO)
        alumno = Alumno.objects.create(gimnasio=gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        plazo = timezone.make_aware(datetime(2026, 10, 21, 22, 30))  # 22/10 01:30 UTC
        pedido = Pedido.objects.create(gimnasio=gimnasio, alumno=alumno, total=1, reservado_hasta=plazo)
        notificar_pedido_al_alumno(pedido, "rechazado")
        self.assertIn("21/10 22:30", push.call_args.args[1]["body"])


class AvisosCancelacionResueltaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil
        )

    @patch("notificaciones.services.notificar_a_usuario")
    def test_aceptada_y_rechazada_se_redactan_distinto(self, push):
        from notificaciones.services import notificar_pedido_al_alumno

        devuelto = Pedido.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, total=1, estado=Pedido.Estado.DEVUELTO
        )
        vivo = Pedido.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, total=1, estado=Pedido.Estado.PAGADO
        )
        notificar_pedido_al_alumno(devuelto, "cancelacion_resuelta")
        aceptada = push.call_args.args[1]["body"]
        notificar_pedido_al_alumno(vivo, "cancelacion_resuelta")
        rechazada = push.call_args.args[1]["body"]
        self.assertIn("aceptó", aceptada)
        self.assertIn("no aceptó", rechazada)
        self.assertNotEqual(aceptada, rechazada)
