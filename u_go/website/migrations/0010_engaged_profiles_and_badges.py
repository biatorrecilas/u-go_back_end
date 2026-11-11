from django.db import migrations, models
from django.db.models import Q


BADGES = [
    ('gentileza', 'Gentileza no caminho', 'Tratou todos com atenção e cordialidade.', 'feedback', '#22c55e'),
    ('pontualidade', 'Pontualidade', 'Respeitou o horário combinado.', 'feedback', '#3b82f6'),
    ('direcao_segura', 'Direção segura', 'Conduziu com cuidado e responsabilidade.', 'feedback', '#8b5cf6'),
    ('boa_conversa', 'Boa companhia', 'Deixou o trajeto mais leve e agradável.', 'feedback', '#f59e0b'),
    ('respeito', 'Respeito sempre', 'Respeitou limites, preferências e combinados.', 'feedback', '#ec4899'),
    ('rota_consciente', 'Rota consciente', 'Colaborou para uma viagem eficiente e sustentável.', 'feedback', '#14b8a6'),
    ('primeira_carona', 'Primeiro passo', 'Conclua sua primeira carona compartilhada.', 'achievement', '#22c55e'),
    ('cinco_caronas', 'Pegando ritmo', 'Conclua 5 caronas compartilhadas.', 'achievement', '#3b82f6'),
    ('vinte_caronas', 'Movimenta a comunidade', 'Conclua 20 caronas compartilhadas.', 'achievement', '#8b5cf6'),
    ('eco_10kg', 'Folha verde', 'Ajude a evitar 10 kg de CO₂.', 'achievement', '#10b981'),
    ('eco_50kg', 'Guardião do planeta', 'Ajude a evitar 50 kg de CO₂.', 'achievement', '#059669'),
    ('reconhecido_5', 'Boa atitude reconhecida', 'Receba 5 reconhecimentos por badges.', 'achievement', '#f59e0b'),
]


def seed_badges(apps, schema_editor):
    Badge = apps.get_model('website', 'Badge')
    for code, name, description, kind, color in BADGES:
        Badge.objects.update_or_create(
            code=code,
            defaults={
                'name': name,
                'description': description,
                'badge_kind': kind,
                'display_color': color,
                'is_active': True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0009_ride_status_start_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='semester',
            field=models.SmallIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='userprofile',
            constraint=models.CheckConstraint(
                condition=Q(semester__isnull=True) | Q(semester__gte=1, semester__lte=12),
                name='user_profile_semester_range',
            ),
        ),
        migrations.RunPython(seed_badges, migrations.RunPython.noop),
    ]
