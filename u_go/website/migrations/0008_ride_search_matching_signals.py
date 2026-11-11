from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0007_notification_delivery_and_searches'),
    ]

    operations = [
        migrations.AddField(
            model_name='ridesearch',
            name='origin_lat',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='ridesearch',
            name='origin_lng',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='ridesearch',
            name='destination_lat',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='ridesearch',
            name='destination_lng',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='ridesearch',
            name='vehicle_type',
            field=models.CharField(blank=True, choices=[('car', 'Car'), ('motorcycle', 'Motorcycle')], max_length=20),
        ),
        migrations.AddField(
            model_name='ridesearch',
            name='prefer_deviation',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ridesearch',
            name='max_distance_km',
            field=models.DecimalField(decimal_places=1, default=8, max_digits=5),
        ),
        migrations.AddField(
            model_name='ridesearch',
            name='time_flexibility_minutes',
            field=models.SmallIntegerField(default=90),
        ),
    ]
