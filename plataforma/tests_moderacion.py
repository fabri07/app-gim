"""Tarea 7: la cola de moderación del superadmin."""

import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda.models import DenunciaProducto, FotoProducto, Producto, Variante
from tienda.testing import PNG, crear_producto


def _foto(producto, aprobada=False, posicion=0):
    foto = FotoProducto(producto=producto, posicion=posicion, aprobada=aprobada)
    foto.imagen.save("f.png", ContentFile(PNG))
    return foto


class _Base(TestCase):
    def setUp(self):
        self.jefe = User.objects.create_superuser("jefe", "j@x.com", "c-123456")
        self.client.force_login(self.jefe)
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio Cima", slug="cima", tienda_habilitada=True
        )

    def producto(self, nombre="Remera", **campos):
        campos.setdefault("moderacion", Producto.Moderacion.PENDIENTE)
        producto = crear_producto(
            gimnasio=self.gimnasio, nombre=nombre, precio=Decimal("100"), **campos
        )
        Variante.objects.create(producto=producto, valor_1="M", stock=2)
        return producto

    def mensajes(self, respuesta):
        return [str(m) for m in get_messages(respuesta.wsgi_request)]


class AccesoTests(_Base):
    def test_staff_y_alumno_reciben_403(self):
        producto = self.producto()
        foto = _foto(producto)
        urls = [
            ("plataforma:moderacion", []),
            ("plataforma:moderacion_foto", [foto.pk]),
            ("plataforma:moderacion_producto", [producto.pk, "aprobar"]),
            ("plataforma:moderacion_foto_accion", [foto.pk, "rechazar"]),
        ]
        for rol in (Perfil.Rol.STAFF, Perfil.Rol.ALUMNO):
            usuario = User.objects.create_user(f"u-{rol}", password="c-123456")
            Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=rol)
            self.client.force_login(usuario)
            for nombre, args in urls:
                r = self.client.get(reverse(nombre, args=args))
                self.assertEqual(r.status_code, 403, (rol, nombre))


class ColaTests(_Base):
    def test_muestra_pendientes_y_cambios_con_sus_fotos(self):
        nuevo = self.producto("Nuevo")
        f1, f2 = _foto(nuevo, posicion=0), _foto(nuevo, posicion=1)
        viejo = self.producto("Viejo", moderacion=Producto.Moderacion.APROBADO, cambio_pendiente=True)
        f3 = _foto(viejo, aprobada=True)
        html = self.client.get(reverse("plataforma:moderacion")).content.decode()
        for f in (f1, f2, f3):
            self.assertIn(reverse("plataforma:moderacion_foto", args=[f.pk]), html)
        self.assertIn("Gimnasio Cima", html)
        self.assertIn("Texto cambiado", html)
        self.assertIn("2 fotos nuevas", html)

    def test_vacia(self):
        html = self.client.get(reverse("plataforma:moderacion")).content.decode()
        self.assertIn("No hay nada para revisar.", html)

    def test_queries_constantes(self):
        url = reverse("plataforma:moderacion")
        self.producto("Uno")
        _foto(Producto.objects.get(nombre="Uno"))
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(url)
        for i in range(6):
            p = self.producto(f"P{i}")
            _foto(p)
            _foto(p, posicion=1)
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(url)
        self.assertEqual(len(muchas), len(pocas))

    def test_la_demo_va_aparte(self):
        demo = Gimnasio.objects.create(nombre="Demo", slug="demo", es_demo=True)
        crear_producto(
            gimnasio=demo, nombre="De la demo", precio=1, moderacion=Producto.Moderacion.PENDIENTE
        )
        html = self.client.get(reverse("plataforma:moderacion")).content.decode()
        self.assertIn("Cuenta demo (1)", html)
        self.assertIn("No hay nada para revisar.", html)


class FotoSuperadminTests(_Base):
    def test_sirve_bytes_sin_cache(self):
        foto = _foto(self.producto())
        r = self.client.get(reverse("plataforma:moderacion_foto", args=[foto.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")
        self.assertEqual(r.content, PNG)


class AccionesTests(_Base):
    def _url(self, producto, accion):
        return reverse("plataforma:moderacion_producto", args=[producto.pk, accion])

    def test_aprobar_desde_la_confirmacion(self):
        producto = self.producto()
        foto = _foto(producto)
        html = self.client.get(self._url(producto, "aprobar")).content.decode()
        version = re.search(r'name="version" value="([^"]*)"', html).group(1)
        fotos = re.findall(r'name="fotos_vistas" value="([^"]*)"', html)
        self.assertEqual(fotos, [str(foto.pk)])
        r = self.client.post(
            self._url(producto, "aprobar"), {"version": version, "fotos_vistas": fotos}
        )
        self.assertRedirects(r, reverse("plataforma:moderacion"))
        producto.refresh_from_db()
        foto.refresh_from_db()
        self.assertEqual(producto.moderacion, Producto.Moderacion.APROBADO)
        self.assertTrue(foto.aprobada)
        self.assertEqual(self.mensajes(r), ["Aprobado: Remera ya está a la venta."])

    def test_version_mal_formada_no_da_500(self):
        producto = self.producto()
        for malo in ("", "basura", "2026-13-45T99:00:00"):
            r = self.client.post(self._url(producto, "aprobar"), {"version": malo, "fotos_vistas": ["x"]})
            self.assertEqual(r.status_code, 302, malo)
            self.assertIn("revisalo de nuevo", self.mensajes(r)[0])
        r = self.client.post(self._url(producto, "aprobar"), {})
        self.assertEqual(r.status_code, 302)
        producto.refresh_from_db()
        self.assertEqual(producto.moderacion, Producto.Moderacion.PENDIENTE)

    def test_aprobar_con_version_vieja_avisa(self):
        producto = self.producto()
        vieja = (producto.modificado - timedelta(seconds=5)).isoformat()
        r = self.client.post(self._url(producto, "aprobar"), {"version": vieja})
        self.assertEqual(
            self.mensajes(r), ["El producto cambió mientras lo mirabas: revisalo de nuevo."]
        )
        producto.refresh_from_db()
        self.assertEqual(producto.moderacion, Producto.Moderacion.PENDIENTE)

    def test_bajar_sin_motivo_muestra_el_error(self):
        producto = self.producto()
        r = self.client.post(self._url(producto, "bajar"), {"motivo": "  "})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Escribí el motivo de la baja.")
        r = self.client.post(self._url(producto, "bajar"), {"motivo": "x" * 301})
        self.assertContains(r, "300 caracteres")
        producto.refresh_from_db()
        self.assertEqual(producto.moderacion, Producto.Moderacion.PENDIENTE)

    def test_bajar_con_motivo_cancela_y_redirige(self):
        producto = self.producto()
        r = self.client.post(self._url(producto, "bajar"), {"motivo": "Es una estafa"})
        self.assertRedirects(r, reverse("plataforma:moderacion"))
        producto.refresh_from_db()
        self.assertEqual(producto.moderacion, Producto.Moderacion.BAJADO)
        self.assertEqual(producto.moderacion_motivo, "Es una estafa")
        self.assertEqual(
            self.mensajes(r), ["Remera quedó dado de baja. Se cancelaron 0 pedido(s) sin pagar."]
        )
        r = self.client.post(self._url(producto, "bajar"), {"motivo": "otra vez"})
        self.assertEqual(self.mensajes(r), ["Este producto ya no estaba en la cola."])

    def test_accion_desconocida_da_404(self):
        producto = self.producto()
        foto = _foto(producto)
        self.assertEqual(self.client.get(self._url(producto, "borrar")).status_code, 404)
        self.assertEqual(self.client.post(self._url(producto, "borrar")).status_code, 404)
        url = reverse("plataforma:moderacion_foto_accion", args=[foto.pk, "borrar"])
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_aprobar_y_rechazar_foto(self):
        producto = self.producto(moderacion=Producto.Moderacion.APROBADO)
        a, b = _foto(producto), _foto(producto, posicion=1)
        r = self.client.post(reverse("plataforma:moderacion_foto_accion", args=[a.pk, "aprobar"]))
        self.assertEqual(self.mensajes(r), ["Foto aprobada."])
        a.refresh_from_db()
        self.assertTrue(a.aprobada)
        url = reverse("plataforma:moderacion_foto_accion", args=[b.pk, "rechazar"])
        self.assertContains(self.client.get(url), "Sí, rechazar la foto")
        self.assertTrue(FotoProducto.objects.filter(pk=b.pk).exists())
        r = self.client.post(url)
        self.assertEqual(self.mensajes(r), ["Foto rechazada; le avisamos al gimnasio."])
        self.assertFalse(FotoProducto.objects.filter(pk=b.pk).exists())

    def test_denuncias_con_alumno_borrado_en_la_confirmacion(self):
        producto = self.producto(moderacion=Producto.Moderacion.APROBADO)
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="Paz")
        DenunciaProducto.objects.create(
            gimnasio=self.gimnasio, producto=producto, alumno=alumno, motivo="estafa", detalle="raro"
        )
        html = self.client.get(reverse("plataforma:moderacion")).content.decode()
        self.assertIn("Ana Paz", html)
        self.assertIn("1 denuncia", html)
        alumno.delete()
        html = self.client.get(self._url(producto, "aprobar")).content.decode()
        self.assertIn("alumno borrado", html)


class InicioTests(_Base):
    def test_contador_en_el_inicio(self):
        url = reverse("plataforma:inicio")
        self.assertNotContains(self.client.get(url), "Productos por revisar")
        self.producto("A")
        self.producto("B")
        r = self.client.get(url)
        self.assertEqual(r.context["moderacion_para_revisar"], 2)
        self.assertContains(r, "Productos por revisar")
        self.assertContains(r, reverse("plataforma:moderacion"))
