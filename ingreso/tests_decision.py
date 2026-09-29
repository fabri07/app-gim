"""Tests del árbol de decisión de la puerta (`ingreso/decision.py`).

Un test por rama, en el orden del árbol, más los que fijan invariantes: el
aislamiento de tenant en la puerta, la normalización de lo que manda el
lector, el antipassback dentro del mismo lote y el costo fijo en queries.
"""

from datetime import date, timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from ingreso import decision, services, tokens
from ingreso.models import (
    ConfiguracionIngreso,
    Credencial,
    Dispositivo,
    EventoIngreso,
    obtener_configuracion,
)
from pagos.models import Cuota
from pagos.testing import crear_cuota
from tenants.models import Gimnasio
from turnos.models import Reserva


class DecisionTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="A", slug="a", dias_tolerancia_pago=5,
        )
        # La señal de `tenants` estampa la activación del bloqueo en la fecha
        # REAL de hoy al crear el gimnasio (pisando lo que se le pase): se
        # retrocede a mano, igual que en `pagos/tests.py`.
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            fecha_activacion_bloqueo=date(2026, 1, 1)
        )
        self.gimnasio.refresh_from_db()
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta",
            tipo=Dispositivo.Tipo.MIXTO, token_hash=tokens.hash_de("t"),
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )
        self.qr = services.generar_credencial_qr(self.alumno)
        self.config = obtener_configuracion(self.gimnasio)

    def _lectura(self, valor, momento=None, tipo="qr",
                 sentido=EventoIngreso.Sentido.ENTRADA):
        return decision.Lectura(
            tipo=tipo, valor=valor, sentido=sentido,
            momento=momento or timezone.now(),
        )

    def _decidir(self, valor, **kwargs):
        lectura = self._lectura(valor, **kwargs)
        contexto = decision.contexto_de(self.dispositivo, [lectura])
        return decision.decidir(lectura, contexto)

    def _evento(self, momento, sentido=EventoIngreso.Sentido.ENTRADA):
        return EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            alumno=self.alumno, credencial=self.qr,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=momento, sentido=sentido,
        )

    def _configurar(self, **campos):
        ConfiguracionIngreso.objects.filter(pk=self.config.pk).update(**campos)

    def test_una_credencial_que_no_existe_no_abre(self):
        resultado = self._decidir("no-existe")

        self.assertFalse(resultado.permitido)
        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.SIN_CREDENCIAL
        )
        self.assertIsNone(resultado.alumno)

    def test_el_qr_de_un_alumno_de_OTRO_gimnasio_no_abre(self):
        """Review Focus 1: aislamiento de tenant EN LA PUERTA. Un QR válido de
        otro gimnasio leído en este molinete no puede abrir."""
        ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Ana", apellido="G"
        )
        credencial_ajena = services.generar_credencial_qr(ajeno)

        resultado = self._decidir(credencial_ajena.valor)

        self.assertFalse(resultado.permitido)
        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.SIN_CREDENCIAL
        )
        self.assertIsNone(resultado.alumno)

    def test_una_lectura_facial_no_matchea_un_qr_con_el_mismo_valor(self):
        """La unicidad de `Credencial` es por `(gimnasio, tipo, valor)`: un id
        facial y un QR pueden compartir el texto. Buscar solo por valor haría
        que el lector facial abra con el QR de otra persona."""
        resultado = self._decidir(self.qr.valor, tipo="facial")

        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.SIN_CREDENCIAL
        )

    def test_una_credencial_facial_abre_por_su_id_en_el_equipo(self):
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )

        self.assertTrue(self._decidir("42", tipo="facial").permitido)

    def test_con_el_mismo_id_facial_activo_e_inactivo_decide_el_activo(self):
        """La unicidad es parcial sobre las activas: el 42 de un ex-alumno
        (dado de baja) convive con el 42 que el equipo le reasignó a otro.
        `contexto_de` indexa por `(tipo, valor)` y tiene que quedarse con la
        ACTIVA, sin depender del orden en que la base devuelve las filas.

        El caso está armado para que la inactiva tenga el pk MÁS ALTO: Ana ya
        tenía su credencial facial (con otro número) y recién después le
        pasaron el 42 que liberó la baja de Juan. Recorriendo por pk, la
        última fila es la inactiva."""
        ana = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez"
        )
        services.guardar_credencial_facial(ana, "7")
        de_juan = services.guardar_credencial_facial(self.alumno, "42")
        services.dar_de_baja_credencial(de_juan)
        de_ana = services.guardar_credencial_facial(ana, "42")
        self.assertLess(de_ana.pk, de_juan.pk)

        resultado = self._decidir("42", tipo="facial")

        self.assertTrue(resultado.permitido)
        self.assertEqual(resultado.alumno, ana)
        self.assertEqual(resultado.credencial.pk, de_ana.pk)

    def test_con_el_mismo_id_facial_activo_e_inactivo_decide_el_activo_por_pk(self):
        """El mismo caso con el orden de pk al revés: la activa es la más
        nueva. Los dos órdenes tienen que dar lo mismo."""
        de_juan = services.guardar_credencial_facial(self.alumno, "42")
        services.dar_de_baja_credencial(de_juan)
        ana = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez"
        )
        de_ana = services.guardar_credencial_facial(ana, "42")

        resultado = self._decidir("42", tipo="facial")

        self.assertTrue(resultado.permitido)
        self.assertEqual(resultado.credencial.pk, de_ana.pk)

    def test_el_lector_que_agrega_espacios_o_salto_de_linea_igual_reconoce(self):
        """Review Focus 4: un lector que manda "abc\\r\\n" no puede hacer que
        la tarjeta buena "no ande"."""
        resultado = self._decidir(f"  {self.qr.valor}\r\n")

        self.assertTrue(resultado.permitido)

    def test_una_credencial_dada_de_baja_no_abre(self):
        Credencial.objects.filter(pk=self.qr.pk).update(activa=False)

        resultado = self._decidir(self.qr.valor)

        self.assertFalse(resultado.permitido)
        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.CREDENCIAL_INACTIVA
        )

    def test_un_alumno_dado_de_baja_no_abre(self):
        Alumno.objects.filter(pk=self.alumno.pk).update(
            estado=Alumno.Estado.INACTIVO
        )

        resultado = self._decidir(self.qr.valor)

        self.assertFalse(resultado.permitido)
        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.ALUMNO_INACTIVO
        )

    def test_con_el_bloqueo_prendido_el_deudor_no_abre(self):
        self._configurar(bloquea_por_deuda=True)
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO,
        )

        resultado = self._decidir(self.qr.valor)

        self.assertFalse(resultado.permitido)
        self.assertEqual(resultado.resultado, EventoIngreso.Resultado.DEUDA)
        # Spec §4.4: el mensaje dice los días de atraso, como `Bloqueo`.
        self.assertIn("30 días", resultado.mensaje)

    def test_con_el_bloqueo_apagado_el_deudor_abre_igual(self):
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO,
        )

        self.assertTrue(self._decidir(self.qr.valor).permitido)

    def test_prender_el_bloqueo_sin_tolerancia_cargada_no_bloquea_a_nadie(self):
        """`pagos/acceso.py::_umbral` hace fail-open sin `dias_tolerancia_pago`.
        El interruptor queda muerto y sin síntoma: es la razón de que el
        formulario lo avise en pantalla (Tarea 12)."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=None
        )
        # `self.dispositivo.gimnasio` ES esta instancia: sin refrescarla, el
        # `update()` de arriba no se vería y el test mediría el estado viejo.
        self.gimnasio.refresh_from_db()
        self._configurar(bloquea_por_deuda=True)
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=90),
            estado=Cuota.Estado.VENCIDO,
        )

        self.assertTrue(self._decidir(self.qr.valor).permitido)

    def test_la_deuda_se_informa_antes_que_la_falta_de_reserva(self):
        """El orden del árbol: al deudor sin turno se le dice lo que tiene que
        resolver primero, que es la cuota."""
        self._configurar(bloquea_por_deuda=True, exige_reserva=True)
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO,
        )

        self.assertEqual(
            self._decidir(self.qr.valor).resultado, EventoIngreso.Resultado.DEUDA
        )

    def test_con_reserva_exigida_y_sin_turno_no_abre(self):
        self._configurar(exige_reserva=True)

        resultado = self._decidir(self.qr.valor)

        self.assertFalse(resultado.permitido)
        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.SIN_RESERVA
        )

    def _reserva_en(self, momento):
        local = timezone.localtime(momento)
        return Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            fecha=local.date(), hora_inicio=local.time().replace(microsecond=0),
        )

    def test_con_reserva_exigida_y_turno_en_curso_abre(self):
        self._configurar(exige_reserva=True)
        ahora = timezone.now()
        self._reserva_en(ahora - timedelta(minutes=10))

        self.assertTrue(self._decidir(self.qr.valor, momento=ahora).permitido)

    def test_la_reserva_se_mide_contra_el_momento_de_la_lectura(self):
        """Spec §5: en un lote en diferido, `momento` es lo que se usa para la
        reserva vigente. Medido contra `now()`, el que entró a su turno de
        hace tres horas quedaría registrado como "sin turno" -- y el reporte
        mentiría."""
        self._configurar(exige_reserva=True)
        hace_tres_horas = timezone.now() - timedelta(hours=3)
        self._reserva_en(hace_tres_horas - timedelta(minutes=10))
        lecturas = [
            self._lectura(self.qr.valor, momento=hace_tres_horas),
            self._lectura(self.qr.valor, momento=timezone.now()),
        ]
        contexto = decision.contexto_de(self.dispositivo, lecturas)

        en_su_turno = decision.decidir(lecturas[0], contexto)
        fuera_de_turno = decision.decidir(lecturas[1], contexto)

        self.assertTrue(en_su_turno.permitido)
        self.assertEqual(
            fuera_de_turno.resultado, EventoIngreso.Resultado.SIN_RESERVA
        )

    def test_el_antipassback_frena_la_doble_lectura(self):
        self._evento(timezone.now())

        resultado = self._decidir(self.qr.valor)

        self.assertFalse(resultado.permitido)
        self.assertEqual(resultado.resultado, EventoIngreso.Resultado.DUPLICADO)

    def test_el_antipassback_no_cruza_sentido(self):
        """Una salida no puede trabar el reingreso."""
        self._evento(timezone.now(), sentido=EventoIngreso.Sentido.SALIDA)

        self.assertTrue(self._decidir(self.qr.valor).permitido)

    def test_con_el_antipassback_en_cero_no_frena(self):
        self._configurar(minutos_antipassback=0)
        self._evento(timezone.now())

        self.assertTrue(self._decidir(self.qr.valor).permitido)

    def test_el_antipassback_se_mide_contra_el_momento_de_la_lectura(self):
        """En un lote en diferido, medir contra `now()` daría cualquier cosa: un
        evento de hace tres horas se compararía contra el instante del upload."""
        self._evento(timezone.now() - timedelta(hours=3))

        resultado = self._decidir(
            self.qr.valor, momento=timezone.now() - timedelta(hours=3, seconds=30)
        )

        self.assertEqual(resultado.resultado, EventoIngreso.Resultado.DUPLICADO)

    def test_el_antipassback_mira_todos_los_ingresos_de_la_ventana(self):
        """No solo el último: una lectura en diferido de las 10:00 con un
        ingreso de las 09:59 y otro de las 10:10 en la base es un duplicado
        del de las 09:59, aunque el último de la base esté lejos."""
        lectura = timezone.now() - timedelta(hours=1)
        self._evento(lectura - timedelta(minutes=1))
        self._evento(lectura + timedelta(minutes=10))

        resultado = self._decidir(self.qr.valor, momento=lectura)

        self.assertEqual(resultado.resultado, EventoIngreso.Resultado.DUPLICADO)

    def test_un_rechazo_guardado_no_cuenta_para_el_antipassback(self):
        """Solo un ingreso PERMITIDO traba el siguiente: el que fue rechazado
        por deuda hace 30 segundos y pagó en el mostrador tiene que poder
        pasar."""
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            alumno=self.alumno, credencial=self.qr,
            resultado=EventoIngreso.Resultado.DEUDA,
            momento=timezone.now() - timedelta(seconds=30),
        )

        self.assertTrue(self._decidir(self.qr.valor).permitido)

    def test_un_ingreso_un_minuto_POSTERIOR_a_la_lectura_es_duplicado(self):
        """En diferido la base puede tener un ingreso posterior a la lectura
        que se está subiendo: la distancia se mide en valor absoluto."""
        lectura = timezone.now() - timedelta(hours=1)
        self._evento(lectura + timedelta(minutes=1))

        resultado = self._decidir(self.qr.valor, momento=lectura)

        self.assertEqual(resultado.resultado, EventoIngreso.Resultado.DUPLICADO)

    def test_un_ingreso_un_dia_POSTERIOR_a_la_lectura_no_la_traba(self):
        lectura = timezone.now() - timedelta(days=2)
        self._evento(lectura + timedelta(days=1))

        self.assertTrue(self._decidir(self.qr.valor, momento=lectura).permitido)

    def test_una_lectura_del_lote_un_dia_POSTERIOR_no_traba_a_la_anterior(self):
        """El mismo caso dentro del lote, donde no hay ventana de query que lo
        filtre: lo único que separa "un día después" de "duplicado" es el
        `abs()` (una resta negativa siempre es menor que el límite)."""
        ahora = timezone.now()
        lecturas = [
            self._lectura(self.qr.valor, momento=ahora),
            self._lectura(self.qr.valor, momento=ahora - timedelta(days=1)),
        ]
        contexto = decision.contexto_de(self.dispositivo, lecturas)

        self.assertTrue(decision.decidir(lecturas[0], contexto).permitido)
        self.assertTrue(decision.decidir(lecturas[1], contexto).permitido)

    def test_a_exactamente_el_limite_del_antipassback_se_permite(self):
        """Borde definido: con 2 minutos, a 120 segundos justos ya pasa (`<`
        estricto). A 119 todavía es duplicado."""
        lectura = timezone.now() - timedelta(hours=1)
        self._evento(lectura - timedelta(seconds=120))

        self.assertTrue(self._decidir(self.qr.valor, momento=lectura).permitido)

        EventoIngreso.objects.all().delete()
        self._evento(lectura - timedelta(seconds=119))
        self.assertEqual(
            self._decidir(self.qr.valor, momento=lectura).resultado,
            EventoIngreso.Resultado.DUPLICADO,
        )

    def test_un_reloj_reseteado_no_trae_el_historial_del_medio(self):
        """Un lector sin pila manda una lectura del 2000 junto con una de
        ahora. Con un rango `[min, max]` del lote, `contexto_de` traería TODOS
        los ingresos y reservas del alumno entre las dos fechas; tiene que
        traer solo lo que cae en la ventana de cada lectura."""
        self._configurar(exige_reserva=True)
        ahora = timezone.now()
        hace_700_dias = ahora - timedelta(days=700)
        # Adentro de las ventanas: tienen que venir.
        self._evento(ahora - timedelta(seconds=30))
        self._evento(hace_700_dias - timedelta(seconds=30))
        self._reserva_en(ahora - timedelta(minutes=10))
        # Afuera de las dos, en el medio: no pueden venir.
        self._evento(ahora - timedelta(days=350))
        self._reserva_en(ahora - timedelta(days=350))
        lecturas = [
            self._lectura(self.qr.valor, momento=hace_700_dias),
            self._lectura(self.qr.valor, momento=ahora),
        ]

        contexto = decision.contexto_de(self.dispositivo, lecturas)

        clave = (self.alumno.pk, EventoIngreso.Sentido.ENTRADA)
        self.assertEqual(len(contexto.ultimo_permitido[clave]), 2)
        self.assertEqual(len(contexto.reservas.por_alumno[self.alumno.pk]), 1)

    def test_dos_lecturas_del_mismo_alumno_en_EL_MISMO_lote(self):
        """Review Focus 3: el antipassback tiene que verlas entre sí, no solo
        contra lo que ya está en la base."""
        ahora = timezone.now()
        lecturas = [
            decision.Lectura("qr", self.qr.valor,
                             EventoIngreso.Sentido.ENTRADA, ahora),
            decision.Lectura("qr", self.qr.valor,
                             EventoIngreso.Sentido.ENTRADA,
                             ahora + timedelta(seconds=20)),
        ]
        contexto = decision.contexto_de(self.dispositivo, lecturas)

        primera = decision.decidir(lecturas[0], contexto)
        segunda = decision.decidir(lecturas[1], contexto)

        self.assertTrue(primera.permitido)
        self.assertEqual(segunda.resultado, EventoIngreso.Resultado.DUPLICADO)

    def test_un_rechazo_del_lote_no_cuenta_para_el_antipassback(self):
        """Solo lo concedido traba la lectura siguiente: el que fue rechazado
        por falta de turno y después reservó no puede quedar "duplicado"."""
        self._configurar(exige_reserva=True)
        ahora = timezone.now()
        lecturas = [
            self._lectura(self.qr.valor, momento=ahora - timedelta(minutes=30)),
            self._lectura(self.qr.valor, momento=ahora),
        ]
        self._reserva_en(ahora - timedelta(minutes=5))
        contexto = decision.contexto_de(self.dispositivo, lecturas)

        primera = decision.decidir(lecturas[0], contexto)
        segunda = decision.decidir(lecturas[1], contexto)

        self.assertEqual(primera.resultado, EventoIngreso.Resultado.SIN_RESERVA)
        self.assertTrue(segunda.permitido)

    def test_el_mensaje_del_permitido_saluda_por_el_nombre(self):
        self.assertIn("Juan", self._decidir(self.qr.valor).mensaje)

    def test_no_carga_el_historial_viejo_en_memoria(self):
        """El test que el de queries NO puede hacer.

        Sin el piso de fecha, `contexto_de` trae todos los ingresos de todos los
        alumnos del lote. La cantidad de queries es la misma, así que el test de
        escala pasa igual: lo único que delata el bug es que un ingreso viejo
        aparezca en el contexto.
        """
        self._evento(timezone.now() - timedelta(days=700))
        lectura = decision.Lectura(
            "qr", self.qr.valor, EventoIngreso.Sentido.ENTRADA, timezone.now()
        )

        contexto = decision.contexto_de(self.dispositivo, [lectura])

        self.assertEqual(contexto.ultimo_permitido, {})

    def test_contexto_de_no_crece_en_queries_con_el_tamano_del_lote(self):
        """La regla más dura del proyecto: el costo depende del catálogo, no de
        la cantidad de filas. 5 queries por evento × 100 eventos es el patrón
        que ya causó un 502 con el importador.

        Lo que crece es lo que de verdad multiplica el costo: alumnos DISTINTOS,
        cada uno con su QR, una cuota que lo bloquea, un turno en curso y un
        ingreso reciente -- con las tres reglas prendidas. Y el contexto no
        puede salir vacío, o la comparación pasa sola.
        """
        self._configurar(bloquea_por_deuda=True, exige_reserva=True)
        ahora = timezone.now()

        def sembrar(cantidad, desde):
            lecturas = []
            for i in range(cantidad):
                alumno = Alumno.objects.create(
                    gimnasio=self.gimnasio, nombre=f"A{desde + i}", apellido="X"
                )
                qr = services.generar_credencial_qr(alumno)
                crear_cuota(
                    gimnasio=self.gimnasio, alumno=alumno,
                    inicio=timezone.localdate() - timedelta(days=30),
                    estado=Cuota.Estado.VENCIDO,
                )
                local = timezone.localtime(ahora - timedelta(minutes=10))
                Reserva.objects.create(
                    gimnasio=self.gimnasio, alumno=alumno, fecha=local.date(),
                    hora_inicio=local.time().replace(microsecond=0),
                )
                EventoIngreso.objects.create(
                    gimnasio=self.gimnasio, dispositivo=self.dispositivo,
                    alumno=alumno, credencial=qr,
                    resultado=EventoIngreso.Resultado.PERMITIDO,
                    momento=ahora - timedelta(seconds=30),
                )
                lecturas.append(self._lectura(qr.valor, momento=ahora))
            return lecturas

        def medir(lecturas):
            with CaptureQueriesContext(connection) as ctx:
                contexto = decision.contexto_de(self.dispositivo, lecturas)
            return len(ctx), contexto

        pocas = sembrar(3, 0)
        muchas = pocas + sembrar(20, 100)
        medir(pocas)  # calienta los `get_or_create` de las configuraciones

        queries_pocas, contexto_pocas = medir(pocas)
        queries_muchas, contexto_muchas = medir(muchas)

        for contexto, cantidad in ((contexto_pocas, 3), (contexto_muchas, 23)):
            self.assertEqual(len(contexto.credenciales), cantidad)
            self.assertEqual(len(contexto.bloqueados), cantidad)
            self.assertEqual(len(contexto.reservas.por_alumno), cantidad)
            self.assertEqual(len(contexto.ultimo_permitido), cantidad)
        self.assertEqual(queries_pocas, queries_muchas)
