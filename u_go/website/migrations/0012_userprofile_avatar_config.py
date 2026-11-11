from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0011_profile_image_uploads'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='avatar_config',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
