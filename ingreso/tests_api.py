"""Tests de la API de la puerta (`POST /ingreso/v1/eventos/`).

Lo que se fija acá: la autenticación por token (y que una sesión de staff no
alcanza), la idempotencia del lote, que el lote se decide en orden cronológico
y se responde en el orden del request, que el `momento` que manda el lector se
valida (sin zona, en el futuro, demasiado viejo) y que nada de lo que mande un
lector mal configurado termina en un 500.
"""

import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import services, tokens
from ingreso.models import (
    ConfiguracionIngreso,
    Dispositivo,
    EventoIngreso,
    obtener_configuracion,
)
from pagos.models import Cuota
from pagos.testing import crear_cuota
from tenants.models import Gimnasio, Perfil
from turnos.models import Reserva


class _BaseApi(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.token = tokens.generar()
        self.dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de(self.token),
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )
        self.qr = services.generar_credencial_qr(self.alumno)
        self.url = reverse("ingreso:api_eventos")

    def _post(self, cuerpo, token=None):
        cabeceras = {}
        if token is not None:
            cabeceras["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(
            self.url, data=json.dumps(cuerpo),
            content_type="application/json", **cabeceras
        )

    def _post_eventos(self, *eventos):
        return self._post({"eventos": list(eventos)}, token=self.token)

    def _diferido(self, momento=None, id_equipo="d1", **extra):
        """Un evento en diferido: el equipo ya abrió, así que trae su id y la
        hora en que abrió."""
        momento = momento or timezone.now() - timedelta(minutes=30)
        return {
            "tipo": "qr", "valor": self.qr.valor,
            "abierto_por_el_equipo": True, "id_evento_equipo": id_equipo,
            "momento": momento if isinstance(momento, str) else momento.isoformat(),
            **extra,
        }


class AutenticacionApiTests(_BaseApi):
    def test_sin_token_da_401(self):
        self.assertEqual(self._post({"eventos": []}).status_code, 401)

    def test_con_un_token_alterado_da_401(self):
        respuesta = self._post({"eventos": []}, token=self.token + "x")

        self.assertEqual(respuesta.status_code, 401)

    def test_el_esquema_bearer_no_distingue_mayusculas(self):
        """RFC 9110: el esquema de `Authorization` es case-insensitive, y hay
        firmwares que mandan `bearer`."""
        respuesta = self.client.post(
            self.url, data=json.dumps({"eventos": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"bearer {self.token}",
        )

        self.assertEqual(respuesta.status_code, 200)

    def test_un_header_sin_bearer_da_401(self):
        respuesta = self.client.post(
            self.url, data=json.dumps({"eventos": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.token,
        )

        self.assertEqual(respuesta.status_code, 401)

    def test_una_sesion_de_staff_no_alcanza(self):
        """La vista ignora `request.user` por completo: es lo que hace seguro
        el `csrf_exempt`."""
        staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-a", password="clave-123456")

        self.assertEqual(self._post({"eventos": []}).status_code, 401)

    def test_un_lector_desactivado_da_403(self):
        Dispositivo.objects.filter(pk=self.dispositivo.pk).update(activo=False)

        respuesta = self._post({"eventos": []}, token=self.token)

        self.assertEqual(respuesta.status_code, 403)

    def test_el_token_identifica_a_SU_lector_y_no_a_otro(self):
        """El token de un lector de otro gimnasio autentica como ESE lector:
        el QR de este gimnasio ahí no es nadie, y el evento queda a su nombre."""
        otro = Gimnasio.objects.create(nombre="B", slug="b")
        token_ajeno = tokens.generar()
        ajeno = Dispositivo.objects.create(
            gimnasio=otro, nombre="Puerta B", token_hash=tokens.hash_de(token_ajeno),
        )

        respuesta = self._post(
            {"eventos": [{"tipo": "qr", "valor": self.qr.valor}]},
            token=token_ajeno,
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta.json()["resultados"][0]["resultado"],
            EventoIngreso.Resultado.SIN_CREDENCIAL,
        )
        evento = EventoIngreso.objects.get()
        self.assertEqual(evento.dispositivo, ajeno)
        self.assertEqual(evento.gimnasio, otro)

    def test_csrf_no_lo_bloquea(self):
        """Sin sesión ni cookie no hay nada que CSRF pueda proteger, y con el
        middleware prendido el endpoint sería imposible de llamar."""
        cliente = Client(enforce_csrf_checks=True)

        respuesta = cliente.post(
            self.url, data=json.dumps({"eventos": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(respuesta.status_code, 200)

    def test_solo_acepta_post(self):
        respuesta = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )

        self.assertEqual(respuesta.status_code, 405)

    def test_el_ultimo_contacto_se_actualiza_sin_tocar_modificado(self):
        modificado_antes = self.dispositivo.modificado

        self._post({"eventos": []}, token=self.token)

        self.dispositivo.refresh_from_db()
        self.assertIsNotNone(self.dispositivo.ultimo_contacto)
        self.assertEqual(self.dispositivo.modificado, modificado_antes)


class EventosApiTests(_BaseApi):
    def test_una_lectura_valida_abre_y_queda_registrada(self):
        respuesta = self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.assertEqual(respuesta.status_code, 200)
        resultado = respuesta.json()["resultados"][0]
        self.assertTrue(resultado["permitido"])
        self.assertEqual(resultado["resultado"], "permitido")
        self.assertIn("Juan", resultado["mensaje"])
        # Nombre corto, como muestra la spec: el apellido completo no le
        # agrega nada a la pantalla del molinete.
        self.assertEqual(resultado["alumno"], "Juan P.")
        evento = EventoIngreso.objects.get()
        self.assertEqual(evento.alumno, self.alumno)
        self.assertEqual(evento.dispositivo, self.dispositivo)
        self.assertFalse(evento.en_diferido)

    def test_una_credencial_desconocida_guarda_solo_la_cola_del_valor(self):
        respuesta = self._post_eventos({"tipo": "qr", "valor": "no-existo-123456"})

        self.assertFalse(respuesta.json()["resultados"][0]["permitido"])
        evento = EventoIngreso.objects.get()
        self.assertEqual(evento.resultado, EventoIngreso.Resultado.SIN_CREDENCIAL)
        self.assertEqual(evento.detalle, "…123456")

    def test_un_lote_en_diferido_queda_marcado(self):
        """El equipo ya abrió con su padrón cacheado. Se registra igual qué
        habría opinado la app, así el reporte no miente."""
        momento = (timezone.now() - timedelta(hours=2)).replace(microsecond=0)

        respuesta = self._post_eventos(self._diferido(momento))

        self.assertEqual(respuesta.status_code, 200)
        evento = EventoIngreso.objects.get()
        self.assertTrue(evento.en_diferido)
        # En diferido se registra la hora del equipo, no la del upload.
        self.assertEqual(evento.momento, momento)

    def test_el_mismo_id_del_equipo_no_duplica_el_ingreso(self):
        evento = {"tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": "8821"}

        primera = self._post_eventos(evento)
        segunda = self._post_eventos(evento)

        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(EventoIngreso.objects.count(), 1)
        # El reintento devuelve el veredicto GUARDADO, no uno nuevo: decidido
        # de nuevo, el antipassback lo daría por duplicado.
        self.assertEqual(
            segunda.json()["resultados"][0],
            primera.json()["resultados"][0],
        )
        self.assertTrue(segunda.json()["resultados"][0]["permitido"])
        self.assertEqual(segunda.json()["resultados"][0]["id_evento_equipo"], "8821")

    def test_el_reintento_reconstruye_el_mensaje_de_un_rechazo(self):
        """M3: el mensaje del reintento sale del resultado guardado; vacío,
        la pantalla del molinete no le dice nada al que está esperando."""
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save()
        evento = {"tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": "5"}

        primera = self._post_eventos(evento).json()["resultados"][0]
        segunda = self._post_eventos(evento).json()["resultados"][0]

        self.assertEqual(primera["resultado"], "alumno_inactivo")
        self.assertEqual(segunda["mensaje"], primera["mensaje"])
        self.assertTrue(segunda["mensaje"])

    def test_el_mismo_id_repetido_dentro_del_lote_se_registra_una_vez(self):
        evento = {"tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": "7"}

        respuesta = self._post_eventos(evento, dict(evento))

        resultados = respuesta.json()["resultados"]
        self.assertEqual(len(resultados), 2)
        self.assertEqual(resultados[0], resultados[1])
        self.assertTrue(resultados[0]["permitido"])
        self.assertEqual(EventoIngreso.objects.count(), 1)

    def test_el_mismo_id_en_OTRO_lector_es_otro_evento(self):
        otro_token = tokens.generar()
        Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta 2",
            token_hash=tokens.hash_de(otro_token),
        )
        evento = {"tipo": "qr", "valor": "desconocido", "id_evento_equipo": "1"}

        self._post({"eventos": [evento]}, token=self.token)
        self._post({"eventos": [evento]}, token=otro_token)

        self.assertEqual(EventoIngreso.objects.count(), 2)

    def test_el_reintento_online_sin_id_da_duplicado(self):
        """Riesgo aceptado (spec, «Riesgos aceptados»): un lector online que no
        recibió la respuesta por un timeout y reintenta SIN `id_evento_equipo`
        registra una segunda lectura, que el antipassback da por DUPLICADO, y
        la puerta no abre. El contrato pide reusar el mismo id al reintentar:
        con id, el reintento devuelve el veredicto guardado (el test de arriba).
        Este test fija el comportamiento sin id para que nadie lo cambie sin
        darse cuenta."""
        evento = {"tipo": "qr", "valor": self.qr.valor}

        self._post_eventos(evento)
        segunda = self._post_eventos(evento).json()["resultados"][0]

        self.assertEqual(segunda["resultado"], EventoIngreso.Resultado.DUPLICADO)
        self.assertFalse(segunda["permitido"])
        self.assertEqual(EventoIngreso.objects.count(), 2)

    def test_una_lectura_con_espacios_del_lector_abre(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": f"  {self.qr.valor}\r\n"}
        )

        self.assertTrue(respuesta.json()["resultados"][0]["permitido"])

    def test_un_lote_vacio_responde_sin_resultados(self):
        respuesta = self._post_eventos()

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), {"resultados": []})


class NombreCortoTests(TestCase):
    def test_nombre_e_inicial_del_apellido(self):
        alumno = Alumno(nombre="Juan", apellido="perez")

        self.assertEqual(services.nombre_corto(alumno), "Juan P.")

    def test_sin_apellido_queda_el_nombre(self):
        self.assertEqual(
            services.nombre_corto(Alumno(nombre="Juan", apellido="")), "Juan"
        )
        self.assertEqual(
            services.nombre_corto(Alumno(nombre=" Juan ", apellido="  ")), "Juan"
        )

    def test_sin_alumno_es_none(self):
        self.assertIsNone(services.nombre_corto(None))


class RelojEnModoOnlineTests(_BaseApi):
    """I1: en online el equipo espera el veredicto para abrir, así que el
    ingreso ES ahora y manda el reloj del servidor. El `momento` que manda el
    lector solo deja un diagnóstico en `detalle`."""

    def test_un_lector_con_la_hora_en_utc_abre_igual(self):
        # Hora naive en UTC: tres horas adelantada respecto de Argentina.
        utc_naive = timezone.now().astimezone(
            timezone.get_fixed_timezone(0)
        ).replace(tzinfo=None)

        antes = timezone.now()
        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor,
            "momento": utc_naive.isoformat(),
        })
        despues = timezone.now()

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta.json()["resultados"][0]["resultado"],
            EventoIngreso.Resultado.PERMITIDO,
        )
        evento = EventoIngreso.objects.get()
        self.assertTrue(antes <= evento.momento <= despues)
        self.assertIn("adelantado 3 h", evento.detalle)

    def test_un_lector_con_el_reloj_en_el_2000_abre_igual(self):
        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor,
            "momento": "2000-01-01T00:00:05-03:00",
        })

        self.assertTrue(respuesta.json()["resultados"][0]["permitido"])
        evento = EventoIngreso.objects.get()
        self.assertGreater(evento.momento, timezone.now() - timedelta(minutes=1))
        self.assertIn("atrasado", evento.detalle)

    def test_un_reloj_casi_en_hora_no_deja_nota(self):
        self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor,
            "momento": (timezone.now() - timedelta(seconds=20)).isoformat(),
        })

        self.assertEqual(EventoIngreso.objects.get().detalle, "")

    def test_un_momento_ilegible_en_online_abre_y_queda_anotado(self):
        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor,
            "momento": "2026-13-45T10:00:00",
        })

        self.assertTrue(respuesta.json()["resultados"][0]["permitido"])
        self.assertIn(
            "reloj del lector: momento ilegible", EventoIngreso.objects.get().detalle
        )

    def test_la_cola_de_la_credencial_gana_el_lugar_en_el_detalle(self):
        """Si no entran las dos cosas, la credencial desconocida es el
        diagnóstico que importa; el desfase se recorta."""
        self._post_eventos({
            "tipo": "qr", "valor": "no-existo-123456",
            "momento": "2000-01-01T00:00:05-03:00",
        })

        detalle = EventoIngreso.objects.get().detalle
        self.assertTrue(detalle.startswith("…123456"))
        self.assertIn("atrasado", detalle)
        self.assertLessEqual(
            len(detalle),
            EventoIngreso._meta.get_field("detalle").max_length,
        )


class OrdenDelLoteTests(_BaseApi):
    """M1 de la revisión de la Tarea 6: el antipassback dentro del lote
    depende del orden en que se recorren las lecturas. Un lote en diferido no
    tiene por qué llegar ordenado, y el veredicto no puede depender de eso."""

    def setUp(self):
        super().setUp()
        config = obtener_configuracion(self.gimnasio)
        config.minutos_antipassback = 15
        config.save()
        # Bien en el pasado y lejos del borde de antigüedad.
        self.base = (timezone.now() - timedelta(hours=3)).replace(
            second=0, microsecond=0
        )

    def test_un_lote_desordenado_se_decide_en_orden_cronologico(self):
        # 10:10, 10:00, 10:20 con 15 minutos. En orden: 10:00 pasa, 10:10 es
        # duplicado, 10:20 pasa (20 minutos de 10:00). Recorrido como llegó,
        # 10:10 pasaba y los otros dos quedaban como duplicados.
        respuesta = self._post_eventos(
            self._diferido(self.base + timedelta(minutes=10), "b"),
            self._diferido(self.base, "a"),
            self._diferido(self.base + timedelta(minutes=20), "c"),
        )

        self.assertEqual(respuesta.status_code, 200)
        resultados = respuesta.json()["resultados"]
        # Las respuestas vuelven en el ORDEN DEL REQUEST: el lector las
        # correlaciona por posición o por id.
        self.assertEqual(
            [r["id_evento_equipo"] for r in resultados], ["b", "a", "c"]
        )
        self.assertEqual(
            [r["resultado"] for r in resultados],
            [
                EventoIngreso.Resultado.DUPLICADO,
                EventoIngreso.Resultado.PERMITIDO,
                EventoIngreso.Resultado.PERMITIDO,
            ],
        )
        self.assertEqual(
            EventoIngreso.objects.filter(
                resultado=EventoIngreso.Resultado.PERMITIDO
            ).count(),
            2,
        )


class EventoInvalidoTests(_BaseApi):
    """I2: un evento mal armado no se registra y ocupa su posición en la
    respuesta; el resto del lote sigue. Nada de eso es un 500 (Review Focus 2
    y 5)."""

    def assertInvalido(self, respuesta, *fragmentos, id_equipo=None):
        self.assertEqual(respuesta.status_code, 200)
        resultado = respuesta.json()["resultados"][0]
        self.assertFalse(resultado["permitido"])
        self.assertEqual(resultado["resultado"], services.RESULTADO_INVALIDO)
        self.assertEqual(resultado["id_evento_equipo"], id_equipo)
        for fragmento in fragmentos:
            self.assertIn(fragmento, resultado["error"])
        self.assertEqual(EventoIngreso.objects.count(), 0)

    def test_un_evento_invalido_en_el_medio_no_tumba_el_lote(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": "1"},
            {"tipo": "rfid", "valor": "x", "id_evento_equipo": "2"},
            {"tipo": "qr", "valor": "desconocido", "id_evento_equipo": "3"},
        )

        self.assertEqual(respuesta.status_code, 200)
        resultados = respuesta.json()["resultados"]
        self.assertEqual(
            [r["id_evento_equipo"] for r in resultados], ["1", "2", "3"]
        )
        self.assertEqual(
            [r["resultado"] for r in resultados],
            [
                EventoIngreso.Resultado.PERMITIDO,
                services.RESULTADO_INVALIDO,
                EventoIngreso.Resultado.SIN_CREDENCIAL,
            ],
        )
        self.assertIn("rfid", resultados[1]["error"])
        self.assertEqual(
            sorted(EventoIngreso.objects.values_list("id_evento_equipo", flat=True)),
            ["1", "3"],
        )

    def test_un_lote_todo_invalido_da_200_sin_filas(self):
        respuesta = self._post_eventos(
            {"tipo": "rfid", "valor": "x"},
            {"tipo": "qr", "valor": 42},
            "no soy un objeto",
        )

        self.assertEqual(respuesta.status_code, 200)
        resultados = respuesta.json()["resultados"]
        self.assertEqual(len(resultados), 3)
        self.assertTrue(
            all(r["resultado"] == services.RESULTADO_INVALIDO for r in resultados)
        )
        self.assertEqual(EventoIngreso.objects.count(), 0)

    def test_un_tipo_desconocido_es_invalido_y_no_un_500(self):
        """Review Focus 5: un lector que manda "rfid"."""
        respuesta = self._post_eventos(
            {"tipo": "rfid", "valor": "x", "id_evento_equipo": 88}
        )

        # El id vuelve tal como vino.
        self.assertInvalido(respuesta, "rfid", id_equipo=88)

    def test_un_sentido_desconocido_es_invalido(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "sentido": "adentro"}
        )

        self.assertInvalido(respuesta, "adentro")

    def test_abierto_por_el_equipo_tiene_que_ser_booleano(self):
        """M4: el string "false" es verdadero en Python; pasado por `bool()`
        mandaba a diferido un evento online."""
        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor,
            "abierto_por_el_equipo": "false", "id_evento_equipo": "4",
        })

        self.assertInvalido(respuesta, "abierto_por_el_equipo", id_equipo="4")

    def test_en_diferido_el_id_del_equipo_es_obligatorio(self):
        """I3: sin id, reintentar el upload de un backlog duplicaría cada
        ingreso."""
        evento = self._diferido()
        del evento["id_evento_equipo"]

        respuesta = self._post_eventos(evento)

        self.assertInvalido(respuesta, "id_evento_equipo", "duplicaría")

    def test_en_diferido_el_momento_es_obligatorio(self):
        evento = self._diferido()
        del evento["momento"]

        respuesta = self._post_eventos(evento)

        self.assertInvalido(respuesta, "momento", id_equipo="d1")

    def test_un_momento_sin_zona_en_diferido_se_toma_como_hora_local(self):
        """Review Focus 2: comparar un naive contra un aware explota."""
        local = timezone.localtime(timezone.now() - timedelta(hours=2))
        naive = local.replace(tzinfo=None, microsecond=0)

        respuesta = self._post_eventos(self._diferido(naive.isoformat()))

        self.assertEqual(respuesta.status_code, 200)
        momento = EventoIngreso.objects.get().momento
        self.assertIsNotNone(momento.tzinfo)
        # Hora de Argentina, no UTC: con UTC quedaría corrido tres horas.
        self.assertEqual(momento, local.replace(microsecond=0))

    def test_un_momento_sin_hora_es_invalido(self):
        """M6: «2026-09-28» se parsea como medianoche desde Python 3.11."""
        respuesta = self._post_eventos(self._diferido("2026-09-28"))

        self.assertInvalido(respuesta, "sin hora", id_equipo="d1")

    def test_un_momento_en_el_futuro_en_diferido_es_invalido(self):
        """Review Focus 2: el lector con el reloj mal ensuciaría el
        antipassback y los reportes para siempre."""
        respuesta = self._post_eventos(
            self._diferido(timezone.now() + timedelta(days=2))
        )

        self.assertInvalido(respuesta, "futuro", id_equipo="d1")

    def test_un_momento_apenas_adelantado_se_acepta(self):
        """Un reloj unos minutos adelantado es lo normal, no un error."""
        adelantado = (
            timezone.now()
            + timedelta(hours=services.HORAS_DE_ADELANTO_TOLERADAS)
            - timedelta(minutes=5)
        )

        respuesta = self._post_eventos(self._diferido(adelantado))

        self.assertEqual(
            respuesta.json()["resultados"][0]["resultado"],
            EventoIngreso.Resultado.PERMITIDO,
        )

    def test_un_momento_del_anio_2000_en_diferido_es_invalido(self):
        """Un lector con el reloj reseteado. Aceptado, envenenaría los
        reportes históricos y el antipassback de ese día para siempre."""
        respuesta = self._post_eventos(
            self._diferido("2000-01-01T00:00:05-03:00")
        )

        self.assertInvalido(respuesta, "reloj", id_equipo="d1")

    def test_el_piso_de_antiguedad_tiene_borde(self):
        limite = timedelta(days=services.DIAS_DE_ANTIGUEDAD_TOLERADOS)

        adentro = self._post_eventos(
            self._diferido(timezone.now() - limite + timedelta(minutes=5), "x")
        )
        self.assertEqual(
            adentro.json()["resultados"][0]["resultado"],
            EventoIngreso.Resultado.PERMITIDO,
        )
        EventoIngreso.objects.all().delete()

        afuera = self._post_eventos(
            self._diferido(timezone.now() - limite - timedelta(minutes=5))
        )
        self.assertInvalido(afuera, "viejo", id_equipo="d1")

    def test_un_momento_ilegible_en_diferido_es_invalido(self):
        self.assertInvalido(
            self._post_eventos(self._diferido("ayer")), "momento", id_equipo="d1"
        )

    def test_una_fecha_imposible_es_invalida(self):
        self.assertInvalido(
            self._post_eventos(self._diferido("2026-13-45T10:00:00")),
            "momento", id_equipo="d1",
        )

    def test_un_momento_que_no_es_texto_es_invalido(self):
        self.assertInvalido(
            self._post_eventos(self._diferido("x")), "momento", id_equipo="d1",
        )
        evento = self._diferido()
        evento["momento"] = 1727470991
        self.assertInvalido(
            self._post_eventos(evento), "momento", id_equipo="d1"
        )

    def test_un_valor_que_no_es_texto_es_invalido(self):
        self.assertInvalido(
            self._post_eventos({"tipo": "qr", "valor": 42}), "valor"
        )

    def test_un_evento_sin_valor_es_invalido(self):
        self.assertInvalido(self._post_eventos({"tipo": "qr"}), "valor")

    def test_un_evento_que_no_es_un_objeto_es_invalido(self):
        self.assertInvalido(self._post_eventos("qr"), "objeto")

    def test_un_id_del_equipo_demasiado_largo_es_invalido(self):
        """Postgres rechaza el `varchar(64)` en el `INSERT` y SQLite no: sin
        esto, un 500 que la suite no ve."""
        largo = "9" * 65
        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": largo,
        })

        self.assertInvalido(respuesta, "id_evento_equipo", id_equipo=largo)


class CuerpoInvalidoTests(_BaseApi):
    """Los errores de FORMA del request entero siguen siendo 400."""

    def assertRechazado(self, respuesta, *fragmentos):
        self.assertEqual(respuesta.status_code, 400)
        for fragmento in fragmentos:
            self.assertIn(fragmento, respuesta.json()["error"])
        self.assertEqual(EventoIngreso.objects.count(), 0)

    def _post_crudo(self, datos):
        return self.client.post(
            self.url, data=datos, content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_eventos_que_no_es_una_lista_da_400(self):
        self.assertRechazado(
            self._post({"eventos": {"tipo": "qr"}}, token=self.token), "lista"
        )

    def test_un_cuerpo_sin_eventos_da_400(self):
        self.assertRechazado(
            self._post({"lecturas": []}, token=self.token), "eventos"
        )

    def test_un_cuerpo_que_es_una_lista_da_400(self):
        self.assertRechazado(self._post([1, 2], token=self.token))

    def test_un_lote_sobre_el_tope_da_400(self):
        eventos = [
            {"tipo": "qr", "valor": self.qr.valor}
            for _ in range(services.MAX_EVENTOS_POR_LOTE + 1)
        ]

        self.assertRechazado(
            self._post({"eventos": eventos}, token=self.token),
            str(services.MAX_EVENTOS_POR_LOTE),
        )

    def test_un_cuerpo_que_no_es_json_da_400(self):
        self.assertRechazado(self._post_crudo("no soy json"))

    def test_un_cuerpo_con_bytes_invalidos_da_400(self):
        self.assertRechazado(self._post_crudo(b"\xff\xfe{"))

    def test_un_json_demasiado_anidado_da_400_y_no_un_500(self):
        """M1: `json.loads` agota la pila con miles de niveles."""
        self.assertRechazado(self._post_crudo("[" * 200_000), "anidado")


class _Escala(_BaseApi):
    def _medir(self, eventos):
        with CaptureQueriesContext(connection) as ctx:
            respuesta = self._post({"eventos": eventos}, token=self.token)
        self.assertEqual(respuesta.status_code, 200)
        resultados = respuesta.json()["resultados"]
        self.assertEqual(len(resultados), len(eventos))
        return len(ctx), resultados


class CostoDelLoteTests(_Escala):
    """El costo en queries no crece con el tamaño del lote. Crecen alumnos
    DISTINTOS, cada uno con su QR, y cada resultado tiene que ser un ingreso
    real: con la misma lectura repetida todo sería DUPLICADO y un N+1 por
    alumno pasaría igual."""

    def _lote(self, cantidad, prefijo):
        eventos = []
        for i in range(cantidad):
            alumno = Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"A{prefijo}{i}", apellido="X"
            )
            qr = services.generar_credencial_qr(alumno)
            eventos.append({
                "tipo": "qr", "valor": qr.valor,
                "id_evento_equipo": f"{prefijo}-{i}",
            })
        return eventos

    def _medir_permitidos(self, eventos):
        queries, resultados = self._medir(eventos)
        self.assertTrue(all(r["permitido"] for r in resultados))
        return queries

    def test_el_costo_no_crece_con_el_tamano_del_lote(self):
        self._medir_permitidos(self._lote(1, "frio"))  # costos de primera vez
        chico = self._medir_permitidos(self._lote(5, "chico"))
        grande = self._medir_permitidos(self._lote(40, "grande"))

        self.assertEqual(grande, chico)

    def test_el_reintento_de_un_lote_tampoco_crece(self):
        """El veredicto guardado lleva el nombre del alumno: sin el
        `select_related`, cada evento ya registrado es una query más."""
        chico, grande = self._lote(5, "chico"), self._lote(40, "grande")
        self._medir_permitidos(chico)
        self._medir_permitidos(grande)

        self.assertEqual(
            self._medir_permitidos(grande), self._medir_permitidos(chico)
        )
        self.assertEqual(EventoIngreso.objects.count(), 45)


class CostoConLasTresReglasTests(_Escala):
    """Lo mismo con deuda, reserva y antipassback prendidos, y alumnos que
    caen en CADA rama: si una sola rama hiciera una query por alumno, el
    total crecería."""

    def setUp(self):
        super().setUp()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=5, fecha_activacion_bloqueo=date(2026, 1, 1)
        )
        self.gimnasio.refresh_from_db()
        self.dispositivo.refresh_from_db()
        config = obtener_configuracion(self.gimnasio)
        ConfiguracionIngreso.objects.filter(pk=config.pk).update(
            bloquea_por_deuda=True, exige_reserva=True, minutos_antipassback=2
        )

    def _lote(self, por_rama, prefijo):
        ahora = timezone.now()
        local = timezone.localtime(ahora - timedelta(minutes=10))
        eventos = []
        for rama in ("deuda", "sin_reserva", "duplicado", "permitido"):
            for i in range(por_rama):
                alumno = Alumno.objects.create(
                    gimnasio=self.gimnasio, nombre=f"{rama}{prefijo}{i}",
                    apellido="X",
                )
                qr = services.generar_credencial_qr(alumno)
                if rama == "deuda":
                    crear_cuota(
                        gimnasio=self.gimnasio, alumno=alumno,
                        inicio=timezone.localdate() - timedelta(days=30),
                        estado=Cuota.Estado.VENCIDO,
                    )
                if rama != "sin_reserva":
                    Reserva.objects.create(
                        gimnasio=self.gimnasio, alumno=alumno,
                        fecha=local.date(),
                        hora_inicio=local.time().replace(microsecond=0),
                    )
                if rama == "duplicado":
                    EventoIngreso.objects.create(
                        gimnasio=self.gimnasio, dispositivo=self.dispositivo,
                        alumno=alumno, credencial=qr,
                        resultado=EventoIngreso.Resultado.PERMITIDO,
                        momento=ahora - timedelta(seconds=30),
                    )
                eventos.append({
                    "tipo": "qr", "valor": qr.valor,
                    "id_evento_equipo": f"{prefijo}-{rama}-{i}",
                })
        return eventos

    def _medir_ramas(self, eventos, por_rama):
        queries, resultados = self._medir(eventos)
        conteo = {}
        for r in resultados:
            conteo[r["resultado"]] = conteo.get(r["resultado"], 0) + 1
        self.assertEqual(conteo, {
            EventoIngreso.Resultado.DEUDA: por_rama,
            EventoIngreso.Resultado.SIN_RESERVA: por_rama,
            EventoIngreso.Resultado.DUPLICADO: por_rama,
            EventoIngreso.Resultado.PERMITIDO: por_rama,
        })
        return queries

    def test_el_costo_no_crece_con_las_tres_reglas_prendidas(self):
        self._medir_ramas(self._lote(1, "frio"), 1)
        chico = self._medir_ramas(self._lote(2, "chico"), 2)  # 8 eventos
        grande = self._medir_ramas(self._lote(10, "grande"), 10)  # 40

        self.assertEqual(grande, chico)
