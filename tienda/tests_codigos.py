"""«Orden» opcional, código de barras de las variantes y búsqueda/escaneo en
la lista de productos (plan «tienda: variantes y códigos», T1)."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda.codigos import codigo_interno, digito_verificador
from tienda.models import CategoriaProducto, Producto, Variante


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(self.staff)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)

    def producto(self, nombre="Remera", gimnasio=None, variantes=(("M", ""),)):
        producto = Producto.objects.create(
            gimnasio=gimnasio or self.gimnasio, nombre=nombre, precio=Decimal("15000")
        )
        creadas = [
            Variante.objects.create(producto=producto, nombre=n, codigo=c, stock=3)
            for n, c in variantes
        ]
        return producto, creadas


def datos_edicion(producto, filas, **extra):
    """POST de edición con las variantes existentes (`filas` = lista de dicts
    con `variante` y lo que se quiera pisar) más una fila extra vacía."""
    datos = {
        "nombre": producto.nombre, "descripcion": "", "precio": "15000", "publicado": "on",
        "orden": "0", "categoria": "",
        "variantes-TOTAL_FORMS": str(len(filas) + 1), "variantes-INITIAL_FORMS": str(len(filas)),
        "variantes-MIN_NUM_FORMS": "0", "variantes-MAX_NUM_FORMS": "50",
    }
    for i, fila in enumerate(filas):
        v = fila["variante"]
        datos[f"variantes-{i}-id"] = str(v.pk)
        datos[f"variantes-{i}-nombre"] = fila.get("nombre", v.nombre)
        datos[f"variantes-{i}-codigo"] = fila.get("codigo", v.codigo)
        if fila.get("activa", True):
            datos[f"variantes-{i}-activa"] = "on"
        if fila.get("borrar"):
            datos[f"variantes-{i}-DELETE"] = "on"
    datos.update(extra)
    return datos


def datos_alta(**extra):
    datos = {
        "nombre": "Proteína", "descripcion": "", "precio": "25000", "publicado": "on",
        "orden": "0", "categoria": "",
        "variantes-TOTAL_FORMS": "2", "variantes-INITIAL_FORMS": "0",
        "variantes-MIN_NUM_FORMS": "0", "variantes-MAX_NUM_FORMS": "50",
        "variantes-0-nombre": "Chocolate", "variantes-0-stock_inicial": "4", "variantes-0-activa": "on",
        "variantes-1-nombre": "Vainilla", "variantes-1-stock_inicial": "2", "variantes-1-activa": "on",
    }
    datos.update(extra)
    return datos


class OrdenOpcionalTests(_Base):
    def test_categoria_sin_orden_queda_en_cero_y_sin_asterisco(self):
        r = self.client.get(reverse("tienda:categoria_crear"))
        self.assertContains(r, "Orden en el catálogo")
        self.assertNotRegex(r.content.decode(), r"Orden en el catálogo\s*<span class=\"campo-obligatorio\"")
        r = self.client.post(reverse("tienda:categoria_crear"), {"nombre": "Ropa", "orden": ""})
        self.assertRedirects(r, reverse("tienda:categorias"))
        self.assertEqual(CategoriaProducto.objects.get(nombre="Ropa").orden, 0)
        # Sin el campo en el POST, igual.
        self.client.post(reverse("tienda:categoria_crear"), {"nombre": "Suplementos"})
        self.assertEqual(CategoriaProducto.objects.get(nombre="Suplementos").orden, 0)

    def test_categoria_con_orden_lo_guarda(self):
        self.client.post(reverse("tienda:categoria_crear"), {"nombre": "Ropa", "orden": "3"})
        self.assertEqual(CategoriaProducto.objects.get(nombre="Ropa").orden, 3)

    def test_producto_sin_orden_queda_en_cero_y_sin_asterisco(self):
        r = self.client.get(reverse("tienda:producto_crear"))
        self.assertContains(r, "Orden en el catálogo")
        self.assertNotRegex(r.content.decode(), r"Orden en el catálogo\s*<span class=\"campo-obligatorio\"")
        datos = datos_alta()
        datos.pop("orden")
        r = self.client.post(reverse("tienda:producto_crear"), datos)
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertEqual(Producto.objects.get(nombre="Proteína").orden, 0)
        r = self.client.post(reverse("tienda:producto_crear"), datos_alta(nombre="Creatina", orden=""))
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertEqual(Producto.objects.get(nombre="Creatina").orden, 0)


class CodigoInternoTests(TestCase):
    def test_digito_verificador_de_un_ean13_conocido(self):
        # 4006381333931: el ejemplo clásico de EAN-13 válido.
        self.assertEqual(digito_verificador("400638133393"), "1")

    def test_codigo_interno_es_ean13_del_rango_20(self):
        self.assertEqual(codigo_interno(1), "2000000000015")
        codigo = codigo_interno(1234567)
        self.assertEqual(len(codigo), 13)
        self.assertTrue(codigo.startswith("200001234567"))
        self.assertEqual(codigo[-1], digito_verificador(codigo[:12]))


class CodigoEnElModeloTests(_Base):
    def test_nueva_sin_codigo_recibe_el_interno(self):
        _, (v,) = self.producto()
        self.assertEqual(v.codigo, codigo_interno(v.pk))  # en memoria también
        v.refresh_from_db()
        self.assertEqual(v.codigo, codigo_interno(v.pk))

    def test_un_save_posterior_no_lo_pisa_con_vacio(self):
        _, (v,) = self.producto()
        v.stock = 9
        v.save(update_fields=["stock"])
        v.save()
        v.refresh_from_db()
        self.assertEqual(v.codigo, codigo_interno(v.pk))

    def test_se_guarda_en_mayusculas_y_sin_espacios(self):
        _, (v,) = self.producto(variantes=(("M", " ab-12 34 "),))
        v.refresh_from_db()
        self.assertEqual(v.codigo, "AB-1234")


class CodigoEnElFormularioTests(_Base):
    def test_alta_sin_codigo_inventa_y_con_codigo_lo_guarda(self):
        r = self.client.post(
            reverse("tienda:producto_crear"), datos_alta(**{"variantes-1-codigo": "7791234567890"})
        )
        self.assertRedirects(r, reverse("tienda:productos"))
        choco = Variante.objects.get(nombre="Chocolate")
        vaini = Variante.objects.get(nombre="Vainilla")
        self.assertEqual(choco.codigo, codigo_interno(choco.pk))
        self.assertEqual(vaini.codigo, "7791234567890")

    def test_editar_el_codigo_manual_se_guarda_en_mayusculas(self):
        producto, (v,) = self.producto()
        r = self.client.post(
            reverse("tienda:producto_editar", args=[producto.pk]),
            datos_edicion(producto, [{"variante": v, "codigo": "rem-m-01"}]),
        )
        self.assertRedirects(r, reverse("tienda:productos"))
        v.refresh_from_db()
        self.assertEqual(v.codigo, "REM-M-01")

    def test_vaciarlo_al_editar_lo_regenera(self):
        producto, (v,) = self.producto(variantes=(("M", "ABCD"),))
        r = self.client.post(
            reverse("tienda:producto_editar", args=[producto.pk]),
            datos_edicion(producto, [{"variante": v, "codigo": ""}]),
        )
        self.assertRedirects(r, reverse("tienda:productos"))
        v.refresh_from_db()
        self.assertEqual(v.codigo, codigo_interno(v.pk))

    def test_el_form_muestra_el_codigo_y_carga_el_js_del_lector(self):
        producto, (v,) = self.producto(variantes=(("M", "ABCD-9"),))
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertContains(r, 'value="ABCD-9"')
        self.assertContains(r, "Código de barras")
        self.assertContains(r, "Vacío: la app inventa uno")
        self.assertContains(r, "js/tienda_variantes.js")
        self.assertContains(r, "data-codigo-barras")

    def _rechaza(self, respuesta, mensaje):
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, mensaje)
        self.assertContains(respuesta, "config-error")

    def test_formato_invalido_es_error_visible(self):
        for malo in ("AB", "AB_12", "ÑAND"):
            with self.subTest(malo=malo):
                r = self.client.post(
                    reverse("tienda:producto_crear"), datos_alta(**{"variantes-0-codigo": malo})
                )
                self._rechaza(r, "letras, números y guiones")
        # El largo lo frena antes el `max_length` del campo, con su mensaje.
        r = self.client.post(
            reverse("tienda:producto_crear"), datos_alta(**{"variantes-0-codigo": "A" * 33})
        )
        self._rechaza(r, "32 caracteres")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_duplicado_con_otro_producto_es_error(self):
        self.producto(variantes=(("M", "ABC-1"),))
        r = self.client.post(
            reverse("tienda:producto_crear"), datos_alta(**{"variantes-0-codigo": "abc-1"})
        )
        self._rechaza(r, "otro producto")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_duplicado_con_una_variante_inactiva_de_otro_producto_es_error(self):
        _, (v,) = self.producto(variantes=(("M", "ABC-1"),))
        Variante.objects.filter(pk=v.pk).update(activa=False)
        r = self.client.post(
            reverse("tienda:producto_crear"), datos_alta(**{"variantes-0-codigo": "ABC-1"})
        )
        self._rechaza(r, "otro producto")

    def test_dos_filas_con_el_mismo_codigo_es_error(self):
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_alta(**{"variantes-0-codigo": "ABC-1", "variantes-1-codigo": "abc-1"}),
        )
        self._rechaza(r, "Hay dos variantes con el código")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_intercambiar_dos_codigos_no_es_error(self):
        producto, (m, l) = self.producto(variantes=(("M", "COD-M"), ("L", "COD-L")))
        r = self.client.post(
            reverse("tienda:producto_editar", args=[producto.pk]),
            datos_edicion(producto, [
                {"variante": m, "codigo": "COD-L"}, {"variante": l, "codigo": "COD-M"},
            ]),
        )
        self.assertRedirects(r, reverse("tienda:productos"))
        m.refresh_from_db()
        l.refresh_from_db()
        self.assertEqual((m.codigo, l.codigo), ("COD-L", "COD-M"))

    def test_quitar_una_libera_su_codigo_para_otra_fila(self):
        producto, (m, l) = self.producto(variantes=(("M", "COD-M"), ("L", "COD-L")))
        r = self.client.post(
            reverse("tienda:producto_editar", args=[producto.pk]),
            datos_edicion(producto, [
                {"variante": m, "codigo": "COD-L"}, {"variante": l, "borrar": True},
            ]),
        )
        self.assertRedirects(r, reverse("tienda:productos"))
        m.refresh_from_db()
        self.assertEqual(m.codigo, "COD-L")

    def test_el_mismo_codigo_en_otro_gimnasio_esta_permitido(self):
        self.producto(gimnasio=self.otro, variantes=(("M", "ABC-1"),))
        r = self.client.post(
            reverse("tienda:producto_crear"), datos_alta(**{"variantes-0-codigo": "ABC-1"})
        )
        self.assertRedirects(r, reverse("tienda:productos"))

    def test_un_codigo_20_ajeno_es_error_y_el_propio_se_conserva(self):
        producto, (v,) = self.producto()
        # El suyo (el interno que le inventó la app) se re-postea sin problema.
        r = self.client.post(
            reverse("tienda:producto_editar", args=[producto.pk]),
            datos_edicion(producto, [{"variante": v}]),
        )
        self.assertRedirects(r, reverse("tienda:productos"))
        # El interno de otra variante (o uno inventado a mano con 20…), no.
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_alta(**{"variantes-0-codigo": codigo_interno(v.pk + 1000)}),
        )
        self._rechaza(r, "Los códigos que empiezan con 20 los reserva la app")

    def test_una_fila_nueva_con_solo_el_codigo_cuenta_como_variante(self):
        producto, (v,) = self.producto()
        datos = datos_edicion(producto, [{"variante": v}])
        datos["variantes-1-codigo"] = "SOLO-COD"
        r = self.client.post(reverse("tienda:producto_editar", args=[producto.pk]), datos)
        # Sin nombre choca con... nada: M tiene nombre. Queda como variante «».
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertTrue(producto.variantes.filter(codigo="SOLO-COD").exists())


class CodigoEnLaListaYElCatalogoTests(_Base):
    def test_la_lista_muestra_el_codigo_bajo_la_variante(self):
        self.producto(variantes=(("M", "REM-M"),))
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, "REM-M")

    def test_sin_codigo_muestra_raya(self):
        _, (v,) = self.producto()
        Variante.objects.filter(pk=v.pk).update(codigo="")  # creada por el código viejo
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, "data-codigo>—<")

    def test_el_catalogo_del_alumno_no_muestra_el_codigo(self):
        from tienda.models import obtener_configuracion

        config = obtener_configuracion(self.gimnasio)
        config.publicada = True
        config.save()
        self.producto(variantes=(("M", "REM-M"), ("L", "REM-L")))
        usuario = User.objects.create_user("ana", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        self.client.force_login(usuario)
        r = self.client.get(reverse("tienda:catalogo"))
        self.assertContains(r, "Remera")
        self.assertNotContains(r, "REM-M")
        self.assertNotContains(r, "REM-L")


class BusquedaTests(_Base):
    def setUp(self):
        super().setUp()
        self.remera, (self.m, self.l) = self.producto(variantes=(("M", "REM-M"), ("L", "REM-L")))
        self.prote, (self.choco,) = self.producto(nombre="Proteína whey", variantes=(("Chocolate", "7791234567890"),))
        self.creatina, _ = self.producto(nombre="Creatina", variantes=(("", ""),))

    def buscar(self, q):
        return self.client.get(reverse("tienda:productos"), {"q": q})

    def productos(self, r):
        return [p for p, _ in r.context["filas"]]

    def test_por_nombre_del_producto(self):
        self.assertEqual(self.productos(self.buscar("prote")), [self.prote])

    def test_por_nombre_de_variante(self):
        self.assertEqual(self.productos(self.buscar("chocol")), [self.prote])

    def test_por_codigo_parcial(self):
        self.assertEqual(self.productos(self.buscar("rem-")), [self.remera])
        self.assertEqual(self.productos(self.buscar("12345")), [self.prote])

    def test_coincidencia_parcial_en_dos_variantes_no_duplica_el_producto(self):
        self.assertEqual(self.productos(self.buscar("rem")), [self.remera])

    def test_codigo_exacto_destaca_la_fila_y_enfoca_su_ajuste(self):
        r = self.buscar("rem-l")
        self.assertEqual(self.productos(r), [self.remera])
        self.assertEqual(r.context["destacadas"], {self.l.pk})
        html = r.content.decode()
        self.assertIn("data-destacada", html)
        # Un solo `autofocus`, en el ajuste de la L; el buscador no.
        self.assertEqual(html.count("autofocus"), 1)
        self.assertRegex(html, rf'aria-label="[^"]*\bL\b[^"]*"[^>]*autofocus')
        self.assertNotRegex(html, r'name="q"[^>]*autofocus')
        # El ajuste vuelve a la lista con la fila marcada, no a la búsqueda.
        self.assertIn('name="desde_busqueda" value="1"', html)

    def test_sin_busqueda_el_buscador_lleva_autofocus_y_el_ajuste_no(self):
        r = self.client.get(reverse("tienda:productos"))
        html = r.content.decode()
        self.assertEqual(html.count("autofocus"), 1)
        self.assertRegex(html, r'name="q"[^>]*autofocus')
        self.assertIn('role="search"', html)
        self.assertNotIn("desde_busqueda", html)

    def test_sin_resultados_lo_dice_y_ofrece_ver_todos(self):
        r = self.buscar("nada-que-ver")
        self.assertContains(r, "No hay productos que coincidan con «nada-que-ver»")
        self.assertContains(r, "Ver todos")

    def test_no_encuentra_productos_de_otro_gimnasio(self):
        self.producto(nombre="Ajeno", gimnasio=self.otro, variantes=(("M", "AJENO-1"),))
        self.assertEqual(self.productos(self.buscar("AJENO-1")), [])
        self.assertEqual(self.productos(self.buscar("ajeno")), [])

    def test_codigo_exacto_de_otro_gimnasio_no_destaca(self):
        self.producto(nombre="Ajeno", gimnasio=self.otro, variantes=(("M", "REM-X"),))
        r = self.buscar("REM-X")
        self.assertEqual(r.context["destacadas"], set())

    def test_busqueda_larga_se_corta_sin_error(self):
        self.assertEqual(self.buscar("x" * 500).status_code, 200)


class EscaneoEnSerieTests(_Base):
    def test_ajuste_desde_la_busqueda_vuelve_con_la_fila_marcada_y_el_buscador_en_foco(self):
        _, (v,) = self.producto(variantes=(("M", "REM-M"),))
        r = self.client.post(
            reverse("tienda:variante_ajustar", args=[v.pk]), {"delta": "2", "desde_busqueda": "1"}
        )
        self.assertRedirects(r, reverse("tienda:productos") + f"?ajustada={v.pk}")
        v.refresh_from_db()
        self.assertEqual(v.stock, 5)
        r = self.client.get(r["Location"])
        html = r.content.decode()
        self.assertIn("data-destacada", html)
        self.assertEqual(html.count("autofocus"), 1)
        self.assertRegex(html, r'name="q"[^>]*value=""[^>]*autofocus')
        # El próximo ajuste sigue en serie.
        self.assertIn('name="desde_busqueda" value="1"', html)

    def test_ajuste_sin_desde_busqueda_redirige_como_siempre(self):
        _, (v,) = self.producto()
        r = self.client.post(reverse("tienda:variante_ajustar", args=[v.pk]), {"delta": "2"})
        self.assertRedirects(r, reverse("tienda:productos"))

    def test_ajustada_invalida_se_ignora(self):
        self.producto()
        for valor in ("abc", "²", "-3", "99999999999999999999999"):
            with self.subTest(valor=valor):
                r = self.client.get(reverse("tienda:productos"), {"ajustada": valor})
                self.assertEqual(r.status_code, 200)
                self.assertNotIn("data-destacada", r.content.decode())

    def test_los_inputs_de_ajuste_no_autocompletan(self):
        self.producto()
        r = self.client.get(reverse("tienda:productos"))
        self.assertRegex(r.content.decode(), r'name="delta"[^>]*autocomplete="off"')


class BusquedaEscalaTests(_Base):
    def _queries(self, params):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(reverse("tienda:productos"), params).status_code, 200)
        return len(ctx)

    def _cargar(self, cantidad):
        for i in range(cantidad):
            self.producto(variantes=(("M", ""), ("L", "")))

    def test_las_queries_con_busqueda_no_dependen_de_la_cantidad(self):
        for params in ({"q": "remera"}, {"q": "rem"}, {"ajustada": "1"}):
            with self.subTest(params=params):
                self._cargar(2)
                self._queries(params)
                pocos = self._queries(params)
                self._cargar(3)
                self.assertEqual(pocos, self._queries(params))

    def test_codigo_exacto_no_depende_de_la_cantidad(self):
        self._cargar(2)
        _, (v,) = self.producto(variantes=(("M", "EXACTO-1"),))
        self._queries({"q": "EXACTO-1"})
        pocos = self._queries({"q": "EXACTO-1"})
        self._cargar(3)
        self.assertEqual(pocos, self._queries({"q": "EXACTO-1"}))


class AdminCodigoTests(_Base):
    def test_codigo_en_minusculas_desde_el_admin_se_guarda_en_mayusculas(self):
        admin = User.objects.create_superuser("root", "r@x.com", "c-123456")
        self.client.force_login(admin)
        producto, (v,) = self.producto()
        url = reverse("admin:tienda_producto_change", args=[producto.pk])
        r = self.client.get(url)
        self.assertContains(r, 'name="variantes-0-codigo"')
        datos = {
            "gimnasio": str(self.gimnasio.pk), "nombre": "Remera", "descripcion": "",
            "precio": "15000", "publicado": "on", "orden": "0", "categoria": "",
            "variantes-TOTAL_FORMS": "1", "variantes-INITIAL_FORMS": "1",
            "variantes-MIN_NUM_FORMS": "0", "variantes-MAX_NUM_FORMS": "1000",
            "variantes-0-id": str(v.pk), "variantes-0-producto": str(producto.pk),
            "variantes-0-nombre": "M", "variantes-0-codigo": "abc-99", "variantes-0-activa": "on",
        }
        r = self.client.post(url, datos)
        self.assertEqual(r.status_code, 302, r.content.decode()[:3000])
        v.refresh_from_db()
        self.assertEqual(v.codigo, "ABC-99")


class MigracionDeCodigoTests(TransactionTestCase):
    """`tienda/0007` agrega `codigo` y llena el interno de las que ya existían."""

    migrate_from = [("tienda", "0006_pedido_cancelado_por_staff")]
    migrate_to = [("tienda", "0007_variante_codigo")]

    def _migrar(self, destino):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(destino)
        return ejecutor.loader.project_state(destino).apps

    def tearDown(self):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_las_existentes_reciben_su_codigo_interno(self):
        apps_viejas = self._migrar(self.migrate_from)
        gimnasio = apps_viejas.get_model("tenants", "Gimnasio").objects.create(nombre="M", slug="m")
        Producto_ = apps_viejas.get_model("tienda", "Producto")
        Variante_ = apps_viejas.get_model("tienda", "Variante")
        producto = Producto_.objects.create(gimnasio=gimnasio, nombre="P", precio=1)
        pks = [Variante_.objects.create(producto=producto, nombre=n).pk for n in ("S", "M", "L")]

        apps_nuevas = self._migrar(self.migrate_to)

        codigos = dict(apps_nuevas.get_model("tienda", "Variante").objects.values_list("pk", "codigo"))
        self.assertEqual(codigos, {pk: codigo_interno(pk) for pk in pks})
