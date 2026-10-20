"""Fotos retenidas: ninguna foto de producto pierde el registro de qué
gimnasio la subió y para qué producto, la borre quien la borre (pedido del
dueño del producto, 2026-10-20, protección de datos)."""

from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from tenants.demo import vaciar_gimnasio
from tenants.models import Gimnasio
from tienda import moderacion
from tienda.models import FotoProducto, FotoRetenida, Producto
from tienda.tests_vistas_staff import GaleriaDeFotosTests, _BaseStaff, jpeg

M = FotoRetenida.Motivo


class _Base(_BaseStaff):
    crear_con_fotos = GaleriaDeFotosTests.crear_con_fotos
    datos_edicion = GaleriaDeFotosTests.datos_edicion
    editar = GaleriaDeFotosTests.editar


class RetencionTests(_Base):
    def test_quitar_una_foto_al_editar_la_retiene(self):
        producto, variante, fotos = self.crear_con_fotos(2)
        nombre = fotos[1].imagen.name
        r = self.editar(producto, self.datos_edicion(
            variante, principal=str(fotos[0].pk), borrar_fotos=[str(fotos[1].pk)],
        ))
        self.assertRedirects(r, reverse("tienda:productos"))
        retenida = FotoRetenida.objects.get()
        self.assertEqual(retenida.motivo, M.QUITADA)
        self.assertEqual(retenida.gimnasio, self.gimnasio)
        self.assertEqual(retenida.producto_original, producto.pk)
        self.assertEqual(retenida.producto_nombre, "Remera")
        self.assertEqual(retenida.imagen.name, nombre)
        self.assertEqual(retenida.usuario, self.staff)
        self.assertTrue(retenida.estaba_aprobada)
        self.assertEqual(retenida.subida, fotos[1].creado)
        self.assertTrue(default_storage.exists(nombre))

    def test_borrar_el_producto_retiene_todas_sus_fotos(self):
        producto, variante, fotos = self.crear_con_fotos(3)
        r = self.client.post(reverse("tienda:producto_borrar", args=[producto.pk]))
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertFalse(Producto.objects.filter(pk=producto.pk).exists())
        self.assertEqual(
            sorted(FotoRetenida.objects.values_list("imagen", flat=True)),
            sorted(f.imagen.name for f in fotos),
        )
        self.assertEqual(set(FotoRetenida.objects.values_list("motivo", flat=True)), {M.PRODUCTO_BORRADO})
        self.assertEqual(set(FotoRetenida.objects.values_list("usuario", flat=True)), {self.staff.pk})

    def test_rechazarla_desde_la_cola_la_retiene(self):
        producto, variante, fotos = self.crear_con_fotos(1)
        FotoProducto.objects.filter(pk=fotos[0].pk).update(aprobada=False)
        root = User.objects.create_superuser("root", "r@x.com", "c-123456")
        self.client.force_login(root)
        r = self.client.post(
            reverse("plataforma:moderacion_foto_accion", args=[fotos[0].pk, "rechazar"])
        )
        self.assertRedirects(r, reverse("plataforma:moderacion"))
        retenida = FotoRetenida.objects.get()
        self.assertEqual(retenida.motivo, M.RECHAZADA)
        self.assertEqual(retenida.usuario, root)
        self.assertFalse(retenida.estaba_aprobada)

    def test_rechazar_sin_vista_tambien_retiene(self):
        producto, variante, fotos = self.crear_con_fotos(1)
        self.assertTrue(moderacion.rechazar_foto(fotos[0].pk))
        retenida = FotoRetenida.objects.get()
        self.assertEqual(retenida.motivo, M.RECHAZADA)
        self.assertIsNone(retenida.usuario)

    def test_cualquier_otro_borrado_tambien_retiene(self):
        """El admin, un shell, un camino que todavía no existe: la señal
        cubre todo `delete()`, sin depender de que el llamador se acuerde."""
        producto, variante, fotos = self.crear_con_fotos(2)
        FotoProducto.objects.filter(producto=producto).delete()
        self.assertEqual(FotoRetenida.objects.filter(motivo=M.OTRO).count(), 2)

    def test_la_demo_no_retiene(self):
        """`restaurar_demo` vacía la cuenta cada 6 h: retener eso sería
        juntar basura, no evidencia."""
        demo = Gimnasio.objects.create(nombre="Demo", slug="demo", es_demo=True)
        producto, variante = self.crear_producto(gimnasio=demo)
        foto = FotoProducto(producto=producto, aprobada=True)
        foto.imagen.save("d.jpg", jpeg(400, 400))
        vaciar_gimnasio(gimnasio=demo)
        self.assertFalse(Producto.objects.filter(gimnasio=demo).exists())
        self.assertFalse(FotoRetenida.objects.exists())


class PanelRetenidasTests(_Base):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("root", "r@x.com", "c-123456")

    def retener(self, cantidad, gimnasio=None):
        producto, variante = self.crear_producto(gimnasio=gimnasio)
        for i in range(cantidad):
            foto = FotoProducto(producto=producto, posicion=i, aprobada=True)
            foto.imagen.save(f"r{i}.jpg", jpeg(400, 400))
        FotoProducto.objects.filter(producto=producto).delete()
        return list(FotoRetenida.objects.order_by("pk"))

    def test_el_superadmin_las_ve_con_gimnasio_y_motivo(self):
        retenidas = self.retener(2)
        self.client.force_login(self.root)
        r = self.client.get(reverse("plataforma:fotos_retenidas"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Remera")
        self.assertContains(r, self.gimnasio.nombre)
        self.assertContains(r, M.OTRO.label)
        for retenida in retenidas:
            self.assertContains(r, reverse("plataforma:foto_retenida", args=[retenida.pk]))

    def test_la_foto_sale_solo_en_cache_privada(self):
        retenida = self.retener(1)[0]
        self.client.force_login(self.root)
        r = self.client.get(reverse("plataforma:foto_retenida", args=[retenida.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, max-age=86400")
        self.assertEqual(r["Content-Type"], "image/jpeg")

    def test_el_staff_no_las_ve(self):
        retenida = self.retener(1)[0]
        r = self.client.get(reverse("plataforma:fotos_retenidas"))
        self.assertEqual(r.status_code, 403)
        r = self.client.get(reverse("plataforma:foto_retenida", args=[retenida.pk]))
        self.assertEqual(r.status_code, 403)

    def test_queries_constantes(self):
        self.client.force_login(self.root)
        self.retener(1)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse("plataforma:fotos_retenidas"))
        self.retener(6, gimnasio=self.otro)
        with CaptureQueriesContext(connection) as muchas:
            r = self.client.get(reverse("plataforma:fotos_retenidas"))
        self.assertContains(r, self.otro.nombre)
        self.assertEqual(len(pocas), len(muchas))

    def test_la_moderacion_linkea_a_las_retenidas(self):
        self.client.force_login(self.root)
        r = self.client.get(reverse("plataforma:moderacion"))
        self.assertContains(r, reverse("plataforma:fotos_retenidas"))

    def test_la_clausula_avisa_que_se_conservan(self):
        from django.template.loader import render_to_string

        texto = render_to_string("tienda/_clausula_uso.html")
        self.assertIn("quedan guardadas", texto)
