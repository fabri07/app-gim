"""Pieza 5, tarea 1: catálogo de premios (modelo + pantallas del staff)."""

from itertools import count

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from reconocimientos.forms import PremioForm
from reconocimientos.models import Premio
from tenants.models import Gimnasio, Perfil
from tenants.terminos import VERSION_TERMINOS

_serie = count(1)


def _gimnasio(slug="vida-plena", habilitado=True):
    return Gimnasio.objects.create(
        nombre=slug, slug=slug, reconocimientos_habilitado=habilitado
    )


def _usuario(gimnasio, rol):
    n = next(_serie)
    usuario = User.objects.create_user(f"p{n}", password="clave-123456")
    Perfil.objects.create(
        usuario=usuario, gimnasio=gimnasio, rol=rol, terminos_version=VERSION_TERMINOS
    )
    return usuario


def _datos(**extra):
    datos = {
        "nombre": "Un mes de regalo", "tipo": "porcentaje", "valor": "10",
        "cuotas": "1", "texto": "", "da_insignia": "on", "activo": "on",
    }
    datos.update(extra)
    return {k: v for k, v in datos.items() if v is not None}


class PremioModeloTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()

    def _premio(self, **kw):
        kw.setdefault("nombre", "P")
        return Premio(gimnasio=self.gimnasio, **kw)

    def test_porcentaje_exige_entre_1_y_90(self):
        for valor in (None, 0, "0.5", 91, 100):
            with self.subTest(valor=valor):
                with self.assertRaises(ValidationError) as cm:
                    self._premio(tipo="porcentaje", valor=valor).full_clean(
                        exclude=["gimnasio"]
                    )
                self.assertIn("valor", cm.exception.message_dict)
        for valor in (1, 90, "12.50"):
            self._premio(tipo="porcentaje", valor=valor).full_clean(exclude=["gimnasio"])

    def test_fijo_exige_mayor_a_cero(self):
        for valor in (None, 0, -5):
            with self.subTest(valor=valor):
                with self.assertRaises(ValidationError) as cm:
                    self._premio(tipo="fijo", valor=valor).full_clean(exclude=["gimnasio"])
                self.assertIn("valor", cm.exception.message_dict)
        self._premio(tipo="fijo", valor=5000).full_clean(exclude=["gimnasio"])

    def test_obsequio_exige_texto_y_normaliza_valor_y_cuotas(self):
        with self.assertRaises(ValidationError) as cm:
            self._premio(tipo="obsequio", texto="  ").full_clean(exclude=["gimnasio"])
        self.assertIn("texto", cm.exception.message_dict)
        premio = self._premio(tipo="obsequio", texto="Una remera", valor=50, cuotas=7)
        premio.full_clean(exclude=["gimnasio"])
        self.assertIsNone(premio.valor)
        self.assertEqual(premio.cuotas, 1)

    def test_cuotas_fuera_de_1_a_13_se_rechazan(self):
        for cuotas in (0, 14):
            with self.subTest(cuotas=cuotas):
                with self.assertRaises(ValidationError) as cm:
                    self._premio(tipo="fijo", valor=1, cuotas=cuotas).full_clean(
                        exclude=["gimnasio"]
                    )
                self.assertIn("cuotas", cm.exception.message_dict)
        self._premio(tipo="fijo", valor=1, cuotas=13).full_clean(exclude=["gimnasio"])

    def test_la_base_rechaza_dos_por_defecto_en_el_mismo_gimnasio(self):
        Premio.objects.create(gimnasio=self.gimnasio, nombre="A", valor=5, por_defecto=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Premio.objects.create(
                gimnasio=self.gimnasio, nombre="B", valor=5, por_defecto=True
            )

    def test_dos_gimnasios_pueden_tener_cada_uno_su_por_defecto(self):
        otro = _gimnasio("otro")
        Premio.objects.create(gimnasio=self.gimnasio, nombre="A", valor=5, por_defecto=True)
        Premio.objects.create(gimnasio=otro, nombre="A", valor=5, por_defecto=True)

    def test_varios_sin_por_defecto_conviven(self):
        for n in "AB":
            Premio.objects.create(gimnasio=self.gimnasio, nombre=n, valor=5)


class PremioFormTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()

    def _form(self, datos, instance=None):
        return PremioForm(datos, gimnasio=self.gimnasio, instance=instance)

    def test_valido_guarda(self):
        form = self._form(_datos())
        self.assertTrue(form.is_valid(), form.errors)
        form.instance.gimnasio = self.gimnasio  # lo estampa la vista
        premio = form.save()
        self.assertEqual(premio.cuotas, 1)

    def test_cada_campo_trae_su_error(self):
        casos = [
            ({"nombre": ""}, "nombre"),
            ({"nombre": "x" * 81}, "nombre"),
            ({"tipo": "inventado"}, "tipo"),
            ({"valor": "95"}, "valor"),
            ({"valor": "abc"}, "valor"),
            ({"cuotas": "14"}, "cuotas"),
            ({"cuotas": "0"}, "cuotas"),
            ({"tipo": "obsequio", "texto": ""}, "texto"),
            ({"tipo": "obsequio", "texto": "y" * 201}, "texto"),
        ]
        for extra, campo in casos:
            with self.subTest(extra=extra):
                form = self._form(_datos(**extra))
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

    def test_cuotas_vacias_quedan_en_1(self):
        form = self._form(_datos(cuotas=""))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["cuotas"], 1)

    def test_obsequio_sin_valor_ni_cuotas(self):
        form = self._form(
            _datos(tipo="obsequio", valor="", cuotas="", texto="Una remera")
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.instance.valor)
        self.assertEqual(form.instance.cuotas, 1)


class PremiosVistasTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        self.staff = _usuario(self.gimnasio, Perfil.Rol.STAFF)
        self.client.force_login(self.staff)

    def _crear(self, **kw):
        kw.setdefault("nombre", "Premio")
        kw.setdefault("valor", 10)
        return Premio.objects.create(gimnasio=self.gimnasio, **kw)

    def test_lista_solo_los_del_gimnasio_activos_e_inactivos(self):
        self._crear(nombre="Mío activo")
        self._crear(nombre="Mío inactivo", activo=False)
        otro = _gimnasio("otro")
        Premio.objects.create(gimnasio=otro, nombre="Ajeno", valor=5)
        r = self.client.get(reverse("reconocimientos:premios"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Mío activo")
        self.assertContains(r, "Mío inactivo")
        self.assertNotContains(r, "Ajeno")

    def test_alumno_recibe_403(self):
        alumno = _usuario(self.gimnasio, Perfil.Rol.ALUMNO)
        self.client.force_login(alumno)
        premio = self._crear()
        for nombre, args in (
            ("premios", ()), ("premio_nuevo", ()), ("premio_editar", (premio.pk,)),
        ):
            with self.subTest(nombre=nombre):
                r = self.client.get(reverse(f"reconocimientos:{nombre}", args=args))
                self.assertEqual(r.status_code, 403)
        r = self.client.post(reverse("reconocimientos:premio_nuevo"), _datos())
        self.assertEqual(r.status_code, 403)

    def test_anonimo_va_al_login(self):
        self.client.logout()
        r = self.client.get(reverse("reconocimientos:premios"))
        self.assertEqual(r.status_code, 302)

    def test_sin_bandera_404_en_get_y_post(self):
        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()
        premio = self._crear()
        self.assertEqual(self.client.get(reverse("reconocimientos:premios")).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("reconocimientos:premio_nuevo")).status_code, 404
        )
        self.assertEqual(
            self.client.post(reverse("reconocimientos:premio_nuevo"), _datos()).status_code,
            404,
        )
        url = reverse("reconocimientos:premio_editar", args=[premio.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, _datos()).status_code, 404)
        self.assertEqual(Premio.objects.count(), 1)

    def test_link_en_la_nav_solo_con_bandera(self):
        r = self.client.get(reverse("reconocimientos:premios"))
        self.assertContains(r, 'href="%s"' % reverse("reconocimientos:premios"))
        self.gimnasio.reconocimientos_habilitado = False
        self.gimnasio.save()
        r = self.client.get(reverse("home"), follow=True)
        self.assertNotContains(r, 'href="%s"' % reverse("reconocimientos:premios"))

    def test_alta_estampa_el_gimnasio_del_servidor(self):
        otro = _gimnasio("otro")
        r = self.client.post(
            reverse("reconocimientos:premio_nuevo"), _datos(gimnasio=otro.pk)
        )
        self.assertRedirects(r, reverse("reconocimientos:premios"))
        premio = Premio.objects.get()
        self.assertEqual(premio.gimnasio, self.gimnasio)

    def test_alta_invalida_vuelve_con_200_y_error_visible(self):
        r = self.client.post(reverse("reconocimientos:premio_nuevo"), _datos(valor="99"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "entre 1 y 90")
        self.assertEqual(Premio.objects.count(), 0)

    def test_por_defecto_desmarca_el_anterior(self):
        viejo = self._crear(nombre="Viejo", por_defecto=True)
        r = self.client.post(
            reverse("reconocimientos:premio_nuevo"),
            _datos(nombre="Nuevo", por_defecto="on"),
        )
        self.assertEqual(r.status_code, 302)
        viejo.refresh_from_db()
        self.assertFalse(viejo.por_defecto)
        self.assertTrue(Premio.objects.get(nombre="Nuevo").por_defecto)
        self.assertEqual(Premio.objects.filter(por_defecto=True).count(), 1)

    def test_por_defecto_no_toca_a_otro_gimnasio(self):
        otro = _gimnasio("otro")
        ajeno = Premio.objects.create(
            gimnasio=otro, nombre="Ajeno", valor=5, por_defecto=True
        )
        self.client.post(
            reverse("reconocimientos:premio_nuevo"), _datos(por_defecto="on")
        )
        ajeno.refresh_from_db()
        self.assertTrue(ajeno.por_defecto)

    def test_editar_y_desactivar_en_vez_de_borrar(self):
        premio = self._crear(nombre="Viejo")
        url = reverse("reconocimientos:premio_editar", args=[premio.pk])
        r = self.client.post(url, _datos(nombre="Nuevo", activo=None))
        self.assertRedirects(r, reverse("reconocimientos:premios"))
        premio.refresh_from_db()
        self.assertEqual(premio.nombre, "Nuevo")
        self.assertFalse(premio.activo)
        self.assertEqual(Premio.objects.count(), 1)

    def test_no_hay_boton_ni_ruta_de_borrado(self):
        premio = self._crear()
        r = self.client.get(reverse("reconocimientos:premios"))
        self.assertNotContains(r, "Borrar")
        self.assertNotContains(r, "Eliminar")
        url = reverse("reconocimientos:premio_editar", args=[premio.pk])
        self.assertEqual(self.client.get(url + "borrar/").status_code, 404)

    def test_no_se_edita_un_premio_de_otro_gimnasio(self):
        otro = _gimnasio("otro")
        ajeno = Premio.objects.create(gimnasio=otro, nombre="Ajeno", valor=5)
        url = reverse("reconocimientos:premio_editar", args=[ajeno.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, _datos(nombre="Hackeado")).status_code, 404)
        ajeno.refresh_from_db()
        self.assertEqual(ajeno.nombre, "Ajeno")


class PremiosCoberturaTransversalTests(TestCase):
    """`vaciar_gimnasio`, `_ensuciar` y la exportación cubren el modelo."""

    def test_vaciar_gimnasio_borra_los_premios_y_deja_los_ajenos(self):
        from tenants.demo import vaciar_gimnasio

        propio = _gimnasio("propio")
        propio.es_demo = True
        propio.save()
        ajeno = _gimnasio("ajeno")
        Premio.objects.create(gimnasio=propio, nombre="A", valor=5, por_defecto=True)
        Premio.objects.create(gimnasio=ajeno, nombre="B", valor=5)
        vaciar_gimnasio(gimnasio=propio)
        self.assertFalse(Premio.objects.filter(gimnasio=propio).exists())
        self.assertTrue(Premio.objects.filter(gimnasio=ajeno).exists())

    def test_la_exportacion_tiene_la_hoja_premios(self):
        from tenants.exportacion import HOJAS

        hoja = next(h for h in HOJAS if h.nombre == "premios")
        self.assertEqual(hoja.modelo, "reconocimientos.Premio")
