"""
Tests de Fase 1 para la biblioteca de ejercicios: creación básica, choices de
`grupo_muscular` y aislamiento por gimnasio. Sigue el patrón de
tenants/tests.py::TenantIsolationTests.

Fase 2 agrega tests de las vistas de gestión (`EjercicioListView`,
`EjercicioCreateView`, `EjercicioUpdateView`): login/rol requerido,
aislamiento por gimnasio y el filtro `?grupo_muscular=`.
"""

from django.contrib.auth.models import User
from django.db.models import ProtectedError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse

from ejercicios.models import CategoriaEjercicio, Ejercicio
from tenants.models import Gimnasio, Perfil


class EjercicioModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_basica_y_str(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )

        self.assertEqual(str(ejercicio), "Sentadilla")
        self.assertTrue(ejercicio.activo)
        self.assertEqual(ejercicio.descripcion, "")
        self.assertEqual(ejercicio.url_video, "")

    def test_grupo_muscular_expone_la_etiqueta_legible(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Press de banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
        )

        self.assertEqual(ejercicio.grupo_muscular, "pecho")
        self.assertEqual(ejercicio.get_grupo_muscular_display(), "Pecho")


class EjercicioTenantIsolationTests(TestCase):
    """Confirma que la biblioteca de ejercicios de un gimnasio no se mezcla
    con la de otro."""

    def test_for_gimnasio_devuelve_solo_los_ejercicios_de_ese_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")

        ejercicio_a = Ejercicio.objects.create(
            gimnasio=gimnasio_a,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        Ejercicio.objects.create(
            gimnasio=gimnasio_b,
            nombre="Dominadas",
            grupo_muscular=Ejercicio.GrupoMuscular.ESPALDA,
        )

        resultado = Ejercicio.objects.for_gimnasio(gimnasio_a)

        self.assertEqual(list(resultado), [ejercicio_a])


class EjercicioViewsTests(TestCase):
    """Tests de punta a punta de las vistas de gestión de Fase 2."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = User.objects.create_user("alumno-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.piernas = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Piernas"
        )
        self.brazos = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Brazos"
        )

    def test_anonimo_es_redirigido_al_login(self):
        url = reverse("ejercicios:listado")
        response = self.client.get(url)
        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_alumno_recibe_403(self):
        self.client.login(username="alumno-a", password="clave-123456")
        response = self.client.get(reverse("ejercicios:listado"))
        self.assertEqual(response.status_code, 403)

    def test_staff_puede_listar_ejercicios_de_su_gimnasio(self):
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla", categoria=self.piernas
        )
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.get(reverse("ejercicios:listado"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sentadilla")

    def test_staff_puede_crear_un_ejercicio(self):
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            reverse("ejercicios:crear"),
            {
                "nombre": "Press militar",
                "categoria_nueva": "Hombros",
                "descripcion": "",
                "url_video": "",
                "activo": "on",
            },
        )
        self.assertRedirects(response, reverse("ejercicios:listado"))
        ejercicio = Ejercicio.objects.get(nombre="Press militar")
        self.assertEqual(ejercicio.gimnasio, self.gimnasio)
        self.assertEqual(ejercicio.categoria.nombre, "Hombros")
        self.assertEqual(ejercicio.categoria.gimnasio, self.gimnasio)

    def test_staff_puede_editar_un_ejercicio_de_su_gimnasio(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Curl de biceps",
            categoria=self.brazos,
        )
        self.client.login(username="staff-a", password="clave-123456")
        response = self.client.post(
            reverse("ejercicios:editar", args=[ejercicio.pk]),
            {
                "nombre": "Curl de biceps con barra",
                "categoria": self.brazos.pk,
                "descripcion": "",
                "url_video": "https://youtube.com/watch?v=abc123",
                "activo": "",  # desmarcado: prueba el toggle de `activo` vía el form
            },
        )
        self.assertRedirects(response, reverse("ejercicios:listado"))
        ejercicio.refresh_from_db()
        self.assertEqual(ejercicio.nombre, "Curl de biceps con barra")
        self.assertEqual(ejercicio.url_video, "https://youtube.com/watch?v=abc123")
        self.assertFalse(ejercicio.activo)

    def test_staff_de_otro_gimnasio_recibe_404_al_editar(self):
        gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        ejercicio_b = Ejercicio.objects.create(
            gimnasio=gimnasio_b,
            nombre="Dominadas",
            grupo_muscular=Ejercicio.GrupoMuscular.ESPALDA,
        )
        self.client.login(username="staff-a", password="clave-123456")

        response_get = self.client.get(
            reverse("ejercicios:editar", args=[ejercicio_b.pk])
        )
        self.assertEqual(response_get.status_code, 404)

        response_post = self.client.post(
            reverse("ejercicios:editar", args=[ejercicio_b.pk]),
            {
                "nombre": "Hackeado",
                "grupo_muscular": Ejercicio.GrupoMuscular.ESPALDA,
                "descripcion": "",
                "url_video": "",
                "activo": "on",
            },
        )
        self.assertEqual(response_post.status_code, 404)
        ejercicio_b.refresh_from_db()
        self.assertEqual(ejercicio_b.nombre, "Dominadas")

    def test_filtro_por_categoria(self):
        pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Pecho"
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla", categoria=self.piernas
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Press de banca", categoria=pecho
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla búlgara",
            categoria=self.piernas,
        )
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("ejercicios:listado"), {"categoria": self.piernas.pk}
        )

        self.assertEqual(response.status_code, 200)
        nombres = {e.nombre for e in response.context["ejercicios"]}
        self.assertEqual(nombres, {"Sentadilla", "Sentadilla búlgara"})
        self.assertNotContains(response, "Press de banca")

    def test_filtro_por_texto_libre(self):
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Press de banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla búlgara",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("ejercicios:listado"),
            {"q": "Sentadilla"},
        )

        self.assertEqual(response.status_code, 200)
        nombres = {e.nombre for e in response.context["ejercicios"]}
        self.assertEqual(nombres, {"Sentadilla", "Sentadilla búlgara"})
        self.assertNotContains(response, "Press de banca")

    def test_filtro_por_texto_libre_case_insensitive(self):
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Press de banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
        )
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("ejercicios:listado"),
            {"q": "SENTADILLA"},
        )

        self.assertEqual(response.status_code, 200)
        nombres = {e.nombre for e in response.context["ejercicios"]}
        self.assertEqual(nombres, {"Sentadilla"})
        self.assertNotContains(response, "Press de banca")

    def test_filtro_combinado_categoria_y_texto_libre(self):
        pecho = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Pecho"
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla", categoria=self.piernas
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Press de banca", categoria=pecho
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla búlgara",
            categoria=self.piernas,
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Press inclinado", categoria=pecho
        )
        # Comparte el texto "Press" con uno de Pecho: sin el filtro de
        # categoría también entraría, así que este ejercicio es el que hace
        # que el test pruebe de verdad la COMBINACIÓN y no solo el `q`.
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Press de piernas",
            categoria=self.piernas,
        )
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("ejercicios:listado"),
            {"categoria": pecho.pk, "q": "Press"},
        )

        self.assertEqual(response.status_code, 200)
        nombres = {e.nombre for e in response.context["ejercicios"]}
        self.assertEqual(nombres, {"Press de banca", "Press inclinado"})


class CategoriaEjercicioModelTests(TestCase):
    """`CategoriaEjercicio` reemplaza el `TextChoices` global de
    `grupo_muscular` por un catálogo propio de cada gimnasio: un gimnasio
    funcional clasifica por patrón de movimiento (EMPUJE/TRACCIÓN) y uno
    clásico por anatomía (Pecho/Espalda), y ninguna lista fija sirve para
    los dos."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")

    def test_creacion_basica_y_str(self):
        categoria = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="EMPUJE"
        )

        self.assertEqual(str(categoria), "EMPUJE")
        self.assertTrue(categoria.activo)
        self.assertEqual(categoria.orden, 0)

    def test_nombre_normalizado_se_calcula_solo(self):
        categoria = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="  TRACCIÓN  "
        )

        self.assertEqual(categoria.nombre_normalizado, "traccion")

    def test_nombre_normalizado_se_recalcula_al_renombrar(self):
        categoria = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="EMPUJE"
        )

        categoria.nombre = "Empujón"
        categoria.save()

        categoria.refresh_from_db()
        self.assertEqual(categoria.nombre_normalizado, "empujon")

    def test_no_admite_dos_categorias_que_normalizan_igual(self):
        """`CATEGORÍA` y `categoria` son la misma categoría escrita distinto.
        Sin esta constraint el importador crearía una por cada variante de
        mayúsculas/tildes que aparezca en el Excel."""
        CategoriaEjercicio.objects.create(gimnasio=self.gimnasio, nombre="CORE")

        with self.assertRaises(IntegrityError):
            CategoriaEjercicio.objects.create(
                gimnasio=self.gimnasio, nombre="Core"
            )

    def test_dos_gimnasios_pueden_tener_la_misma_categoria(self):
        """La constraint es por gimnasio, no global."""
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        CategoriaEjercicio.objects.create(gimnasio=self.gimnasio, nombre="Core")

        categoria_b = CategoriaEjercicio.objects.create(
            gimnasio=gimnasio_b, nombre="Core"
        )

        self.assertEqual(categoria_b.nombre, "Core")

    def test_ordena_por_orden_y_despues_por_nombre(self):
        CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Zaguero", orden=1
        )
        CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Empuje", orden=2
        )
        CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Absoluto", orden=1
        )

        nombres = list(
            CategoriaEjercicio.objects.for_gimnasio(self.gimnasio).values_list(
                "nombre", flat=True
            )
        )

        self.assertEqual(nombres, ["Absoluto", "Zaguero", "Empuje"])

    def test_ejercicio_referencia_una_categoria(self):
        categoria = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="EMPUJE"
        )

        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Push up", categoria=categoria
        )

        self.assertEqual(ejercicio.categoria, categoria)
        self.assertEqual(list(categoria.ejercicios.all()), [ejercicio])

    def test_ejercicio_puede_quedar_sin_categoria(self):
        """El Excel real del primer cliente trae una fila con la categoría
        vacía; el importador no debe trabarse por eso."""
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sin clasificar"
        )

        self.assertIsNone(ejercicio.categoria)

    def test_no_se_puede_borrar_una_categoria_en_uso(self):
        """`on_delete=PROTECT`, mismo criterio que el resto del proyecto:
        borrar una categoría usada debe ser consciente, no silencioso."""
        categoria = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="EMPUJE"
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Push up", categoria=categoria
        )

        with self.assertRaises(ProtectedError):
            categoria.delete()


class CategoriaEjercicioTenantIsolationTests(TestCase):
    """Patrón `novedades/tests.py::NovedadTenantIsolationTests`."""

    def test_for_gimnasio_no_devuelve_categorias_de_otro_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")

        categoria_a = CategoriaEjercicio.objects.create(
            gimnasio=gimnasio_a, nombre="EMPUJE"
        )
        CategoriaEjercicio.objects.create(gimnasio=gimnasio_b, nombre="Pecho")

        categorias_de_a = CategoriaEjercicio.objects.for_gimnasio(gimnasio_a)

        self.assertEqual(list(categorias_de_a), [categoria_a])
        self.assertNotIn(
            "Pecho", categorias_de_a.values_list("nombre", flat=True)
        )


class BackfillCategoriasMigrationTests(TestCase):
    """`0003_backfill_categorias.py`: convierte el `grupo_muscular` de texto
    de cada ejercicio en una `CategoriaEjercicio` del gimnasio que lo tiene.

    Se ejercita llamando la función de la migración directamente, mismo
    patrón que `rutinas/tests.py` con `0006_backfill_grupo_muscular_snapshot`.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")

    def _backfill(self):
        import importlib

        from django.apps import apps

        migracion = importlib.import_module(
            "ejercicios.migrations.0003_backfill_categorias"
        )
        migracion.backfill_categorias(apps, None)

    def test_crea_solo_las_categorias_que_el_gimnasio_usa(self):
        """Decisión del dueño del producto: un gimnasio que solo usa Pecho y
        Piernas no arranca con las 8 anatómicas, para no ensuciarle el filtro
        y las zonas de drag-and-drop con categorías que nunca va a usar."""
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Press", grupo_muscular="pecho"
        )
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla", grupo_muscular="piernas"
        )

        self._backfill()

        nombres = list(
            CategoriaEjercicio.objects.for_gimnasio(self.gimnasio).values_list(
                "nombre", flat=True
            )
        )
        self.assertEqual(nombres, ["Pecho", "Piernas"])

    def test_reapunta_cada_ejercicio_a_su_categoria(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Press", grupo_muscular="pecho"
        )

        self._backfill()

        ejercicio.refresh_from_db()
        self.assertEqual(ejercicio.categoria.nombre, "Pecho")

    def test_usa_la_etiqueta_legible_no_el_valor_crudo(self):
        """`cuerpo_completo` es el valor de base; lo que el staff tiene que
        leer es 'Cuerpo completo'."""
        Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Burpee",
            grupo_muscular="cuerpo_completo",
        )

        self._backfill()

        categoria = CategoriaEjercicio.objects.for_gimnasio(self.gimnasio).get()
        self.assertEqual(categoria.nombre, "Cuerpo completo")
        self.assertEqual(categoria.nombre_normalizado, "cuerpo completo")

    def test_no_mezcla_categorias_entre_gimnasios(self):
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Press", grupo_muscular="pecho"
        )
        ejercicio_b = Ejercicio.objects.create(
            gimnasio=self.otro, nombre="Remo", grupo_muscular="espalda"
        )

        self._backfill()

        ejercicio_b.refresh_from_db()
        self.assertEqual(ejercicio_b.categoria.gimnasio, self.otro)
        self.assertEqual(
            CategoriaEjercicio.objects.for_gimnasio(self.otro).count(), 1
        )

    def test_respeta_el_orden_del_catalogo_original(self):
        """Pecho antes que Core, como en el `TextChoices` de siempre, no
        alfabético -- el staff ya está acostumbrado a ese orden."""
        for nombre, grupo in [
            ("Plancha", "core"),
            ("Press", "pecho"),
            ("Trote", "cardio"),
        ]:
            Ejercicio.objects.create(
                gimnasio=self.gimnasio, nombre=nombre, grupo_muscular=grupo
            )

        self._backfill()

        nombres = list(
            CategoriaEjercicio.objects.for_gimnasio(self.gimnasio).values_list(
                "nombre", flat=True
            )
        )
        self.assertEqual(nombres, ["Pecho", "Core", "Cardio"])

    def test_es_idempotente(self):
        """Correrla dos veces no debe duplicar categorías: `migrate` puede
        reintentarse tras un deploy cortado a la mitad."""
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Press", grupo_muscular="pecho"
        )

        self._backfill()
        self._backfill()

        self.assertEqual(
            CategoriaEjercicio.objects.for_gimnasio(self.gimnasio).count(), 1
        )

    def test_sin_ejercicios_no_crea_nada(self):
        self._backfill()

        self.assertEqual(CategoriaEjercicio.objects.count(), 0)

    def test_ignora_ejercicios_sin_grupo_muscular(self):
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Suelto", grupo_muscular=""
        )

        self._backfill()

        self.assertEqual(CategoriaEjercicio.objects.count(), 0)


class SembrarCategoriasInicialesTests(TestCase):
    """Un gimnasio nuevo arranca con un catálogo sugerido en vez de un
    desplegable vacío -- si no, el primer ejercicio que el staff quiera
    cargar lo obliga a inventar una categoría antes de poder guardar."""

    def test_crear_gimnasio_siembra_las_categorias_iniciales(self):
        from tenants.services import crear_gimnasio

        gimnasio, _usuario, _password = crear_gimnasio(
            "Gimnasio Nuevo", "dueno@ejemplo.com"
        )

        nombres = list(
            CategoriaEjercicio.objects.for_gimnasio(gimnasio).values_list(
                "nombre", flat=True
            )
        )
        self.assertEqual(
            nombres,
            [
                "Pecho",
                "Espalda",
                "Piernas",
                "Hombros",
                "Brazos",
                "Core",
                "Cardio",
                "Cuerpo completo",
            ],
        )

    def test_las_categorias_sembradas_son_del_gimnasio_nuevo(self):
        from tenants.services import crear_gimnasio

        gimnasio_a, _u, _p = crear_gimnasio("Uno", "uno@ejemplo.com")
        gimnasio_b, _u, _p = crear_gimnasio("Dos", "dos@ejemplo.com")

        self.assertEqual(
            CategoriaEjercicio.objects.for_gimnasio(gimnasio_a).count(), 8
        )
        self.assertEqual(
            CategoriaEjercicio.objects.for_gimnasio(gimnasio_b).count(), 8
        )
        self.assertEqual(CategoriaEjercicio.objects.count(), 16)

    def test_sembrar_es_idempotente(self):
        from ejercicios.services import sembrar_categorias_iniciales
        from tenants.services import crear_gimnasio

        gimnasio, _u, _p = crear_gimnasio("Uno", "uno@ejemplo.com")

        sembrar_categorias_iniciales(gimnasio)

        self.assertEqual(
            CategoriaEjercicio.objects.for_gimnasio(gimnasio).count(), 8
        )


class EjercicioFormCategoriaTests(TestCase):
    """El staff puede elegir una categoría existente **o escribir una nueva**
    sin salir del alta del ejercicio: mandarlo a otra pantalla y hacerlo
    volver a tipear todo era la fricción que pidió sacar el dueño."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.empuje = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="EMPUJE"
        )

    def _form(self, **datos):
        from ejercicios.forms import EjercicioForm

        base = {"nombre": "Push up", "descripcion": "", "url_video": "", "activo": True}
        return EjercicioForm(data={**base, **datos}, gimnasio=self.gimnasio)

    def test_elegir_una_categoria_existente(self):
        form = self._form(categoria=self.empuje.pk)

        self.assertTrue(form.is_valid(), form.errors)
        ejercicio = form.save(commit=False)
        ejercicio.gimnasio = self.gimnasio
        ejercicio.save()
        self.assertEqual(ejercicio.categoria, self.empuje)

    def test_escribir_una_categoria_nueva_la_crea(self):
        form = self._form(categoria_nueva="TRACCIÓN")

        self.assertTrue(form.is_valid(), form.errors)
        ejercicio = form.save(commit=False)
        ejercicio.gimnasio = self.gimnasio
        ejercicio.save()

        self.assertEqual(ejercicio.categoria.nombre, "TRACCIÓN")
        self.assertEqual(ejercicio.categoria.gimnasio, self.gimnasio)

    def test_categoria_nueva_que_ya_existe_reusa_la_que_hay(self):
        """Escribir 'empuje' con 'EMPUJE' ya cargada no debe crear una
        segunda: es la misma categoría escrita distinto."""
        form = self._form(categoria_nueva="empuje")

        self.assertTrue(form.is_valid(), form.errors)
        ejercicio = form.save(commit=False)
        ejercicio.gimnasio = self.gimnasio
        ejercicio.save()

        self.assertEqual(ejercicio.categoria, self.empuje)
        self.assertEqual(
            CategoriaEjercicio.objects.for_gimnasio(self.gimnasio).count(), 1
        )

    def test_no_se_puede_elegir_y_escribir_a_la_vez(self):
        form = self._form(categoria=self.empuje.pk, categoria_nueva="TRACCIÓN")

        self.assertFalse(form.is_valid())

    def test_la_categoria_es_obligatoria(self):
        """La base la acepta vacía (por la fila suelta del Excel real), pero
        el alta manual no: si no, se acumulan ejercicios que no salen en
        ningún filtro."""
        form = self._form()

        self.assertFalse(form.is_valid())

    def test_no_ofrece_categorias_de_otro_gimnasio(self):
        ajena = CategoriaEjercicio.objects.create(
            gimnasio=self.otro, nombre="Ajena"
        )

        form = self._form(categoria=ajena.pk)

        self.assertFalse(form.is_valid())

    def test_no_ofrece_categorias_desactivadas(self):
        """Una categoría desactivada no se ofrece para asignar, pero los
        ejercicios que ya la tienen la conservan."""
        vieja = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Vieja", activo=False
        )

        form = self._form(categoria=vieja.pk)

        self.assertFalse(form.is_valid())

    def test_editar_conserva_la_categoria_desactivada_que_ya_tenia(self):
        vieja = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Vieja", activo=False
        )
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Antiguo", categoria=vieja
        )

        from ejercicios.forms import EjercicioForm

        form = EjercicioForm(instance=ejercicio, gimnasio=self.gimnasio)

        self.assertIn(vieja, form.fields["categoria"].queryset)


class CategoriaCRUDTests(TestCase):
    """CRUD del catálogo, molde de `pagos.MedioCobro`. Sin `DeleteView`:
    "eliminar" es destildar `activo`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = User.objects.create_user("alumno-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.empuje = CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="EMPUJE"
        )

    def test_anonimo_es_redirigido_al_login(self):
        url = reverse("ejercicios:categorias_listado")
        self.assertRedirects(
            self.client.get(url), f"{reverse('login')}?next={url}"
        )

    def test_alumno_recibe_403(self):
        self.client.login(username="alumno-a", password="clave-123456")
        response = self.client.get(reverse("ejercicios:categorias_listado"))
        self.assertEqual(response.status_code, 403)

    def test_staff_ve_sus_categorias_y_no_las_de_otro_gimnasio(self):
        CategoriaEjercicio.objects.create(gimnasio=self.otro, nombre="AJENA")
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(reverse("ejercicios:categorias_listado"))

        self.assertContains(response, "EMPUJE")
        self.assertNotContains(response, "AJENA")

    def test_lista_tambien_las_inactivas_para_poder_reactivarlas(self):
        CategoriaEjercicio.objects.create(
            gimnasio=self.gimnasio, nombre="VIEJA", activo=False
        )
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(reverse("ejercicios:categorias_listado"))

        self.assertContains(response, "VIEJA")

    def test_muestra_cuantos_ejercicios_tiene_cada_categoria(self):
        Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Push up", categoria=self.empuje
        )
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(reverse("ejercicios:categorias_listado"))

        self.assertEqual(
            response.context["categorias"].get(pk=self.empuje.pk).total_ejercicios, 1
        )

    def test_staff_puede_crear_una_categoria(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.post(
            reverse("ejercicios:categorias_crear"),
            {"nombre": "TRACCIÓN", "orden": 1, "activo": "on"},
        )

        self.assertRedirects(response, reverse("ejercicios:categorias_listado"))
        categoria = CategoriaEjercicio.objects.get(nombre="TRACCIÓN")
        self.assertEqual(categoria.gimnasio, self.gimnasio)

    def test_renombrar_no_toca_los_ejercicios_asignados(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Push up", categoria=self.empuje
        )
        self.client.login(username="staff-a", password="clave-123456")

        self.client.post(
            reverse("ejercicios:categorias_editar", args=[self.empuje.pk]),
            {"nombre": "EMPUJE HORIZONTAL", "orden": 0, "activo": "on"},
        )

        ejercicio.refresh_from_db()
        self.assertEqual(ejercicio.categoria.nombre, "EMPUJE HORIZONTAL")

    def test_no_deja_crear_una_categoria_que_ya_existe_escrita_distinto(self):
        """Sin este guard la `UniqueConstraint` explota como IntegrityError
        (un 500) en vez de mostrar un error de campo."""
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.post(
            reverse("ejercicios:categorias_crear"),
            {"nombre": "empuje", "orden": 0, "activo": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya tenés una categoría")
        self.assertEqual(
            CategoriaEjercicio.objects.for_gimnasio(self.gimnasio).count(), 1
        )

    def test_editar_una_categoria_sin_cambiarle_el_nombre_no_choca_consigo_misma(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.post(
            reverse("ejercicios:categorias_editar", args=[self.empuje.pk]),
            {"nombre": "EMPUJE", "orden": 5, "activo": "on"},
        )

        self.assertRedirects(response, reverse("ejercicios:categorias_listado"))
        self.empuje.refresh_from_db()
        self.assertEqual(self.empuje.orden, 5)

    def test_editar_una_categoria_de_otro_gimnasio_da_404(self):
        ajena = CategoriaEjercicio.objects.create(
            gimnasio=self.otro, nombre="AJENA"
        )
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("ejercicios:categorias_editar", args=[ajena.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_desactivar_una_categoria_no_borra_sus_ejercicios(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Push up", categoria=self.empuje
        )
        self.client.login(username="staff-a", password="clave-123456")

        self.client.post(
            reverse("ejercicios:categorias_editar", args=[self.empuje.pk]),
            {"nombre": "EMPUJE", "orden": 0, "activo": ""},
        )

        ejercicio.refresh_from_db()
        self.assertEqual(ejercicio.categoria, self.empuje)
        self.assertFalse(ejercicio.categoria.activo)

    def test_el_listado_de_ejercicios_enlaza_al_crud(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(reverse("ejercicios:listado"))

        self.assertContains(response, reverse("ejercicios:categorias_listado"))
