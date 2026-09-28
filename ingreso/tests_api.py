"""Tests de la API de la puerta (`POST /ingreso/v1/eventos/`).

Lo que se fija acá: la autenticación por token (y que una sesión de staff no
alcanza), la idempotencia del lote, que el lote se decide en orden cronológico
y se responde en el orden del request, que el `momento` que manda el lector se
valida (sin zona, en el futuro, demasiado viejo) y que nada de lo que mande un
lector mal configurado termina en un 500.
"""

import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import services, tokens
from ingreso.models import Dispositivo, EventoIngreso, obtener_configuracion
from tenants.models import Gimnasio, Perfil


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


class AutenticacionApiTests(_BaseApi):
    def test_sin_token_da_401(self):
        self.assertEqual(self._post({"eventos": []}).status_code, 401)

    def test_con_un_token_alterado_da_401(self):
        respuesta = self._post({"eventos": []}, token=self.token + "x")

        self.assertEqual(respuesta.status_code, 401)

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
        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor,
            "abierto_por_el_equipo": True, "id_evento_equipo": "1",
        })

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(EventoIngreso.objects.get().en_diferido)

    def test_el_mismo_id_del_equipo_no_duplica_el_ingreso(self):
        evento = {"tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": "8821"}

        primera = self._post_eventos(evento)
        segunda = self._post_eventos(evento)

        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(EventoIngreso.objects.count(), 1)
        # El reintento devuelve el veredicto GUARDADO, no uno nuevo: decidido
        # de nuevo, el antipassback lo daría por duplicado.
        self.assertEqual(
            segunda.json()["resultados"][0]["resultado"],
            primera.json()["resultados"][0]["resultado"],
        )
        self.assertTrue(segunda.json()["resultados"][0]["permitido"])
        self.assertEqual(segunda.json()["resultados"][0]["id_evento_equipo"], "8821")

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

    def test_una_lectura_con_espacios_del_lector_abre(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": f"  {self.qr.valor}\r\n"}
        )

        self.assertTrue(respuesta.json()["resultados"][0]["permitido"])

    def test_un_lote_vacio_responde_sin_resultados(self):
        respuesta = self._post_eventos()

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), {"resultados": []})


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

    def _evento(self, minutos, id_equipo):
        return {
            "tipo": "qr", "valor": self.qr.valor,
            "momento": (self.base + timedelta(minutes=minutos)).isoformat(),
            "id_evento_equipo": id_equipo, "abierto_por_el_equipo": True,
        }

    def test_un_lote_desordenado_se_decide_en_orden_cronologico(self):
        # 10:10, 10:00, 10:20 con 15 minutos. En orden: 10:00 pasa, 10:10 es
        # duplicado, 10:20 pasa (20 minutos de 10:00). Recorrido como llegó,
        # 10:10 pasaba y los otros dos quedaban como duplicados.
        respuesta = self._post_eventos(
            self._evento(10, "b"), self._evento(0, "a"), self._evento(20, "c"),
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


class LecturaInvalidaTests(_BaseApi):
    """Nada que mande un lector mal configurado termina en un 500 (Review
    Focus 2 y 5), y un 400 no deja nada registrado a medias."""

    def assertRechazado(self, respuesta, *fragmentos):
        self.assertEqual(respuesta.status_code, 400)
        error = respuesta.json()["error"]
        for fragmento in fragmentos:
            self.assertIn(fragmento, error)
        self.assertEqual(EventoIngreso.objects.count(), 0)

    def test_un_tipo_desconocido_da_400_y_no_un_500(self):
        """Review Focus 5: un lector que manda "rfid"."""
        respuesta = self._post_eventos({"tipo": "rfid", "valor": "x"})

        self.assertRechazado(respuesta, "rfid")

    def test_el_error_dice_cual_evento_del_lote_fallo(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor},
            {"tipo": "rfid", "valor": "x"},
        )

        self.assertRechazado(respuesta, "evento 2")

    def test_un_sentido_desconocido_da_400(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "sentido": "adentro"}
        )

        self.assertRechazado(respuesta, "adentro")

    def test_un_momento_sin_zona_horaria_se_toma_como_hora_local(self):
        """Review Focus 2: comparar un naive contra un aware explota."""
        local = timezone.localtime(timezone.now() - timedelta(hours=2))
        naive = local.replace(tzinfo=None, microsecond=0)

        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor, "momento": naive.isoformat(),
        })

        self.assertEqual(respuesta.status_code, 200)
        momento = EventoIngreso.objects.get().momento
        self.assertIsNotNone(momento.tzinfo)
        # Hora de Argentina, no UTC: con UTC quedaría corrido tres horas.
        self.assertEqual(momento, local.replace(microsecond=0))

    def test_un_momento_en_el_futuro_se_rechaza(self):
        """Review Focus 2: el lector con el reloj mal ensuciaría el
        antipassback y los reportes para siempre."""
        futuro = (timezone.now() + timedelta(days=2)).isoformat()

        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "momento": futuro}
        )

        self.assertRechazado(respuesta, "futuro")

    def test_un_momento_apenas_adelantado_se_acepta(self):
        """Un reloj unos minutos adelantado es lo normal, no un error."""
        adelantado = (
            timezone.now()
            + timedelta(hours=services.HORAS_DE_ADELANTO_TOLERADAS)
            - timedelta(minutes=5)
        ).isoformat()

        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "momento": adelantado}
        )

        self.assertEqual(respuesta.status_code, 200)

    def test_un_momento_del_anio_2000_se_rechaza(self):
        """Un lector con el reloj reseteado. Aceptado, envenenaría los
        reportes históricos y el antipassback de ese día para siempre."""
        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor,
            "momento": "2000-01-01T00:00:05-03:00",
        })

        self.assertRechazado(respuesta, "reloj")

    def test_el_piso_de_antiguedad_tiene_borde(self):
        limite = timedelta(days=services.DIAS_DE_ANTIGUEDAD_TOLERADOS)
        adentro = (timezone.now() - limite + timedelta(minutes=5)).isoformat()
        afuera = (timezone.now() - limite - timedelta(minutes=5)).isoformat()

        respuesta_adentro = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "momento": adentro}
        )
        self.assertEqual(respuesta_adentro.status_code, 200)
        EventoIngreso.objects.all().delete()

        respuesta_afuera = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "momento": afuera}
        )
        self.assertRechazado(respuesta_afuera, "viejo")

    def test_un_momento_ilegible_da_400(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "momento": "ayer"}
        )

        self.assertRechazado(respuesta, "momento")

    def test_una_fecha_imposible_da_400(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "momento": "2026-13-45T10:00:00"}
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_un_momento_que_no_es_texto_da_400(self):
        respuesta = self._post_eventos(
            {"tipo": "qr", "valor": self.qr.valor, "momento": 1727470991}
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_un_valor_que_no_es_texto_da_400(self):
        respuesta = self._post_eventos({"tipo": "qr", "valor": 42})

        self.assertRechazado(respuesta, "valor")

    def test_un_evento_sin_valor_da_400(self):
        respuesta = self._post_eventos({"tipo": "qr"})

        self.assertRechazado(respuesta, "valor")

    def test_un_evento_que_no_es_un_objeto_da_400(self):
        respuesta = self._post_eventos("qr")

        self.assertEqual(respuesta.status_code, 400)

    def test_un_id_del_equipo_demasiado_largo_da_400(self):
        """Postgres rechaza el `varchar(64)` en el `INSERT` y SQLite no: sin
        esto, un 500 que la suite no ve."""
        respuesta = self._post_eventos({
            "tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": "9" * 65,
        })

        self.assertRechazado(respuesta, "id_evento_equipo")

    def test_eventos_que_no_es_una_lista_da_400(self):
        respuesta = self._post({"eventos": {"tipo": "qr"}}, token=self.token)

        self.assertEqual(respuesta.status_code, 400)

    def test_un_cuerpo_sin_eventos_da_400(self):
        respuesta = self._post({"lecturas": []}, token=self.token)

        self.assertRechazado(respuesta, "eventos")

    def test_un_cuerpo_que_es_una_lista_da_400(self):
        respuesta = self._post([1, 2], token=self.token)

        self.assertEqual(respuesta.status_code, 400)

    def test_un_lote_sobre_el_tope_da_400(self):
        eventos = [
            {"tipo": "qr", "valor": self.qr.valor}
            for _ in range(services.MAX_EVENTOS_POR_LOTE + 1)
        ]

        respuesta = self._post({"eventos": eventos}, token=self.token)

        self.assertRechazado(respuesta, str(services.MAX_EVENTOS_POR_LOTE))

    def test_un_cuerpo_que_no_es_json_da_400(self):
        respuesta = self.client.post(
            self.url, data="no soy json", content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_un_cuerpo_con_bytes_invalidos_da_400(self):
        respuesta = self.client.post(
            self.url, data=b"\xff\xfe{", content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(respuesta.status_code, 400)


class CostoDelLoteTests(_BaseApi):
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

    def _medir(self, eventos):
        with CaptureQueriesContext(connection) as ctx:
            respuesta = self._post({"eventos": eventos}, token=self.token)
        self.assertEqual(respuesta.status_code, 200)
        resultados = respuesta.json()["resultados"]
        self.assertEqual(len(resultados), len(eventos))
        self.assertTrue(all(r["permitido"] for r in resultados))
        return len(ctx)

    def test_el_costo_no_crece_con_el_tamano_del_lote(self):
        self._medir(self._lote(1, "frio"))  # descarta costos de primera vez
        chico = self._medir(self._lote(5, "chico"))
        grande = self._medir(self._lote(40, "grande"))

        self.assertEqual(grande, chico)

    def test_el_reintento_de_un_lote_tampoco_crece(self):
        """El veredicto guardado lleva el nombre del alumno: sin el
        `select_related`, cada evento ya registrado es una query más."""
        chico, grande = self._lote(5, "chico"), self._lote(40, "grande")
        self._medir(chico)
        self._medir(grande)

        self.assertEqual(self._medir(grande), self._medir(chico))
        self.assertEqual(EventoIngreso.objects.count(), 45)
