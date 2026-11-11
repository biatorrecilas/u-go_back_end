# Generated migration to add deviation fields
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('website', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='rideseries',
            name='accept_deviation',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='rideseries',
            name='deviation_distance_km',
            field=models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='rideseries',
            name='deviation_time_min',
            field=models.SmallIntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='ride',
            name='accept_deviation',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='ride',
            name='deviation_distance_km',
            field=models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='ride',
            name='deviation_time_min',
            field=models.SmallIntegerField(null=True, blank=True),
        ),
    ]
