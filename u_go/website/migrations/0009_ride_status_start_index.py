from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0008_ride_search_matching_signals'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='ride',
            index=models.Index(fields=['status', 'scheduled_start_at'], name='ride_status_start_idx'),
        ),
    ]
