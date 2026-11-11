from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from website.models import (
    Badge,
    Course,
    GenderRestriction,
    Ride,
    RideBadgeFeedback,
    RidePassenger,
    RideRequest,
    RideSeat,
    User,
    UserProfile,
    University,
)
from website.services.profiles import sync_user_achievements


DEMO_PASSWORD = 'UGoDemo2026!'
DEMO_DOMAIN = 'mail.usf.edu.br'


class Command(BaseCommand):
    help = 'Cria dados locais de demonstração para capturas de tela do U-Go.'

    def handle(self, *args, **options):
        university = University.objects.get(name='USF Sagrado')
        course = Course.objects.filter(university=university, is_active=True).first()
        if course is None:
            self.stderr.write(self.style.ERROR('Cadastre ao menos um curso ativo na USF Sagrado.'))
            return

        account_specs = [
            ('demo.motorista', 'Marina Demo', User.VisibilityGroup.WOMAN,
             'Ofereço caronas entre o Centro de Campinas e o campus USF Sagrado.'),
            ('demo.passageira', 'Lia Demo', User.VisibilityGroup.WOMAN,
             'Procuro caronas seguras para chegar ao campus pela manhã.'),
        ]
        accounts = {}
        for local_part, name, visibility_group, bio in account_specs:
            email = f'{local_part}@{DEMO_DOMAIN}'
            user, _ = User.objects.get_or_create(
                email=email,
                defaults={
                    'full_name': name,
                    'phone': '11999990000',
                    'university': university,
                    'course': course,
                    'ride_visibility_group': visibility_group,
                    'account_status': User.AccountStatus.ACTIVE,
                    'is_email_verified': True,
                },
            )
            user.full_name = name
            user.phone = '11999990000'
            user.university = university
            user.course = course
            user.ride_visibility_group = visibility_group
            user.account_status = User.AccountStatus.ACTIVE
            user.is_email_verified = True
            user.set_password(DEMO_PASSWORD)
            user.save()
            UserProfile.objects.update_or_create(user=user, defaults={'bio': bio, 'semester': 4})
            accounts[local_part] = user

        main_passenger = User.objects.get(email=f'demo.u-go@{DEMO_DOMAIN}')
        main_passenger.account_status = User.AccountStatus.ACTIVE
        main_passenger.is_email_verified = True
        main_passenger.save(update_fields=['account_status', 'is_email_verified', 'updated_at'])
        accounts['demo.u-go'] = main_passenger

        driver = accounts['demo.motorista']
        woman_driver = driver
        woman_passenger = accounts['demo.passageira']
        now = timezone.now()

        def ride(marker, driver_user, days, *, origin, restriction='none', status=Ride.RideStatus.SCHEDULED,
                 distance=None, notes='Carona de demonstração do U-Go.'):
            scheduled_start_at = timezone.localtime(now + timedelta(days=days)).replace(
                hour=7, minute=30, second=0, microsecond=0,
            )
            defaults = {
                'origin_text': origin,
                'destination_text': 'USF Sagrado - Campus Swift, Campinas',
                'scheduled_start_at': scheduled_start_at,
                'vehicle_type': 'car',
                'vehicle_model': 'Honda Fit EX 2020',
                'vehicle_plate': 'UGO1D23',
                'seat_capacity': 4,
                'driver_contact_phone': driver_user.phone,
                'gender_restriction': restriction,
                'price_amount': '7.50',
                'distance_km': distance,
                'duration_minutes': 35 if distance is None else 55,
                'notes': notes,
                'accept_deviation': True,
                'deviation_distance_km': '3.00',
                'deviation_time_min': 10,
                'status': status,
                'started_at': (scheduled_start_at - timedelta(minutes=5)) if status == Ride.RideStatus.COMPLETED else None,
                'completed_at': (scheduled_start_at + timedelta(minutes=55)) if status == Ride.RideStatus.COMPLETED else None,
            }
            obj = Ride.objects.filter(
                driver_user=driver_user,
                notes__contains=f'[{marker}]',
            ).first()
            if obj is None:
                obj = Ride.objects.filter(driver_user=driver_user, notes=notes).first()
            if obj is None:
                obj = Ride(driver_user=driver_user)
            for field, value in defaults.items():
                setattr(obj, field, value)
            obj.save()
            for seat_number in range(1, 5):
                RideSeat.objects.get_or_create(ride=obj, seat_number=seat_number)
            return obj

        def request(ride_obj, passenger, status, *, seat_number=1, contact_unlocked=False):
            decision_at = now if status != RideRequest.RequestStatus.PENDING else None
            req, _ = RideRequest.objects.update_or_create(
                ride=ride_obj,
                passenger_user=passenger,
                defaults={
                    'seat_number': seat_number,
                    'status': status,
                    'driver_decision_at': decision_at,
                    'passenger_decision_at': now if status == RideRequest.RequestStatus.CONFIRMED else None,
                    'contact_unlocked_at': now if contact_unlocked else None,
                },
            )
            seat = RideSeat.objects.get(ride=ride_obj, seat_number=seat_number)
            if status == RideRequest.RequestStatus.APPROVED:
                seat.seat_status = RideSeat.SeatStatus.RESERVED
                seat.reserved_for_request = req
                seat.occupant_user = None
                seat.save()
            elif status == RideRequest.RequestStatus.CONFIRMED:
                seat.seat_status = RideSeat.SeatStatus.OCCUPIED
                seat.reserved_for_request = None
                seat.occupant_user = passenger
                seat.save()
                RidePassenger.objects.update_or_create(
                    ride=ride_obj,
                    passenger_user=passenger,
                    defaults={'seat_number': seat_number, 'completed_at': None},
                )
            else:
                seat.seat_status = RideSeat.SeatStatus.AVAILABLE
                seat.reserved_for_request = None
                seat.occupant_user = None
                seat.save()
            return req

        pending_ride = ride(
            'pending-request', driver, 2, origin='Centro, Campinas',
            notes='Centro → USF Sagrado • solicitação aguardando aprovação.',
        )
        request(pending_ride, main_passenger, RideRequest.RequestStatus.PENDING)

        approved_ride = ride(
            'awaiting-passenger', woman_driver, 3, origin='Taquaral, Campinas',
            restriction=GenderRestriction.WOMEN_ONLY,
            notes='Taquaral → USF Sagrado • motorista aprovou; falta confirmar.',
        )
        request(approved_ride, woman_passenger, RideRequest.RequestStatus.APPROVED)

        confirmed_ride = ride(
            'confirmed-contact', driver, 4, origin='Cambuí, Campinas',
            notes='Cambuí → USF Sagrado • match confirmado e contato liberado.',
        )
        request(
            confirmed_ride,
            main_passenger,
            RideRequest.RequestStatus.CONFIRMED,
            contact_unlocked=True,
        )

        ride(
            'open-search-result', woman_driver, 5, origin='Barão Geraldo, Campinas',
            restriction=GenderRestriction.WOMEN_ONLY,
            notes='Barão Geraldo → USF Sagrado • carona aberta para busca por preferência.',
        )

        for marker, days, origin in [
            ('open-search-women-cambui', 6, 'Cambuí, Campinas'),
            ('open-search-women-taquaral', 7, 'Taquaral, Campinas'),
            ('open-search-women-flamboyant', 8, 'Jardim Flamboyant, Campinas'),
        ]:
            ride(
                marker, woman_driver, days, origin=origin,
                restriction=GenderRestriction.WOMEN_ONLY,
                notes=f'[{marker}] Carona exclusiva para mulheres • {origin} → USF Sagrado.',
            )

        completed_rides = []
        for marker, past_days, origin in [
            ('completed-october-1', -14, 'Cambuí, Campinas'),
            ('completed-october-2', -7, 'Centro, Campinas'),
        ]:
            completed = ride(
                marker, driver, past_days, origin=origin,
                status=Ride.RideStatus.COMPLETED,
                distance=28,
                notes=f'Carona concluída de demonstração • saída de {origin}.',
            )
            RideSeat.objects.filter(ride=completed, seat_number=1).update(
                seat_status=RideSeat.SeatStatus.OCCUPIED,
                occupant_user=main_passenger,
            )
            RidePassenger.objects.update_or_create(
                ride=completed,
                passenger_user=main_passenger,
                defaults={'seat_number': 1, 'completed_at': completed.completed_at},
            )
            completed_rides.append(completed)

        driver_evaluation_ride = ride(
            'driver-evaluation-pending', driver, -3, origin='Taquaral, Campinas',
            status=Ride.RideStatus.COMPLETED,
            distance=28,
            notes='[driver-evaluation-pending] Carona concluída com feedback do motorista pendente.',
        )
        RideSeat.objects.filter(ride=driver_evaluation_ride, seat_number=1).update(
            seat_status=RideSeat.SeatStatus.OCCUPIED,
            occupant_user=main_passenger,
        )
        RidePassenger.objects.update_or_create(
            ride=driver_evaluation_ride,
            passenger_user=main_passenger,
            defaults={'seat_number': 1, 'completed_at': driver_evaluation_ride.completed_at},
        )

        feedback_badges = [
            ('gentileza-demo', 'Gentileza', 'Demonstra cuidado e respeito durante a viagem.', '#f97316'),
            ('pontualidade-demo', 'Pontualidade', 'Chega no horário combinado.', '#2563eb'),
            ('viagem-segura-demo', 'Viagem segura', 'Condução atenta e viagem tranquila.', '#16a34a'),
        ]
        saved_badges = []
        for code, name, description, color in feedback_badges:
            badge, _ = Badge.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'description': description,
                    'badge_kind': Badge.BadgeKind.FEEDBACK,
                    'display_color': color,
                    'is_active': True,
                },
            )
            saved_badges.append(badge)

        for index, completed in enumerate(completed_rides):
            RideBadgeFeedback.objects.get_or_create(
                ride=completed,
                giver_user=driver,
                receiver_user=main_passenger,
                badge=saved_badges[index],
            )
        RideBadgeFeedback.objects.get_or_create(
            ride=completed_rides[0],
            giver_user=main_passenger,
            receiver_user=driver,
            badge=saved_badges[1],
        )

        achievement_badges = [
            ('primeira_carona', 'Eco Iniciante', 'Conclua sua primeira carona compartilhada.', '#16a34a'),
            ('cinco_caronas', 'Comunidade Ativa', 'Participe de cinco caronas concluídas.', '#7c3aed'),
            ('eco_10kg', 'Impacto Verde', 'Evite 10 kg de CO₂ com caronas compartilhadas.', '#059669'),
            ('reconhecido_5', 'Boa Companhia', 'Receba cinco reconhecimentos da comunidade.', '#d97706'),
        ]
        for code, name, description, color in achievement_badges:
            Badge.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'description': description,
                    'badge_kind': Badge.BadgeKind.ACHIEVEMENT,
                    'display_color': color,
                    'is_active': True,
                },
            )

        for user in accounts.values():
            sync_user_achievements(user)

        self.stdout.write(self.style.SUCCESS('Dados de demonstração preparados.'))
        self.stdout.write('Solicitação pendente: /acompanhar-carona como demo.motorista.')
        self.stdout.write('Confirmação pendente: /acompanhar-carona como demo.passageira.')
        self.stdout.write('Busca feminina: /caronas como demo.passageira.')
        self.stdout.write('Perfil, impacto e badges: /perfil como demo.u-go.')
        self.stdout.write(f'Senha das contas demo.motorista e demo.passageira: {DEMO_PASSWORD}')
