from django.core.management.base import BaseCommand

from website.services.notifications import send_upcoming_ride_reminders


class Command(BaseCommand):
    help = 'Cria lembretes deduplicados para caronas confirmadas nas próximas 24 horas.'

    def handle(self, *args, **options):
        created = send_upcoming_ride_reminders()
        self.stdout.write(self.style.SUCCESS(f'{created} lembrete(s) criado(s).'))
