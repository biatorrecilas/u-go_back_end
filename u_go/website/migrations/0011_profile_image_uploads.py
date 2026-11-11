from django.db import migrations, models
import website.models


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0010_engaged_profiles_and_badges'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='avatar_image',
            field=models.ImageField(blank=True, null=True, upload_to=website.models.profile_avatar_upload_to),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='cover_photo_image',
            field=models.ImageField(blank=True, null=True, upload_to=website.models.profile_cover_upload_to),
        ),
    ]
