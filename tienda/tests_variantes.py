"""Variantes por características (plan «tienda: variantes y códigos», T2).

Un producto tiene hasta dos características («Talle», «Color») y cada
variante un valor por característica; `Variante.nombre` se calcula («M ·
Negro»). La unicidad del nombre la valida el formset (la base ya no tiene la
constraint desde `tienda/0008`)."""

import re
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from tienda import services
from tienda.codigos import codigo_interno
from tienda.models import Producto, Variante, obtener_configuracion
from tienda.variantes import SUGERENCIAS, clave, componer_nombre

URL_ALTA = "tienda:producto_crear"
URL_LISTA = "tienda:productos"


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(self.staff)

    def producto(self, c1="Talle", c2="", filas=(("M", ""),), nombre="Remera"):
        producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre=nombre, precio=Decimal("15000"),
            caracteristica_1=c1, caracteristica_2=c2,
        )
        variantes = [
            Variante.objects.create(producto=producto, valor_1=v1, valor_2=v2, stock=3)
            for v1, v2 in filas
        ]
        return producto, variantes

    def vender(self, variante):
        alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        services.crear_pedido(
            gimnasio=self.gimnasio, alumno=alumno, carrito={variante.pk: 1}, suplantando=False
        )

    def alta(self, c1="", c2="", filas=(), extra=1, **mas):
        """POST de alta: `filas` = dicts con `valor_1`, `valor_2`, `stock`,
        `codigo`, `activa` (True por defecto), `borrar`; más `extra` filas
        vacías al final (como las dibuja el form)."""
        datos = {
            "nombre": "Proteína", "descripcion": "", "precio": "25000", "publicado": "on",
            "orden": "", "categoria": "", "caracteristica_1": c1, "caracteristica_2": c2,
            "variantes-TOTAL_FORMS": str(len(filas) + extra), "variantes-INITIAL_FORMS": "0",
            "variantes-MIN_NUM_FORMS": "0", "variantes-MAX_NUM_FORMS": "50",
        }
        for i, fila in enumerate(filas):
            self._fila(datos, i, fila)
        # Las extra vienen como las dibuja el form: «Activa» tildada.
        for i in range(len(filas), len(filas) + extra):
            datos[f"variantes-{i}-activa"] = "on"
        datos.update(mas)
        return self.client.post(reverse(URL_ALTA), datos)

    def edicion(self, producto, filas, c1=None, c2=None, extra=1, **mas):
        """POST de edición: `filas` = dicts con `variante` y lo que se pisa;
        `None` en c1/c2 = las del producto. Las filas nuevas van como dicts
        sin `variante`."""
        existentes = [f for f in filas if "variante" in f]
        nuevas = [f for f in filas if "variante" not in f]
        datos = {
            "nombre": producto.nombre, "descripcion": "", "precio": "15000", "publicado": "on",
            "orden": "0", "categoria": "",
            "caracteristica_1": producto.caracteristica_1 if c1 is None else c1,
            "caracteristica_2": producto.caracteristica_2 if c2 is None else c2,
            "variantes-TOTAL_FORMS": str(len(filas) + extra),
            "variantes-INITIAL_FORMS": str(len(existentes)),
            "variantes-MIN_NUM_FORMS": "0", "variantes-MAX_NUM_FORMS": "50",
        }
        for i, fila in enumerate(existentes + nuevas):
            v = fila.get("variante")
            if v is not None:
                datos[f"variantes-{i}-id"] = str(v.pk)
                fila = {"valor_1": v.valor_1, "valor_2": v.valor_2, "codigo": v.codigo,
                        "activa": v.activa, **fila}
            self._fila(datos, i, fila)
        for i in range(len(filas), len(filas) + extra):
            datos[f"variantes-{i}-activa"] = "on"
        datos.update(mas)
        return self.client.post(reverse("tienda:producto_editar", args=[producto.pk]), datos)

    @staticmethod
    def _fila(datos, i, fila):
        datos[f"variantes-{i}-valor_1"] = fila.get("valor_1", "")
        datos[f"variantes-{i}-valor_2"] = fila.get("valor_2", "")
        datos[f"variantes-{i}-codigo"] = fila.get("codigo", "")
        if "stock" in fila:
            datos[f"variantes-{i}-stock_inicial"] = str(fila["stock"])
        if fila.get("activa", True):
            datos[f"variantes-{i}-activa"] = "on"
        if fila.get("borrar"):
            datos[f"variantes-{i}-DELETE"] = "on"

    def nombres(self, producto=None):
        producto = producto or Producto.objects.get(gimnasio=self.gimnasio, nombre="Proteína")
        return sorted(producto.variantes.values_list("nombre", flat=True))

    def errores(self, r):
        """Los mensajes de error visibles (las líneas `config-error` de los
        campos y el bloque de errores generales), sin el aviso del generador."""
        html = r.content.decode()
        return re.findall(r'<p class="config-error">([^<]*)</p>', html) + re.findall(
            r'<ul class="errorlist nonform"[^>]*>(.*?)</ul>', html, re.S
        )

    def rechaza(self, r, mensaje):
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, mensaje)


class SugerenciasTests(TestCase):
    def test_nombre_calculado_y_clave(self):
        self.assertEqual(componer_nombre("M", "Negro"), "M · Negro")
        self.assertEqual(componer_nombre("M", ""), "M")
        self.assertEqual(componer_nombre("", ""), "")
        self.assertEqual(clave(" Sábor "), "sabor")

    def test_las_sugerencias_del_plan(self):
        self.assertEqual(SUGERENCIAS["Talle numérico"], ["34", "36", "38", "40", "42", "44", "46"])
        self.assertEqual(set(SUGERENCIAS), {"Talle", "Talle numérico", "Color", "Peso", "Sabor"})


class AltaTests(_Base):
    def test_sin_caracteristicas_es_una_variante_sin_nombre_y_el_catalogo_va_directo(self):
        r = self.alta(filas=[{"stock": 5}], extra=0)
        self.assertRedirects(r, reverse(URL_LISTA))
        self.assertEqual(self.nombres(), [""])
        # El catálogo del alumno: botón directo, sin select (como hoy).
        alumno = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        Alumno.objects.create(gimnasio=self.gimnasio, nombre="B", apellido="C", perfil=perfil)
        config = obtener_configuracion(self.gimnasio)
        config.publicada = True
        config.save()
        self.client.force_login(alumno)
        r = self.client.get(reverse("tienda:catalogo"))
        self.assertContains(r, "Proteína")
        self.assertNotContains(r, "<select")

    def test_sin_caracteristicas_con_stock_y_codigo_vacios_es_una_variante(self):
        """La fila única se guarda aunque nadie la toque: nunca un producto
        sin variantes."""
        r = self.alta(extra=1)
        self.assertRedirects(r, reverse(URL_LISTA))
        producto = Producto.objects.get(nombre="Proteína")
        self.assertEqual(list(producto.variantes.values_list("nombre", "stock")), [("", 0)])

    def test_talle(self):
        r = self.alta(c1="Talle", filas=[{"valor_1": "S", "stock": 1}, {"valor_1": "M", "stock": 2}])
        self.assertRedirects(r, reverse(URL_LISTA))
        self.assertEqual(self.nombres(), ["M", "S"])

    def test_talle_y_color_y_el_catalogo_muestra_la_combinacion(self):
        r = self.alta(c1="Talle", c2="Color", filas=[
            {"valor_1": "M", "valor_2": "Negro", "stock": 1},
            {"valor_1": "M", "valor_2": "Blanco", "stock": 1},
        ])
        self.assertRedirects(r, reverse(URL_LISTA))
        self.assertEqual(self.nombres(), ["M · Blanco", "M · Negro"])
        producto = Producto.objects.get(nombre="Proteína")
        self.assertEqual((producto.caracteristica_1, producto.caracteristica_2), ("Talle", "Color"))
        v = producto.variantes.get(nombre="M · Negro")
        self.assertEqual((v.valor_1, v.valor_2), ("M", "Negro"))
        alumno = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        Alumno.objects.create(gimnasio=self.gimnasio, nombre="B", apellido="C", perfil=perfil)
        config = obtener_configuracion(self.gimnasio)
        config.publicada = True
        config.save()
        self.client.force_login(alumno)
        r = self.client.get(reverse("tienda:catalogo"))
        self.assertContains(r, "<select")
        self.assertContains(r, "M · Negro")

    def test_falta_el_valor_es_error_en_la_fila(self):
        r = self.alta(c1="Talle", c2="Color", filas=[
            {"valor_1": "M", "valor_2": "", "stock": 1},
            {"valor_1": "", "valor_2": "Negro", "stock": 1},
        ])
        self.assertEqual(
            self.errores(r),
            ["Completá «Color» o desactivá esta fila.", "Completá «Talle» o desactivá esta fila."],
        )
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_la_2_sin_la_1_con_filas_que_traen_valor_2_es_un_unico_error(self):
        r = self.alta(c1="", c2="Color", filas=[
            {"valor_2": "Negro", "stock": 1}, {"valor_2": "Blanco", "stock": 1},
        ])
        self.assertEqual(self.errores(r), ["Poné primero la característica 1."])

    def test_1_igual_a_2_con_filas_combinadas_es_un_unico_error(self):
        """Sin duplicados falsos: con una característica inválida las filas no
        limpian valores ni corren las reglas de nombres."""
        r = self.alta(c1="Color", c2="color", filas=[
            {"valor_1": "M", "valor_2": "Negro", "stock": 1},
            {"valor_1": "M", "valor_2": "Blanco", "stock": 1},
        ])
        self.assertEqual(len(self.errores(r)), 1)
        self.assertContains(r, "Las dos características son la misma")

    def test_errores_del_formset_y_del_form_en_el_primer_intento(self):
        r = self.alta(
            c1="Talle", filas=[{"valor_1": "M", "stock": 1}, {"valor_1": "M", "stock": 1}], nombre="",
        )
        self.assertContains(r, "Hay dos variantes «M»")
        self.assertContains(r, "Este campo es obligatorio")

    def test_mas_de_60_es_error_en_valor_1(self):
        r = self.alta(c1="Talle", c2="Color", filas=[
            {"valor_1": "A" * 40, "valor_2": "B" * 25, "stock": 1},
        ])
        self.rechaza(r, "pasa de 60 caracteres")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_cincuenta_filas_guardan_y_cincuenta_y_una_no(self):
        filas = [{"valor_1": f"T{i}", "stock": 1} for i in range(50)]
        # El generador reusó la fila extra: TOTAL_FORMS = 50 justo.
        r = self.alta(c1="Talle", filas=filas, extra=0)
        self.assertRedirects(r, reverse(URL_LISTA))
        self.assertEqual(len(self.nombres()), 50)
        Producto.objects.filter(nombre="Proteína").delete()
        filas.append({"valor_1": "T50", "stock": 1})
        r = self.alta(c1="Talle", filas=filas, extra=0)
        self.rechaza(r, "Son más de 50 variantes.")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_fila_extra_con_solo_activa_destildada_no_crea_variante(self):
        r = self.alta(c1="Talle", filas=[{"valor_1": "M", "stock": 1}], extra=0, **{
            "variantes-TOTAL_FORMS": "2",
        })
        self.assertRedirects(r, reverse(URL_LISTA))
        self.assertEqual(self.nombres(), ["M"])

    def test_fila_nueva_con_errores_nunca_se_descarta(self):
        for fila, mensaje in (
            ({"codigo": "AB", "activa": False}, "letras, números y guiones"),
            ({"stock": -1, "activa": False}, "mayor o igual a 0"),
            ({"valor_1": "A" * 61, "activa": False}, "como máximo 60"),
        ):
            with self.subTest(fila=fila):
                r = self.alta(c1="Talle", filas=[{"valor_1": "M", "stock": 1}, fila], extra=0)
                self.rechaza(r, mensaje)
                self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_talle_generado_con_stock_y_despues_borrado_pide_una_sola_activa(self):
        r = self.alta(c1="", filas=[
            {"valor_1": "S", "stock": 1}, {"valor_1": "M", "stock": 2},
        ])
        self.rechaza(r, "Sin características el producto tiene una sola variante")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())

    def test_la_fila_unica_no_dibuja_quitar_y_con_delete_posteado_pide_una_variante(self):
        r = self.client.get(reverse(URL_ALTA))
        self.assertNotContains(r, 'name="variantes-0-DELETE"')
        r = self.alta(filas=[{"stock": 3, "borrar": True}], extra=0)
        self.rechaza(r, "al menos una variante")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())


class EdicionTests(_Base):
    def test_segunda_caracteristica_en_un_producto_con_variantes_guia_fila_por_fila(self):
        producto, (m, l) = self.producto(filas=(("M", ""), ("L", "")))
        r = self.edicion(producto, [{"variante": m}, {"variante": l}], c2="Color")
        self.assertEqual(self.errores(r), ["Completá «Color» o desactivá esta fila."] * 2)
        r = self.edicion(
            producto, [{"variante": m, "valor_2": "Negro"}, {"variante": l, "activa": False}], c2="Color",
        )
        self.assertRedirects(r, reverse(URL_LISTA))
        self.assertEqual(self.nombres(producto), ["L", "M · Negro"])

    def test_duplicado_contra_una_inactiva_pide_tildar_activa(self):
        producto, (m,) = self.producto()
        Variante.objects.filter(pk=m.pk).update(activa=False)
        m.refresh_from_db()
        r = self.edicion(producto, [{"variante": m}, {"valor_1": "M", "stock": 1}], extra=0)
        self.rechaza(r, "Ya existe «M» desactivada: tildá Activa")
        self.assertEqual(producto.variantes.count(), 1)

    def test_con_ventas_quitar_posteado_a_mano_la_desactiva(self):
        producto, (m, l) = self.producto(filas=(("M", ""), ("L", "")))
        self.vender(m)
        r = self.edicion(producto, [{"variante": m, "borrar": True}, {"variante": l}])
        self.assertRedirects(r, reverse(URL_LISTA))
        m.refresh_from_db()
        self.assertFalse(m.activa)

    def test_la_unica_con_ventas_posteada_quitar_cuenta_como_variante(self):
        """Con ventas, «Quitar» la conserva desactivada: sigue siendo la
        variante del producto (no «al menos una variante»)."""
        producto, (m,) = self.producto()
        self.vender(m)
        r = self.edicion(producto, [{"variante": m, "borrar": True}])
        self.assertRedirects(r, reverse(URL_LISTA))
        m.refresh_from_db()
        self.assertFalse(m.activa)

    def test_con_ventas_inactiva_no_exige_valores(self):
        producto, (m, l) = self.producto(filas=(("M", ""), ("L", "")))
        self.vender(m)
        Variante.objects.filter(pk=m.pk).update(activa=False)
        m.refresh_from_db()
        r = self.edicion(producto, [{"variante": m}, {"variante": l, "valor_2": "Negro"}], c2="Color")
        self.assertRedirects(r, reverse(URL_LISTA))

    def test_sacar_talle_con_una_vendida_inactiva_no_deja_callejon(self):
        producto, (s, m) = self.producto(filas=(("S", ""), ("M", "")))
        self.vender(s)
        Variante.objects.filter(pk=s.pk).update(activa=False)
        s.refresh_from_db()
        r = self.edicion(producto, [{"variante": s}, {"variante": m}], c1="")
        self.assertRedirects(r, reverse(URL_LISTA))
        self.assertEqual(self.nombres(producto), ["", ""])

    def test_rotacion_de_nombres_guarda_sin_500(self):
        producto, (a, b, c) = self.producto(filas=(("S", ""), ("M", ""), ("L", "")))
        r = self.edicion(producto, [
            {"variante": a, "valor_1": "M"}, {"variante": b, "valor_1": "L"},
            {"variante": c, "valor_1": "XL"},
        ])
        self.assertRedirects(r, reverse(URL_LISTA))
        a.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual((a.nombre, c.nombre), ("M", "XL"))

    def test_quitar_l_y_renombrar_m_a_l(self):
        producto, (m, l) = self.producto(filas=(("M", ""), ("L", "")))
        r = self.edicion(producto, [{"variante": m, "valor_1": "L"}, {"variante": l, "borrar": True}])
        self.assertRedirects(r, reverse(URL_LISTA))
        self.assertEqual(list(producto.variantes.values_list("pk", "nombre")), [(m.pk, "L")])

    def test_sin_caracteristicas_con_su_unica_variante_desactivada_se_guarda(self):
        producto, (v,) = self.producto(c1="", filas=(("", ""),))
        r = self.edicion(producto, [{"variante": v, "activa": False}])
        self.assertRedirects(r, reverse(URL_LISTA))
        v.refresh_from_db()
        self.assertFalse(v.activa)

    def test_borrar_la_caracteristica_2_limpia_los_valores_sin_errores_ocultos(self):
        producto, (mn, ln) = self.producto(c2="Color", filas=(("M", "Negro"), ("L", "Negro")))
        r = self.edicion(producto, [{"variante": mn}, {"variante": ln}], c2="")
        self.assertRedirects(r, reverse(URL_LISTA))
        mn.refresh_from_db()
        self.assertEqual((mn.nombre, mn.valor_2), ("M", ""))
        self.assertEqual(self.nombres(producto), ["L", "M"])

    def test_borrar_la_caracteristica_2_que_produce_duplicados_se_informa(self):
        producto, (mn, mb) = self.producto(c2="Color", filas=(("M", "Negro"), ("M", "Blanco")))
        r = self.edicion(producto, [{"variante": mn}, {"variante": mb}], c2="")
        self.rechaza(r, "Hay dos variantes «M»")

    def test_borrar_la_caracteristica_recalcula_aunque_lo_posteado_no_cambie(self):
        """`has_changed`: la fila vuelve con el mismo «M» posteado (el JS sólo
        lo esconde), pero su nombre calculado ahora es «»."""
        producto, (m,) = self.producto()
        r = self.edicion(producto, [{"variante": m}], c1="")
        self.assertRedirects(r, reverse(URL_LISTA))
        m.refresh_from_db()
        self.assertEqual((m.nombre, m.valor_1), ("", ""))

    def test_variante_vieja_precargada_guarda_su_valor_aunque_no_se_toque(self):
        """La precarga hace que lo posteado sea igual al `initial`: sin
        comparar contra la instancia, la fila no «cambiaba» y `valor_1`
        quedaba en «» para siempre."""
        producto, _ = self.producto(filas=())
        vieja = Variante.objects.create(producto=producto, nombre="S", stock=1)
        r = self.edicion(producto, [{"variante": vieja, "valor_1": "S"}])
        self.assertRedirects(r, reverse(URL_LISTA))
        vieja.refresh_from_db()
        self.assertEqual((vieja.valor_1, vieja.nombre), ("S", "S"))

    def test_variante_vieja_sin_valores_precarga_el_nombre(self):
        producto, _ = self.producto(filas=())
        Variante.objects.create(producto=producto, nombre="S", stock=1)
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertRegex(r.content.decode(), r'name="variantes-0-valor_1"[^>]*value="S"')


class HtmlDelFormTests(_Base):
    def test_las_caracteristicas_van_en_variantes_y_los_valores_siempre_se_dibujan(self):
        r = self.client.get(reverse(URL_ALTA))
        html = r.content.decode()
        variantes = html.index("<h2>Variantes</h2>")
        self.assertGreater(html.index('name="caracteristica_1"'), variantes)
        self.assertGreater(html.index('name="caracteristica_2"'), variantes)
        # Sin características igual se dibujan (sin JS, el error nunca queda
        # sobre un campo ausente).
        self.assertIn('name="variantes-0-valor_1"', html)
        self.assertIn('name="variantes-0-valor_2"', html)
        self.assertIn('id="sugerencias-variantes"', html)
        self.assertIn('<datalist id="sugerencias-caracteristicas">', html)
        plantilla = re.search(r"<template data-fila-vacia>(.*?)</template>", html, re.S).group(1)
        self.assertIn('name="variantes-__prefix__-valor_1"', plantilla)
        self.assertIn('name="variantes-__prefix__-stock_inicial"', plantilla)

    def test_etiquetas_desde_la_instancia_y_desde_el_post(self):
        producto, _ = self.producto(c2="Color", filas=(("M", "Negro"),))
        r = self.client.get(reverse("tienda:producto_editar", args=[producto.pk]))
        self.assertRegex(r.content.decode(), r'for="id_variantes-0-valor_2">\s*Color')
        r = self.alta(c1="Sabor", filas=[{"valor_1": "", "stock": 1}])
        self.assertRegex(r.content.decode(), r'for="id_variantes-0-valor_1">\s*Sabor')
        r = self.client.get(reverse(URL_ALTA))
        self.assertRegex(r.content.decode(), r'for="id_variantes-0-valor_1">\s*Valor 1')


class AdminTests(_Base):
    def setUp(self):
        super().setUp()
        root = User.objects.create_superuser("root", "r@x.com", "c-123456")
        self.client.force_login(root)

    def post_admin(self, producto, filas):
        datos = {
            "gimnasio": str(self.gimnasio.pk), "nombre": producto.nombre, "descripcion": "",
            "precio": "15000", "publicado": "on", "orden": "0", "categoria": "",
            "caracteristica_1": producto.caracteristica_1, "caracteristica_2": producto.caracteristica_2,
            "variantes-TOTAL_FORMS": str(len(filas)),
            "variantes-INITIAL_FORMS": str(sum(1 for f in filas if "variante" in f)),
            "variantes-MIN_NUM_FORMS": "0", "variantes-MAX_NUM_FORMS": "1000",
        }
        for i, fila in enumerate(filas):
            v = fila.get("variante")
            if v is not None:
                datos[f"variantes-{i}-id"] = str(v.pk)
                fila = {"valor_1": v.valor_1, "valor_2": v.valor_2, "codigo": v.codigo, **fila}
            datos[f"variantes-{i}-producto"] = str(producto.pk)
            datos[f"variantes-{i}-valor_1"] = fila.get("valor_1", "")
            datos[f"variantes-{i}-valor_2"] = fila.get("valor_2", "")
            datos[f"variantes-{i}-codigo"] = fila.get("codigo", "")
            if fila.get("activa", True):
                datos[f"variantes-{i}-activa"] = "on"
        return self.client.post(reverse("admin:tienda_producto_change", args=[producto.pk]), datos)

    def test_nombre_de_solo_lectura(self):
        producto, _ = self.producto()
        r = self.client.get(reverse("admin:tienda_producto_change", args=[producto.pk]))
        self.assertContains(r, 'name="variantes-0-valor_1"')
        self.assertNotContains(r, 'name="variantes-0-nombre"')
        self.assertContains(r, 'name="caracteristica_1"')

    def test_duplicado_en_el_inline_es_error(self):
        producto, (m,) = self.producto()
        r = self.post_admin(producto, [{"variante": m}, {"valor_1": "M"}])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Hay dos variantes «M»")
        self.assertEqual(producto.variantes.count(), 1)

    def test_dos_sin_nombre_con_una_inactiva_no_es_duplicado(self):
        """El mismo estado que el form del staff permite (una activa y otra
        desactivada, las dos «»): sólo se comparan las activas entre sí."""
        producto, (a, b) = self.producto(c1="", filas=(("", ""), ("", "")))
        Variante.objects.filter(pk=b.pk).update(activa=False)
        r = self.post_admin(producto, [{"variante": a}, {"variante": b, "activa": False}])
        self.assertEqual(r.status_code, 302)
        r = self.post_admin(producto, [{"variante": a}, {"variante": b}])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Hay dos variantes")

    def test_variante_vieja_precargada_guarda_su_valor(self):
        producto, _ = self.producto(filas=())
        vieja = Variante.objects.create(producto=producto, nombre="S", stock=1)
        r = self.post_admin(producto, [{"variante": vieja, "valor_1": "S"}])
        self.assertEqual(r.status_code, 302)
        vieja.refresh_from_db()
        self.assertEqual((vieja.valor_1, vieja.nombre), ("S", "S"))

    def test_mas_de_60_es_error_en_valor_1(self):
        producto, (m,) = self.producto(c2="Color")
        r = self.post_admin(producto, [{"variante": m, "valor_1": "A" * 40, "valor_2": "B" * 25}])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "pasa de 60 caracteres")

    def test_vaciar_los_valores_recalcula_el_nombre(self):
        producto, (m,) = self.producto()
        r = self.post_admin(producto, [{"variante": m, "valor_1": ""}])
        self.assertEqual(r.status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.nombre, "")

    def test_codigo_invalido_es_error(self):
        producto, (m,) = self.producto()
        r = self.post_admin(producto, [{"variante": m, "codigo": "AB"}])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "letras, números y guiones")


class MigracionDeCaracteristicasTests(TransactionTestCase):
    """`tienda/0008`: saca la constraint de nombre único y pasa los nombres
    libres a características y valores."""

    migrate_from = [("tienda", "0007_variante_codigo")]
    migrate_to = [("tienda", "0008_variantes_por_caracteristicas")]

    def _migrar(self, destino):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(destino)
        return ejecutor.loader.project_state(destino).apps

    def tearDown(self):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_llena_caracteristicas_y_valores_y_saca_la_constraint(self):
        apps = self._migrar(self.migrate_from)
        gimnasio = apps.get_model("tenants", "Gimnasio").objects.create(nombre="M", slug="m")
        Producto_ = apps.get_model("tienda", "Producto")
        Variante_ = apps.get_model("tienda", "Variante")
        largo = "N" * 60
        casos = {
            "vacia": [""], "unico": ["Único"], "unica": ["unica"], "chocolate": ["Chocolate"],
            "varias": ["S", "M", "L"], "mixto": ["", "Chocolate"], "largo": [largo],
        }
        pks = {}
        for nombre, variantes in casos.items():
            producto = Producto_.objects.create(gimnasio=gimnasio, nombre=nombre, precio=1)
            pks[nombre] = producto.pk
            for n in variantes:
                Variante_.objects.create(producto=producto, nombre=n)

        apps = self._migrar(self.migrate_to)

        Producto_ = apps.get_model("tienda", "Producto")
        Variante_ = apps.get_model("tienda", "Variante")

        def estado(nombre):
            producto = Producto_.objects.get(pk=pks[nombre])
            return producto.caracteristica_1, sorted(
                Variante_.objects.filter(producto=producto).values_list("nombre", "valor_1")
            )

        self.assertEqual(estado("vacia"), ("", [("", "")]))
        self.assertEqual(estado("unico"), ("", [("", "")]))
        self.assertEqual(estado("unica"), ("", [("", "")]))
        self.assertEqual(estado("chocolate"), ("Opción", [("Chocolate", "Chocolate")]))
        self.assertEqual(estado("varias"), ("Opción", [("L", "L"), ("M", "M"), ("S", "S")]))
        self.assertEqual(estado("mixto"), ("Opción", [("", ""), ("Chocolate", "Chocolate")]))
        self.assertEqual(estado("largo"), ("Opción", [(largo, largo)]))
        # Sin la constraint: dos «S» en el mismo producto ya no explotan.
        producto = Producto_.objects.get(pk=pks["varias"])
        Variante_.objects.create(producto=producto, nombre="S")
        self.assertEqual(Variante_.objects.filter(producto=producto, nombre="S").count(), 2)


class VentanaDelDeployTests(_Base):
    """Lo que dejan la versión vieja y sus pestañas abiertas mientras corre el
    deploy (después de `tienda/0008`)."""

    def post_viejo(self, url, filas, **mas):
        """El form viejo: `variantes-N-nombre`, sin características ni valores."""
        iniciales = sum(1 for pk, _n in filas if pk)
        datos = {
            "nombre": "Proteína", "descripcion": "", "precio": "25000", "publicado": "on",
            "orden": "0", "categoria": "",
            "variantes-TOTAL_FORMS": str(len(filas)), "variantes-INITIAL_FORMS": str(iniciales),
            "variantes-MIN_NUM_FORMS": "0", "variantes-MAX_NUM_FORMS": "50",
        }
        for i, (pk, nombre) in enumerate(filas):
            if pk:
                datos[f"variantes-{i}-id"] = str(pk)
            datos[f"variantes-{i}-nombre"] = nombre
            datos[f"variantes-{i}-stock_inicial"] = "3"
            datos[f"variantes-{i}-activa"] = "on"
        datos.update(mas)
        return self.client.post(url, datos)

    def test_alta_con_el_form_viejo_pide_recargar_y_no_guarda(self):
        r = self.post_viejo(reverse(URL_ALTA), [(None, "Chocolate"), (None, "Vainilla")])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "La página quedó vieja: recargala y volvé a guardar.")
        self.assertFalse(Producto.objects.filter(nombre="Proteína").exists())
        self.assertNotContains(r, "Este campo es obligatorio")

    def test_edicion_con_el_form_viejo_pide_recargar_y_no_toca_nada(self):
        producto, (m, l) = self.producto(filas=(("M", ""), ("L", "")))
        url = reverse("tienda:producto_editar", args=[producto.pk])
        r = self.post_viejo(url, [(m.pk, "M"), (l.pk, "L")], nombre="Remera Nueva")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "La página quedó vieja: recargala y volvé a guardar.")
        producto.refresh_from_db()
        self.assertEqual((producto.nombre, producto.caracteristica_1), ("Remera", "Talle"))
        self.assertEqual(self.nombres(producto), ["L", "M"])
        # El form se redibuja con lo guardado, no con lo posteado.
        self.assertContains(r, 'value="Remera"')
        self.assertNotContains(r, 'value="Remera Nueva"')

    def test_producto_creado_por_el_codigo_viejo_se_edita_con_opcion(self):
        """Creado después de `0008` por la versión vieja: sin característica y
        con una variante «Chocolate» sin valores. Como hizo la migración, el
        form propone «Opción» y precarga el valor: guardar no borra el nombre."""
        producto, _ = self.producto(c1="", filas=())
        vieja = Variante.objects.create(producto=producto, nombre="Chocolate", stock=2)
        url = reverse("tienda:producto_editar", args=[producto.pk])
        html = self.client.get(url).content.decode()
        self.assertRegex(html, r'name="caracteristica_1"[^>]*value="Opción"')
        self.assertRegex(html, r'name="variantes-0-valor_1"[^>]*value="Chocolate"')
        self.assertRegex(html, r'for="id_variantes-0-valor_1">\s*Opción')
        r = self.edicion(producto, [{"variante": vieja, "valor_1": "Chocolate"}], c1="Opción")
        self.assertRedirects(r, reverse(URL_LISTA))
        vieja.refresh_from_db()
        producto.refresh_from_db()
        self.assertEqual((vieja.nombre, vieja.valor_1), ("Chocolate", "Chocolate"))
        self.assertEqual(producto.caracteristica_1, "Opción")

    def test_producto_sin_caracteristicas_sin_nombres_no_propone_opcion(self):
        producto, _ = self.producto(c1="", filas=(("", ""),))
        html = self.client.get(reverse("tienda:producto_editar", args=[producto.pk])).content.decode()
        self.assertNotIn('value="Opción"', html)

    def test_variante_sin_codigo_lo_recibe_al_guardar_sin_tocarla(self):
        producto, (m,) = self.producto()
        Variante.objects.filter(pk=m.pk).update(codigo="")
        m.refresh_from_db()
        r = self.edicion(producto, [{"variante": m}])
        self.assertRedirects(r, reverse(URL_LISTA))
        m.refresh_from_db()
        self.assertEqual(m.codigo, codigo_interno(m.pk))
