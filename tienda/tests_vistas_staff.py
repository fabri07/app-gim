import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image
from PIL.JpegImagePlugin import JpegImageFile

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda import services
from tienda.models import CategoriaProducto, FotoProducto, Pedido, Producto, Variante


def jpeg(ancho=1600, alto=1200):
    salida = io.BytesIO()
    Image.new("RGB", (ancho, alto), "red").save(salida, "JPEG")
    return SimpleUploadedFile("foto.jpg", salida.getvalue(), content_type="image/jpeg")


class _BaseStaff(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(self.staff)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)

    def crear_producto(self, gimnasio=None, stock=3):
        producto = Producto.objects.create(
            gimnasio=gimnasio or self.gimnasio, nombre="Remera", precio=Decimal("15000"),
            caracteristica_1="Talle",
        )
        variante = Variante.objects.create(producto=producto, valor_1="M", stock=stock)
        return producto, variante


def datos_producto(**extra):
    datos = {
        "nombre": "Proteína", "descripcion": "", "precio": "25000", "publicado": "on",
        "orden": "0", "categoria": "", "caracteristica_1": "Sabor", "caracteristica_2": "",
        "variantes-TOTAL_FORMS": "2", "variantes-INITIAL_FORMS": "0",
        "variantes-MIN_NUM_FORMS": "1", "variantes-MAX_NUM_FORMS": "50",
        "variantes-0-valor_1": "Chocolate", "variantes-0-stock_inicial": "4", "variantes-0-activa": "on",
        "variantes-1-valor_1": "Vainilla", "variantes-1-stock_inicial": "2", "variantes-1-activa": "on",
    }
    datos.update(extra)
    return datos


class ProductoFormTests(_BaseStaff):
    def test_crear_con_variantes_y_foto_reescalada(self):
        datos = datos_producto()
        datos["fotos_nuevas"] = jpeg()
        r = self.client.post(reverse("tienda:producto_crear"), datos)
        self.assertRedirects(r, reverse("tienda:productos"))
        producto = Producto.objects.get(gimnasio=self.gimnasio, nombre="Proteína")
        self.assertEqual(
            sorted(producto.variantes.values_list("nombre", "stock")),
            [("Chocolate", 4), ("Vainilla", 2)],
        )
        foto = producto.foto_principal
        with foto.imagen.open("rb") as f:
            self.assertLessEqual(max(Image.open(f).size), 1200)
        # La columna vieja queda muerta: el form nuevo no la escribe.
        self.assertFalse(producto.foto)

    def test_sin_caracteristicas_y_todo_vacio_es_una_variante_desactivada(self):
        """Sin características la primera fila es LA variante: se guarda
        aunque venga sin tocar (nunca un producto sin variantes)."""
        datos = datos_producto(**{"caracteristica_1": "",
                                   "variantes-0-valor_1": "", "variantes-0-stock_inicial": "",
                                   "variantes-1-valor_1": "", "variantes-1-stock_inicial": ""})
        datos.pop("variantes-0-activa")
        datos.pop("variantes-1-activa")
        r = self.client.post(reverse("tienda:producto_crear"), datos)
        self.assertRedirects(r, reverse("tienda:productos"))
        producto = Producto.objects.get(nombre="Proteína")
        self.assertEqual(list(producto.variantes.values_list("nombre", "stock", "activa")), [("", 0, False)])

    def test_con_caracteristica_y_sin_filas_es_error_visible(self):
        datos = datos_producto(**{"variantes-0-valor_1": "", "variantes-0-stock_inicial": "",
                                   "variantes-1-valor_1": "", "variantes-1-stock_inicial": ""})
        datos.pop("variantes-0-activa")
        datos.pop("variantes-1-activa")
        r = self.client.post(reverse("tienda:producto_crear"), datos)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "al menos una variante")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_editar_el_nombre_no_pisa_el_stock_reservado(self):
        producto, variante = self.crear_producto(stock=3)
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                              carrito={variante.pk: 2}, suplantando=False)
        datos = {
            "nombre": "Remera Dry", "descripcion": "", "precio": "15000", "publicado": "on",
            "orden": "0", "categoria": "", "caracteristica_1": "Talle", "caracteristica_2": "",
            "variantes-TOTAL_FORMS": "1", "variantes-INITIAL_FORMS": "1",
            "variantes-MIN_NUM_FORMS": "1", "variantes-MAX_NUM_FORMS": "50",
            "variantes-0-id": str(variante.pk), "variantes-0-valor_1": "M",
            "variantes-0-activa": "on",
        }
        r = self.client.post(reverse("tienda:producto_editar", args=[producto.pk]), datos)
        self.assertRedirects(r, reverse("tienda:productos"))
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 1)

    def test_errores_del_formset_se_ven(self):
        """`core.tests.ErroresDeFormularioVisiblesTests` sólo mira `form.<campo>`:
        los campos del formset de variantes no los cubre, así que se prueban acá."""
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(**{"variantes-1-valor_1": "Chocolate"}),
        )
        self.assertContains(r, "Hay dos variantes «Chocolate»")
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(**{"variantes-0-stock_inicial": "-3"}),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "config-error")
        # Los campos nuevos: valor 1 y 2, código y las características.
        casos = [
            ({"variantes-1-valor_1": ""}, "Completá el Sabor o desactivá esta fila."),
            ({"caracteristica_2": "Peso"}, "Completá el Peso o desactivá esta fila."),
            ({"variantes-0-codigo": "AB"}, "letras, números y guiones"),
            ({"caracteristica_1": "", "caracteristica_2": "Peso"}, "Poné primero la característica 1."),
            ({"caracteristica_2": "sabor"}, "Las dos características son la misma"),
        ]
        for extra, mensaje in casos:
            with self.subTest(extra=extra):
                r = self.client.post(reverse("tienda:producto_crear"), datos_producto(**extra))
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, mensaje)
                self.assertRegex(r.content.decode(), r'<p class="config-error">[^<]*' + mensaje[:12])
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_categoria_de_otro_gimnasio_no_se_puede_elegir(self):
        ajena = CategoriaProducto.objects.create(gimnasio=self.otro, nombre="Ajena")
        r = self.client.post(reverse("tienda:producto_crear"), datos_producto(categoria=str(ajena.pk)))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_no_edita_un_producto_ajeno(self):
        producto, _ = self.crear_producto(gimnasio=self.otro)
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertEqual(r.status_code, 404)

    def test_editar_muestra_el_formulario_con_variantes(self):
        producto, variante = self.crear_producto()
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="M"')


class CategoriaTests(_BaseStaff):
    def test_nombre_repetido_es_error_visible_no_500(self):
        CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        r = self.client.post(reverse("tienda:categoria_crear"), {"nombre": "ropa", "orden": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Ya hay una categoría")

    def test_editar_no_crea_otra(self):
        cat = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        self.client.post(
            reverse("tienda:categoria_editar", args=[cat.pk]), {"nombre": "Indumentaria", "orden": "0"}
        )
        self.assertEqual(CategoriaProducto.objects.for_gimnasio(self.gimnasio).count(), 1)
        cat.refresh_from_db()
        self.assertEqual(cat.nombre, "Indumentaria")

    def test_lista_y_borrar(self):
        cat = CategoriaProducto.objects.create(gimnasio=self.gimnasio, nombre="Ropa")
        self.assertContains(self.client.get(reverse("tienda:categorias")), "Ropa")
        self.assertEqual(
            self.client.get(reverse("tienda:categoria_borrar", args=[cat.pk])).status_code, 200
        )
        self.client.post(reverse("tienda:categoria_borrar", args=[cat.pk]))
        self.assertFalse(CategoriaProducto.objects.filter(pk=cat.pk).exists())


class AjusteStockTests(_BaseStaff):
    def test_suma_y_resta(self):
        _, variante = self.crear_producto(stock=3)
        url = reverse("tienda:variante_ajustar", args=[variante.pk])
        self.client.post(url, {"delta": "5"})
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 8)
        r = self.client.post(url, {"delta": "-20"}, follow=True)
        self.assertContains(r, "No hay tantas unidades")
        variante.refresh_from_db()
        self.assertEqual(variante.stock, 8)

    def test_variante_ajena_404(self):
        _, ajena = self.crear_producto(gimnasio=self.otro)
        r = self.client.post(reverse("tienda:variante_ajustar", args=[ajena.pk]), {"delta": "5"})
        self.assertEqual(r.status_code, 404)


class BorrarProductoTests(_BaseStaff):
    def test_sin_ventas_se_borra(self):
        producto, _ = self.crear_producto()
        self.client.post(reverse("tienda:producto_borrar", args=[producto.pk]))
        self.assertFalse(Producto.objects.filter(pk=producto.pk).exists())

    def test_con_ventas_no_se_borra_y_lo_explica(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                              carrito={variante.pk: 1}, suplantando=False)
        r = self.client.get(reverse("tienda:producto_borrar", args=[producto.pk]))
        self.assertContains(r, "pedidos")
        r = self.client.post(reverse("tienda:producto_borrar", args=[producto.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Producto.objects.filter(pk=producto.pk).exists())
        self.assertContains(r, "Destildá «Publicado»")


class BorrarProductoConFotosTests(_BaseStaff):
    def test_la_confirmacion_avisa_que_se_borran_las_fotos(self):
        producto, _ = self.crear_producto()
        for i in range(2):
            FotoProducto.objects.create(producto=producto, imagen=f"tienda/productos/{i}.jpg", posicion=i)
        from core.borrado import arrastres_de_borrado

        self.assertIn(("fotos", 2), arrastres_de_borrado(producto))
        r = self.client.get(reverse("tienda:producto_borrar", args=[producto.pk]))
        self.assertContains(r, "2 fotos")


class ListadoDeProductosEscalaTests(_BaseStaff):
    def _cargar(self, cantidad):
        for _ in range(cantidad):
            producto, _v = self.crear_producto()
            for i in range(2):
                FotoProducto.objects.create(
                    producto=producto, imagen=f"tienda/productos/{producto.pk}-{i}.jpg", posicion=i,
                )

    def _queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(reverse("tienda:productos")).status_code, 200)
        return len(ctx)

    def test_las_queries_no_dependen_de_la_cantidad_de_productos(self):
        self._cargar(2)
        self._queries()  # calienta lo que se cachea en la primera request
        pocos = self._queries()
        self._cargar(2)
        muchos = self._queries()
        self.assertEqual(pocos, muchos)


class PrepararFotoDraftTests(TestCase):
    def test_el_draft_pide_el_lado_maximo_y_no_mas(self):
        from tienda import fotos

        original = JpegImageFile.draft
        pedidos = []

        def espia(self, modo, tamanio):
            pedidos.append(tamanio)
            return original(self, modo, tamanio)

        with patch.object(JpegImageFile, "draft", espia):
            fotos.preparar_foto(jpeg(3000, 2000))
        self.assertEqual(pedidos, [(fotos.LADO_MAXIMO, fotos.LADO_MAXIMO)])


class FotoTests(_BaseStaff):
    def crear_foto(self, producto, posicion=0):
        foto = FotoProducto(producto=producto, posicion=posicion)
        foto.imagen.save("f.jpg", jpeg(400, 400))
        return foto

    def test_sirve_la_foto_cacheable_sin_cookie(self):
        producto, _ = self.crear_producto()
        foto = self.crear_foto(producto)
        self.client.logout()
        r = self.client.get(foto.url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r["Cache-Control"])
        self.assertNotIn("sessionid", r.cookies)

    def test_logueado_tampoco_lleva_cookie_ni_registra_actividad(self):
        """El anónimo corta antes en el middleware y no prueba nada: con sesión
        es donde `URLS_SIN_BLOQUEO` evita la cookie en una respuesta que se
        cachea un año (molde: `plataforma/tests_actividad.py::ArchivosCacheablesTests`)."""
        from plataforma.models import ActividadDiaria

        producto, _ = self.crear_producto()
        foto = self.crear_foto(producto)
        r = self.client.get(foto.url_cacheable)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.cookies.keys()), [])
        self.assertFalse(ActividadDiaria.objects.exists())

    def test_gimnasio_sin_tienda_404(self):
        producto, _ = self.crear_producto(gimnasio=Gimnasio.objects.create(nombre="C", slug="c"))
        foto = self.crear_foto(producto)
        self.assertEqual(self.client.get(foto.url_cacheable).status_code, 404)

    def test_url_versionada_con_el_nombre_del_archivo(self):
        producto, _ = self.crear_producto()
        foto = self.crear_foto(producto)
        nombre = foto.imagen.name.rsplit("/", 1)[-1]
        self.assertEqual(
            foto.url_cacheable,
            reverse("tienda:tienda_foto_producto", args=[producto.pk, foto.pk]) + f"?v={nombre}",
        )

    def test_foto_de_otro_producto_404(self):
        producto, _ = self.crear_producto()
        otro_producto, _ = self.crear_producto()
        foto = self.crear_foto(otro_producto)
        url = reverse("tienda:tienda_foto_producto", args=[producto.pk, foto.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_foto_de_otro_gimnasio_404(self):
        # Con su propio `producto_id` correcto: lo único que la frena es la
        # puerta del gimnasio (tienda no habilitada / inactivo).
        ajeno, _ = self.crear_producto(gimnasio=self.otro)
        foto = self.crear_foto(ajeno)
        url = reverse("tienda:tienda_foto_producto", args=[ajeno.pk, foto.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        Gimnasio.objects.filter(pk=self.otro.pk).update(tienda_habilitada=False)
        self.assertEqual(self.client.get(url).status_code, 404)
        Gimnasio.objects.filter(pk=self.otro.pk).update(tienda_habilitada=True, activo=False)
        self.assertEqual(self.client.get(url).status_code, 404)


class GaleriaDeFotosTests(_BaseStaff):
    """Hasta `MAX_FOTOS_POR_PRODUCTO` fotos por producto, cargadas desde el
    mismo `ProductoForm` (sin un segundo formset)."""

    def crear_con_fotos(self, cantidad):
        producto, variante = self.crear_producto()
        fotos = []
        for i in range(cantidad):
            foto = FotoProducto(producto=producto, posicion=i)
            foto.imagen.save(f"f{i}.jpg", jpeg(400, 400))
            fotos.append(foto)
        return producto, variante, fotos

    def datos_edicion(self, variante, **extra):
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

    def editar(self, producto, datos):
        return self.client.post(reverse("tienda:producto_editar", args=[producto.pk]), datos)

    def test_subir_tres_de_una_vez(self):
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(fotos_nuevas=[jpeg(), jpeg(), jpeg()]),
        )
        self.assertRedirects(r, reverse("tienda:productos"))
        producto = Producto.objects.get(gimnasio=self.gimnasio, nombre="Proteína")
        self.assertEqual(list(producto.fotos.values_list("posicion", flat=True)), [0, 1, 2])
        self.assertEqual(producto.foto_principal, producto.fotos.first())

    def test_seis_fotos_es_error_visible_y_no_guarda(self):
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(fotos_nuevas=[jpeg(400, 400) for _ in range(6)]),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Máximo 5 fotos por producto")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())
        self.assertFalse(FotoProducto.objects.exists())

    def test_cada_foto_guardada_es_una_fila_con_radio_y_borrar(self):
        producto, variante, fotos = self.crear_con_fotos(2)
        html = self.client.get(reverse("tienda:producto_editar", args=[producto.pk])).content.decode()
        self.assertEqual(html.count('class="foto-fila"'), 2)
        for f in fotos:
            self.assertIn(f'type="radio" name="principal" value="{f.pk}"', html)
        self.assertEqual(html.count('type="checkbox" name="borrar_fotos"'), 2)
        self.assertEqual(html.count("Principal actual"), 1)

    def test_las_que_ya_tiene_cuentan_para_el_maximo(self):
        producto, variante, fotos = self.crear_con_fotos(4)
        r = self.editar(producto, self.datos_edicion(
            variante, principal=str(fotos[0].pk), fotos_nuevas=[jpeg(400, 400), jpeg(400, 400)],
        ))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Máximo 5 fotos por producto")
        self.assertEqual(producto.fotos.count(), 4)
        # Borrando una, entran las dos.
        r = self.editar(producto, self.datos_edicion(
            variante, principal=str(fotos[0].pk), borrar_fotos=[str(fotos[3].pk)],
            fotos_nuevas=[jpeg(400, 400), jpeg(400, 400)],
        ))
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertEqual(producto.fotos.count(), 5)

    def test_nuevas_van_al_final(self):
        producto, variante, fotos = self.crear_con_fotos(2)
        self.editar(producto, self.datos_edicion(
            variante, principal=str(fotos[0].pk), fotos_nuevas=[jpeg(400, 400)],
        ))
        nueva = producto.fotos.exclude(pk__in=[f.pk for f in fotos]).get()
        self.assertEqual(list(producto.fotos.all()), [fotos[0], fotos[1], nueva])

    def test_borrar_quita_la_fila_y_deja_el_archivo(self):
        from django.core.files.storage import default_storage

        producto, variante, fotos = self.crear_con_fotos(2)
        nombre = fotos[1].imagen.name
        r = self.editar(producto, self.datos_edicion(
            variante, principal=str(fotos[0].pk), borrar_fotos=[str(fotos[1].pk)],
        ))
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertEqual(list(producto.fotos.all()), [fotos[0]])
        self.assertTrue(default_storage.exists(nombre))

    def test_cambiar_la_principal_la_pone_primera_aunque_otra_este_en_cero(self):
        producto, variante, fotos = self.crear_con_fotos(3)
        r = self.editar(producto, self.datos_edicion(variante, principal=str(fotos[2].pk)))
        self.assertRedirects(r, reverse("tienda:productos"))
        self.assertEqual(producto.foto_principal, fotos[2])
        self.assertEqual(
            list(producto.fotos.values_list("pk", "posicion")),
            [(fotos[2].pk, 0), (fotos[0].pk, 1), (fotos[1].pk, 2)],
        )

    def test_borrar_la_unica_foto_deja_cero_fotos(self):
        producto, variante, fotos = self.crear_con_fotos(1)
        r = self.editar(producto, self.datos_edicion(
            variante, principal=str(fotos[0].pk), borrar_fotos=[str(fotos[0].pk)],
        ))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(producto.fotos.count(), 0)
        self.assertIsNone(producto.foto_principal)
        html = self.client.get(reverse("tienda:productos")).content.decode()
        self.assertIn("Remera", html)
        self.assertNotIn("<img", html.split("Remera", 1)[1])

    def test_borrar_la_principal_con_otra_foto_promueve_la_que_queda(self):
        producto, variante, fotos = self.crear_con_fotos(3)
        r = self.editar(producto, self.datos_edicion(
            variante, principal=str(fotos[0].pk), borrar_fotos=[str(fotos[0].pk)],
        ))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            list(producto.fotos.order_by("posicion").values_list("pk", "posicion")),
            [(fotos[1].pk, 0), (fotos[2].pk, 1)],
        )

    def test_borrar_la_principal_y_subir_una_nueva_en_un_guardado(self):
        producto, variante, fotos = self.crear_con_fotos(1)
        r = self.editar(producto, self.datos_edicion(
            variante, principal=str(fotos[0].pk), borrar_fotos=[str(fotos[0].pk)],
            fotos_nuevas=[jpeg()],
        ))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            list(producto.fotos.values_list("posicion", flat=True)), [0],
        )
        self.assertFalse(FotoProducto.objects.filter(pk=fotos[0].pk).exists())

    def test_foto_de_otro_producto_no_es_opcion_valida(self):
        producto, variante, fotos = self.crear_con_fotos(1)
        otro, _, ajenas = self.crear_con_fotos(1)
        r = self.editar(producto, self.datos_edicion(variante, borrar_fotos=[str(ajenas[0].pk)]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("borrar_fotos", r.context["form"].errors)
        self.assertTrue(FotoProducto.objects.filter(pk=ajenas[0].pk).exists())
        r = self.editar(producto, self.datos_edicion(variante, principal=str(ajenas[0].pk)))
        self.assertEqual(r.status_code, 200)
        self.assertIn("principal", r.context["form"].errors)
        self.assertEqual(otro.foto_principal, ajenas[0])
        self.assertEqual(producto.foto_principal, fotos[0])

    def test_archivo_que_no_es_imagen_dice_cual_es(self):
        roto = SimpleUploadedFile("roto.jpg", b"no soy una imagen", content_type="image/jpeg")
        r = self.client.post(
            reverse("tienda:producto_crear"), datos_producto(fotos_nuevas=[jpeg(), roto]),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "roto.jpg")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_cartel_si_otro_campo_rechaza_con_fotos_elegidas(self):
        r = self.client.post(
            reverse("tienda:producto_crear"), datos_producto(nombre="", fotos_nuevas=[jpeg()]),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Las fotos no se guardaron: volvé a elegirlas")

    def test_cartel_si_fallan_las_variantes_con_el_form_valido(self):
        r = self.client.post(
            reverse("tienda:producto_crear"),
            datos_producto(fotos_nuevas=[jpeg()], **{"variantes-1-valor_1": "Chocolate"}),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Hay dos variantes «Chocolate»")
        self.assertContains(r, "Las fotos no se guardaron: volvé a elegirlas")
        self.assertFalse(FotoProducto.objects.exists())

    def test_sin_fotos_elegidas_no_hay_cartel(self):
        r = self.client.post(reverse("tienda:producto_crear"), datos_producto(nombre=""))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Las fotos no se guardaron")

    def test_el_form_no_va_por_boost_y_sube_varias(self):
        r = self.client.get(reverse("tienda:producto_crear"))
        self.assertContains(r, 'enctype="multipart/form-data"')
        self.assertContains(r, 'hx-boost="false"')
        self.assertRegex(r.content.decode(), r'<input[^>]*name="fotos_nuevas"[^>]*multiple')

    def test_editar_muestra_las_fotos_con_la_principal_marcada(self):
        producto, _, fotos = self.crear_con_fotos(2)
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertContains(r, fotos[0].url_cacheable)
        self.assertContains(r, fotos[1].url_cacheable)
        html = r.content.decode()
        self.assertRegex(html, rf'<input[^>]*name="principal"[^>]*value="{fotos[0].pk}"[^>]*checked')
        self.assertNotRegex(html, rf'<input[^>]*name="principal"[^>]*value="{fotos[1].pk}"[^>]*checked')

    def test_producto_creado_por_el_form_muestra_su_foto_en_la_lista_y_el_catalogo(self):
        from tienda.models import obtener_configuracion

        self.client.post(reverse("tienda:producto_crear"), datos_producto(fotos_nuevas=[jpeg()]))
        producto = Producto.objects.get(gimnasio=self.gimnasio, nombre="Proteína")
        url = producto.foto_principal.url_cacheable
        self.assertContains(self.client.get(reverse("tienda:productos")), url)

        config = obtener_configuracion(self.gimnasio)
        config.publicada = True
        config.save()
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        self.client.force_login(usuario)
        self.assertContains(self.client.get(reverse("tienda:catalogo")), url)


class AccesoStaffTests(_BaseStaff):
    def test_sin_add_on_muestra_cartel_200(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("tienda:productos"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no está habilitada")

    def test_alumno_no_entra(self):
        u = User.objects.create_user("alu", password="c-123456")
        Perfil.objects.create(usuario=u, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("tienda:productos")).status_code, 403)


class StockBajoTests(_BaseStaff):
    def test_badge_de_stock_bajo_hasta_el_umbral(self):
        _, variante = self.crear_producto(stock=services.UMBRAL_POCAS_UNIDADES)
        self.assertContains(self.client.get(reverse("tienda:productos")), "data-stock-bajo")
        Variante.objects.filter(pk=variante.pk).update(stock=services.UMBRAL_POCAS_UNIDADES + 1)
        self.assertNotContains(self.client.get(reverse("tienda:productos")), "data-stock-bajo")

    def test_sin_stock_no_es_stock_bajo(self):
        self.crear_producto(stock=0)
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, "Sin stock")
        self.assertNotContains(r, "data-stock-bajo")


class PedidosStaffTests(_BaseStaff):
    def setUp(self):
        super().setUp()
        self.producto, self.variante = self.crear_producto(stock=5)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        self.pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno,
            carrito={self.variante.pk: 2}, suplantando=False,
        )

    def accion(self, accion, **extra):
        return self.client.post(
            reverse("tienda:pedido_accion", args=[self.pedido.pk]),
            {"accion": accion, **extra}, follow=True,
        )

    def test_listado_filtra_a_revisar(self):
        # Se busca el link al detalle y no «#<pk>»: `base.html` inyecta el
        # color del gimnasio (`#1d6f56`) y «#1» aparece en cualquier página.
        detalle = reverse("tienda:pedido_detalle", args=[self.pedido.pk])
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=revisar")
        self.assertNotContains(r, detalle)
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=revisar")
        self.assertContains(r, detalle)

    @patch("notificaciones.services.notificar_pedido_al_alumno")
    def test_marcar_pagado_avisa_al_alumno(self, aviso):
        with self.captureOnCommitCallbacks(execute=True):
            self.accion("pagado")
        self.assertEqual(aviso.call_args.args[1], "confirmado")

    def test_marcar_pagado_y_entregar(self):
        self.accion("pagado")
        self.accion("entregar")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.ENTREGADO)

    def test_rechazar_pide_otro_comprobante_y_sigue_reservado(self):
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.accion("rechazar", motivo="No se lee")
        self.assertContains(r, "puede subir otro")
        self.pedido.refresh_from_db()
        self.assertEqual(
            (self.pedido.estado, self.pedido.motivo), (Pedido.Estado.PENDIENTE_PAGO, "No se lee")
        )
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 3)  # 5 - 2: sigue reservado

    def test_cancelar_desde_revision_si_devuelve_el_stock(self):
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        self.accion("cancelar", motivo="No llegó la plata")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.CANCELADO)
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 5)

    def test_accion_invalida_es_cartel_no_500(self):
        r = self.accion("entregar")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ya cambió de estado")

    def test_pedido_ajeno_404(self):
        otro_alumno = Alumno.objects.create(gimnasio=self.otro, nombre="B", apellido="P")
        _, var = self.crear_producto(gimnasio=self.otro)
        ajeno = services.crear_pedido(gimnasio=self.otro, alumno=otro_alumno,
                                      carrito={var.pk: 1}, suplantando=False)
        self.assertEqual(self.client.get(reverse("tienda:pedido_detalle", args=[ajeno.pk])).status_code, 404)
        r = self.client.post(reverse("tienda:pedido_accion", args=[ajeno.pk]), {"accion": "pagado"})
        self.assertEqual(r.status_code, 404)

    def test_detalle_vence_perezosamente_sin_push(self):
        from django.utils import timezone
        from datetime import timedelta

        Pedido.objects.filter(pk=self.pedido.pk).update(
            reservado_hasta=timezone.now() - timedelta(hours=1)
        )
        with patch("notificaciones.services.notificar_pedido_al_alumno") as aviso:
            r = self.client.get(reverse("tienda:pedido_detalle", args=[self.pedido.pk]))
        self.assertContains(r, "Vencido")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.VENCIDO)
        aviso.assert_not_called()

    def _pagado_con_cancelacion_pedida(self):
        services.marcar_pagado(self.pedido)
        services.pedir_cancelacion(self.pedido, alumno=self.alumno)

    def test_aceptar_cancelacion_devuelve_y_libera_el_stock(self):
        self._pagado_con_cancelacion_pedida()
        r = self.accion("aceptar_cancelacion", motivo="Devolvimos por transferencia")
        self.assertContains(r, "Cancelación aceptada")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.DEVUELTO)
        self.assertIsNotNone(self.pedido.cancelacion_resuelta_en)
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 5)

    def test_rechazar_cancelacion_deja_el_pedido_y_el_stock(self):
        self._pagado_con_cancelacion_pedida()
        r = self.accion("rechazar_cancelacion", motivo="Ya lo usó")
        self.assertContains(r, "Cancelación rechazada")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.PAGADO)
        self.assertIsNotNone(self.pedido.cancelacion_resuelta_en)
        self.assertEqual(self.pedido.motivo, "Ya lo usó")
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 3)

    def test_resolver_sin_cancelacion_pedida_es_cartel(self):
        services.marcar_pagado(self.pedido)
        r = self.accion("aceptar_cancelacion")
        self.assertContains(r, "ya cambió de estado")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.PAGADO)

    def test_listado_vence_perezosamente(self):
        from django.utils import timezone
        from datetime import timedelta

        Pedido.objects.filter(pk=self.pedido.pk).update(
            reservado_hasta=timezone.now() - timedelta(hours=1)
        )
        self.client.get(reverse("tienda:pedidos"))
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.Estado.VENCIDO)


class TiendaApagadaTests(_BaseStaff):
    def test_el_staff_sigue_gestionando_los_pedidos_vivos(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                                       carrito={variante.pk: 1}, suplantando=False)
        services.marcar_pagado(pedido)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("tienda:pedidos") + "?filtro=entregar")
        self.assertContains(r, reverse("tienda:pedido_detalle", args=[pedido.pk]))
        self.client.post(reverse("tienda:pedido_accion", args=[pedido.pk]), {"accion": "entregar"})
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ENTREGADO)
        # ...pero productos y configuración no.
        self.assertContains(self.client.get(reverse("tienda:productos")), "no está habilitada")


class ConfiguracionTests(_BaseStaff):
    def test_guardar(self):
        r = self.client.post(reverse("tienda:configuracion"), {
            "publicada": "on", "horas_reserva_transferencia": "48",
            "max_pedidos_activos": "3", "instrucciones_retiro": "Recepción",
        })
        self.assertRedirects(r, reverse("tienda:configuracion"))
        from tienda.models import obtener_configuracion
        c = obtener_configuracion(self.gimnasio)
        self.assertEqual((c.publicada, c.horas_reserva_transferencia), (True, 48))

    def test_plazo_fuera_de_rango_es_error_visible(self):
        r = self.client.post(reverse("tienda:configuracion"), {
            "horas_reserva_transferencia": "0", "max_pedidos_activos": "2",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "config-error")  # o la clase del partial


class PanelYBarraTests(_BaseStaff):
    def test_tarjeta_del_panel_con_pendientes(self):
        producto, variante = self.crear_producto()
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                                       carrito={variante.pk: 1}, suplantando=False)
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.Estado.EN_REVISION)
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Pedidos de la tienda")
        self.assertContains(r, reverse("tienda:pedidos") + "?filtro=revisar")

    def test_sin_add_on_ni_link_ni_tarjeta(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, reverse("tienda:productos"))
        self.assertNotContains(r, "Pedidos de la tienda")

    def test_con_add_on_link_en_la_barra(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, f'href="{reverse("tienda:productos")}"')

    def test_chip_adicional_solo_en_la_demo(self):
        """La tienda es un adicional que se cobra aparte (Hito 3): en la demo,
        que no paga, se marca como adicional para que el prospecto lo sepa."""
        self.assertNotContains(self.client.get(reverse("home")), "data-adicional")
        self.assertNotContains(self.client.get(reverse("tienda:productos")), "data-adicional")
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.assertContains(self.client.get(reverse("home")), "data-adicional")
        self.assertContains(self.client.get(reverse("tienda:productos")), "data-adicional")
class VentasEnPantallasTests(_BaseStaff):
    def setUp(self):
        super().setUp()
        _, variante = self.crear_producto(stock=5)
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        pedido = services.crear_pedido(gimnasio=self.gimnasio, alumno=alumno,
                                       carrito={variante.pk: 2}, suplantando=False)
        services.marcar_pagado(pedido)

    def test_vendido_este_mes_en_el_listado_de_pedidos(self):
        r = self.client.get(reverse("tienda:pedidos"))
        self.assertContains(r, "Vendido este mes")
        self.assertContains(r, "30.000")

    def test_tarjeta_de_ventas_en_el_panel(self):
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Ventas de la tienda")
        self.assertContains(r, 'id="ventas-tienda-data"')

    def test_sin_tienda_no_calcula_la_tarjeta_de_ventas(self):
        """Prueba sólo que `panel_de_ventas` no se llama (el contador de alertas sí corre)."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        with patch("tienda.analitica.panel_de_ventas", side_effect=AssertionError("no se llama sin tienda")):
            r = self.client.get(reverse("home"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Ventas de la tienda")
        self.assertNotContains(r, "ventas-tienda-data")


class BadgesYPantallaTests(_BaseStaff):
    def test_ningun_template_usa_badge_a_secas(self):
        """`.badge` sin variante no tiene fondo ni color (ver `.badge--neutro`
        en styles/input.css): se ve como texto suelto."""
        import re
        from pathlib import Path

        from django.conf import settings

        archivos = list((Path(settings.BASE_DIR) / "templates" / "tienda").glob("*.html"))
        archivos.append(Path(settings.BASE_DIR) / "templates" / "base.html")
        a_secas = re.compile(r'class="badge(?:\s*"|\s+(?!badge--|\{\{))')
        malos = [
            f"{a.name}: {m.group(0)}"
            for a in archivos
            for m in a_secas.finditer(a.read_text())
        ]
        self.assertEqual(malos, [])

    def test_cada_estado_de_pedido_tiene_su_variante(self):
        for estado in Pedido.Estado.values:
            self.assertTrue(Pedido(estado=estado).badge_clase.startswith("badge--"), estado)

    def test_estado_y_stock_con_su_color(self):
        producto, variante = self.crear_producto(stock=0)
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, '<span class="badge badge--riesgo">Sin stock</span>')
        Variante.objects.filter(pk=variante.pk).update(stock=1)
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, 'class="badge badge--alerta" data-stock-bajo')

    def test_variante_sin_nombre_no_muestra_dos_puntos_sueltos(self):
        producto = Producto.objects.create(gimnasio=self.gimnasio, nombre="Creatina", precio=Decimal("21000"))
        Variante.objects.create(producto=producto, nombre="", stock=12)
        r = self.client.get(reverse("tienda:productos"))
        self.assertNotContains(r, ": \n")
        html = r.content.decode()
        fila = html[html.index("Creatina"):]
        self.assertNotRegex(fila[:900], r"\s:\s*\n\s*<span class=\"tabular-nums\">12")

    def test_el_ajuste_va_en_linea_y_los_montos_no_se_parten(self):
        self.crear_producto()
        r = self.client.get(reverse("tienda:productos"))
        self.assertContains(r, 'class="flex items-center gap-2 mt-1"')
        self.assertContains(r, "whitespace-nowrap")
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        _, variante = self.crear_producto()
        pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=alumno, carrito={variante.pk: 1}, suplantando=False
        )
        r = self.client.get(reverse("tienda:pedido_detalle", args=[pedido.pk]))
        self.assertContains(r, 'class="tabular-nums whitespace-nowrap">$ 15.000,00')
