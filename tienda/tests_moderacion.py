"""Moderación de la tienda: los estados, `q_vendible` y la migración que deja
aprobado todo lo que ya estaba a la venta (Tarea 1); la venta, las fotos y el
staff (Tareas 3-5); y las transiciones, la cola, el contador y el aviso de
`tienda/moderacion.py` (Tarea 6)."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.base import ContentFile
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda.models import (
    ConfiguracionTienda, DenunciaProducto, FotoProducto, Pedido, Producto, Variante, q_vendible,
)
from tienda import moderacion, services, tests_vistas_alumno
from tienda import tests_mercadopago_acreditar as t_mp
from tienda import tests_services as t_services
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


class StaffModeracionTests(TestCase):
    """Tarea 5: el staff guarda sin pisar la moderación, ve los badges y no
    puede borrar ni editar lo moderado."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(self.staff)

    def _producto(self, **campos):
        producto = crear_producto(
            gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000"),
            caracteristica_1="Talle", **campos,
        )
        variante = Variante.objects.create(producto=producto, valor_1="M", stock=3)
        return producto, variante

    def _datos_edicion(self, variante, **extra):
        datos = {
            "nombre": "Remera", "descripcion": "", "precio": "15000", "publicado": "on",
            "orden": "0", "categoria": "", "caracteristica_1": "Talle", "caracteristica_2": "",
            "variantes-TOTAL_FORMS": "1", "variantes-INITIAL_FORMS": "1",
            "variantes-MIN_NUM_FORMS": "1", "variantes-MAX_NUM_FORMS": "50",
            "variantes-0-id": str(variante.pk), "variantes-0-valor_1": "M",
            "variantes-0-activa": "on",
        }
        datos.update(extra)
        return datos

    def _editar(self, producto, variante, follow=False, **extra):
        return self.client.post(
            reverse("tienda:producto_editar", args=[producto.pk]),
            self._datos_edicion(variante, **extra), follow=follow,
        )

    def test_guardar_el_producto_limpia_el_aviso_de_foto_rechazada_y_no_otros(self):
        producto, variante = self._producto()
        Producto.objects.filter(pk=producto.pk).update(
            moderacion_motivo=moderacion.AVISO_FOTO_RECHAZADA
        )
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, moderacion.AVISO_FOTO_RECHAZADA)

        self._editar(producto, variante, precio="16000")

        self.assertEqual(Producto.objects.get(pk=producto.pk).moderacion_motivo, "")
        r = self.client.get(reverse("tienda:productos"))
        self.assertNotContains(r, moderacion.AVISO_FOTO_RECHAZADA)

        Producto.objects.filter(pk=producto.pk).update(
            moderacion_motivo=moderacion.MOTIVO_POR_DENUNCIAS
        )
        self._editar(producto, variante, precio="17000")
        self.assertEqual(
            Producto.objects.get(pk=producto.pk).moderacion_motivo, moderacion.MOTIVO_POR_DENUNCIAS
        )

    def test_el_alta_queda_pendiente_y_avisa(self):
        from tienda.tests_vistas_staff import datos_producto

        r = self.client.post(reverse("tienda:producto_crear"), datos_producto(), follow=True)
        producto = Producto.objects.get(gimnasio=self.gimnasio, nombre="Proteína")
        self.assertEqual(producto.moderacion, "pendiente")
        self.assertContains(r, "Producto guardado. Lo revisamos y lo publicamos en el día.")

    def test_editar_el_nombre_de_uno_aprobado_lo_manda_a_revision_y_sigue_a_la_venta(self):
        producto, variante = self._producto()
        r = self._editar(producto, variante, follow=True, nombre="Remera Dry")
        producto.refresh_from_db()
        self.assertIs(producto.cambio_pendiente, True)
        self.assertEqual(producto.moderacion, "aprobado")
        self.assertIsNotNone(producto.moderacion_desde)
        self.assertContains(
            r, "El cambio queda en revisión de la plataforma; mientras tanto sigue a la venta."
        )

    def test_editar_el_precio_no_lo_manda_a_revision(self):
        # Ya pasaba antes de implementar: no se le exige FAIL.
        producto, variante = self._producto()
        r = self._editar(producto, variante, follow=True, precio="16000")
        producto.refresh_from_db()
        self.assertIs(producto.cambio_pendiente, False)
        self.assertEqual(producto.precio, Decimal("16000"))
        self.assertContains(r, "Producto guardado.")
        self.assertNotContains(r, "queda en revisión")

    def test_si_lo_aprueban_mientras_se_edita_el_cambio_queda_en_revision(self):
        from tienda.forms import ProductoForm

        producto, variante = self._producto(moderacion=Producto.Moderacion.PENDIENTE)
        original = ProductoForm.save

        def guardar(form, *args, **kwargs):
            Producto.objects.filter(pk=producto.pk).update(moderacion="aprobado")
            return original(form, *args, **kwargs)

        with patch.object(ProductoForm, "save", guardar):
            self._editar(producto, variante, nombre="Remera Dry")
        producto.refresh_from_db()
        self.assertEqual(producto.moderacion, "aprobado")
        self.assertIs(producto.cambio_pendiente, True)

    def test_la_edicion_no_pisa_una_baja_en_paralelo(self):
        from tienda.forms import ProductoForm

        producto, variante = self._producto()
        original = ProductoForm.save

        def guardar(form, *args, **kwargs):
            Producto.objects.filter(pk=producto.pk).update(moderacion="bajado")
            return original(form, *args, **kwargs)

        with patch.object(ProductoForm, "save", guardar):
            self._editar(producto, variante, nombre="Remera Dry")
        producto.refresh_from_db()
        self.assertEqual(producto.moderacion, "bajado")
        self.assertEqual(producto.nombre, "Remera Dry")

    def test_uno_bajado_no_se_puede_editar(self):
        producto, variante = self._producto(moderacion=Producto.Moderacion.BAJADO)
        url = reverse("tienda:producto_editar", args=[producto.pk])
        aviso = "Este producto fue dado de baja por la plataforma y no se puede editar."
        r = self.client.get(url, follow=True)
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertContains(r, aviso)
        r = self._editar(producto, variante, follow=True, nombre="Otro")
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertContains(r, aviso)
        producto.refresh_from_db()
        self.assertEqual(producto.nombre, "Remera")

    def test_uno_bajado_no_se_puede_borrar(self):
        producto, _ = self._producto(moderacion=Producto.Moderacion.BAJADO)
        url = reverse("tienda:producto_borrar", args=[producto.pk])
        self.assertContains(self.client.get(url), "denuncias o una baja de la plataforma")
        r = self.client.post(url, follow=True)
        self.assertTrue(Producto.objects.filter(pk=producto.pk).exists())
        self.assertContains(r, "No se puede eliminar")

    def test_uno_denunciado_no_se_puede_borrar(self):
        producto, _ = self._producto()
        DenunciaProducto.objects.create(
            gimnasio=self.gimnasio, producto=producto, motivo="otro",
        )
        url = reverse("tienda:producto_borrar", args=[producto.pk])
        self.assertContains(self.client.get(url), "denuncias o una baja de la plataforma")
        r = self.client.post(url, follow=True)
        self.assertTrue(Producto.objects.filter(pk=producto.pk).exists())
        self.assertContains(r, "No se puede eliminar")

    def test_el_listado_muestra_los_badges(self):
        crear_producto(
            gimnasio=self.gimnasio, nombre="Pendiente", precio=1,
            moderacion=Producto.Moderacion.PENDIENTE,
        )
        crear_producto(
            gimnasio=self.gimnasio, nombre="Bajado", precio=1,
            moderacion=Producto.Moderacion.BAJADO, moderacion_motivo="Foto no permitida",
        )
        crear_producto(gimnasio=self.gimnasio, nombre="Cambiado", precio=1, cambio_pendiente=True)
        crear_producto(gimnasio=self.gimnasio, nombre="Normal", precio=1)
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, '<span class="badge badge--alerta">Esperando aprobación</span>', count=1)
        self.assertContains(r, "Dado de baja por la plataforma", count=1)
        self.assertContains(r, '<span class="badge badge--alerta">Cambio en revisión</span>', count=1)
        self.assertContains(r, '<p class="texto-suave">Foto no permitida</p>', count=1)

    def test_el_form_marca_la_foto_sin_aprobar(self):
        producto, _ = self._producto()
        crear_foto(producto=producto, imagen="tienda/productos/a.png", posicion=0)
        FotoProducto.objects.create(producto=producto, imagen="tienda/productos/b.png", posicion=1)
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertContains(r, '<span class="badge badge--alerta">En revisión</span>', count=1)

    # --- Tarea 8: el aviso por mail desde el form del staff ---

    def _mails_tras(self, hacer):
        with self.captureOnCommitCallbacks(execute=True):
            r = hacer()
        return r, list(mail.outbox)

    @override_settings(SOLICITUDES_AVISO_EMAIL="plataforma@tugimapp.com")
    def test_el_alta_de_un_producto_avisa_a_la_plataforma(self):
        from tienda.tests_vistas_staff import datos_producto

        r, enviados = self._mails_tras(
            lambda: self.client.post(reverse("tienda:producto_crear"), datos_producto())
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(enviados), 1)
        self.assertIn(
            "http://testserver" + reverse("plataforma:moderacion"), enviados[0].body
        )

    @override_settings(SOLICITUDES_AVISO_EMAIL="plataforma@tugimapp.com")
    def test_un_cambio_de_texto_en_uno_aprobado_avisa(self):
        producto, variante = self._producto()
        _, enviados = self._mails_tras(
            lambda: self._editar(producto, variante, nombre="Remera Dry")
        )
        self.assertEqual(len(enviados), 1)

    @override_settings(SOLICITUDES_AVISO_EMAIL="plataforma@tugimapp.com")
    def test_fotos_nuevas_en_uno_aprobado_avisan_y_lo_dicen(self):
        from tienda.tests_vistas_staff import jpeg

        producto, variante = self._producto()
        r, enviados = self._mails_tras(
            lambda: self._editar(producto, variante, follow=True, fotos_nuevas=[jpeg()])
        )
        self.assertEqual(len(enviados), 1)
        self.assertContains(
            r, "Las fotos nuevas se ven en la tienda cuando la plataforma las apruebe."
        )

    @override_settings(SOLICITUDES_AVISO_EMAIL="plataforma@tugimapp.com")
    def test_editar_solo_el_precio_no_avisa(self):
        producto, variante = self._producto()
        _, enviados = self._mails_tras(lambda: self._editar(producto, variante, precio="16000"))
        self.assertEqual(enviados, [])

    def test_las_fotos_nuevas_leen_la_moderacion_de_la_base(self):
        # Se abrió la pantalla con el producto PENDIENTE y lo aprobaron
        # mientras se editaba: las fotos nuevas igual esperan aprobación.
        from tienda.forms import ProductoForm
        from tienda.tests_vistas_staff import jpeg

        producto, variante = self._producto(moderacion=Producto.Moderacion.PENDIENTE)
        original = ProductoForm.save

        def guardar(form, *args, **kwargs):
            Producto.objects.filter(pk=producto.pk).update(moderacion="aprobado")
            return original(form, *args, **kwargs)

        with patch.object(ProductoForm, "save", guardar):
            r = self._editar(producto, variante, follow=True, fotos_nuevas=[jpeg()])
        self.assertContains(
            r, "Las fotos nuevas se ven en la tienda cuando la plataforma las apruebe."
        )

    def test_el_admin_muestra_la_moderacion_de_solo_lectura(self):
        from django.contrib import admin

        readonly = admin.site._registry[Producto].readonly_fields
        for campo in ("moderacion", "cambio_pendiente", "moderacion_motivo", "moderacion_desde"):
            self.assertIn(campo, readonly)
        self.assertIn("moderacion", admin.site._registry[Producto].list_filter)
        denuncias = admin.site._registry[DenunciaProducto]
        self.assertFalse(denuncias.has_add_permission(None))
        self.assertFalse(denuncias.has_change_permission(None))
        self.assertFalse(denuncias.has_delete_permission(None))


# --- Tarea 6: transiciones, cola, contador y aviso -----------------------------

M = Producto.Moderacion
EP = Pedido.Estado
ABIERTA = DenunciaProducto.Estado.ABIERTA


class _BaseModeracion(_Base):
    def setUp(self):
        super().setUp()
        self.producto = crear_producto(gimnasio=self.gimnasio, nombre="Proteína", precio=10)
        self.variante = Variante.objects.create(producto=self.producto, nombre="1 kg", stock=10)
        self.alumnos = [
            Alumno.objects.create(gimnasio=self.gimnasio, nombre=f"Alu{i}", apellido="X")
            for i in range(4)
        ]

    def denunciar(self, alumno, producto=None):
        return moderacion.registrar_denuncia(
            producto=producto or self.producto, alumno=alumno,
            motivo=DenunciaProducto.Motivo.ESTAFA, detalle="",
        )

    def foto(self, producto=None, aprobada=False):
        foto = FotoProducto(producto=producto or self.producto, aprobada=aprobada)
        foto.imagen.save("f.png", ContentFile(PNG), save=False)
        foto.save()
        return foto

    def recargar(self, objeto):
        objeto.refresh_from_db()
        return objeto


class TransicionesTests(_BaseModeracion):
    def test_aprobar_aprueba_producto_fotos_vistas_y_resuelve_denuncias_vistas(self):
        Producto.objects.filter(pk=self.producto.pk).update(
            moderacion=M.PENDIENTE, moderacion_motivo="algo"
        )
        foto = self.foto()
        self.denunciar(self.alumnos[0])
        denuncia = DenunciaProducto.objects.get()
        self.recargar(self.producto)
        ahora = timezone.now()

        hecho = moderacion.aprobar_producto(
            self.producto.pk, version=self.producto.modificado,
            fotos_vistas=[foto.pk], denuncias_vistas=[denuncia.pk], ahora=ahora,
        )

        self.assertIs(hecho, True)
        self.recargar(self.producto)
        self.assertEqual(self.producto.moderacion, M.APROBADO)
        self.assertIs(self.producto.cambio_pendiente, False)
        self.assertEqual(self.producto.moderacion_motivo, "")
        self.assertEqual(self.producto.moderacion_desde, ahora)
        self.assertIs(self.recargar(foto).aprobada, True)
        self.assertEqual(self.recargar(denuncia).estado, DenunciaProducto.Estado.RESUELTA)

    def test_aprobar_no_aprueba_una_foto_que_no_vio(self):
        vista, nueva = self.foto(), self.foto()
        Producto.objects.filter(pk=self.producto.pk).update(cambio_pendiente=True)
        self.recargar(self.producto)
        moderacion.aprobar_producto(
            self.producto.pk, version=self.producto.modificado,
            fotos_vistas=[vista.pk], denuncias_vistas=[],
        )
        self.assertIs(self.recargar(vista).aprobada, True)
        self.assertIs(self.recargar(nueva).aprobada, False)

    def test_aprobar_no_aprueba_fotos_de_otro_producto(self):
        otro = crear_producto(gimnasio=self.gimnasio, nombre="Otro", precio=1)
        ajena = self.foto(producto=otro)
        self.recargar(self.producto)
        moderacion.aprobar_producto(
            self.producto.pk, version=self.producto.modificado,
            fotos_vistas=[ajena.pk], denuncias_vistas=[],
        )
        self.assertIs(self.recargar(ajena).aprobada, False)

    def test_aprobar_con_version_vieja_no_hace_nada(self):
        Producto.objects.filter(pk=self.producto.pk).update(cambio_pendiente=True)
        self.recargar(self.producto)
        version = self.producto.modificado
        # El staff edita el texto con la pantalla del superadmin abierta:
        # `save()` mueve `modificado` (un `update()` no lo movería).
        self.producto.nombre = "Proteína nueva"
        self.producto.save()

        hecho = moderacion.aprobar_producto(
            self.producto.pk, version=version, fotos_vistas=[], denuncias_vistas=[],
        )

        self.assertIs(hecho, False)
        self.assertIs(self.recargar(self.producto).cambio_pendiente, True)

    def test_aprobar_un_bajado_no_hace_nada(self):
        Producto.objects.filter(pk=self.producto.pk).update(moderacion=M.BAJADO)
        foto = self.foto()
        self.recargar(self.producto)
        hecho = moderacion.aprobar_producto(
            self.producto.pk, version=self.producto.modificado,
            fotos_vistas=[foto.pk], denuncias_vistas=[],
        )
        self.assertIs(hecho, False)
        self.assertEqual(self.recargar(self.producto).moderacion, M.BAJADO)
        self.assertIs(self.recargar(foto).aprobada, False)

    def test_aprobar_foto_no_vale_para_un_producto_bajado(self):
        foto = self.foto()
        self.assertIs(moderacion.aprobar_foto(foto.pk), True)
        self.assertIs(self.recargar(foto).aprobada, True)
        self.assertIs(moderacion.aprobar_foto(foto.pk), False)  # ya estaba
        otra = self.foto()
        Producto.objects.filter(pk=self.producto.pk).update(moderacion=M.BAJADO)
        self.assertIs(moderacion.aprobar_foto(otra.pk), False)
        self.assertIs(self.recargar(otra).aprobada, False)

    def test_rechazar_foto_borra_la_fila_y_avisa_al_staff(self):
        foto = self.foto(aprobada=True)
        nombre, almacen = foto.imagen.name, foto.imagen.storage

        self.assertIs(moderacion.rechazar_foto(foto.pk), True)

        self.assertFalse(FotoProducto.objects.filter(pk=foto.pk).exists())
        self.assertTrue(almacen.exists(nombre))
        self.assertEqual(
            self.recargar(self.producto).moderacion_motivo, moderacion.AVISO_FOTO_RECHAZADA
        )
        self.assertIs(moderacion.rechazar_foto(foto.pk), False)

    def test_rechazar_foto_no_pisa_el_motivo_de_denuncias(self):
        Producto.objects.filter(pk=self.producto.pk).update(
            moderacion=M.PENDIENTE, moderacion_motivo=moderacion.MOTIVO_POR_DENUNCIAS
        )
        foto = self.foto()
        self.assertIs(moderacion.rechazar_foto(foto.pk), True)
        self.assertEqual(
            self.recargar(self.producto).moderacion_motivo, moderacion.MOTIVO_POR_DENUNCIAS
        )

    def test_aprobar_despues_de_rechazar_una_foto_conserva_el_aviso(self):
        Producto.objects.filter(pk=self.producto.pk).update(moderacion=M.PENDIENTE)
        mala = self.foto()
        buena = self.foto()
        moderacion.rechazar_foto(mala.pk)
        self.recargar(self.producto)
        moderacion.aprobar_producto(
            self.producto.pk, version=self.producto.modificado,
            fotos_vistas=[buena.pk], denuncias_vistas=[],
        )
        self.recargar(self.producto)
        self.assertEqual(self.producto.moderacion, M.APROBADO)
        self.assertEqual(self.producto.moderacion_motivo, moderacion.AVISO_FOTO_RECHAZADA)

    def test_aprobar_limpia_el_motivo_de_denuncias(self):
        Producto.objects.filter(pk=self.producto.pk).update(
            moderacion=M.PENDIENTE, moderacion_motivo=moderacion.MOTIVO_POR_DENUNCIAS
        )
        self.recargar(self.producto)
        moderacion.aprobar_producto(
            self.producto.pk, version=self.producto.modificado,
            fotos_vistas=[], denuncias_vistas=[],
        )
        self.assertEqual(self.recargar(self.producto).moderacion_motivo, "")

    def test_rechazar_foto_de_un_bajado_no_hace_nada(self):
        foto = self.foto()
        Producto.objects.filter(pk=self.producto.pk).update(moderacion=M.BAJADO)
        self.assertIs(moderacion.rechazar_foto(foto.pk), False)
        self.assertTrue(FotoProducto.objects.filter(pk=foto.pk).exists())


class BajarTests(_BaseModeracion):
    def setUp(self):
        super().setUp()
        self.otro = crear_producto(gimnasio=self.gimnasio, nombre="Shaker", precio=5)
        self.otra_variante = Variante.objects.create(producto=self.otro, stock=5)

    def pedido(self, estado, items, alumno=None):
        """Un pedido con su stock YA descontado (como lo deja el checkout)."""
        pedido = Pedido.objects.create(
            gimnasio=self.gimnasio, alumno=alumno or self.alumnos[0], estado=estado,
            total=Decimal("1"), reservado_hasta=timezone.now() + timedelta(hours=24),
        )
        for variante, cantidad in items:
            pedido.items.create(
                variante=variante, nombre=str(variante), precio_unitario=1, cantidad=cantidad
            )
        return pedido

    def stock(self, variante):
        return self.recargar(variante).stock

    def test_bajar_cancela_pendientes_devuelve_stock_y_no_toca_los_pagados(self):
        p1 = self.pedido(EP.PENDIENTE_PAGO, [(self.variante, 2), (self.otra_variante, 1)])
        p2 = self.pedido(EP.EN_REVISION, [(self.variante, 1)])
        p3 = self.pedido(EP.PAGADO, [(self.variante, 1)])
        p4 = self.pedido(EP.ENTREGADO, [(self.variante, 1)])
        ajeno = self.pedido(EP.PENDIENTE_PAGO, [(self.otra_variante, 1)])

        with patch("notificaciones.services.notificar_pedido_al_alumno") as push, \
                self.captureOnCommitCallbacks(execute=True):
            cancelados = moderacion.bajar_producto(self.producto.pk, motivo="Vende anabólicos")

        self.assertEqual(cancelados, [p1.pk])
        self.recargar(p1)
        self.assertEqual(p1.estado, EP.CANCELADO)
        self.assertIs(p1.cancelado_por_staff, True)
        self.assertEqual(p1.motivo, moderacion.MOTIVO_PEDIDO_CANCELADO)
        # Vuelve el stock de los DOS ítems de P1; lo de P2 y P3 sigue reservado.
        self.assertEqual(self.stock(self.variante), 12)
        self.assertEqual(self.stock(self.otra_variante), 6)
        self.assertEqual(self.recargar(p2).estado, EP.EN_REVISION)
        self.assertEqual(self.recargar(p3).estado, EP.PAGADO)
        self.assertEqual(self.recargar(p4).estado, EP.ENTREGADO)
        self.assertEqual(self.recargar(ajeno).estado, EP.PENDIENTE_PAGO)
        self.recargar(self.producto)
        self.assertEqual(self.producto.moderacion, M.BAJADO)
        self.assertEqual(self.producto.moderacion_motivo, "Vende anabólicos")
        self.assertIsNotNone(self.producto.moderacion_desde)
        self.assertEqual(
            [(c.args[0].pk, c.args[1]) for c in push.call_args_list],
            [(p1.pk, "cancelado_plataforma")],
        )

    def test_bajar_corta_el_motivo_y_limpia_el_cambio_pendiente(self):
        Producto.objects.filter(pk=self.producto.pk).update(cambio_pendiente=True)
        self.assertEqual(moderacion.bajar_producto(self.producto.pk, motivo="x" * 400), [])
        self.recargar(self.producto)
        self.assertEqual(len(self.producto.moderacion_motivo), 300)
        self.assertIs(self.producto.cambio_pendiente, False)

    def test_bajar_dos_veces_devuelve_none_la_segunda(self):
        self.assertEqual(moderacion.bajar_producto(self.producto.pk, motivo="x"), [])
        self.assertIsNone(moderacion.bajar_producto(self.producto.pk, motivo="y"))
        self.assertEqual(self.recargar(self.producto).moderacion_motivo, "x")

    def test_bajar_resuelve_las_denuncias(self):
        self.denunciar(self.alumnos[0])
        self.denunciar(self.alumnos[1])
        moderacion.bajar_producto(self.producto.pk, motivo="x")
        self.assertFalse(DenunciaProducto.objects.filter(estado=ABIERTA).exists())
        self.assertEqual(DenunciaProducto.objects.count(), 2)

    def test_el_aviso_al_alumno_dice_que_fue_la_plataforma(self):
        from notificaciones import services as avisos

        p1 = self.pedido(EP.PENDIENTE_PAGO, [(self.variante, 1)])
        with patch.object(avisos, "notificar_a_usuario") as push:
            usuario = User.objects.create_user("alu-push", password="c-123456")
            perfil = Perfil.objects.create(
                usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
            )
            Alumno.objects.filter(pk=p1.alumno_id).update(perfil=perfil)
            with self.captureOnCommitCallbacks(execute=True):
                moderacion.bajar_producto(self.producto.pk, motivo="x")
        payload = push.call_args.args[1]
        self.assertEqual(payload["title"], "Pedido cancelado")
        self.assertEqual(
            payload["body"],
            f"La plataforma dio de baja un producto de tu pedido #{p1.pk} y lo canceló.",
        )

    def test_tres_denuncias_no_cancelan_los_pedidos_pendientes(self):
        """Decisión 8: con 3 denuncias sale de la venta, pero sus pedidos sin
        pagar siguen vivos hasta que el superadmin lo baje."""
        p1 = self.pedido(EP.PENDIENTE_PAGO, [(self.variante, 1)])
        for alumno in self.alumnos[:3]:
            self.denunciar(alumno)
        self.assertEqual(self.recargar(self.producto).moderacion, M.PENDIENTE)
        self.assertEqual(self.recargar(p1).estado, EP.PENDIENTE_PAGO)
        self.assertEqual(self.stock(self.variante), 10)

    def test_cancelar_pedidos_con_carrera_va_de_a_uno(self):
        """Si otro proceso movió un pedido entre el SELECT y el UPDATE masivo,
        se cancela de a uno: el que ya no está pendiente no se toca ni
        devuelve stock dos veces."""
        a = self.pedido(EP.PENDIENTE_PAGO, [(self.variante, 1)])
        b = self.pedido(EP.PENDIENTE_PAGO, [(self.variante, 2)])
        Pedido.objects.filter(pk=b.pk).update(estado=EP.PAGADO)
        with patch.object(
            services, "_pendientes_de_producto", lambda producto_id: [a.pk, b.pk]
        ):
            cancelados = services.cancelar_pedidos_de_producto(self.producto.pk, motivo="m")
        self.assertEqual(cancelados, [a.pk])
        self.assertEqual(self.recargar(b).estado, EP.PAGADO)
        self.assertEqual(self.stock(self.variante), 11)


class PagoTardioTests(t_mp._Base):
    def test_un_pago_tardio_de_mp_sobre_un_pedido_cancelado_por_bajar_va_a_revisar(self):
        p = self.pedido()
        moderacion.bajar_producto(self.producto.pk, motivo="x")
        self.assertEqual(self.estado(p), EP.CANCELADO)
        self.assertEqual(self.stock(), 6)
        final, staff, _ = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, EP.REVISAR)
        self.assertIsNone(final.pagado_en)
        self.assertEqual(self.stock(), 6)
        self.assertEqual(staff, ["revisar_mp"])

    def test_un_pago_tardio_sobre_un_vencido_de_un_producto_bajado_va_a_revisar(self):
        """Decisión 1: `_re_reservar` revivía a PAGADO un vencido sin mirar la
        moderación."""
        p = self.pedido(EP.VENCIDO)
        moderacion.bajar_producto(self.producto.pk, motivo="x")
        final, staff, alumno = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, EP.REVISAR)
        self.assertEqual(final.payment_id, "1")
        self.assertEqual(self.stock(), 5)  # no se re-reservó
        self.assertEqual((staff, alumno), (["revisar_mp"], ["revisar_mp"]))

    def test_un_pago_tardio_sobre_un_cancelado_por_el_alumno_de_un_pendiente_va_a_revisar(self):
        p = self.pedido(EP.CANCELADO)
        Producto.objects.filter(pk=self.producto.pk).update(moderacion=M.PENDIENTE)
        final, _, _ = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, EP.REVISAR)
        self.assertEqual(self.stock(), 5)


class CheckoutContraBajaTests(t_services._Base):
    def test_checkout_que_cruza_una_baja_no_deja_pedido_vivo(self):
        """Foco 6: «Bajar» entra entre la validación de `crear_pedido` y los
        UPDATE de stock. La segunda mirada (dentro del `atomic`, después de los
        UPDATE) ya lo ve bajado: no queda pedido y el stock vuelve."""
        original = services._variantes_vendibles
        llamadas = []

        def con_baja_en_el_medio(gimnasio):
            llamadas.append(gimnasio)
            if len(llamadas) == 1:
                return original(gimnasio)
            # Lo que ve el checkout después de que la baja commiteó.
            return original(gimnasio).exclude(producto=self.producto)

        with patch.object(services, "_variantes_vendibles", con_baja_en_el_medio):
            with self.assertRaises(services.ErrorDeTienda) as e:
                self.comprar({self.m.pk: 1, self.l.pk: 1})
        self.assertEqual(e.exception.motivo, "producto_no_disponible")
        self.assertFalse(Pedido.objects.exists())
        self.assertEqual(self.stock(self.m), 3)
        self.assertEqual(self.stock(self.l), 1)


class DenunciaServicioTests(_BaseModeracion):
    def test_tres_denuncias_de_alumnos_distintos_lo_vuelven_pendiente(self):
        ahora = timezone.now()
        for alumno in self.alumnos[:3]:
            self.assertIs(
                moderacion.registrar_denuncia(
                    producto=self.producto, alumno=alumno,
                    motivo=DenunciaProducto.Motivo.DROGAS, detalle="raro", ahora=ahora,
                ),
                True,
            )
        self.recargar(self.producto)
        self.assertEqual(self.producto.moderacion, M.PENDIENTE)
        self.assertEqual(self.producto.moderacion_motivo, moderacion.MOTIVO_POR_DENUNCIAS)
        self.assertEqual(self.producto.moderacion_desde, ahora)
        denuncia = DenunciaProducto.objects.filter(alumno=self.alumnos[0]).get()
        self.assertEqual(denuncia.gimnasio, self.gimnasio)
        self.assertEqual(denuncia.detalle, "raro")

    def test_dos_no_alcanzan(self):
        for alumno in self.alumnos[:2]:
            self.denunciar(alumno)
        self.assertEqual(self.recargar(self.producto).moderacion, M.APROBADO)

    def test_la_misma_persona_no_denuncia_dos_veces(self):
        self.assertIs(self.denunciar(self.alumnos[0]), True)
        self.assertIs(self.denunciar(self.alumnos[0]), False)
        self.assertEqual(DenunciaProducto.objects.count(), 1)

    def test_el_choque_de_la_constraint_tambien_da_false(self):
        """Dos denuncias simultáneas del mismo alumno: la mirada en Python no
        ve la otra, la constraint sí."""
        self.denunciar(self.alumnos[0])
        with patch.object(moderacion, "_ya_denuncio", lambda producto, alumno: False):
            self.assertIs(self.denunciar(self.alumnos[0]), False)
        self.assertEqual(DenunciaProducto.objects.count(), 1)

    def test_despues_de_aprobar_solo_cuentan_las_nuevas(self):
        for alumno in self.alumnos[:3]:
            self.denunciar(alumno)
        self.recargar(self.producto)
        self.assertEqual(self.producto.moderacion, M.PENDIENTE)
        vistas = list(DenunciaProducto.objects.values_list("pk", flat=True))
        self.assertIs(
            moderacion.aprobar_producto(
                self.producto.pk, version=self.producto.modificado,
                fotos_vistas=[], denuncias_vistas=vistas,
            ),
            True,
        )
        self.denunciar(self.alumnos[3])
        self.assertEqual(self.recargar(self.producto).moderacion, M.APROBADO)
        # Los mismos tres pueden volver a denunciar: la vieja está resuelta.
        self.assertIs(self.denunciar(self.alumnos[0]), True)


@override_settings(SOLICITUDES_AVISO_EMAIL="plataforma@tugimapp.com")
class AvisoTests(_BaseModeracion):
    URL = "https://www.tugimapp.com/plataforma/moderacion/"

    def avisar(self, gimnasio=None, ahora=None):
        with self.captureOnCommitCallbacks(execute=True):
            return moderacion.avisar_a_la_plataforma(
                gimnasio or self.gimnasio, url_cola=self.URL, ahora=ahora
            )

    def test_el_aviso_sale_una_vez_por_hora_por_gimnasio(self):
        ahora = timezone.now()
        self.assertIs(self.avisar(ahora=ahora), True)
        self.assertIs(self.avisar(ahora=ahora + timedelta(minutes=5)), False)
        self.assertEqual(len(mail.outbox), 1)
        enviado = mail.outbox[0]
        self.assertEqual(enviado.to, ["plataforma@tugimapp.com"])
        self.assertEqual(enviado.subject, "Moderación: A tiene algo para revisar")
        self.assertIn(self.URL, enviado.body)

        self.assertIs(self.avisar(ahora=ahora + timedelta(minutes=61)), True)
        self.assertEqual(len(mail.outbox), 2)

        otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.assertIs(self.avisar(otro, ahora=ahora + timedelta(minutes=62)), True)
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(mail.outbox[2].subject, "Moderación: B tiene algo para revisar")

    @override_settings(SOLICITUDES_AVISO_EMAIL="", DEFAULT_FROM_EMAIL="no-reply@tugimapp.com")
    def test_sin_casilla_de_aviso_va_al_remitente(self):
        self.assertIs(self.avisar(), True)
        self.assertEqual(mail.outbox[0].to, ["no-reply@tugimapp.com"])

    def test_la_demo_no_manda_mail_ni_suma_al_contador(self):
        demo = Gimnasio.objects.create(nombre="Demo", slug="demo", es_demo=True)
        de_la_demo = crear_producto(
            gimnasio=demo, nombre="Demo", precio=1, moderacion=M.PENDIENTE
        )
        self.assertEqual(moderacion.contador(), 0)
        self.assertIs(self.avisar(demo), False)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(ConfiguracionTienda.objects.filter(gimnasio=demo).exists())
        cola = moderacion.cola()
        self.assertEqual(list(cola["pendientes"]), [])
        self.assertEqual(list(cola["demo"]), [de_la_demo])


class ColaTests(_BaseModeracion):
    def test_q_para_revisar_y_las_secciones_de_la_cola(self):
        g = self.gimnasio
        nuevo = crear_producto(gimnasio=g, nombre="Nuevo", precio=1, moderacion=M.PENDIENTE)
        texto = crear_producto(gimnasio=g, nombre="Texto", precio=1, cambio_pendiente=True)
        con_foto = crear_producto(gimnasio=g, nombre="Foto", precio=1)
        self.foto(producto=con_foto)
        denunciado = crear_producto(gimnasio=g, nombre="Denunciado", precio=1)
        self.denunciar(self.alumnos[0], producto=denunciado)
        crear_producto(gimnasio=g, nombre="Bajado", precio=1, moderacion=M.BAJADO,
                       cambio_pendiente=True)
        con_foto_aprobada = crear_producto(gimnasio=g, nombre="Limpio", precio=1)
        self.foto(producto=con_foto_aprobada, aprobada=True)

        self.assertEqual(moderacion.contador(), 4)
        cola = moderacion.cola()
        self.assertEqual(list(cola["pendientes"]), [nuevo])
        self.assertEqual(list(cola["a_la_venta"]), [texto, con_foto, denunciado])
        tarjeta = list(cola["a_la_venta"])[2]
        self.assertEqual(len(tarjeta.denuncias_abiertas), 1)
        self.assertEqual(tarjeta.denuncias_abiertas[0].alumno, self.alumnos[0])

    def _para_revisar(self, cantidad):
        for i in range(cantidad):
            producto = crear_producto(
                gimnasio=self.gimnasio, nombre=f"P{i}-{cantidad}", precio=1,
                moderacion=M.PENDIENTE if i % 2 else M.APROBADO,
                cambio_pendiente=not i % 2,
            )
            Variante.objects.create(producto=producto, stock=1)
            self.foto(producto=producto)
            self.denunciar(self.alumnos[0], producto=producto)

    def test_contador_y_cola_tienen_queries_constantes(self):
        self._para_revisar(2)
        with self.assertNumQueries(1):
            self.assertEqual(moderacion.contador(), 2)
        with CaptureQueriesContext(connection) as pocos:
            cola = moderacion.cola()
            list(cola["pendientes"]) + list(cola["a_la_venta"])
        self._para_revisar(6)
        with self.assertNumQueries(1):
            self.assertEqual(moderacion.contador(), 8)
        with CaptureQueriesContext(connection) as muchos:
            cola = moderacion.cola()
            productos = list(cola["pendientes"]) + list(cola["a_la_venta"])
        self.assertEqual(len(productos), 8)
        self.assertEqual(len(pocos), len(muchos))
        # Lo que la tarjeta lee no dispara queries nuevas.
        with self.assertNumQueries(0):
            for producto in productos:
                producto.gimnasio.nombre
                producto.categoria
                list(producto.fotos.all())
                list(producto.variantes.all())
                [d.alumno for d in producto.denuncias_abiertas]


# --- Tarea 8: «Denunciar» del alumno --------------------------------------------


@override_settings(SOLICITUDES_AVISO_EMAIL="plataforma@tugimapp.com")
class DenunciarVistaTests(tests_vistas_alumno._BaseAlumno):
    def url(self, producto=None):
        return reverse("tienda:denunciar", args=[(producto or self.producto).pk])

    def datos(self, **extra):
        datos = {"motivo": "estafa", "detalle": "Parece falso"}
        datos.update(extra)
        return datos

    def denunciar(self, producto=None, follow=False, **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url(producto), self.datos(**extra), follow=follow)

    def test_el_formulario_se_muestra(self):
        r = self.client.get(self.url())
        self.assertContains(r, "Remera")
        self.assertContains(r, "csrfmiddlewaretoken")

    def test_denunciar_crea_la_denuncia_y_avisa(self):
        r = self.denunciar(follow=True)
        self.assertRedirects(r, reverse("tienda:catalogo"))
        self.assertContains(r, "Gracias. La plataforma va a revisar este producto.")
        d = DenunciaProducto.objects.get()
        self.assertEqual((d.producto, d.alumno, d.motivo, d.detalle), (self.producto, self.alumno, "estafa", "Parece falso"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/plataforma/moderacion/", mail.outbox[0].body)

    def test_sin_motivo_muestra_el_error_en_su_linea(self):
        r = self.denunciar(motivo="")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<p class="config-error">')
        self.assertFalse(DenunciaProducto.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_un_detalle_largo_muestra_el_error_en_su_linea(self):
        r = self.denunciar(detalle="x" * 501)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<p class="config-error">')
        self.assertFalse(DenunciaProducto.objects.exists())

    def test_suplantando_no_se_puede(self):
        from tenants import suplantacion

        sesion = self.client.session
        sesion[suplantacion.CLAVE_SESION] = {"inicio": timezone.now().isoformat()}
        sesion.save()
        r = self.denunciar(follow=True)
        self.assertRedirects(r, reverse("tienda:catalogo"))
        self.assertContains(r, "Mientras ves la app como un alumno no podés denunciar productos.")
        self.assertFalse(DenunciaProducto.objects.exists())
        self.assertEqual(mail.outbox, [])

    def _no_esta_en_la_tienda(self, producto):
        # Un redirect con mensaje y no un 404: bajo `hx-boost` es un click mudo.
        with self.captureOnCommitCallbacks(execute=True):
            for r in (
                self.client.get(self.url(producto), follow=True),
                self.client.post(self.url(producto), self.datos(), follow=True),
            ):
                self.assertRedirects(r, reverse("tienda:catalogo"))
                self.assertContains(r, "Ese producto ya no está en la tienda.")
        self.assertFalse(DenunciaProducto.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_un_producto_de_otro_gimnasio_avisa_y_vuelve_al_catalogo(self):
        otro = Gimnasio.objects.create(nombre="B", slug="b")
        ajeno = crear_producto(gimnasio=otro, nombre="Ajeno", precio=1)
        self._no_esta_en_la_tienda(ajeno)

    def test_un_producto_pendiente_avisa_y_vuelve_al_catalogo(self):
        pendiente = crear_producto(
            gimnasio=self.gimnasio, nombre="Nuevo", precio=1, moderacion=Producto.Moderacion.PENDIENTE
        )
        self._no_esta_en_la_tienda(pendiente)

    def test_un_producto_bajado_avisa_y_vuelve_al_catalogo(self):
        bajado = crear_producto(
            gimnasio=self.gimnasio, nombre="Bajado", precio=1, moderacion=Producto.Moderacion.BAJADO
        )
        self._no_esta_en_la_tienda(bajado)

    def test_denunciar_dos_veces_avisa_y_no_duplica(self):
        self.denunciar()
        mail.outbox.clear()
        r = self.denunciar(follow=True)
        self.assertContains(r, "Ya lo denunciaste: lo estamos revisando.")
        self.assertEqual(DenunciaProducto.objects.count(), 1)
        self.assertEqual(mail.outbox, [])

    def test_el_catalogo_dice_ya_lo_denunciaste(self):
        r = self.client.get(reverse("tienda:catalogo"))
        self.assertContains(r, self.url())
        self.assertNotContains(r, "Ya lo denunciaste")
        self.denunciar()
        r = self.client.get(reverse("tienda:catalogo"))
        self.assertContains(r, "Ya lo denunciaste")
        self.assertNotContains(r, self.url())


class ClausulaDeUsoTests(TestCase):
    """La cláusula vive en UN parcial y las dos pantallas la incluyen."""

    def test_las_dos_pantallas_la_muestran(self):
        from django.template.loader import render_to_string

        texto = render_to_string("tienda/_clausula_uso.html").strip()
        self.assertIn("Está prohibido vender drogas", texto)
        gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=staff, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(staff)
        r = self.client.get(reverse("tienda:configuracion"))
        self.assertContains(r, texto, html=True)

        root = User.objects.create_superuser("root", "r@x.com", "c-123456")
        self.client.force_login(root)
        r = self.client.get(reverse("plataforma:gimnasio_detalle", args=[gimnasio.pk]))
        self.assertContains(r, texto, html=True)
