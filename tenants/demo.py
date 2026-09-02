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
from datetime import date, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

MARCA = "[demo]"

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

_EJERCICIOS = [
    ("Sentadilla con barra", "Piernas"),
    ("Peso muerto", "Cadena posterior"),
    ("Press de banca", "Empuje"),
    ("Dominadas", "Tracción"),
    ("Remo con barra", "Tracción"),
    ("Press militar", "Empuje"),
    ("Zancadas", "Piernas"),
    ("Plancha", "Core"),
    ("Hip thrust", "Cadena posterior"),
    ("Fondos en paralelas", "Empuje"),
    ("Curl de bíceps", "Accesorios"),
    ("Elevaciones laterales", "Accesorios"),
]

_NOVEDADES = [
    ("Cambio de horario los feriados",
     "El lunes feriado abrimos de 9 a 13. El resto de la semana, horario normal."),
    ("Nuevos horarios de funcional",
     "Sumamos turno de funcional los martes y jueves a las 19. Cupos limitados."),
    ("Recordatorio de cuota",
     "La cuota vence el día 10. Podés pagar por transferencia y subir el comprobante desde la app."),
]


def sembrar_demo(*, gimnasio, cantidad_alumnos=24, meses=6, semilla=42):
    """Llena `gimnasio` con datos verosímiles y devuelve un resumen.

    `semilla` fija: dos corridas con los mismos parámetros dan el mismo
    resultado, así que una captura de pantalla se puede rehacer igual.
    """
    from alumnos.models import Alumno
    from ejercicios.models import CategoriaEjercicio, Ejercicio
    from importaciones.parsing import normalizar_texto
    from novedades.models import Novedad
    from pagos.models import PagoMensual
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

    with transaction.atomic():
        # --- Biblioteca -------------------------------------------------
        # La clave de búsqueda tiene que ser la MISMA normalización que aplica
        # `CategoriaEjercicio.save()` (saca acentos, colapsa espacios). Con un
        # `.lower()` a mano, "Tracción" se guardaba como "traccion" y no se
        # encontraba en la corrida siguiente: el `get_or_create` intentaba
        # insertar de nuevo y reventaba contra la UniqueConstraint.
        categorias = {}
        for _, nombre_categoria in _EJERCICIOS:
            if nombre_categoria not in categorias:
                categorias[nombre_categoria], _ = CategoriaEjercicio.objects.get_or_create(
                    gimnasio=gimnasio,
                    nombre_normalizado=normalizar_texto(nombre_categoria),
                    defaults={"nombre": nombre_categoria},
                )
        ejercicios = []
        for nombre, nombre_categoria in _EJERCICIOS:
            ejercicio, _ = Ejercicio.objects.get_or_create(
                gimnasio=gimnasio, nombre=nombre,
                defaults={"categoria": categorias[nombre_categoria]},
            )
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
        alumnos = []
        for i in range(cantidad_alumnos):
            nombre, genero = _NOMBRES[i % len(_NOMBRES)]
            apellido = _APELLIDOS[(i * 7) % len(_APELLIDOS)]
            # 1 de cada 8 inactivo: el panel tiene que mostrar que el filtro
            # de estado hace algo.
            inactivo = i % 8 == 7
            alumno = Alumno.objects.create(
                gimnasio=gimnasio,
                nombre=nombre, apellido=apellido,
                email=f"{nombre.lower()}.{apellido.lower()}@ejemplo.com".replace("í", "i").replace("á", "a").replace("é", "e").replace("ó", "o").replace("ú", "u"),
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

        # --- Pagos --------------------------------------------------------
        pagos = 0
        for desplazamiento in range(meses):
            mes = hoy.month - desplazamiento
            anio = hoy.year
            while mes <= 0:
                mes += 12
                anio -= 1
            for alumno in alumnos:
                if alumno.estado == Alumno.Estado.INACTIVO and desplazamiento < 2:
                    continue  # se dio de baja: no genera cuotas nuevas
                if desplazamiento == 0:
                    # El mes en curso: mezcla realista, es lo que se ve en el panel.
                    estado = azar.choices(
                        [PagoMensual.Estado.PAGADO, PagoMensual.Estado.PENDIENTE,
                         PagoMensual.Estado.VENCIDO],
                        weights=[6, 3, 1],
                    )[0]
                else:
                    estado = PagoMensual.Estado.PAGADO
                PagoMensual.objects.create(
                    gimnasio=gimnasio, alumno=alumno, mes=mes, anio=anio,
                    monto=Decimal("28000"),
                    estado=estado,
                    fecha_pago=hoy - timedelta(days=desplazamiento * 30)
                    if estado == PagoMensual.Estado.PAGADO else None,
                    medio_pago_texto="Transferencia" if estado == PagoMensual.Estado.PAGADO else "",
                )
                pagos += 1
        resumen["pagos"] = pagos

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
    """Saca exactamente lo que sembró `sembrar_demo` (los alumnos marcados y
    todo lo que cuelga de ellos). No toca ejercicios, plantillas ni novedades:
    son inofensivos y puede haberlos editado alguien."""
    from alumnos.models import Alumno
    from novedades.models import NovedadLeida
    from pagos.models import PagoMensual
    from rutinas.models import RutinaAsignada, RutinaAsignadaDiaCompletado
    from turnos.models import Reserva

    with transaction.atomic():
        alumnos = Alumno.objects.for_gimnasio(gimnasio).filter(observaciones=MARCA)
        borrados = alumnos.count()
        RutinaAsignadaDiaCompletado.objects.filter(
            rutina_asignada__alumno__in=alumnos
        ).delete()
        # PROTECT en pagos y rutinas: hay que sacarlos antes que el alumno.
        RutinaAsignada.objects.filter(alumno__in=alumnos).delete()
        PagoMensual.objects.filter(alumno__in=alumnos).delete()
        Reserva.objects.filter(alumno__in=alumnos).delete()
        NovedadLeida.objects.filter(alumno__in=alumnos).delete()
        alumnos.delete()
    return borrados
