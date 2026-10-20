"""Moderación de la tienda, Tarea 1: los estados, `q_vendible` y la migración
que deja aprobado todo lo que ya estaba a la venta."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda.models import DenunciaProducto, FotoProducto, Pedido, Producto, Variante, q_vendible
from tienda import services, tests_vistas_alumno
from tienda.testing import PNG, crear_foto, crear_producto


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")


class EstadosTests(_Base):
    def test_un_producto_nuevo_nace_pendiente(self):
        producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre="Proteína", precio=Decimal("10")
        )
        producto.refresh_from_db()
        self.assertEqual(producto.moderacion, "pendiente")
        self.assertIs(producto.cambio_pendiente, False)
        self.assertEqual(producto.moderacion_motivo, "")
        self.assertIsNone(producto.moderacion_desde)

    def test_una_foto_nueva_nace_sin_aprobar(self):
        producto = crear_producto(gimnasio=self.gimnasio, nombre="P", precio=1)
        foto = FotoProducto.objects.create(producto=producto, imagen="tienda/productos/a.png")
        foto.refresh_from_db()
        self.assertIs(foto.aprobada, False)

    def test_los_helpers_de_test_nacen_aprobados(self):
        producto = crear_producto(gimnasio=self.gimnasio, nombre="P", precio=1)
        self.assertEqual(producto.moderacion, Producto.Moderacion.APROBADO)
        foto = crear_foto(producto=producto, imagen="tienda/productos/a.png")
        self.assertIs(foto.aprobada, True)
        otro = crear_producto(
            gimnasio=self.gimnasio, nombre="Q", precio=1, moderacion=Producto.Moderacion.BAJADO
        )
        self.assertEqual(otro.moderacion, "bajado")

    def test_q_vendible_exige_publicado_y_aprobado(self):
        A, P = Producto.Moderacion.APROBADO, Producto.Moderacion.PENDIENTE
        productos = {}
        for nombre, publicado, mod in (
            ("ok", True, A), ("oculto", False, A), ("pend", True, P), ("pend_oculto", False, P),
        ):
            productos[nombre] = crear_producto(
                gimnasio=self.gimnasio, nombre=nombre, precio=1, publicado=publicado, moderacion=mod
            )
            Variante.objects.create(producto=productos[nombre], stock=1)
        self.assertEqual(
            list(Producto.objects.filter(q_vendible())), [productos["ok"]]
        )
        self.assertEqual(
            list(Variante.objects.filter(q_vendible("producto__")).values_list("producto_id", flat=True)),
            [productos["ok"].pk],
        )


class VentanaDelDeployModeracionTests(_Base):
    """El `migrate` corre mientras la versión vieja atiende: sus INSERT no
    nombran las columnas nuevas y tienen que caer en `pendiente`."""

    def test_un_insert_viejo_cae_en_pendiente(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO tienda_producto (creado, modificado, gimnasio_id, nombre, "
                "descripcion, precio, foto, publicado, orden) VALUES (CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, %s, 'Viejo', '', 1, '', TRUE, 0)",
                [self.gimnasio.pk],
            )
            producto_id = Producto.objects.get(nombre="Viejo").pk
            cursor.execute(
                "INSERT INTO tienda_fotoproducto (producto_id, imagen, posicion, creado) "
                "VALUES (%s, 'tienda/productos/v.png', 0, CURRENT_TIMESTAMP)",
                [producto_id],
            )
        producto = Producto.objects.get(pk=producto_id)
        self.assertEqual(producto.moderacion, "pendiente")
        self.assertIs(producto.cambio_pendiente, False)
        self.assertEqual(producto.moderacion_motivo, "")
        self.assertIs(producto.fotos.get().aprobada, False)


class MigracionDeModeracionTests(TransactionTestCase):
    """`tienda/0009` deja aprobado lo que ya existía. Se migra de verdad: un
    fixture a mano no probaría que el `RunPython` corre sobre filas reales."""

    migrate_from = [("tienda", "0008_variantes_por_caracteristicas")]
    migrate_to = [("tienda", "0009_moderacion")]

    def _migrar(self, destino):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(destino)
        return ejecutor.loader.project_state(destino).apps

    def tearDown(self):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_lo_existente_queda_aprobado(self):
        apps = self._migrar(self.migrate_from)
        gimnasio = apps.get_model("tenants", "Gimnasio").objects.create(nombre="M", slug="m")
        producto = apps.get_model("tienda", "Producto").objects.create(
            gimnasio=gimnasio, nombre="Viejo", precio=1
        )
        foto = apps.get_model("tienda", "FotoProducto").objects.create(
            producto=producto, imagen="tienda/productos/v.png"
        )

        apps = self._migrar(self.migrate_to)

        producto = apps.get_model("tienda", "Producto").objects.get(pk=producto.pk)
        foto = apps.get_model("tienda", "FotoProducto").objects.get(pk=foto.pk)
        self.assertEqual(producto.moderacion, "aprobado")
        self.assertIs(foto.aprobada, True)


class DenunciaModeloTests(_Base):
    def setUp(self):
        super().setUp()
        self.producto = crear_producto(gimnasio=self.gimnasio, nombre="P", precio=1)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="Paz")

    def _denunciar(self, **campos):
        datos = dict(
            gimnasio=self.gimnasio, producto=self.producto, alumno=self.alumno,
            motivo=DenunciaProducto.Motivo.ESTAFA,
        )
        datos.update(campos)
        return DenunciaProducto.objects.create(**datos)

    def test_dos_abiertas_del_mismo_alumno_chocan(self):
        self._denunciar()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._denunciar(motivo=DenunciaProducto.Motivo.OTRO)

    def test_una_resuelta_no_impide_otra_abierta(self):
        self._denunciar(estado=DenunciaProducto.Estado.RESUELTA)
        self._denunciar()
        self.assertEqual(DenunciaProducto.objects.count(), 2)

    def test_aislamiento_por_gimnasio(self):
        self._denunciar()
        otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.assertEqual(DenunciaProducto.objects.for_gimnasio(otro).count(), 0)
        self.assertEqual(DenunciaProducto.objects.for_gimnasio(self.gimnasio).count(), 1)

    def test_borrar_un_alumno_que_denuncio_no_lo_delata(self):
        """`alumno` es SET_NULL: la pantalla de borrar no puede mostrarle al
        staff que ese alumno denunció algo, ni impedirle borrarlo."""
        staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(staff)
        denuncia = self._denunciar()
        url = reverse("alumnos:eliminar", args=[self.alumno.pk])

        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("denuncia", respuesta.content.decode().lower())

        respuesta = self.client.post(url, follow=True)
        self.assertNotIn("denuncia", respuesta.content.decode().lower())
        self.assertFalse(Alumno.objects.filter(pk=self.alumno.pk).exists())
        denuncia.refresh_from_db()
        self.assertIsNone(denuncia.alumno)


class VentaTests(tests_vistas_alumno._BaseAlumno):
    """Tarea 3: el catálogo y el checkout sólo ven lo aprobado. `_BaseAlumno`
    crea `self.producto` con el helper (aprobado) y `self.variante`."""

    def _otro(self, nombre, **campos):
        producto = crear_producto(
            gimnasio=self.gimnasio, nombre=nombre, precio=Decimal("100"), **campos
        )
        return producto, Variante.objects.create(producto=producto, nombre="U", stock=5)

    def test_un_producto_pendiente_no_aparece_en_el_catalogo(self):
        self._otro("Pendientazo", moderacion=Producto.Moderacion.PENDIENTE)
        html = self.client.get(reverse("tienda:catalogo")).content.decode()
        self.assertIn("Remera", html)
        self.assertNotIn("Pendientazo", html)

    def test_un_producto_bajado_no_aparece_en_el_catalogo(self):
        self._otro("Bajadazo", moderacion=Producto.Moderacion.BAJADO)
        html = self.client.get(reverse("tienda:catalogo")).content.decode()
        self.assertIn("Remera", html)
        self.assertNotIn("Bajadazo", html)

    def test_no_se_puede_agregar_al_carrito_uno_pendiente(self):
        _, variante = self._otro("Pendientazo", moderacion=Producto.Moderacion.PENDIENTE)
        respuesta = self.agregar(1, variante=variante)
        self.assertContains(respuesta, services.MENSAJES["producto_no_disponible"])
        self.assertEqual(services.leer_carrito(self.client.session), {})

    def test_uno_bajado_que_ya_estaba_en_el_carrito_se_cae_y_el_resto_se_compra(self):
        _, otra = self._otro("Gorra")
        self.agregar(1)
        self.agregar(1, variante=otra)
        Producto.objects.filter(pk=otra.producto_id).update(
            moderacion=Producto.Moderacion.BAJADO
        )
        respuesta = self.client.post(reverse("tienda:checkout"), follow=True)
        self.assertEqual(respuesta.status_code, 200)
        pedido = Pedido.objects.get(alumno=self.alumno)
        self.assertEqual(
            list(pedido.items.values_list("variante_id", flat=True)), [self.variante.pk]
        )
        otra.refresh_from_db()
        self.assertEqual(otra.stock, 5)

    def test_la_foto_sin_aprobar_no_se_muestra_en_el_catalogo(self):
        buena = crear_foto(producto=self.producto, imagen="tienda/productos/buena.jpg", posicion=0)
        mala = FotoProducto.objects.create(
            producto=self.producto, imagen="tienda/productos/mala.jpg", posicion=1
        )
        html = self.client.get(reverse("tienda:catalogo")).content.decode()
        self.assertIn(buena.url_cacheable.replace("&", "&amp;"), html)
        self.assertNotIn(mala.url_cacheable.replace("&", "&amp;"), html)
        self.assertNotIn("2 fotos", html)


class FotoModosTests(_Base):
    """Tarea 4: la foto aprobada de un producto aprobado es pública e
    `immutable`; cualquier otra sólo la ven el staff del gimnasio y el
    superadmin, sin caché."""

    def setUp(self):
        super().setUp()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.producto = crear_producto(gimnasio=self.gimnasio, nombre="P", precio=1)

    def _foto(self, producto=None, aprobada=True):
        foto = FotoProducto(producto=producto or self.producto, posicion=0, aprobada=aprobada)
        foto.imagen.save("f.png", ContentFile(PNG))
        return foto

    def _entrar(self, usuario):
        self.client.force_login(usuario)

    def test_aprobada_es_publica_e_immutable_sin_cookie(self):
        from plataforma.models import ActividadDiaria

        foto = self._foto()
        self._entrar(self.staff)
        r = self.client.get(foto.url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "public, max-age=31536000, immutable")
        self.assertEqual(list(r.cookies.keys()), [])
        self.assertFalse(ActividadDiaria.objects.exists())

    def test_sin_aprobar_da_404_no_store_al_anonimo(self):
        foto = self._foto(aprobada=False)
        r = self.client.get(foto.url_cacheable)
        self.assertEqual(r.status_code, 404)
        self.assertIn("no-store", r["Cache-Control"])

    def test_sin_aprobar_la_ve_el_staff_del_gimnasio_sin_cache(self):
        foto = self._foto(aprobada=False)
        self._entrar(self.staff)
        r = self.client.get(foto.url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_sin_aprobar_no_la_ve_el_staff_de_otro_gimnasio(self):
        foto = self._foto(aprobada=False)
        otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        ajeno = User.objects.create_user("ajeno", password="c-123456")
        Perfil.objects.create(usuario=ajeno, gimnasio=otro, rol=Perfil.Rol.STAFF)
        self._entrar(ajeno)
        r = self.client.get(foto.url_cacheable)
        self.assertEqual(r.status_code, 404)
        self.assertIn("no-store", r["Cache-Control"])

    def test_sin_aprobar_no_la_ve_un_alumno_del_gimnasio(self):
        foto = self._foto(aprobada=False)
        usuario = User.objects.create_user("alu", password="c-123456")
        Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self._entrar(usuario)
        self.assertEqual(self.client.get(foto.url_cacheable).status_code, 404)

    def test_de_un_producto_bajado_da_404_aunque_este_aprobada(self):
        foto = self._foto()
        Producto.objects.filter(pk=self.producto.pk).update(moderacion=Producto.Moderacion.BAJADO)
        self.assertEqual(self.client.get(foto.url_cacheable).status_code, 404)

    def test_de_un_producto_pendiente_por_denuncias_da_404_aunque_este_aprobada(self):
        foto = self._foto()
        Producto.objects.filter(pk=self.producto.pk).update(
            moderacion=Producto.Moderacion.PENDIENTE
        )
        self.assertEqual(self.client.get(foto.url_cacheable).status_code, 404)

    def test_un_usuario_sin_perfil_recibe_404(self):
        foto = self._foto(aprobada=False)
        self._entrar(User.objects.create_user("sinperfil", password="c-123456"))
        self.assertEqual(self.client.get(foto.url_cacheable).status_code, 404)

    def test_el_staff_de_un_gimnasio_suspendido_recibe_404(self):
        foto = self._foto(aprobada=False)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA
        )
        self._entrar(self.staff)
        self.assertEqual(self.client.get(foto.url_cacheable).status_code, 404)

    def test_el_superadmin_ve_una_sin_aprobar(self):
        foto = self._foto(aprobada=False)
        self._entrar(User.objects.create_superuser("root", password="c-123456"))
        r = self.client.get(foto.url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")
