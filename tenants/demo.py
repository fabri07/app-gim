"""Datos de demostración para un gimnasio de prueba.

Para qué existe: una cuenta vacía no muestra NADA de la app. La tarjeta de
planes por vencer necesita alumnos con plan, los gráficos del panel necesitan
reservas y pagos, los botones de eliminar necesitan algo que eliminar. Sin
datos, una captura de pantalla para promocionar el producto se ve como un
formulario en blanco.

**Nunca correr esto sobre un gimnasio real.** El comando
(`manage.py sembrar_demo`) se niega si el gimnasio ya tiene alumnos que no
sean de demo, salvo `--confirmar`. Acá adentro no hay ninguna red: esta
función escribe lo que le pidan.

Todo lo que crea queda marcado con `MARCA` en `Alumno.observaciones`, que es
lo que le permite a `borrar_demo` sacar exactamente esto y nada más.
"""

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote_plus

from django.db import transaction
from django.utils import timezone

MARCA = "[demo]"

#: Contraseña compartida por todos los alumnos de demo. Es fija y conocida a
#: propósito: durante una demostración hay que poder abrir el portal del alumno
#: en un segundo dispositivo, y una contraseña al azar por alumno lo impide (no
#: se guarda en ningún lado, ver `alumnos/services.py`). Es aceptable SOLO
#: porque el comando se niega a correr sobre un gimnasio con alumnos reales sin
#: `--confirmar`. Nunca la reuses fuera de la siembra.
PASSWORD_DEMO = "demo1234"


_ACENTOS = str.maketrans("áéíóúñ", "aeioun")


def _email_demo(nombre, apellido, dominio):
    """`sofia.gonzalez@<slug>.ejemplo.com`, sin acentos.

    Es a la vez el email que se ve en la ficha y el usuario con el que el
    alumno de demo inicia sesión, así que la ficha y el panel de accesos
    muestran el mismo dato.
    """
    local = f"{nombre}.{apellido}".lower().translate(_ACENTOS)
    return f"{local}@{dominio}"


def _dominio_demo(gimnasio):
    """Dominio de los emails de demo, namespaceado por gimnasio.

    `User.username` es único GLOBAL y `semilla` es fija, así que con un dominio
    compartido el segundo gimnasio de prueba que se siembre choca contra el
    primero en el alumno #1. El slug lo separa.

    Se lo pasa por un filtro porque el dominio termina dentro de un email que
    `validate_email` tiene que aceptar: un slug con `_` (posible desde /admin/,
    aunque `slugify` no lo genere) o con guiones en las puntas daría un email
    inválido y volteraría la siembra entera.
    """
    etiqueta = "".join(
        c if c.isalnum() else "-" for c in (gimnasio.slug or "").lower()
    ).strip("-")
    return f"{etiqueta or 'demo'}.ejemplo.com"


#: Búsqueda de YouTube, no un video puntual -- ver `_EJERCICIOS`.
_VIDEO_BASE = "https://www.youtube.com/results?search_query="

_NOMBRES = [
    ("Sofía", "F"), ("Martina", "F"), ("Valentina", "F"), ("Camila", "F"),
    ("Lucía", "F"), ("Emilia", "F"), ("Renata", "F"), ("Julieta", "F"),
    ("Mateo", "M"), ("Benjamín", "M"), ("Thiago", "M"), ("Bautista", "M"),
    ("Santiago", "M"), ("Lorenzo", "M"), ("Joaquín", "M"), ("Francisco", "M"),
    ("Agustín", "M"), ("Tomás", "M"), ("Delfina", "F"), ("Catalina", "F"),
    ("Bruno", "M"), ("Ignacio", "M"), ("Pilar", "F"), ("Manuel", "M"),
]
_APELLIDOS = [
    "González", "Rodríguez", "Fernández", "López", "Martínez", "Pérez",
    "Gómez", "Sánchez", "Romero", "Sosa", "Torres", "Álvarez", "Ruiz",
    "Ramírez", "Flores", "Acosta", "Benítez", "Medina", "Herrera", "Suárez",
    "Aguirre", "Molina", "Silva", "Castro",
]

#: Horas donde de verdad se llena un gimnasio de barrio. Los pesos hacen que
#: la grilla de calor del panel muestre un patrón reconocible (mañana temprano
#: y después del trabajo) en vez de ruido uniforme.
_FRANJAS = [
    (time(7, 0), 3), (time(8, 0), 4), (time(9, 0), 2), (time(10, 0), 1),
    (time(17, 0), 3), (time(18, 0), 6), (time(19, 0), 7), (time(20, 0), 5),
]

#: (nombre, categoría, video). El video es una BÚSQUEDA de YouTube por el
#: nombre del ejercicio, no un video puntual elegido a dedo. Es deliberado:
#: son datos de demostración, y linkear un video concreto sin que nadie haya
#: verificado que muestra ESE ejercicio con buena técnica es justo lo que no
#: se hace en la biblioteca de un gimnasio real -- el que se lastima es una
#: persona. Una búsqueda siempre lleva a algo pertinente, y en una captura de
#: pantalla el botón "Ver video" se ve igual.
_EJERCICIOS = [
    ("Sentadilla con barra", "Piernas", "sentadilla con barra tecnica"),
    ("Peso muerto", "Cadena posterior", "peso muerto tecnica"),
    ("Press de banca", "Empuje", "press de banca tecnica"),
    ("Dominadas", "Tracción", "dominadas tecnica"),
    ("Remo con barra", "Tracción", "remo con barra tecnica"),
    ("Press militar", "Empuje", "press militar tecnica"),
    ("Zancadas", "Piernas", "zancadas tecnica"),
    ("Plancha", "Core", "plancha abdominal tecnica"),
    ("Hip thrust", "Cadena posterior", "hip thrust tecnica"),
    ("Fondos en paralelas", "Empuje", "fondos en paralelas tecnica"),
    ("Curl de bíceps", "Accesorios", "curl de biceps tecnica"),
    ("Elevaciones laterales", "Accesorios", "elevaciones laterales tecnica"),
]

_NOVEDADES = [
    ("Cambio de horario los feriados",
     "El lunes feriado abrimos de 9 a 13. El resto de la semana, horario normal."),
    ("Nuevos horarios de funcional",
     "Sumamos turno de funcional los martes y jueves a las 19. Cupos limitados."),
    ("Recordatorio de cuota",
     "Podés pagar por transferencia y subir el comprobante desde la app."),
]


#: Días de tolerancia que se le configuran al gimnasio de demo, para que el
#: estado "acceso pausado" se vea en una captura.
_TOLERANCIA_DEMO = 5


def sembrar_demo(*, gimnasio, cantidad_alumnos=24, meses=6, semilla=42):
    """Llena `gimnasio` con datos verosímiles y devuelve un resumen.

    `semilla` fija: dos corridas con los mismos parámetros dan el mismo
    resultado, así que una captura de pantalla se puede rehacer igual.
    """
    from alumnos.models import Alumno
    from alumnos.services import IdentificadorEnUso, crear_acceso
    from ejercicios.models import CategoriaEjercicio, Ejercicio
    from importaciones.parsing import normalizar_texto
    from novedades.models import Novedad
    from pagos.models import DIAS_CICLO, Cuota
    from tenants.models import Gimnasio
    from rutinas.models import (
        RutinaAsignada,
        RutinaAsignadaDiaCompletado,
        RutinaAsignadaItem,
        RutinaPlantilla,
        RutinaPlantillaItem,
    )
    from turnos.models import ConfiguracionTurnos, HorarioAtencion, Reserva

    azar = random.Random(semilla)
    hoy = timezone.localdate()
    resumen = {}
    # ANTES de crear nada: si el gimnasio ya tiene alumnos que no son de demo
    # (el camino `--confirmar`), es el gimnasio de un cliente y su
    # configuración de bloqueo no se toca -- ver el bloque de tolerancia.
    hay_alumnos_reales = (
        Alumno.objects.for_gimnasio(gimnasio).exclude(observaciones=MARCA).exists()
    )


    with transaction.atomic():
        # --- Biblioteca -------------------------------------------------
        # La clave de búsqueda tiene que ser la MISMA normalización que aplica
        # `CategoriaEjercicio.save()` (saca acentos, colapsa espacios). Con un
        # `.lower()` a mano, "Tracción" se guardaba como "traccion" y no se
        # encontraba en la corrida siguiente: el `get_or_create` intentaba
        # insertar de nuevo y reventaba contra la UniqueConstraint.
        categorias = {}
        for _, nombre_categoria, _video in _EJERCICIOS:
            if nombre_categoria not in categorias:
                categorias[nombre_categoria], _ = CategoriaEjercicio.objects.get_or_create(
                    gimnasio=gimnasio,
                    nombre_normalizado=normalizar_texto(nombre_categoria),
                    defaults={"nombre": nombre_categoria},
                )
        ejercicios = []
        for nombre, nombre_categoria, busqueda in _EJERCICIOS:
            ejercicio, creado_ej = Ejercicio.objects.get_or_create(
                gimnasio=gimnasio, nombre=nombre,
                defaults={
                    "categoria": categorias[nombre_categoria],
                    "url_video": _VIDEO_BASE + quote_plus(busqueda),
                },
            )
            # Un ejercicio preexistente al que le falta el video lo recibe;
            # uno que ya tiene el suyo NO se pisa (puede haberlo cargado el
            # entrenador).
            if not creado_ej and not ejercicio.url_video:
                ejercicio.url_video = _VIDEO_BASE + quote_plus(busqueda)
                ejercicio.save(update_fields=["url_video", "modificado"])
            ejercicios.append(ejercicio)
        resumen["ejercicios"] = len(ejercicios)

        # --- Plantilla: 3 días x 4 ejercicios x 4 semanas ---------------
        plantilla, creada = RutinaPlantilla.objects.get_or_create(
            gimnasio=gimnasio, nombre="Full body 3 días",
            defaults={
                "objetivo": "Fuerza general e hipertrofia",
                "nivel": RutinaPlantilla.Nivel.INTERMEDIO,
                "dias_por_semana": 3,
            },
        )
        if creada:
            nombres_dia = {1: "Tren inferior", 2: "Tren superior", 3: "Full body · Core"}
            for dia in (1, 2, 3):
                del_dia = ejercicios[(dia - 1) * 4:(dia - 1) * 4 + 4]
                for semana in range(1, 5):
                    for orden, ejercicio in enumerate(del_dia, start=1):
                        RutinaPlantillaItem.objects.create(
                            rutina=plantilla, ejercicio=ejercicio,
                            semana=semana, dia=dia, dia_nombre=nombres_dia[dia],
                            orden=orden,
                            # La carga sube semana a semana: es lo que hace
                            # que la progresión se vea en el portal.
                            series=3 + (semana > 2),
                            repeticiones=["12", "10", "8", "8"][semana - 1],
                            kilos=f"{20 + semana * 5} kg",
                            descanso="90s",
                            bloque=f"{'ABCD'[orden - 1]}1",
                        )
        resumen["plantilla"] = plantilla.nombre

        # --- Turnos ------------------------------------------------------
        ConfiguracionTurnos.objects.get_or_create(
            gimnasio=gimnasio, defaults={"duracion_minutos": 60, "vacantes_default": 12}
        )
        for dia_semana in range(0, 5):  # lunes a viernes
            HorarioAtencion.objects.get_or_create(
                gimnasio=gimnasio, dia_semana=dia_semana,
                hora_desde=time(7, 0), hora_hasta=time(21, 0),
            )

        # --- Alumnos -----------------------------------------------------
        dominio = _dominio_demo(gimnasio)
        alumnos = []
        accesos = 0
        usados = set()
        for i in range(cantidad_alumnos):
            nombre, genero = _NOMBRES[i % len(_NOMBRES)]
            apellido = _APELLIDOS[(i * 7) % len(_APELLIDOS)]
            # Las dos listas tienen 24 entradas y 24*7 % 24 == 0, así que a
            # partir del alumno 25 el par (nombre, apellido) se repite. El
            # sufijo lo desempata: sin él, `--alumnos 30` chocaba contra el
            # UNIQUE de `auth_user.username` en el alumno 25.
            email = _email_demo(nombre, apellido, dominio)
            if email in usados:
                email = _email_demo(f"{nombre}{i}", apellido, dominio)
            usados.add(email)
            # 1 de cada 8 inactivo: el panel tiene que mostrar que el filtro
            # de estado hace algo.
            inactivo = i % 8 == 7
            alumno = Alumno.objects.create(
                gimnasio=gimnasio,
                nombre=nombre, apellido=apellido,
                email=email,
                telefono=f"11{azar.randint(20000000, 69999999)}",
                fecha_nacimiento=date(
                    hoy.year - azar.randint(18, 55), azar.randint(1, 12), azar.randint(1, 28)
                ),
                estado=Alumno.Estado.INACTIVO if inactivo else Alumno.Estado.ACTIVO,
                sexo=Alumno.Sexo.FEMENINO if genero == "F" else Alumno.Sexo.MASCULINO,
                actividad_fisica_previa=azar.random() < 0.6,
                observaciones=MARCA,
            )
            alumnos.append(alumno)
            # El acceso se crea acá y no en un bucle aparte para que el
            # `alumno.save()` de `crear_acceso` (el que dispara el signal que
            # apaga el `User` de un alumno dado de baja) corra ANTES de que
            # el bloque siguiente reescriba `creado` y `fecha_baja`.
            #
            # Sí, es una query por fila, que es justo lo que este proyecto
            # prohíbe en los procesos en lote. Acá se acepta: la regla existe
            # por el timeout de 30 s de gunicorn y esto es un comando de
            # consola sobre 24 filas. Un `bulk_create` de `User` se saltearía
            # el signal y duplicaría la lógica de `crear_acceso`, que es el
            # único lugar donde vive el alta de un acceso.
            try:
                crear_acceso(alumno, "email", email, password=PASSWORD_DEMO)
                accesos += 1
            except IdentificadorEnUso:
                # El identificador ya está tomado en la plataforma: pasa al
                # sembrar dos veces el mismo gimnasio sin `--borrar` en el
                # medio. El alumno queda sin acceso (igual que antes de esta
                # feature) en vez de voltear la siembra entera.
                pass
        resumen["accesos"] = accesos

        # `creado` se reparte hacia atrás en la ventana de historial: sin
        # esto los 24 alumnos caen todos en el mes actual y el gráfico de
        # altas es una sola columna gigante, que en una captura se lee como
        # un dato falso. `update()` porque `auto_now_add` no se puede setear
        # al crear; se hace en UNA query para todos los del mismo mes.
        for i, alumno in enumerate(alumnos):
            antiguedad = azar.randint(0, max(meses - 1, 0) * 30 + 25)
            Alumno.objects.filter(pk=alumno.pk).update(
                creado=timezone.make_aware(
                    datetime.combine(hoy - timedelta(days=antiguedad), datetime.min.time())
                )
            )
            # Las bajas también se reparten: el signal las estampó todas hoy,
            # y doce meses de bajas concentradas en uno solo miente igual que
            # las altas.
            if alumno.estado == Alumno.Estado.INACTIVO:
                Alumno.objects.filter(pk=alumno.pk).update(
                    fecha_baja=hoy - timedelta(days=azar.randint(0, antiguedad))
                )
        resumen["alumnos"] = len(alumnos)

        activos = [a for a in alumnos if a.estado == Alumno.Estado.ACTIVO]

        # --- Rutinas asignadas -------------------------------------------
        # Escalonadas a propósito: unos pocos con el plan por vencer (para que
        # la tarjeta del panel tenga contenido), el resto repartido en el ciclo.
        rutinas = []
        for i, alumno in enumerate(activos):
            dias_desde_el_inicio = [26, 24, 21][i % 3] if i < 3 else azar.randint(1, 20)
            rutina = RutinaAsignada.crear_desde_plantilla(
                gimnasio=gimnasio, alumno=alumno, plantilla=plantilla,
                fecha_inicio=hoy - timedelta(days=dias_desde_el_inicio),
            )
            rutinas.append(rutina)
        resumen["rutinas"] = len(rutinas)

        # --- RPE y días entrenados ---------------------------------------
        # Solo sobre semanas YA transcurridas: calificar una sesión futura
        # sería un dato imposible, y la adherencia del panel lo tomaría en
        # serio.
        pesos_rpe = [
            (RutinaAsignadaItem.RPE.MAS_INTENSO, 2),
            (RutinaAsignadaItem.RPE.SEGUIR_INTENSIDAD, 5),
            (RutinaAsignadaItem.RPE.AL_LIMITE, 3),
            (RutinaAsignadaItem.RPE.BAJAR_INTENSIDAD, 1),
        ]
        opciones = [valor for valor, peso in pesos_rpe for _ in range(peso)]
        calificados = 0
        completados = 0
        for rutina in rutinas:
            semanas_hechas = rutina.semana_actual
            for item in rutina.items.filter(semana__lt=semanas_hechas):
                if azar.random() < 0.7:
                    item.rpe = azar.choice(opciones)
                    item.save(update_fields=["rpe", "modificado"])
                    calificados += 1
            for semana in range(1, semanas_hechas):
                for dia in (1, 2, 3):
                    if azar.random() < 0.8:
                        RutinaAsignadaDiaCompletado.objects.get_or_create(
                            rutina_asignada=rutina, semana=semana, dia=dia
                        )
                        completados += 1
        resumen["calificaciones"] = calificados
        resumen["dias_entrenados"] = completados

        # --- Cuotas -------------------------------------------------------
        # Ciclos de 28 días hacia atrás desde el ciclo en curso de cada alumno,
        # no meses calendario: es como cobra el sistema desde la migración a
        # ciclos. `ciclos` se deriva de `meses` para no cambiar la firma del
        # comando (13,04 ciclos al año contra 12 meses).
        pagos = 0
        ciclos = max(1, round(meses * 365 / 12 / DIAS_CICLO))
        for alumno in alumnos:
            # Cada alumno arranca su ciclo un día distinto: es justamente lo
            # que el panel tiene que poder mostrar. Sin esto todos cobrarían
            # el mismo día y la demo escondería el caso interesante.
            alumno.fecha_inicio_ciclo = hoy - timedelta(
                days=DIAS_CICLO * (ciclos - 1) + azar.randrange(DIAS_CICLO)
            )
            alumno.save(update_fields=["fecha_inicio_ciclo", "modificado"])
            for indice in range(ciclos):
                inicio = alumno.fecha_inicio_ciclo + timedelta(days=DIAS_CICLO * indice)
                es_el_actual = indice == ciclos - 1
                if alumno.estado == Alumno.Estado.INACTIVO and es_el_actual:
                    continue  # se dio de baja: no genera cuotas nuevas
                if es_el_actual:
                    # Mezcla realista: es lo que se ve en el panel.
                    estado = azar.choices(
                        [Cuota.Estado.PAGADO, Cuota.Estado.PENDIENTE,
                         Cuota.Estado.VENCIDO],
                        weights=[6, 3, 1],
                    )[0]
                else:
                    estado = Cuota.Estado.PAGADO
                Cuota.objects.create(
                    gimnasio=gimnasio, alumno=alumno,
                    periodo_inicio=inicio,
                    periodo_fin=inicio + timedelta(days=DIAS_CICLO - 1),
                    monto=Decimal("28000"),
                    estado=estado,
                    fecha_pago=inicio if estado == Cuota.Estado.PAGADO else None,
                    medio_pago_texto="Transferencia" if estado == Cuota.Estado.PAGADO else "",
                )
                pagos += 1
        resumen["pagos"] = pagos

        # Un alumno con el acceso pausado. Sin esto, el estado nuevo no se ve
        # en ninguna captura: hace falta que el gimnasio tenga la tolerancia
        # configurada Y que alguien tenga una cuota impaga vieja, y una cuenta
        # recién sembrada no cumple ninguna de las dos.
        #
        # SOLO en una cuenta de prueba. Retroceder `fecha_activacion_bloqueo`
        # un año es anular a propósito la garantía de que prender el bloqueo
        # no es retroactivo; sobre el gimnasio de un cliente (`--confirmar`)
        # eso bloqueaba de golpe a todos sus alumnos reales con cualquier
        # impaga histórica. Ahí la configuración queda como estaba y la demo
        # muestra el bloqueo solo si el dueño ya lo tenía prendido.
        if hay_alumnos_reales:
            resumen["bloqueo_configurado"] = False
        else:
            if gimnasio.dias_tolerancia_pago is None:
                gimnasio.dias_tolerancia_pago = _TOLERANCIA_DEMO
                gimnasio.save(update_fields=["dias_tolerancia_pago", "modificado"])
            # La fecha de activación va por `update()` y NO por `save()`: la
            # señal `registrar_activacion_del_bloqueo` la estampa en HOY
            # durante la transición apagado -> prendido, así que asignarla
            # antes de guardar no sirve, la pisa. Se retrocede porque en una
            # demo el bloqueo tiene que verse ya, y `bloqueo_de` ignora las
            # cuotas anteriores a esa fecha.
            #
            # Fuera del `if` a propósito: al resembrar, la tolerancia ya quedó
            # configurada de la corrida anterior y la fecha seguiría en el
            # "hoy" de aquella vez, dejando la demo sin ningún alumno
            # bloqueado.
            Gimnasio.objects.filter(pk=gimnasio.pk).update(
                fecha_activacion_bloqueo=hoy - timedelta(days=365)
            )
            gimnasio.refresh_from_db()
            resumen["bloqueo_configurado"] = True


        # El moroso: se le atrasa el ciclo en curso lo suficiente como para
        # cruzar la tolerancia. No alcanza con marcarlo VENCIDO -- el bloqueo
        # se decide por la FECHA de arranque del ciclo, no por el estado.
        moroso = alumnos[0]
        vencida = (
            Cuota.objects.filter(alumno=moroso, periodo_inicio__lte=hoy)
            .order_by("-periodo_inicio")
            .first()
        )
        if vencida is not None:
            atraso = _TOLERANCIA_DEMO + 3
            vencida.periodo_inicio = hoy - timedelta(days=atraso)
            vencida.periodo_fin = vencida.periodo_inicio + timedelta(days=DIAS_CICLO - 1)
            vencida.estado = Cuota.Estado.VENCIDO
            vencida.fecha_pago = None
            vencida.medio_pago_texto = ""
            vencida.save()
            resumen["alumnos_bloqueados"] = 1

        # Y EXACTAMENTE uno: los ciclos se siembran con arranques al azar
        # dentro de los últimos 28 días y una mezcla de estados, así que sin
        # esto quedaban ~5 de 8 alumnos bloqueados y la demo parecía un
        # gimnasio roto en vez de una app funcionando con un moroso. A los
        # demás se les da por saldado el ciclo en curso si ya cruzó la
        # tolerancia; los pendientes recientes quedan, que es lo que hace
        # realista el panel.
        Cuota.objects.filter(
            gimnasio=gimnasio,
            estado__in=Cuota.ESTADOS_IMPAGOS,
            periodo_inicio__lte=hoy - timedelta(days=_TOLERANCIA_DEMO),
        ).exclude(alumno=moroso).update(
            estado=Cuota.Estado.PAGADO,
            medio_pago_texto="Transferencia",
            modificado=timezone.now(),
        )

        # --- Reservas ------------------------------------------------------
        # 10 semanas hacia atrás: la grilla de calor del panel agrupa TODO el
        # historial por día y hora, así que con una sola semana no se ve
        # ningún patrón.
        horas = [hora for hora, peso in _FRANJAS for _ in range(peso)]
        reservas = 0
        # Se recorren los 70 días hacia atrás DE A UNO y se filtra por día de
        # semana. La versión anterior avanzaba 5 días desde hoy dentro de cada
        # semana, así que si hoy era miércoles solo tocaba lunes, martes y
        # miércoles: la grilla de calor mostraba jueves y viernes en CERO, que
        # en una captura se lee como que la app está rota.
        for dias_atras in range(70):
            fecha = hoy - timedelta(days=dias_atras)
            if fecha.weekday() > 4:  # sábado y domingo: el gimnasio no abre
                continue
            for alumno in activos:
                if azar.random() < 0.35:
                    _, creado = Reserva.objects.get_or_create(
                        gimnasio=gimnasio, alumno=alumno, fecha=fecha,
                        hora_inicio=azar.choice(horas),
                    )
                    reservas += bool(creado)
        resumen["reservas"] = reservas

        # --- Novedades -----------------------------------------------------
        for i, (titulo, mensaje) in enumerate(_NOVEDADES):
            Novedad.objects.get_or_create(
                gimnasio=gimnasio, titulo=titulo,
                defaults={
                    "mensaje": mensaje,
                    "fecha_publicacion": hoy - timedelta(days=i * 5),
                },
            )
        resumen["novedades"] = len(_NOVEDADES)

    return resumen


def borrar_demo(*, gimnasio):
    """Saca exactamente lo que sembró `sembrar_demo` (los alumnos marcados,
    sus accesos y todo lo que cuelga de ellos). No toca ejercicios, plantillas
    ni novedades: son inofensivos y puede haberlos editado alguien."""
    from django.contrib.auth import get_user_model

    from alumnos.models import Alumno
    from novedades.models import NovedadLeida
    from pagos.models import Cuota
    from rutinas.models import RutinaAsignada, RutinaAsignadaDiaCompletado
    from tenants.models import RegistroSuplantacion
    from turnos.models import Reserva

    with transaction.atomic():
        alumnos = Alumno.objects.for_gimnasio(gimnasio).filter(observaciones=MARCA)
        borrados = alumnos.count()
        # Los `User` se anotan ANTES de borrar los alumnos: `Alumno.perfil` es
        # `SET_NULL`, así que después del `delete()` no queda forma de saber
        # cuáles eran. Sin esto quedarían usuarios huérfanos que PUEDEN
        # loguearse y no aparecen en ningún panel del gimnasio.
        usuarios = list(
            alumnos.filter(perfil__isnull=False).values_list(
                "perfil__usuario_id", flat=True
            )
        )
        RutinaAsignadaDiaCompletado.objects.filter(
            rutina_asignada__alumno__in=alumnos
        ).delete()
        # PROTECT en pagos y rutinas: hay que sacarlos antes que el alumno.
        RutinaAsignada.objects.filter(alumno__in=alumnos).delete()
        Cuota.objects.filter(alumno__in=alumnos).delete()
        Reserva.objects.filter(alumno__in=alumnos).delete()
        NovedadLeida.objects.filter(alumno__in=alumnos).delete()
        # `RegistroSuplantacion.alumno` es PROTECT: el rastro de auditoría no
        # se borra por cascada a propósito. Acá hay que sacarlo a mano, o
        # `--borrar` revienta con `ProtectedError` justo para quien usó
        # «Entrar como», que es para lo que se siembran los accesos. Se pierde
        # la auditoría de un alumno inventado que se está borrando igual.
        RegistroSuplantacion.objects.filter(alumno__in=alumnos).delete()
        alumnos.delete()
        # El `Perfil` se va en cascada con el `User` (su FK es CASCADE).
        get_user_model().objects.filter(pk__in=usuarios).delete()
    return borrados
