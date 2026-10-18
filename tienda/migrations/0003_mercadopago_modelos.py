"""Mercado Pago de la tienda: cuenta del gimnasio y campos de config/pedido.

`referencia_externa` nace NULL y sin `unique` (0003): agregar una columna única
con un `default` callable le daría el MISMO uuid a todos los pedidos que ya
existen. 0004 los llena y 0005 la vuelve única. Tres migraciones y no una:
en Postgres, mezclar el ALTER y el UPDATE masivo en una transacción es frágil.
"""

import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import calendario.fields


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0015_gimnasio_tienda_habilitada'),
        ('tienda', '0002_fotoproducto'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CuentaMercadoPago',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('modificado', models.DateTimeField(auto_now=True)),
                ('access_token', calendario.fields.EncryptedTextField(blank=True, clave_setting='MP_TOKEN_ENCRYPTION_KEY', default='')),
                ('refresh_token', calendario.fields.EncryptedTextField(blank=True, clave_setting='MP_TOKEN_ENCRYPTION_KEY', default='')),
                ('mp_user_id', models.CharField(blank=True, max_length=40)),
                ('token_expira', models.DateTimeField(blank=True, null=True)),
                ('uuid_webhook', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('es_de_prueba', models.BooleanField(default=False)),
                ('estado', models.CharField(choices=[('conectada', 'Conectada'), ('reconectar', 'Hay que reconectarla'), ('desconectada', 'Desconectada')], default='conectada', max_length=20)),
                ('conectada_en', models.DateTimeField(blank=True, null=True)),
                ('desconectada_en', models.DateTimeField(blank=True, null=True)),
                ('conectada_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('gimnasio', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='%(class)ss', to='tenants.gimnasio', verbose_name='gimnasio')),
            ],
            options={
                'verbose_name': 'cuenta de Mercado Pago',
                'verbose_name_plural': 'cuentas de Mercado Pago',
            },
        ),
        migrations.AddConstraint(
            model_name='cuentamercadopago',
            constraint=models.UniqueConstraint(fields=('gimnasio',), name='cuenta_mp_unica_por_gimnasio'),
        ),
        migrations.AddField(
            model_name='configuraciontienda',
            name='acepta_transferencia',
            field=models.BooleanField(db_default=True, default=True, verbose_name='aceptar transferencia'),
        ),
        migrations.AddField(
            model_name='configuraciontienda',
            name='acepta_mercadopago',
            field=models.BooleanField(db_default=False, default=False, verbose_name='aceptar Mercado Pago'),
        ),
        migrations.AddField(
            model_name='configuraciontienda',
            name='minutos_reserva_mp',
            field=models.PositiveSmallIntegerField(db_default=30, default=30, help_text='Si el alumno no paga en este plazo, el pedido vence y el stock vuelve.', validators=[django.core.validators.MinValueValidator(20), django.core.validators.MaxValueValidator(120)], verbose_name='minutos para pagar con Mercado Pago'),
        ),
        migrations.AlterField(
            model_name='pedido',
            name='medio',
            field=models.CharField(choices=[('transferencia', 'Transferencia'), ('mercadopago', 'Mercado Pago')], default='transferencia', max_length=20),
        ),
        migrations.AlterField(
            model_name='pedido',
            name='estado',
            field=models.CharField(choices=[('pendiente_pago', 'Esperando el pago'), ('en_revision', 'Comprobante en revisión'), ('pagado', 'Pagado, para retirar'), ('entregado', 'Entregado'), ('vencido', 'Vencido'), ('cancelado', 'Cancelado'), ('devuelto', 'Devuelto'), ('revisar', 'Pagado, a revisar')], default='pendiente_pago', max_length=20),
        ),
        migrations.AddField(
            model_name='pedido',
            name='referencia_externa',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='pedido',
            name='preference_id',
            field=models.TextField(blank=True, default='', db_default=''),
        ),
        migrations.AddField(
            model_name='pedido',
            name='init_point',
            field=models.TextField(blank=True, default='', db_default=''),
        ),
        migrations.AddField(
            model_name='pedido',
            name='payment_id',
            field=models.TextField(blank=True, default='', db_default=''),
        ),
        migrations.AddField(
            model_name='pedido',
            name='estado_mp',
            field=models.TextField(blank=True, default='', db_default=''),
        ),
        migrations.AddField(
            model_name='pedido',
            name='respuesta_mp',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pedido',
            name='revision_mp_pendiente',
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddConstraint(
            model_name='pedido',
            constraint=models.UniqueConstraint(condition=models.Q(('payment_id', ''), _negated=True), fields=('payment_id',), name='pedido_payment_id_unico'),
        ),
    ]
