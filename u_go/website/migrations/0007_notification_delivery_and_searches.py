import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0006_essential_ride_flow'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='event_key',
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
        migrations.CreateModel(
            name='RideSearch',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('origin_text', models.CharField(blank=True, max_length=150)),
                ('destination_text', models.CharField(blank=True, max_length=150)),
                ('requested_date', models.DateField(blank=True, null=True)),
                ('requested_time', models.TimeField(blank=True, null=True)),
                ('gender_restriction', models.CharField(choices=[('none', 'None'), ('women_only', 'Women Only'), ('men_only', 'Men Only'), ('non_binary_only', 'Non-binary Only'), ('other_only', 'Other Only')], default='none', max_length=30)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ride_searches', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='ridesearch',
            index=models.Index(fields=['user', '-created_at'], name='website_rid_user_id_918c9f_idx'),
        ),
    ]
