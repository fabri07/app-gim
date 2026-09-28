"""Tests de la API de la puerta (`POST /ingreso/v1/eventos/` y
`GET /ingreso/v1/padron/`).

Lo que se fija acá: la autenticación por token (y que una sesión de staff no
alcanza), la idempotencia del lote, que el lote se decide en orden cronológico
y se responde en el orden del request, que el `momento` que manda el lector se
valida (sin zona, en el futuro, demasiado viejo) y que nada de lo que mande un
lector mal configurado termina en un 500.
"""

import json
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import decision, limites, services, tokens
from ingreso.models import (
    ConfiguracionIngreso,
    Credencial,
    Dispositivo,
    EventoIngreso,
    obtener_configuracion,
)
from pagos.models import Cuota
from pagos.testing import crear_cuota
from tenants.models import (
    ESTADOS_SIN_ACCESO_ALUMNO,
    ESTADOS_SIN_ACCESO_STAFF,
    Gimnasio,
    Perfil,
)
from turnos.models import Reserva


class _BaseApi(TestCase):
    def setUp(self):
        # El freno por IP vive en el cache (`LocMemCache`, por proceso), que
        # sobrevive entre tests: sin esto, los 401 de toda la suite se suman
        # contra 127.0.0.1 y un test cualquiera recibe un 429 que no buscó.
        cache.clear()
        self.addCleanup(cache.clear)
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

    def _post(self, cuerpo, token=None, **extra):
        cabeceras = dict(extra)
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


class _BasePadron(_BaseApi):
    """El padrón: lo que el lector guarda para abrir sin internet."""

    def setUp(self):
        super().setUp()
        # `self.url` sigue siendo la de eventos: varios tests comparan contra
        # lo que la puerta online responde.
        self.url_padron = reverse("ingreso:api_padron")

    def _get(self, token=None, **extra):
        cabeceras = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(self.url_padron, **cabeceras, **extra)

    def _padron(self):
        respuesta = self._get(token=self.token)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.json()

    def _fila(self, alumno, datos=None):
        datos = datos or self._padron()
        filas = [f for f in datos["alumnos"] if f["id"] == alumno.pk]
        self.assertLessEqual(len(filas), 1)
        return filas[0] if filas else None

    def _prender_deuda(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=5, fecha_activacion_bloqueo=date(2026, 1, 1)
        )
        ConfiguracionIngreso.objects.filter(
            pk=obtener_configuracion(self.gimnasio).pk
        ).update(bloquea_por_deuda=True)

    def _deber(self, alumno):
        crear_cuota(
            gimnasio=self.gimnasio, alumno=alumno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO,
        )


class PadronAutenticacionTests(_BasePadron):
    def test_sin_token_da_401(self):
        self.assertEqual(self._get().status_code, 401)

    def test_con_un_token_alterado_da_401(self):
        self.assertEqual(self._get(token=self.token + "x").status_code, 401)

    def test_un_lector_desactivado_da_403(self):
        Dispositivo.objects.filter(pk=self.dispositivo.pk).update(activo=False)

        self.assertEqual(self._get(token=self.token).status_code, 403)

    def test_una_sesion_de_staff_no_alcanza(self):
        staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-a", password="clave-123456")

        self.assertEqual(self._get().status_code, 401)

    def test_solo_acepta_get(self):
        respuesta = self.client.post(
            self.url_padron, HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )

        self.assertEqual(respuesta.status_code, 405)

    def test_el_ultimo_contacto_se_actualiza_sin_tocar_modificado(self):
        """El poll del padrón es el latido del lector."""
        modificado_antes = self.dispositivo.modificado

        self._get(token=self.token)

        self.dispositivo.refresh_from_db()
        self.assertIsNotNone(self.dispositivo.ultimo_contacto)
        self.assertEqual(self.dispositivo.modificado, modificado_antes)

    def test_un_304_tambien_cuenta_como_latido(self):
        etag = self._get(token=self.token)["ETag"]
        Dispositivo.objects.filter(pk=self.dispositivo.pk).update(
            ultimo_contacto=None
        )

        self._get(token=self.token, HTTP_IF_NONE_MATCH=etag)

        self.dispositivo.refresh_from_db()
        self.assertIsNotNone(self.dispositivo.ultimo_contacto)


class PadronContenidoTests(_BasePadron):
    def test_trae_al_alumno_con_su_credencial(self):
        datos = self._padron()

        self.assertEqual(datos["alumnos"], [{
            "id": self.alumno.pk,
            "nombre": "Juan P.",
            "habilitado": True,
            "motivo": None,
            "qr": self.qr.valor,
            "id_facial": None,
        }])

    def test_el_nombre_es_el_mismo_que_muestra_la_puerta_online(self):
        """Online y offline la pantallita tiene que decir lo mismo: sale de
        `services.nombre_corto`, igual que la respuesta de eventos."""
        online = self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.assertEqual(
            self._fila(self.alumno)["nombre"],
            online.json()["resultados"][0]["alumno"],
        )

    def test_la_cara_y_el_qr_del_mismo_alumno_van_en_una_sola_fila(self):
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )

        datos = self._padron()

        self.assertEqual(len(datos["alumnos"]), 1)
        self.assertEqual(datos["alumnos"][0]["qr"], self.qr.valor)
        self.assertEqual(datos["alumnos"][0]["id_facial"], "42")

    def test_un_alumno_solo_con_cara_trae_qr_nulo(self):
        ana = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez"
        )
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=ana,
            tipo=Credencial.Tipo.FACIAL, valor="7",
        )

        fila = self._fila(ana)

        self.assertIsNone(fila["qr"])
        self.assertEqual(fila["id_facial"], "7")

    def test_declara_las_reglas_que_no_puede_aplicar_offline(self):
        """El equipo con el padrón cacheado no puede exigir reserva ni aplicar
        antipassback, y la app lo dice en vez de prometerlo."""
        datos = self._padron()

        self.assertIn("reserva", datos["reglas_no_incluidas"])
        self.assertIn("antipassback", datos["reglas_no_incluidas"])

    def test_los_textos_de_la_pantalla_van_una_sola_vez(self):
        """Los mismos de la puerta online (`decision.MENSAJES`/`SALUDO`), uno
        por motivo y no uno por fila. Una credencial que no está en el padrón
        muestra lo que online da SIN_CREDENCIAL."""
        self.assertEqual(self._padron()["mensajes"], {
            "permitido": decision.SALUDO,
            "deuda": decision.MENSAJES[EventoIngreso.Resultado.DEUDA],
            "sin_credencial":
                decision.MENSAJES[EventoIngreso.Resultado.SIN_CREDENCIAL],
        })

    def test_el_saludo_del_puente_es_el_de_la_puerta_online(self):
        """El puente completa la plantilla con el `nombre` de la fila; con el
        nombre de pila, da exactamente lo que responde la puerta online."""
        online = self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.assertEqual(
            self._padron()["mensajes"]["permitido"].format(nombre="Juan"),
            online.json()["resultados"][0]["mensaje"],
        )

    def test_una_credencial_dada_de_baja_no_viaja(self):
        """Una llave revocada no se reparte: offline no está, así que no abre."""
        Credencial.objects.filter(pk=self.qr.pk).update(activa=False)

        datos = self._padron()

        self.assertEqual(datos["alumnos"], [])
        self.assertNotIn(self.qr.valor, json.dumps(datos))

    def test_la_cara_de_baja_no_viaja_pero_el_qr_activo_si(self):
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42", activa=False,
        )

        fila = self._fila(self.alumno)

        self.assertEqual(fila["qr"], self.qr.valor)
        self.assertIsNone(fila["id_facial"])

    def test_el_alumno_dado_de_baja_no_viaja(self):
        """Ni él ni sus llaves: offline es una credencial desconocida (niega
        igual que online) y el puente lo borra del equipo, cara incluida."""
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )
        Alumno.objects.filter(pk=self.alumno.pk).update(
            estado=Alumno.Estado.INACTIVO
        )

        respuesta = self._get(token=self.token)

        self.assertEqual(respuesta.json()["alumnos"], [])
        self.assertNotIn(self.qr.valor, respuesta.content.decode())

    def test_el_deudor_viaja_como_no_habilitado(self):
        self._prender_deuda()
        self._deber(self.alumno)

        fila = self._fila(self.alumno)

        # El deudor SÍ viaja: vuelve apenas paga, así que el puente lo
        # bloquea en el equipo en vez de borrarlo.
        self.assertFalse(fila["habilitado"])
        self.assertEqual(fila["motivo"], EventoIngreso.Resultado.DEUDA)
        self.assertEqual(fila["qr"], self.qr.valor)

    def test_la_deuda_no_cuenta_si_el_gimnasio_no_la_pidio_en_la_puerta(self):
        """Misma regla que online: con `bloquea_por_deuda` apagado el que
        debe entra, aunque el portal lo tenga bloqueado."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=5, fecha_activacion_bloqueo=date(2026, 1, 1)
        )
        self._deber(self.alumno)

        self.assertTrue(self._fila(self.alumno)["habilitado"])

    def test_el_dado_de_baja_que_ademas_debe_tampoco_viaja(self):
        """Online la baja gana sobre la deuda (ALUMNO_INACTIVO). Offline no
        hay orden que respetar: el dado de baja no viaja, deba o no, así que
        no puede aparecer como deudor bloqueable en el equipo."""
        self._prender_deuda()
        self._deber(self.alumno)
        Alumno.objects.filter(pk=self.alumno.pk).update(
            estado=Alumno.Estado.INACTIVO
        )

        self.assertIsNone(self._fila(self.alumno))

    def test_la_reserva_y_el_antipassback_no_deshabilitan(self):
        """Son momentáneas: dependen de la hora de la lectura. Precomputadas
        acá, dejarían afuera a todo el que no reservó para la hora del poll."""
        ConfiguracionIngreso.objects.filter(
            pk=obtener_configuracion(self.gimnasio).pk
        ).update(exige_reserva=True, minutos_antipassback=30)
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            alumno=self.alumno, credencial=self.qr,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=timezone.now() - timedelta(minutes=1),
        )

        self.assertTrue(self._fila(self.alumno)["habilitado"])


class PadronFechaLocalTests(_BasePadron):
    """La deuda se mide contra `timezone.localdate()`. Entre las 21:00 y las
    23:59 de Buenos Aires la fecha UTC ya es la de mañana, y con
    `timezone.now().date()` el corte se adelantaría un día: el alumno que
    todavía está dentro de la tolerancia quedaría afuera esta noche. Reloj
    congelado en una fecha LEJANA a hoy, para que el test no pase o falle por
    la fecha real."""

    def test_de_noche_la_deuda_se_corta_con_la_fecha_local(self):
        self._prender_deuda()  # tolerancia 5 días
        # En UTC, como lo devuelve `timezone.now()` de verdad: con la zona de
        # Buenos Aires puesta, `.date()` ya daría la fecha local y el test
        # pasaría con el bug (se verificó mutando).
        ahora = datetime(2031, 3, 11, 1, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(
            timezone.localtime(ahora).date(), date(2031, 3, 10)
        )  # 22:30 del 10/03 en Buenos Aires
        # Local: hoy 10/03, corta lo que arrancó hasta el 05/03.
        # UTC: hoy 11/03, cortaría hasta el 06/03.
        en_el_borde = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Borde", apellido="X"
        )
        services.generar_credencial_qr(en_el_borde)
        crear_cuota(
            gimnasio=self.gimnasio, alumno=en_el_borde,
            inicio=date(2031, 3, 6), estado=Cuota.Estado.PENDIENTE,
        )
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=date(2031, 3, 5), estado=Cuota.Estado.PENDIENTE,
        )
        self.gimnasio.refresh_from_db()

        with patch("django.utils.timezone.now", return_value=ahora):
            datos = services.padron_de(self.gimnasio)

        habilitado = {f["id"]: f["habilitado"] for f in datos["alumnos"]}
        self.assertTrue(habilitado[en_el_borde.pk])
        # Y el control: el que arrancó un día antes sí queda cortado.
        self.assertFalse(habilitado[self.alumno.pk])


class PadronCoherenteConLaPuertaTests(_BasePadron):
    """Lo que el lector offline abre con el padrón es exactamente lo que la
    puerta online abriría, salvo reserva y antipassback. Se arma un gimnasio
    con un alumno en cada rama del árbol y se le pregunta a los DOS."""

    def test_offline_abre_lo_mismo_que_online(self):
        self._prender_deuda()
        ConfiguracionIngreso.objects.filter(
            pk=obtener_configuracion(self.gimnasio).pk
        ).update(minutos_antipassback=0)

        def alumno(nombre, **extra):
            return Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=nombre, apellido="X", **extra
            )

        credenciales = [self.qr]
        credenciales.append(Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="1",
        ))
        deudor = alumno("Deudor")
        self._deber(deudor)
        credenciales.append(services.generar_credencial_qr(deudor))
        baja = alumno("Baja", estado=Alumno.Estado.INACTIVO)
        credenciales.append(services.generar_credencial_qr(baja))
        revocada = services.generar_credencial_qr(alumno("Revocada"))
        Credencial.objects.filter(pk=revocada.pk).update(activa=False)
        credenciales.append(revocada)
        cara_revocada = Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=alumno("CaraRevocada"),
            tipo=Credencial.Tipo.FACIAL, valor="2", activa=False,
        )
        credenciales.append(cara_revocada)

        datos = self._padron()
        abre_offline = {}
        for fila in datos["alumnos"]:
            for tipo, clave in (("qr", "qr"), ("facial", "id_facial")):
                if fila[clave] is not None:
                    abre_offline[(tipo, fila[clave])] = fila["habilitado"]

        for credencial in credenciales:
            online = self._post_eventos(
                {"tipo": credencial.tipo, "valor": credencial.valor}
            ).json()["resultados"][0]
            with self.subTest(alumno=credencial.alumno.nombre, tipo=credencial.tipo):
                self.assertEqual(
                    abre_offline.get((credencial.tipo, credencial.valor), False),
                    online["permitido"],
                )
        # Y que el caso no sea trivial: hay de las dos.
        self.assertIn(True, abre_offline.values())
        self.assertIn(False, abre_offline.values())
        # El dado de baja y las dos credenciales revocadas no viajan: offline
        # son desconocidas, igual que una credencial de otro gimnasio.
        for credencial in (credenciales[3], revocada, cara_revocada):
            with self.subTest(ausente=credencial.alumno.nombre):
                self.assertNotIn(
                    (credencial.tipo, credencial.valor), abre_offline
                )


class PadronAislamientoTests(_BasePadron):
    def setUp(self):
        super().setUp()
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Ana", apellido="Gomez"
        )
        self.qr_ajeno = services.generar_credencial_qr(self.ajeno)
        # El mismo id facial en los dos gimnasios: la unicidad es por
        # gimnasio, así que es legal y es justo el caso que confundiría un
        # filtro por valor sin gimnasio.
        for gimnasio, alumno in (
            (self.gimnasio, self.alumno), (self.otro, self.ajeno)
        ):
            Credencial.objects.create(
                gimnasio=gimnasio, alumno=alumno,
                tipo=Credencial.Tipo.FACIAL, valor="42",
            )

    def test_el_padron_no_trae_alumnos_ni_credenciales_del_otro(self):
        respuesta = self._get(token=self.token)
        datos = respuesta.json()

        self.assertEqual([f["id"] for f in datos["alumnos"]], [self.alumno.pk])
        self.assertNotIn(self.qr_ajeno.valor, respuesta.content.decode())
        self.assertNotIn("Ana", respuesta.content.decode())

    def test_el_token_del_otro_gimnasio_trae_SU_padron(self):
        token_ajeno = tokens.generar()
        Dispositivo.objects.create(
            gimnasio=self.otro, nombre="Puerta B",
            token_hash=tokens.hash_de(token_ajeno),
        )

        datos = self._get(token=token_ajeno).json()

        self.assertEqual([f["id"] for f in datos["alumnos"]], [self.ajeno.pk])
        self.assertEqual(datos["alumnos"][0]["qr"], self.qr_ajeno.valor)

    def test_la_deuda_del_otro_gimnasio_no_toca_este_padron(self):
        """`bloqueados_entre` recibe el gimnasio: una cuota impaga de otro
        tenant no deshabilita a nadie acá."""
        self._prender_deuda()
        crear_cuota(
            gimnasio=self.otro, alumno=self.ajeno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO,
        )

        self.assertTrue(self._fila(self.alumno)["habilitado"])


class PadronDatosPersonalesTests(_BasePadron):
    """El padrón queda escrito en la memoria de un equipo de terceros: lleva
    las llaves (es su razón de ser), si puede pasar y por qué no, y nada más.
    `motivo: "deuda"` deja en el equipo QUIÉN debe, a sabiendas (online la
    pantalla ya se lo dice a quien pasa); lo que no deja es cuánto ni nada
    de su ficha."""

    def test_no_lleva_datos_personales_ni_montos(self):
        Alumno.objects.filter(pk=self.alumno.pk).update(
            apellido="Zabaleta-Unico",
            email="juan.secreto@correo-privado.com",
            telefono="+5491155550199",
            fecha_nacimiento=date(1987, 3, 14),
            observaciones="nota-interna-del-staff",
            discapacidad_detalle="detalle-de-salud-1",
            enfermedad_cronica_detalle="detalle-de-salud-2",
            deportes_practica="deporte-privado",
        )
        self._prender_deuda()
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO, monto=98765,
        )

        respuesta = self._get(token=self.token)
        cuerpo = respuesta.content.decode()

        for dato in (
            "Zabaleta", "juan.secreto", "5550199", "1987", "nota-interna",
            "detalle-de-salud", "deporte-privado", "98765",
            self.dispositivo.token_hash, self.token,
        ):
            with self.subTest(dato=dato):
                self.assertNotIn(dato, cuerpo)
        self.assertEqual(set(respuesta.json()), {
            "version", "reglas_no_incluidas", "mensajes", "alumnos",
        })
        fila = respuesta.json()["alumnos"][0]
        self.assertEqual(set(fila), {
            "id", "nombre", "habilitado", "motivo", "qr", "id_facial",
        })
        self.assertEqual(fila["nombre"], "Juan Z.")
        # Lo que sí queda en el equipo, dicho a sabiendas.
        self.assertEqual(fila["motivo"], EventoIngreso.Resultado.DEUDA)

    def test_los_campos_personales_ni_se_leen_de_la_base(self):
        """Más fuerte que "no están en el JSON": el `.only()` hace que la
        query ni los traiga, así que un cambio futuro que los agregue a la
        fila no los encuentra a mano."""
        with CaptureQueriesContext(connection) as ctx:
            services.padron_de(self.gimnasio)

        sql = " ".join(q["sql"] for q in ctx.captured_queries)
        self.assertIn('"alumnos_alumno"."nombre"', sql)
        for columna in (
            "email", "telefono", "fecha_nacimiento", "observaciones",
            "discapacidad_detalle", "enfermedad_cronica_detalle",
        ):
            with self.subTest(columna=columna):
                self.assertNotIn(f'"alumnos_alumno"."{columna}"', sql)


class PadronCacheTests(_BasePadron):
    def test_el_etag_es_la_version_entre_comillas(self):
        respuesta = self._get(token=self.token)

        self.assertEqual(respuesta["ETag"], f'"{respuesta.json()["version"]}"')

    def test_un_padron_sin_cambios_responde_304(self):
        primera = self._get(token=self.token)

        segunda = self._get(token=self.token, HTTP_IF_NONE_MATCH=primera["ETag"])

        self.assertEqual(segunda.status_code, 304)
        self.assertEqual(segunda.content, b"")
        self.assertEqual(segunda["ETag"], primera["ETag"])

    def test_el_etag_debilitado_por_un_proxy_tambien_da_304(self):
        """Cloudflare pasa a `W/` el ETag de una respuesta que comprime: el
        puente lo devuelve así y tiene que seguir valiendo."""
        etag = self._get(token=self.token)["ETag"]

        segunda = self._get(token=self.token, HTTP_IF_NONE_MATCH=f"W/{etag}")

        self.assertEqual(segunda.status_code, 304)

    def test_la_version_sin_comillas_tambien_da_304(self):
        """Un puente que guarda `version` del cuerpo y la manda pelada."""
        version = self._padron()["version"]

        segunda = self._get(token=self.token, HTTP_IF_NONE_MATCH=version)

        self.assertEqual(segunda.status_code, 304)

    def test_un_etag_viejo_trae_el_padron_nuevo(self):
        etag = self._get(token=self.token)["ETag"]
        # Atrasarse con la cuota cambia el padrón sin tocar ninguna credencial.
        self._prender_deuda()
        self._deber(self.alumno)

        segunda = self._get(token=self.token, HTTP_IF_NONE_MATCH=etag)

        self.assertEqual(segunda.status_code, 200)
        self.assertFalse(segunda.json()["alumnos"][0]["habilitado"])
        self.assertNotEqual(segunda["ETag"], etag)

    def test_la_version_cambia_con_lo_que_el_lector_guarda(self):
        v1 = self._padron()["version"]
        services.generar_credencial_qr(self.alumno, regenerar=True)

        self.assertNotEqual(self._padron()["version"], v1)

    def test_la_version_es_estable_si_nada_cambio(self):
        self.assertEqual(self._padron()["version"], self._padron()["version"])

    def test_no_queda_en_caches_intermedios(self):
        """El padrón son las llaves de la puerta y Cloudflare proxea el sitio:
        ni el 200 ni el 304 pueden quedar guardados en el edge."""
        primera = self._get(token=self.token)
        segunda = self._get(token=self.token, HTTP_IF_NONE_MATCH=primera["ETag"])

        for respuesta in (primera, segunda):
            with self.subTest(status=respuesta.status_code):
                self.assertIn("no-store", respuesta["Cache-Control"])
                self.assertIn("private", respuesta["Cache-Control"])


class PuertaConLaCuentaCongeladaTests(_BasePadron):
    """Que un gimnasio le deba la app al dueño del producto NO le cierra la
    puerta a sus alumnos: dejar ochenta personas afuera de un edificio no es
    una palanca de cobro. Lo que se corta es el panel, no el molinete.

    No agrega código: fija una invariante para que nadie "ordene"
    `plataforma/middleware.py` (o le sume un chequeo de `estado_cuenta` a la
    API) y le corte la puerta a un gimnasio congelado. Cubre los DOS endpoints
    —eventos es la puerta online; el padrón, la offline: si dejara de
    refrescarse, la puerta abriría hoy y dejaría de abrir mañana— y los DOS
    estados congelados, porque son dos reglas distintas del middleware
    (`ESTADOS_SIN_ACCESO_ALUMNO` y `ESTADOS_SIN_ACCESO_STAFF`).

    **El cliente NO se loguea, y eso es parte del test.** El corte del
    middleware es `if usuario is None or not usuario.is_authenticated: return
    None`: con una sesión el usuario estaría autenticado, el middleware
    seguiría hasta `sin_acceso(perfil)` y el test fallaría por el motivo
    equivocado. El lector real no tiene cookie; el test tampoco, y lo verifica.
    """

    ESTADOS_CONGELADOS = (
        Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS,
        Gimnasio.EstadoCuenta.SUSPENDIDA,
    )

    def _congelar(self, estado):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=estado, estado_cuenta_desde=timezone.now()
        )

    def _assert_la_puerta_responde_como_siempre(self, respuesta):
        self.assertTemplateNotUsed(respuesta, "plataforma/cuenta_bloqueada.html")
        # Ni la sesión ni el registro del día: el middleware no tocó al
        # lector. Una cookie en la respuesta es la señal de que empezó a
        # mirarlo.
        self.assertEqual(dict(respuesta.cookies), {})
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)

    def test_los_estados_congelados_son_los_que_corta_el_middleware(self):
        """Si alguien suma un tercer estado congelado, esta clase tiene que
        cubrirlo: la lista de arriba no puede quedar vieja en silencio."""
        self.assertEqual(
            set(self.ESTADOS_CONGELADOS),
            set(ESTADOS_SIN_ACCESO_ALUMNO | ESTADOS_SIN_ACCESO_STAFF),
        )

    def test_el_estado_de_verdad_le_corta_la_app_al_alumno(self):
        """Control positivo: sin esto, los tests de la puerta pasarían igual
        si el estado no congelara nada (un fixture que no congela). El mismo
        gimnasio, con un alumno logueado desde el navegador, ve el cartel."""
        usuario = User.objects.create_user("alumno-a", password="clave-123456")
        Perfil.objects.create(
            usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        navegador = Client()
        navegador.force_login(usuario)
        for estado in self.ESTADOS_CONGELADOS:
            with self.subTest(estado=estado):
                self._congelar(estado)

                respuesta = navegador.get(reverse("home"))

                self.assertTemplateUsed(
                    respuesta, "plataforma/cuenta_bloqueada.html"
                )

    def test_la_puerta_online_sigue_abriendo(self):
        for n, estado in enumerate(self.ESTADOS_CONGELADOS):
            with self.subTest(estado=estado):
                self._congelar(estado)
                # Un alumno por estado: con el mismo, el antipassback (2 min
                # por defecto) negaría el segundo ingreso por otro motivo.
                alumno = Alumno.objects.create(
                    gimnasio=self.gimnasio, nombre=f"Ana{n}", apellido="Gomez"
                )
                qr = services.generar_credencial_qr(alumno)

                respuesta = self._post_eventos({"tipo": "qr", "valor": qr.valor})

                # Primero lo que diría QUÉ se rompió: el cartel (HTML) haría
                # explotar el `.json()` de abajo con un mensaje que no lo dice.
                self._assert_la_puerta_responde_como_siempre(respuesta)
                self.assertEqual(respuesta.status_code, 200)
                resultado = respuesta.json()["resultados"][0]
                self.assertTrue(resultado["permitido"])
                self.assertEqual(resultado["resultado"], "permitido")
                self.assertEqual(
                    EventoIngreso.objects.get(alumno=alumno).resultado,
                    EventoIngreso.Resultado.PERMITIDO,
                )

    def test_el_padron_sigue_respondiendo(self):
        for estado in self.ESTADOS_CONGELADOS:
            with self.subTest(estado=estado):
                self._congelar(estado)

                respuesta = self._get(token=self.token)

                self._assert_la_puerta_responde_como_siempre(respuesta)
                self.assertEqual(respuesta.status_code, 200)
                self.assertTrue(self._fila(self.alumno, respuesta.json())["habilitado"])


class PadronCostoTests(_BasePadron):
    """El costo no crece con el padrón. Crece lo que de verdad multiplica el
    costo: alumnos DISTINTOS, con QR y cara, repartidos entre habilitados,
    deudores y dados de baja, con la regla de deuda prendida. `habilitado` se
    anota con `bloqueados_entre` (una query), nunca con `bloqueo_de` por
    fila."""

    def setUp(self):
        super().setUp()
        self._prender_deuda()

    def _poblar(self, por_rama, prefijo):
        for rama in ("al_dia", "deudor", "baja"):
            for i in range(por_rama):
                alumno = Alumno.objects.create(
                    gimnasio=self.gimnasio, nombre=f"{rama}{prefijo}{i}",
                    apellido="X",
                    estado=(
                        Alumno.Estado.INACTIVO if rama == "baja"
                        else Alumno.Estado.ACTIVO
                    ),
                )
                services.generar_credencial_qr(alumno)
                Credencial.objects.create(
                    gimnasio=self.gimnasio, alumno=alumno,
                    tipo=Credencial.Tipo.FACIAL, valor=f"{prefijo}-{rama}-{i}",
                )
                if rama == "deudor":
                    self._deber(alumno)

    def _medir(self):
        with CaptureQueriesContext(connection) as ctx:
            respuesta = self._get(token=self.token)
        self.assertEqual(respuesta.status_code, 200)
        return len(ctx), respuesta.json()["alumnos"]

    def _motivos(self, filas):
        conteo = {}
        for fila in filas:
            conteo[fila["motivo"]] = conteo.get(fila["motivo"], 0) + 1
        return conteo

    def test_el_costo_no_crece_con_la_cantidad_de_alumnos(self):
        self._medir()  # costos de primera vez (la fila de configuración)
        self._poblar(2, "chico")
        chico, filas_chico = self._medir()
        self._poblar(13, "grande")
        grande, filas_grande = self._medir()

        # Sin esto, un padrón vacío por un filtro roto pasaría el test. Los
        # dados de baja crecen igual (el filtro también cuesta) y no viajan.
        self.assertEqual(self._motivos(filas_chico), {
            None: 3, EventoIngreso.Resultado.DEUDA: 2,
        })
        self.assertEqual(self._motivos(filas_grande), {
            None: 16, EventoIngreso.Resultado.DEUDA: 15,
        })
        self.assertEqual(sum(1 for f in filas_grande if f["qr"]), 31)
        self.assertEqual(sum(1 for f in filas_grande if f["id_facial"]), 30)
        self.assertEqual(grande, chico)


class FrenoPorIpTests(_BasePadron):
    """El camino de FALLO (sin token o con uno que no matchea) no es gratis:
    pasadas `MAX_FALLOS_POR_MINUTO` fallas desde una IP, el resto de la
    ventana responde 429 sin tocar la base.

    Y la otra mitad, que pesa más: **un lector legítimo no recibe nunca este
    429**. Una lectura online frenada es una puerta cerrada, y la IP de un
    gimnasio es compartida (NAT) con cualquier cosa que esté mal configurada
    en el mismo edificio.
    """

    def _fallar(self, veces, **extra):
        for _ in range(veces):
            respuesta = self._post({"eventos": []}, token="invalido", **extra)
            self.assertEqual(respuesta.status_code, 401)

    def test_muchos_intentos_fallidos_desde_la_misma_ip_dan_429(self):
        self._fallar(limites.MAX_FALLOS_POR_MINUTO)

        respuesta = self._post({"eventos": []}, token="invalido")

        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.json()["motivo"], "intentos_fallidos")
        self.assertEqual(
            respuesta["Retry-After"], str(limites.SEGUNDOS_VENTANA_FALLOS)
        )

    def test_sin_token_tambien_cuenta_como_fallo(self):
        for _ in range(limites.MAX_FALLOS_POR_MINUTO):
            self.assertEqual(self._post({"eventos": []}).status_code, 401)

        self.assertEqual(self._post({"eventos": []}).status_code, 429)

    def test_el_freno_cubre_tambien_el_padron(self):
        """Vive en `_lector_del_request`: las fallas contra eventos frenan el
        padrón y al revés, porque es el mismo atacante probando llaves."""
        self._fallar(limites.MAX_FALLOS_POR_MINUTO // 2)
        for _ in range(limites.MAX_FALLOS_POR_MINUTO // 2):
            self.assertEqual(self._get(token="invalido").status_code, 401)

        self.assertEqual(self._get(token="invalido").status_code, 429)
        self.assertEqual(
            self._post({"eventos": []}, token="invalido").status_code, 429
        )

    def test_con_la_ip_frenada_un_token_invalido_no_llega_a_la_base(self):
        """Es el punto del freno: el atacante sin llave no cuesta ni la query
        del token."""
        self._fallar(limites.MAX_FALLOS_POR_MINUTO)

        with self.assertNumQueries(0):
            respuesta = self._post({"eventos": []}, token="invalido")

        self.assertEqual(respuesta.status_code, 429)

    def test_pasada_la_ventana_vuelve_a_responder_401(self):
        ahora = 2_000_000_000.0
        with patch("time.time", return_value=ahora):
            self._fallar(limites.MAX_FALLOS_POR_MINUTO)
            self.assertEqual(
                self._post({"eventos": []}, token="invalido").status_code, 429
            )
        with patch(
            "time.time",
            return_value=ahora + limites.SEGUNDOS_VENTANA_FALLOS + 1,
        ):
            respuesta = self._post({"eventos": []}, token="invalido")

        self.assertEqual(respuesta.status_code, 401)

    def test_el_limite_no_frena_al_lector_legitimo(self):
        """Un gimnasio a las 19:00 es legítimamente a ráfagas: el límite es del
        camino de FALLO, no del autenticado."""
        for _ in range(limites.MAX_FALLOS_POR_MINUTO + 5):
            respuesta = self._post({"eventos": []}, token=self.token)
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(self._get(token=self.token).status_code, 200)

    def test_el_lector_legitimo_sigue_abriendo_con_su_ip_frenada(self):
        """Un puente viejo con el token rotado, en la misma red del gimnasio,
        martillando la API: frena su IP, que es la misma del molinete bueno.
        El bueno sigue abriendo."""
        self.assertEqual(self._get(token=self.token).status_code, 200)
        self._fallar(limites.MAX_FALLOS_POR_MINUTO)
        self.assertEqual(
            self._post({"eventos": []}, token="invalido").status_code, 429
        )

        respuesta = self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()["resultados"][0]["permitido"])
        self.assertEqual(self._get(token=self.token).status_code, 200)

    def test_se_cuenta_por_ip_de_cloudflare_y_no_por_x_forwarded_for(self):
        """`X-Forwarded-For` lo manda el cliente y lo puede rotar para saltear
        el límite. Mismo criterio que `solicitudes/antispam.py::ip_de`."""
        self._fallar(
            limites.MAX_FALLOS_POR_MINUTO,
            HTTP_CF_CONNECTING_IP="1.2.3.4",
            HTTP_X_FORWARDED_FOR="8.8.8.8",
        )

        respuesta = self._post(
            {"eventos": []}, token="invalido",
            HTTP_CF_CONNECTING_IP="1.2.3.4",
            HTTP_X_FORWARDED_FOR="9.9.9.9",
        )

        self.assertEqual(respuesta.status_code, 429)

    def test_el_freno_de_una_ip_no_alcanza_a_otra(self):
        self._fallar(
            limites.MAX_FALLOS_POR_MINUTO, HTTP_CF_CONNECTING_IP="1.2.3.4"
        )

        respuesta = self._post(
            {"eventos": []}, token="invalido", HTTP_CF_CONNECTING_IP="5.6.7.8"
        )

        self.assertEqual(respuesta.status_code, 401)

    def test_un_lector_desactivado_no_suma_fallos(self):
        """El 403 solo se alcanza con un token VÁLIDO: no es alguien probando
        llaves, es un lector apagado que sigue polleando. Si contara, frenaría
        la IP del gimnasio justo cuando instalan el lector nuevo, que todavía
        no es conocido y se comería el 429."""
        Dispositivo.objects.filter(pk=self.dispositivo.pk).update(activo=False)
        for _ in range(limites.MAX_FALLOS_POR_MINUTO + 5):
            self.assertEqual(self._get(token=self.token).status_code, 403)

        token_nuevo = tokens.generar()
        Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta nueva",
            token_hash=tokens.hash_de(token_nuevo),
        )

        self.assertEqual(self._get(token=token_nuevo).status_code, 200)


class TopeDiarioTests(_BasePadron):
    """El respaldo durable del freno: un tope de eventos NUEVOS por lector y
    por día, contado en la base (`Dispositivo.eventos_hoy`), que sobrevive a un
    reinicio y a `--workers 2`. Contra un token robado, o un puente que reenvía
    la misma lectura en bucle, pone un techo a lo que se escribe en
    `EventoIngreso`.

    El tope se parchea: llegar a las 20.000 filas reales sería medio minuto de
    suite por un test que prueba una comparación.
    """

    def _llenar(self, dispositivo, cantidad, fecha=None):
        Dispositivo.objects.filter(pk=dispositivo.pk).update(
            eventos_hoy=cantidad,
            eventos_hoy_fecha=fecha or timezone.localdate(),
        )

    def _leidos(self, dispositivo):
        dispositivo.refresh_from_db()
        return dispositivo.eventos_hoy

    def _lote(self, *ids):
        return {"eventos": [
            {"tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": i}
            for i in ids
        ]}

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_un_lector_pasado_de_tope_recibe_un_429_claro(self):
        self._llenar(self.dispositivo, 3)

        respuesta = self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.json()["motivo"], "tope_diario")
        self.assertIn("3", respuesta.json()["error"])
        self.assertTrue(0 < int(respuesta["Retry-After"]) <= 24 * 60 * 60)
        self.assertFalse(EventoIngreso.objects.exists())

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_el_tope_diario_no_corta_el_padron(self):
        """Invariante de la Tarea 9: si el lector no pudiera refrescar su
        padrón, la puerta abriría hoy y dejaría de abrir mañana. El padrón no
        escribe nada, así que no hay nada que acotar."""
        self._llenar(self.dispositivo, 3)

        respuesta = self._get(token=self.token)

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(self._fila(self.alumno, respuesta.json())["habilitado"])

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_cuenta_los_eventos_nuevos_hasta_el_tope(self):
        self.assertEqual(self._post(self._lote("a", "b"), token=self.token).status_code, 200)
        self.assertEqual(self._leidos(self.dispositivo), 2)
        self.assertEqual(self._post(self._lote("c"), token=self.token).status_code, 200)
        self.assertEqual(self._leidos(self.dispositivo), 3)

        respuesta = self._post(self._lote("d"), token=self.token)

        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(EventoIngreso.objects.count(), 3)

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_los_reintentos_idempotentes_no_gastan_el_tope(self):
        """Un puente que perdió la respuesta reintenta el MISMO lote con los
        mismos ids, y está bien que lo haga (es el contrato). Si eso gastara
        cupo, una conexión inestable le cerraría la puerta a un gimnasio sin
        que haya entrado nadie de más."""
        for _ in range(5):
            respuesta = self._post(self._lote("a", "b"), token=self.token)
            self.assertEqual(respuesta.status_code, 200)

        self.assertEqual(self._leidos(self.dispositivo), 2)
        self.assertEqual(EventoIngreso.objects.count(), 2)
        self.assertEqual(self._post(self._lote("c"), token=self.token).status_code, 200)

    def test_un_evento_invalido_no_gasta_el_tope(self):
        """No se registra: no hay nada que acotar."""
        self._post_eventos({"tipo": "rfid", "valor": "x"})

        self.assertEqual(self._leidos(self.dispositivo), 0)

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_el_tope_de_un_lector_no_afecta_a_otro(self):
        """Un gimnasio con dos puertas no se traba entero porque una se pasó."""
        token_b = tokens.generar()
        puerta_b = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta B",
            token_hash=tokens.hash_de(token_b),
        )
        self._llenar(self.dispositivo, 3)

        respuesta = self._post(
            {"eventos": [{"tipo": "qr", "valor": self.qr.valor}]}, token=token_b
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()["resultados"][0]["permitido"])
        self.assertEqual(self._leidos(puerta_b), 1)
        self.assertEqual(self._leidos(self.dispositivo), 3)
        self.assertEqual(
            self._post_eventos({"tipo": "qr", "valor": self.qr.valor}).status_code,
            429,
        )

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_el_contador_arranca_de_cero_cada_dia(self):
        """Si arrastrara el historial, un lector con dos años de uso quedaría
        trabado para siempre."""
        self._llenar(
            self.dispositivo, 3, fecha=timezone.localdate() - timedelta(days=1)
        )

        respuesta = self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.assertEqual(respuesta.status_code, 200)
        self.dispositivo.refresh_from_db()
        self.assertEqual(self.dispositivo.eventos_hoy, 1)
        self.assertEqual(self.dispositivo.eventos_hoy_fecha, timezone.localdate())

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_el_dia_es_el_local_y_no_el_de_utc(self):
        """A las 23:30 de Buenos Aires la fecha UTC ya es la de mañana: con
        `now().date()` el tope se reiniciaría tres horas antes de medianoche.
        Reloj congelado en una fecha lejana, para que no pase por casualidad."""
        congelado = datetime(2031, 3, 10, 2, 30, tzinfo=dt_timezone.utc)
        with patch("django.utils.timezone.now", return_value=congelado):
            self._llenar(self.dispositivo, 3, fecha=date(2031, 3, 9))

            respuesta = self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.assertEqual(respuesta.status_code, 429)
        # Retry-After apunta a la medianoche LOCAL: faltan 30 minutos.
        self.assertEqual(int(respuesta["Retry-After"]), 30 * 60)

    def test_contar_no_toca_modificado(self):
        """`version_media` no deriva de acá, pero el criterio del proyecto es
        el mismo que el latido: una escritura de telemetría no es una edición."""
        modificado = self.dispositivo.modificado

        self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.dispositivo.refresh_from_db()
        self.assertEqual(self.dispositivo.modificado, modificado)
        self.assertEqual(self.dispositivo.eventos_hoy, 1)

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_el_tope_no_mira_el_estado_de_la_cuenta(self):
        """Invariante de la Tarea 9: con cupo, un gimnasio congelado abre
        igual. El freno es de volumen, no de cobranza."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA,
            estado_cuenta_desde=timezone.now(),
        )
        self._llenar(self.dispositivo, 2)

        respuesta = self._post_eventos({"tipo": "qr", "valor": self.qr.valor})

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()["resultados"][0]["permitido"])
