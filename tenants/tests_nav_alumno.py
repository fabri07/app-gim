"""Menú del alumno (`templates/partials/nav_alumno.html`).

Se renderiza con el cliente de tests y no con `render_to_string` suelto: el
`aria-current` depende de `request.resolver_match`, que sólo existe cuando el
request pasó por el resolver de URLs.
"""

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from rutinas.models import RutinaAsignada, RutinaAsignadaItem
from tenants.models import Gimnasio, Perfil

CLAVE = "clave-123456"


def _gimnasio(**extra):
    return Gimnasio.objects.create(nombre="Vida Plena", slug="vida-plena", **extra)


def _staff(gimnasio, username="duenio"):
    usuario = User.objects.create_user(username, password=CLAVE)
    Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)
    return usuario


def _alumno(gimnasio, username="alumno"):
    usuario = User.objects.create_user(username, password=CLAVE)
    perfil = Perfil.objects.create(
        usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.ALUMNO
    )
    alumno = Alumno.objects.create(
        gimnasio=gimnasio, nombre="Ana", apellido="Pérez", perfil=perfil,
        estado=Alumno.Estado.ACTIVO,
    )
    return alumno, usuario


NAV = 'id="nav-alumno"'
HAMBURGUESA = 'aria-controls="nav-alumno"'


class NavAlumnoTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        self.alumno, self.usuario = _alumno(self.gimnasio)
        self.client.force_login(self.usuario)

    def _nav(self, respuesta):
        """El HTML del `<nav>` del alumno, para no confundir sus links con los
        del resto de la página (las tarjetas del inicio también enlazan)."""
        html = respuesta.content.decode()
        inicio = html.index(NAV)
        return html[inicio:html.index("</nav>", inicio)]

    def test_el_alumno_ve_inicio_mi_rutina_y_turnos(self):
        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, NAV)
        self.assertContains(respuesta, HAMBURGUESA)
        nav = self._nav(respuesta)
        self.assertIn(f'href="{reverse("home")}"', nav)
        self.assertIn(f'href="{reverse("home")}#mi-rutina"', nav)
        self.assertIn(f'href="{reverse("turnos:mis_turnos")}"', nav)
        self.assertIn(">Inicio<", nav)
        self.assertIn(">Mi rutina<", nav)
        self.assertIn(">Turnos<", nav)

    def test_la_tienda_solo_aparece_con_el_add_on(self):
        respuesta = self.client.get(reverse("home"))
        self.assertNotIn(reverse("tienda:catalogo"), self._nav(respuesta))

        self.gimnasio.tienda_habilitada = True
        self.gimnasio.save()
        respuesta = self.client.get(reverse("home"))
        nav = self._nav(respuesta)
        self.assertIn(f'href="{reverse("tienda:catalogo")}"', nav)
        self.assertIn(">Tienda<", nav)

    def test_el_body_del_alumno_tiene_x_data(self):
        """El ☰ y el menú comparten `navAbierto`: sin `x-data` en el `<body>`
        Alpine no tiene scope y el ☰ no despliega nada."""
        respuesta = self.client.get(reverse("home"))
        html = respuesta.content.decode()
        body = html[html.index("<body"):html.index(">", html.index("<body"))]

        self.assertIn('x-data="{ navAbierto: false }"', body)

    def test_el_hamburguesa_y_el_nav_llevan_sus_atributos_de_accesibilidad(self):
        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, 'aria-label="Menú"')
        self.assertContains(respuesta, ':aria-expanded="navAbierto"')
        self.assertContains(respuesta, 'aria-label="Secciones"')
        self.assertContains(respuesta, 'x-init="navAbierto = false"')

    def test_aria_current_en_inicio(self):
        nav = self._nav(self.client.get(reverse("home")))

        self.assertEqual(nav.count('aria-current="page"'), 1)
        self.assertIn(f'href="{reverse("home")}" aria-current="page"', nav)

    def test_aria_current_en_turnos(self):
        nav = self._nav(self.client.get(reverse("turnos:mis_turnos")))

        self.assertEqual(nav.count('aria-current="page"'), 1)
        self.assertIn(
            f'href="{reverse("turnos:mis_turnos")}" aria-current="page"', nav
        )

    def test_aria_current_en_mi_rutina(self):
        asignada = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, nombre_snapshot="Plan",
            fecha_inicio=timezone.localdate(), activa=True,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=asignada, ejercicio_nombre_snapshot="Sentadilla",
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
        )
        respuesta = self.client.get(reverse("rutinas:mi_dia_detalle", args=[1]))
        self.assertEqual(respuesta.status_code, 200)
        nav = self._nav(respuesta)

        self.assertEqual(nav.count('aria-current="page"'), 1)
        self.assertIn(
            f'href="{reverse("home")}#mi-rutina" aria-current="page"', nav
        )

    def test_aria_current_en_la_tienda(self):
        self.gimnasio.tienda_habilitada = True
        self.gimnasio.save()
        nav = self._nav(self.client.get(reverse("tienda:mis_pedidos")))

        self.assertEqual(nav.count('aria-current="page"'), 1)
        self.assertIn(
            f'href="{reverse("tienda:catalogo")}" aria-current="page"', nav
        )

    def test_la_tienda_cerrada_ofrece_mis_pedidos(self):
        """Con la tienda apagada el alumno sigue viendo lo que compró, y el
        menú ya no tiene «Tienda» para llegar hasta ahí."""
        respuesta = self.client.get(reverse("tienda:catalogo"))

        self.assertTemplateUsed(respuesta, "tienda/cerrada.html")
        self.assertContains(
            respuesta, f'href="{reverse("tienda:mis_pedidos")}"'
        )

    def test_el_menu_no_suma_queries(self):
        for habilitada in (False, True):
            with self.subTest(tienda=habilitada):
                self.gimnasio.tienda_habilitada = habilitada
                self.gimnasio.save()
                r = self.client.get(reverse("home"))
                req = r.wsgi_request

                with self.assertNumQueries(0):
                    html = render_to_string(
                        "partials/nav_alumno.html",
                        {"user": req.user, "request": req},
                        request=req,
                    )

                self.assertIn(NAV, html)
                self.assertEqual(reverse("tienda:catalogo") in html, habilitada)


class NavAlumnoNoAparecePara(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio()
        self.alumno, self.usuario_alumno = _alumno(self.gimnasio)
        self.staff = _staff(self.gimnasio)

    def test_el_staff_no_lo_ve_y_conserva_el_suyo(self):
        self.client.force_login(self.staff)
        respuesta = self.client.get(reverse("home"))

        self.assertNotContains(respuesta, NAV)
        self.assertNotContains(respuesta, HAMBURGUESA)
        self.assertContains(respuesta, 'class="nav-staff"')
        self.assertContains(respuesta, "Abrir menú")

    def test_el_anonimo_no_lo_ve(self):
        respuesta = self.client.get(reverse("login"))

        self.assertNotContains(respuesta, NAV)

    def test_suplantando_se_ve(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("suplantar", args=[self.alumno.pk]))
        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "banner-suplantacion")
        self.assertContains(respuesta, NAV)
        self.assertContains(respuesta, HAMBURGUESA)
        self.assertNotContains(respuesta, 'class="nav-staff"')

    def _congelar(self, estado):
        self.gimnasio.estado_cuenta = estado
        self.gimnasio.estado_cuenta_desde = timezone.now()
        self.gimnasio.save()

    def test_sin_acceso_no_aparecen_ni_menu_ni_hamburguesa(self):
        for estado in (
            Gimnasio.EstadoCuenta.SUSPENDIDA,
            Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
        ):
            with self.subTest(estado=estado):
                self._congelar(estado)
                self.client.force_login(self.usuario_alumno)
                respuesta = self.client.get(reverse("home"))

                self.assertTemplateUsed(
                    respuesta, "plataforma/cuenta_bloqueada.html"
                )
                self.assertNotContains(respuesta, NAV)
                self.assertNotContains(respuesta, HAMBURGUESA)

    def test_sin_acceso_suplantando_no_aparecen_ni_menu_ni_hamburguesa(self):
        for estado in (
            Gimnasio.EstadoCuenta.SUSPENDIDA,
            Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
        ):
            with self.subTest(estado=estado):
                self._congelar(Gimnasio.EstadoCuenta.NORMAL)
                self.client.force_login(self.staff)
                self.client.post(reverse("suplantar", args=[self.alumno.pk]))
                self._congelar(estado)
                respuesta = self.client.get(reverse("home"))

                self.assertContains(respuesta, "banner-suplantacion")
                self.assertNotContains(respuesta, NAV)
                self.assertNotContains(respuesta, HAMBURGUESA)
                self.client.logout()
