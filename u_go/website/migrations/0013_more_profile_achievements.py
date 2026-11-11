from django.db import migrations


BADGES = [
    ('cinquenta_caronas', 'Sempre em movimento', 'Conclua 50 caronas compartilhadas.', '#7c3aed'),
    ('cem_caronas', 'Centena compartilhada', 'Conclua 100 caronas compartilhadas.', '#6d28d9'),
    ('eco_100kg', 'Impacto que cresce', 'Ajude a evitar 100 kg de CO2.', '#047857'),
    ('eco_250kg', 'Legado sustentável', 'Ajude a evitar 250 kg de CO2.', '#065f46'),
    ('reconhecido_10', 'Companhia que inspira', 'Receba 10 reconhecimentos por boas atitudes.', '#ea580c'),
    ('reconhecido_25', 'Referência na comunidade', 'Receba 25 reconhecimentos por boas atitudes.', '#c2410c'),
]


def seed_more_achievements(apps, schema_editor):
    Badge = apps.get_model('website', 'Badge')
    for code, name, description, color in BADGES:
        Badge.objects.update_or_create(
            code=code,
            defaults={
                'name': name,
                'description': description,
                'badge_kind': 'achievement',
                'display_color': color,
                'is_active': True,
            },
        )


def remove_more_achievements(apps, schema_editor):
    Badge = apps.get_model('website', 'Badge')
    Badge.objects.filter(code__in=[item[0] for item in BADGES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0012_userprofile_avatar_config'),
    ]

    operations = [
        migrations.RunPython(seed_more_achievements, remove_more_achievements),
    ]
