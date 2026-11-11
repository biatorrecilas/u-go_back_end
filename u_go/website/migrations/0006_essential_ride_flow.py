# Generated for the complete U-Go ride lifecycle.
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0005_merge_20260619_1411'),
    ]

    operations = [
        migrations.AddField(
            model_name='rideseries',
            name='end_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RemoveConstraint(
            model_name='rideseriesweekday',
            name='ride_series_weekday_range',
        ),
        migrations.AddConstraint(
            model_name='rideseriesweekday',
            constraint=models.CheckConstraint(
                condition=Q(weekday__gte=1, weekday__lte=7),
                name='ride_series_weekday_range',
            ),
        ),
        migrations.AlterField(
            model_name='ride',
            name='status',
            field=models.CharField(choices=[('scheduled', 'Scheduled'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='scheduled', max_length=20),
        ),
        migrations.AlterField(
            model_name='ridestatushistory',
            name='from_status',
            field=models.CharField(blank=True, choices=[('scheduled', 'Scheduled'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], max_length=20, null=True),
        ),
        migrations.AlterField(
            model_name='ridestatushistory',
            name='to_status',
            field=models.CharField(choices=[('scheduled', 'Scheduled'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], max_length=20),
        ),
        migrations.AlterField(
            model_name='riderequest',
            name='status',
            field=models.CharField(choices=[('pending_driver', 'Pending Driver'), ('approved_by_driver', 'Approved by Driver'), ('confirmed', 'Confirmed'), ('rejected_by_driver', 'Rejected by Driver'), ('declined_by_passenger', 'Declined by Passenger'), ('cancelled_by_passenger', 'Cancelled by Passenger')], default='pending_driver', max_length=30),
        ),
        migrations.AlterField(
            model_name='riderequeststatushistory',
            name='from_status',
            field=models.CharField(blank=True, choices=[('pending_driver', 'Pending Driver'), ('approved_by_driver', 'Approved by Driver'), ('confirmed', 'Confirmed'), ('rejected_by_driver', 'Rejected by Driver'), ('declined_by_passenger', 'Declined by Passenger'), ('cancelled_by_passenger', 'Cancelled by Passenger')], max_length=30, null=True),
        ),
        migrations.AlterField(
            model_name='riderequeststatushistory',
            name='to_status',
            field=models.CharField(choices=[('pending_driver', 'Pending Driver'), ('approved_by_driver', 'Approved by Driver'), ('confirmed', 'Confirmed'), ('rejected_by_driver', 'Rejected by Driver'), ('declined_by_passenger', 'Declined by Passenger'), ('cancelled_by_passenger', 'Cancelled by Passenger')], max_length=30),
        ),
        migrations.AlterField(
            model_name='notification',
            name='type',
            field=models.CharField(choices=[('request', 'Request'), ('approval', 'Approval'), ('reminder', 'Reminder'), ('profile', 'Profile'), ('ride_update', 'Ride update'), ('status', 'Status change'), ('match', 'Match recommendation')], max_length=20),
        ),
    ]
