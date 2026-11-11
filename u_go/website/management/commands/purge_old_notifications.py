from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from website.models import Notification, RideSearch


class Command(BaseCommand):
    help = 'Remove notificações já lidas e sinais de busca antigos conforme a retenção configurada.'

    def add_arguments(self, parser):
        parser.add_argument('--notification-days', type=int, default=180)
        parser.add_argument('--search-days', type=int, default=90)

    def handle(self, *args, **options):
        notification_days = options['notification_days']
        search_days = options['search_days']
        if notification_days < 1 or search_days < 1:
            raise CommandError('Os prazos de retenção precisam ser positivos.')

        now = timezone.now()
        notification_count, _ = Notification.objects.filter(
            is_read=True,
            created_at__lt=now - timedelta(days=notification_days),
        ).delete()
        search_count, _ = RideSearch.objects.filter(
            created_at__lt=now - timedelta(days=search_days),
        ).delete()
        self.stdout.write(self.style.SUCCESS(
            f'{notification_count} notificação(ões) e {search_count} busca(s) removidas.',
        ))
