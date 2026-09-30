"""`suscripcion/avisos.py`: el mail de vencimiento al staff del gimnasio.

`TransactionTestCase`: el mail sale por `transaction.on_commit`, que bajo
`TestCase` no corre jamás (ver `tests_services.py`).
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from plataforma import precios
from suscripcion import avisos
from suscripcion.models import AvisoDeCobro
from tenants.models import Gimnasio, Perfil

Tipo = AvisoDeCobro.Tipo


def _gimnasio(slug, *, vence_en, alumnos=2, **extra):
    """Un gimnasio que nunca pagó y cuyo primer cobro vence en `vence_en`
    días (negativo: ya venció). Sin pagos, el vencimiento es fin de prueba."""
    hoy = timezone.localdate()
    inicio = hoy + timedelta(days=vence_en) - timedelta(days=precios.DIAS_GRATIS)
    gimnasio = Gimnasio.objects.create(
        nombre=slug.title(), slug=slug, facturacion_inicio=inicio, **extra
    )
    for i in range(alumnos):
        Alumno.objects.create(gimnasio=gimnasio, nombre=f"A{i}", apellido="X")
    return gimnasio


def _usuario(gimnasio, email, *, rol=Perfil.Rol.STAFF, activo=True):
    usuario = User.objects.create_user(
        username=f"{gimnasio.slug}-{email}", email=email, password="x", is_active=activo
    )
    Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=rol)
    return usuario


class AvisarCobrosTests(TransactionTestCase):
    def _asuntos(self):
        return [m.subject for m in mail.outbox]

    def test_por_vencer_dentro_de_la_ventana(self):
        g = _gimnasio("por-vencer", vence_en=precios.DIAS_AVISO_COBRO)
        _usuario(g, "dueno@x.com")
        self.assertEqual(avisos.avisar(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["dueno@x.com"])
        self.assertIn("vence pronto", mail.outbox[0].subject)
        self.assertIn("https://www.tugimapp.com/", mail.outbox[0].body)
        self.assertTrue(AvisoDeCobro.objects.filter(gimnasio=g, tipo=Tipo.POR_VENCER).exists())

    def test_vence_hoy(self):
        g = _gimnasio("hoy", vence_en=0)
        _usuario(g, "dueno@x.com")
        self.assertEqual(avisos.avisar(), 1)
        self.assertIn("vence hoy", mail.outbox[0].subject)
        self.assertTrue(AvisoDeCobro.objects.filter(gimnasio=g, tipo=Tipo.VENCE_HOY).exists())

    def test_vencido(self):
        g = _gimnasio("vencido", vence_en=-3)
        _usuario(g, "dueno@x.com")
        self.assertEqual(avisos.avisar(), 1)
        self.assertIn("vencida", mail.outbox[0].subject)
        self.assertTrue(AvisoDeCobro.objects.filter(gimnasio=g, tipo=Tipo.VENCIDO).exists())

    def test_el_mismo_dia_dos_veces_manda_un_solo_mail(self):
        g = _gimnasio("dedup", vence_en=3)
        _usuario(g, "dueno@x.com")
        self.assertEqual(avisos.avisar(), 1)
        self.assertEqual(avisos.avisar(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(AvisoDeCobro.objects.count(), 1)

    def test_el_dia_siguiente_el_tipo_cambia_y_manda_otro(self):
        g = _gimnasio("cambia", vence_en=1)
        _usuario(g, "dueno@x.com")
        hoy = timezone.localdate()
        avisos.avisar(hoy)
        avisos.avisar(hoy + timedelta(days=1))
        self.assertEqual(len(mail.outbox), 2)

    def test_demo_y_exento_no_reciben_nada(self):
        demo = _gimnasio("demo", vence_en=-5, es_demo=True)
        exento = _gimnasio("exento", vence_en=-5, facturacion_exenta=True)
        _usuario(demo, "demo@x.com")
        _usuario(exento, "exento@x.com")
        self.assertEqual(avisos.avisar(), 0)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(AvisoDeCobro.objects.count(), 0)

    def test_al_dia_no_recibe_nada(self):
        g = _gimnasio("aldia", vence_en=20)
        _usuario(g, "dueno@x.com")
        self.assertEqual(avisos.avisar(), 0)
        self.assertEqual(mail.outbox, [])

    def test_solo_el_staff_activo_con_email(self):
        g = _gimnasio("dest", vence_en=2)
        _usuario(g, "staff@x.com")
        _usuario(g, "otro@x.com")
        _usuario(g, "alumno@x.com", rol=Perfil.Rol.ALUMNO)
        _usuario(g, "baja@x.com", activo=False)
        _usuario(g, "")
        avisos.avisar()
        self.assertEqual(len(mail.outbox), 1)
        self.assertCountEqual(mail.outbox[0].to, ["staff@x.com", "otro@x.com"])

    def test_costo_de_queries_constante_con_mas_gimnasios(self):
        def armar(n, prefijo):
            for i in range(n):
                g = _gimnasio(f"{prefijo}{i}", vence_en=2)
                _usuario(g, f"{prefijo}{i}@x.com")

        armar(2, "chico")
        with CaptureQueriesContext(connection) as chico:
            avisos.avisar()
        AvisoDeCobro.objects.all().delete()
        armar(4, "grande")  # ahora hay 6 candidatos
        with CaptureQueriesContext(connection) as grande:
            avisos.avisar()
        # Por aviso entran el dedup (SELECT + INSERT + savepoints): eso crece
        # con la cantidad de avisos y es esperado. Lo que NO puede crecer es
        # el resto (monitor, destinatarios, precios): ninguna query por gimnasio.
        self.assertEqual(self._fijas(chico), self._fijas(grande))
        self.assertGreater(len(grande), len(chico))

    @staticmethod
    def _fijas(captura):
        """Queries que no son del dedup por gimnasio (monitor + destinatarios)."""
        return sum(
            1
            for q in captura.captured_queries
            if "suscripcion_avisodecobro" not in q["sql"]
            and q["sql"].split()[0].upper() not in {"BEGIN", "COMMIT", "SAVEPOINT", "RELEASE"}
        )
