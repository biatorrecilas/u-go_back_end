import json
import re
from io import BytesIO
import tempfile
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase
from django.test import override_settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image

from .models import (
    Course,
    Badge,
    Notification,
    Ride,
    RideBadgeFeedback,
    RidePassenger,
    RideRequest,
    RideSearch,
    RideSeat,
    University,
    User,
)
from .checks import check_production_email
from .services.notifications import (
    create_notification,
    notify_matching_users,
    send_upcoming_ride_reminders,
)
from .services.matching import haversine_km, text_similarity
from u_go.asgi import application


class EssentialRideFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.university = University.objects.create(name='USF Sagrado', email_domain='mail.usf.edu.br')
        self.course = Course.objects.create(university=self.university, name='Engenharia')
        self.driver = self.create_user('motorista@mail.usf.edu.br', 'man', 'Motorista Teste')
        self.woman = self.create_user('aluna@mail.usf.edu.br', 'woman', 'Aluna Teste')
        self.man = self.create_user('aluno@mail.usf.edu.br', 'man', 'Aluno Teste')

    def create_user(self, email, visibility_group, name):
        return User.objects.create_user(
            email=email,
            password='SenhaForte123!',
            full_name=name,
            phone='11999999999',
            university=self.university,
            course=self.course,
            ride_visibility_group=visibility_group,
            account_status=User.AccountStatus.ACTIVE,
            is_email_verified=True,
        )

    def create_ride(self, *, restriction='none', starts_in=timedelta(days=3)):
        ride = Ride.objects.create(
            driver_user=self.driver,
            origin_text='Centro, Campinas',
            destination_text='USF Sagrado',
            scheduled_start_at=timezone.now() + starts_in,
            vehicle_type='car',
            vehicle_model='Honda Fit',
            vehicle_plate='ABC1D23',
            seat_capacity=4,
            driver_contact_phone=self.driver.phone,
            gender_restriction=restriction,
            price_amount='8.00',
        )
        RideSeat.objects.bulk_create([
            RideSeat(ride=ride, seat_number=number)
            for number in range(1, 5)
        ])
        return ride

    def test_exclusive_ride_is_only_visible_to_compatible_profiles(self):
        ride = self.create_ride(restriction='women_only')

        self.client.force_authenticate(self.man)
        man_response = self.client.get('/api/rides/?status=scheduled')
        self.assertEqual(man_response.status_code, 200)
        self.assertNotIn(str(ride.id), {item['id'] for item in man_response.data})

        self.client.force_authenticate(self.woman)
        woman_response = self.client.get('/api/rides/?status=scheduled')
        self.assertIn(str(ride.id), {item['id'] for item in woman_response.data})

        self.client.force_authenticate(self.driver)
        driver_response = self.client.get('/api/rides/?status=scheduled')
        self.assertNotIn(str(ride.id), {item['id'] for item in driver_response.data})

    def test_contact_is_unlocked_only_after_both_decisions(self):
        ride = self.create_ride()
        self.client.force_authenticate(self.man)
        request_response = self.client.post('/api/ride-requests/', {'ride': str(ride.id)}, format='json')
        self.assertEqual(request_response.status_code, 201, request_response.data)
        request_id = request_response.data['id']
        self.assertTrue(Notification.objects.filter(recipient_user=self.driver, type='request').exists())

        self.client.force_authenticate(self.driver)
        approval_response = self.client.post(f'/api/ride-requests/{request_id}/approve/', {}, format='json')
        self.assertEqual(approval_response.status_code, 200)

        self.client.force_authenticate(self.man)
        approved_tracking = self.client.get('/api/rides/tracking/?type=passenger').data[0]
        self.assertEqual(approved_tracking['driver_user']['full_name'], self.driver.full_name)
        self.assertIsNone(approved_tracking['driver_contact_phone'])
        self.assertIsNone(approved_tracking['vehicle_plate'])

        confirm_response = self.client.post(f'/api/ride-requests/{request_id}/confirm/', {}, format='json')
        self.assertEqual(confirm_response.status_code, 200)
        confirmed_tracking = self.client.get('/api/rides/tracking/?type=passenger').data[0]
        self.assertEqual(confirmed_tracking['driver_contact_phone'], self.driver.phone)
        self.assertEqual(confirmed_tracking['vehicle_plate'], 'ABC1D23')
        self.assertTrue(RidePassenger.objects.filter(ride=ride, passenger_user=self.man).exists())

    def test_driver_and_passenger_cancellations_enforce_one_day_deadline(self):
        late_ride = self.create_ride(starts_in=timedelta(hours=12))
        self.client.force_authenticate(self.driver)
        late_response = self.client.post(f'/api/rides/{late_ride.id}/cancel/', {}, format='json')
        self.assertEqual(late_response.status_code, 400)

        ride = self.create_ride()
        self.client.force_authenticate(self.man)
        request_response = self.client.post('/api/ride-requests/', {'ride': str(ride.id)}, format='json')
        self.assertEqual(request_response.status_code, 201, request_response.data)
        cancel_response = self.client.post(f"/api/ride-requests/{request_response.data['id']}/cancel/", {}, format='json')
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.data['status'], RideRequest.RequestStatus.CANCELLED)
        self.assertEqual(ride.seats.get(seat_number=request_response.data['seat_number']).seat_status, RideSeat.SeatStatus.AVAILABLE)
        self.assertTrue(Notification.objects.filter(recipient_user=self.driver, title='Participação cancelada').exists())

    def test_recurring_creation_generates_one_card_per_selected_date(self):
        self.client.force_authenticate(self.driver)
        start = (timezone.localdate() + timedelta(days=2))
        end = start + timedelta(days=6)
        weekday_label = ['seg', 'ter', 'qua', 'qui', 'sex', 'sab', 'dom'][start.weekday()]
        response = self.client.post('/api/rides/', {
            'origin_text': 'Centro',
            'destination_text': 'USF Sagrado',
            'scheduled_start_at': None,
            'vehicle_type': 'car',
            'vehicle_model': 'Honda Fit',
            'vehicle_plate': 'ABC1D23',
            'seat_capacity': 4,
            'driver_contact_phone': self.driver.phone,
            'gender_restriction': 'none',
            'price_amount': '8.00',
            'is_recurring': True,
            'recurring_days': [weekday_label],
            'recurring_start_date': start.isoformat(),
            'recurring_end_date': end.isoformat(),
            'departure_time': '07:30',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Ride.objects.filter(driver_user=self.driver, ride_series__isnull=False).count(), 1)

    def test_signup_requires_institutional_email_and_explicit_legal_acceptance(self):
        base_payload = {
            'full_name': 'Nova Pessoa',
            'phone': '11988887777',
            'ride_visibility_group': 'woman',
            'university': str(self.university.id),
            'course': str(self.course.id),
            'password': 'SenhaForte123!',
            'confirm_password': 'SenhaForte123!',
        }
        invalid_email = self.client.post('/api/auth/signup/', {
            **base_payload,
            'email': 'pessoa@gmail.com',
            'accept_terms': True,
        }, format='json')
        self.assertEqual(invalid_email.status_code, 400)
        self.assertIn('email', invalid_email.data)

        missing_terms = self.client.post('/api/auth/signup/', {
            **base_payload,
            'email': 'pessoa@mail.usf.edu.br',
            'accept_terms': False,
        }, format='json')
        self.assertEqual(missing_terms.status_code, 400)
        self.assertIn('accept_terms', missing_terms.data)

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        FRONTEND_URL='https://app.example.com',
    )
    def test_signup_delivers_code_and_verification_activates_account(self):
        mail.outbox.clear()
        response = self.client.post('/api/auth/signup/', {
            'full_name': 'Pessoa Verificada',
            'email': 'verificada@mail.usf.edu.br',
            'phone': '11977776666',
            'ride_visibility_group': 'woman',
            'university': str(self.university.id),
            'course': str(self.course.id),
            'password': 'SenhaForte123!',
            'confirm_password': 'SenhaForte123!',
            'accept_terms': True,
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertNotIn('debug_verification_code', response.data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['verificada@mail.usf.edu.br'])
        code_match = re.search(r'\b(\d{6})\b', mail.outbox[0].body)
        self.assertIsNotNone(code_match)

        user = User.objects.get(email='verificada@mail.usf.edu.br')
        self.assertFalse(user.is_email_verified)
        verification = self.client.post('/api/auth/verify-email/', {
            'email': user.email,
            'code': code_match.group(1),
        }, format='json')
        self.assertEqual(verification.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)
        self.assertEqual(user.account_status, User.AccountStatus.ACTIVE)

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        FRONTEND_URL='https://app.example.com',
        PASSWORD_RESET_TIMEOUT=3600,
    )
    def test_password_reset_email_link_changes_password_once(self):
        mail.outbox.clear()
        response = self.client.post('/api/auth/password-reset/', {'email': self.man.email}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('debug_reset_token', response.data)
        self.assertEqual(len(mail.outbox), 1)
        reset_url = re.search(r'https://app\.example\.com/login\?\S+', mail.outbox[0].body).group(0)
        query = parse_qs(urlparse(reset_url).query)
        payload = {
            'uid': query['reset_uid'][0],
            'token': query['reset_token'][0],
            'password': 'NovaSenhaForte456!',
            'confirm_password': 'NovaSenhaForte456!',
        }

        confirmation = self.client.post('/api/auth/password-reset/confirm/', payload, format='json')
        self.assertEqual(confirmation.status_code, 200)
        self.man.refresh_from_db()
        self.assertTrue(self.man.check_password('NovaSenhaForte456!'))
        reused = self.client.post('/api/auth/password-reset/confirm/', payload, format='json')
        self.assertEqual(reused.status_code, 400)

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='',
        EMAIL_HOST_USER='',
        EMAIL_HOST_PASSWORD='',
        FRONTEND_URL='http://localhost:5173',
        REDIS_URL='',
    )
    def test_deploy_check_rejects_missing_smtp_and_insecure_frontend_url(self):
        error_ids = {error.id for error in check_production_email(None)}
        self.assertIn('website.E002', error_ids)
        self.assertIn('website.E003', error_ids)
        self.assertIn('website.E004', error_ids)

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='',
        EMAIL_HOST_USER='',
        EMAIL_HOST_PASSWORD='',
        FRONTEND_URL='https://app.example.com',
    )
    def test_signup_rolls_back_when_production_email_is_unavailable(self):
        response = self.client.post('/api/auth/signup/', {
            'full_name': 'Cadastro Sem Entrega',
            'email': 'sem-entrega@mail.usf.edu.br',
            'phone': '11966665555',
            'ride_visibility_group': 'man',
            'university': str(self.university.id),
            'course': str(self.course.id),
            'password': 'SenhaForte123!',
            'confirm_password': 'SenhaForte123!',
            'accept_terms': True,
        }, format='json')

        self.assertEqual(response.status_code, 503)
        self.assertFalse(User.objects.filter(email='sem-entrega@mail.usf.edu.br').exists())

    def test_exclusive_ride_cannot_be_requested_by_incompatible_profile(self):
        ride = self.create_ride(restriction='women_only')
        self.client.force_authenticate(self.man)
        response = self.client.post('/api/ride-requests/', {'ride': str(ride.id)}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(RideRequest.objects.filter(ride=ride, passenger_user=self.man).exists())

    def test_pending_requests_consume_distinct_slots_and_update_public_availability(self):
        ride = self.create_ride()
        self.client.force_authenticate(self.woman)
        first = self.client.post('/api/ride-requests/', {'ride': str(ride.id)}, format='json')
        self.client.force_authenticate(self.man)
        second = self.client.post('/api/ride-requests/', {'ride': str(ride.id)}, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertNotEqual(first.data['seat_number'], second.data['seat_number'])

        public_ride = self.client.get(f'/api/rides/{ride.id}/').data
        self.assertEqual(public_ride['available_seat_count'], 2)
        self.assertNotIn(first.data['seat_number'], public_ride['available_seat_numbers'])
        self.assertNotIn(second.data['seat_number'], public_ride['available_seat_numbers'])

    def test_non_driver_cannot_edit_or_cancel_someone_elses_ride(self):
        ride = self.create_ride()
        self.client.force_authenticate(self.man)
        edit = self.client.patch(f'/api/rides/{ride.id}/', {'price_amount': '1.00'}, format='json')
        cancel = self.client.post(f'/api/rides/{ride.id}/cancel/', {}, format='json')
        self.assertEqual(edit.status_code, 403)
        self.assertEqual(cancel.status_code, 403)
        ride.refresh_from_db()
        self.assertEqual(str(ride.price_amount), '8.00')
        self.assertEqual(ride.status, Ride.RideStatus.SCHEDULED)

    def test_public_metrics_are_derived_from_persisted_records(self):
        ride = self.create_ride()
        ride.status = Ride.RideStatus.COMPLETED
        ride.distance_km = 10
        ride.completed_at = timezone.now()
        ride.save(update_fields=['status', 'distance_km', 'completed_at', 'updated_at'])
        RidePassenger.objects.create(ride=ride, passenger_user=self.woman, seat_number=1)
        badge, _ = Badge.objects.get_or_create(
            code='gentileza',
            defaults={'name': 'Gentileza', 'description': 'Pessoa gentil', 'badge_kind': Badge.BadgeKind.FEEDBACK},
        )
        RideBadgeFeedback.objects.create(ride=ride, giver_user=self.driver, receiver_user=self.woman, badge=badge)

        response = self.client.get('/api/public/stats/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['active_users'], 3)
        self.assertEqual(response.data['completed_rides'], 1)
        self.assertEqual(response.data['confirmed_matches'], 1)
        self.assertEqual(response.data['badge_feedback_count'], 1)
        self.assertEqual(response.data['shared_distance_km'], 10.0)
        self.assertEqual(response.data['co2_saved_kg'], 1.92)
        self.assertEqual(response.data['urban_tree_equivalent_count'], 0.03)
        self.assertIn('60 kg de CO₂', response.data['urban_tree_equivalent_methodology'])
        self.assertTrue(response.data['urban_tree_equivalent_source_url'].startswith('https://www.epa.gov/'))
        self.assertNotIn('email', response.data)
        self.assertNotIn('phone', response.data)

    def test_profile_edit_updates_academic_and_private_fields_safely(self):
        another_course = Course.objects.create(university=self.university, name='Arquitetura')
        self.client.force_authenticate(self.woman)
        updated = self.client.patch('/api/me/profile/', {
            'bio': 'Gosto de compartilhar trajetos e boas conversas.',
            'address_text': 'Centro, Campinas',
            'semester': 4,
            'course': str(another_course.id),
            'phone': '(11) 98888-7777',
            'avatar_url': 'https://images.example/avatar.jpg',
            'cover_photo_url': 'https://images.example/cover.jpg',
        }, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.woman.refresh_from_db()
        self.assertEqual(self.woman.course_id, another_course.id)
        self.assertEqual(self.woman.phone, '(11) 98888-7777')
        self.assertEqual(self.woman.profile.semester, 4)

        public = self.client.get(f'/api/users/{self.woman.id}/profile/')
        self.assertEqual(public.status_code, 200, public.data)
        self.assertEqual(public.data['course_name'], 'Arquitetura')
        self.assertEqual(public.data['semester'], 4)
        self.assertNotIn('phone', public.data)
        self.assertNotIn('address_text', public.data)
        self.assertNotIn('email', public.data)

    def test_profile_avatar_customization_is_validated_and_public(self):
        avatar_config = {
            'enabled': True,
            'skin': 'tan',
            'hairColor': 'red',
            'hairStyle': 'braids',
            'hairLength': 'long',
            'facialHair': 'goatee',
            'facialHairColor': 'dark_brown',
            'outfitType': 'jacket',
            'outfitColor': 'blue',
            'headwear': 'beanie',
            'eyewear': 'round_glasses',
            'earAccessory': 'hearing_aid',
            'outfitAccessory': 'animal_pin',
            'animalPin': 'dog',
            'coursePin': 'Arquitetura',
            'pinColor': '#A560E8',
            'expression': 'wink',
        }
        self.client.force_authenticate(self.woman)
        updated = self.client.patch('/api/me/profile/', {
            'avatar_config': avatar_config,
        }, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.woman.profile.refresh_from_db()
        self.assertEqual(self.woman.profile.avatar_config, avatar_config)

        public = self.client.get(f'/api/users/{self.woman.id}/profile/')
        self.assertEqual(public.status_code, 200, public.data)
        self.assertEqual(public.data['avatar_config'], avatar_config)

        for outfit_type in ('polo', 'varsity', 'overalls', 'jersey', 'turtleneck'):
            with self.subTest(outfit_type=outfit_type):
                new_outfit = self.client.patch('/api/me/profile/', {
                    'avatar_config': {**avatar_config, 'outfitType': outfit_type},
                }, format='json')
                self.assertEqual(new_outfit.status_code, 200, new_outfit.data)
                self.assertEqual(new_outfit.data['avatar_config']['outfitType'], outfit_type)

        custom_colors = self.client.patch('/api/me/profile/', {
            'avatar_config': {
                **avatar_config,
                'hairColor': '#1CB0F6',
                'outfitColor': '#FF4FA3',
            },
        }, format='json')
        self.assertEqual(custom_colors.status_code, 200, custom_colors.data)
        self.assertEqual(custom_colors.data['avatar_config']['hairColor'], '#1CB0F6')
        self.assertEqual(custom_colors.data['avatar_config']['outfitColor'], '#FF4FA3')

        course_pin = self.client.patch('/api/me/profile/', {
            'avatar_config': {
                **avatar_config,
                'outfitAccessory': 'course_pin',
                'coursePin': 'Engenharia de Computação',
                'pinColor': '#0EA5E9',
            },
        }, format='json')
        self.assertEqual(course_pin.status_code, 200, course_pin.data)
        self.assertEqual(course_pin.data['avatar_config']['coursePin'], 'Engenharia de Computação')
        self.assertEqual(course_pin.data['avatar_config']['pinColor'], '#0EA5E9')

        animal_pin = self.client.patch('/api/me/profile/', {
            'avatar_config': {
                **avatar_config,
                'outfitAccessory': 'animal_pin',
                'animalPin': 'bird',
            },
        }, format='json')
        self.assertEqual(animal_pin.status_code, 200, animal_pin.data)
        self.assertEqual(animal_pin.data['avatar_config']['animalPin'], 'bird')

        invalid_custom_color = self.client.patch('/api/me/profile/', {
            'avatar_config': {**avatar_config, 'hairColor': '#12FG00'},
        }, format='json')
        self.assertEqual(invalid_custom_color.status_code, 400, invalid_custom_color.data)

        invalid = self.client.patch('/api/me/profile/', {
            'avatar_config': {**avatar_config, 'outfitType': 'admin_only_outfit'},
        }, format='json')
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertIn('avatar_config', invalid.data)

        invalid_boolean = self.client.patch('/api/me/profile/', {
            'avatar_config': {**avatar_config, 'enabled': 1},
        }, format='json')
        self.assertEqual(invalid_boolean.status_code, 400, invalid_boolean.data)

        legacy = self.client.patch('/api/me/profile/', {
            'avatar_config': {
                'enabled': True, 'skin': 'warm', 'hair': 'waves',
                'outfit': 'coral_tee', 'accessory': 'none', 'expression': 'excited',
            },
        }, format='json')
        self.assertEqual(legacy.status_code, 200, legacy.data)
        self.assertEqual(legacy.data['avatar_config']['hairStyle'], 'wavy')
        self.assertEqual(legacy.data['avatar_config']['hairLength'], 'medium')
        self.assertEqual(legacy.data['avatar_config']['outfitType'], 'tee')
        self.assertEqual(legacy.data['avatar_config']['outfitColor'], 'coral')
        self.assertEqual(legacy.data['avatar_config']['expression'], 'bored')
        self.assertEqual(legacy.data['avatar_config']['headwear'], 'none')

        bald = self.client.patch('/api/me/profile/', {
            'avatar_config': {**avatar_config, 'hairStyle': 'bald', 'hairLength': 'long'},
        }, format='json')
        self.assertEqual(bald.status_code, 200, bald.data)
        self.assertEqual(bald.data['avatar_config']['hairLength'], 'short')

    def test_profile_images_are_uploaded_normalized_and_removable(self):
        image_data = BytesIO()
        Image.new('RGB', (900, 900), color=(22, 163, 74)).save(image_data, format='JPEG')
        cover_data = BytesIO()
        Image.new('RGB', (1800, 600), color=(16, 185, 129)).save(cover_data, format='PNG')

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.client.force_authenticate(self.woman)
            uploaded = self.client.patch('/api/me/profile/', {
                'avatar_image': SimpleUploadedFile('foto.jpg', image_data.getvalue(), content_type='image/jpeg'),
                'cover_photo_image': SimpleUploadedFile('capa.png', cover_data.getvalue(), content_type='image/png'),
                'bio': 'Perfil atualizado pelo formulário multipart.',
                'address_text': '',
                'phone': self.woman.phone,
                'course': str(self.woman.course_id),
                'semester': '',
                'avatar_config': json.dumps({
                    'enabled': True,
                    'skin': 'warm',
                    'hairColor': 'black',
                    'hairStyle': 'wavy',
                    'hairLength': 'medium',
                    'facialHair': 'mustache',
                    'facialHairColor': 'red',
                    'outfitType': 'hoodie',
                    'outfitColor': 'green',
                    'headwear': 'headscarf',
                    'eyewear': 'square_glasses',
                    'earAccessory': 'earrings',
                    'outfitAccessory': 'leaf_pin',
                    'expression': 'bored',
                }),
            }, format='multipart')
            self.assertEqual(uploaded.status_code, 200, uploaded.data)
            self.assertIn('/media/profiles/', uploaded.data['avatar_url'])
            self.assertIn('/media/profiles/', uploaded.data['cover_photo_url'])
            self.assertTrue(uploaded.data['avatar_config']['enabled'])

            self.woman.profile.refresh_from_db()
            self.assertTrue(self.woman.profile.avatar_image.name.endswith('.webp'))
            self.assertTrue(self.woman.profile.cover_photo_image.name.endswith('.webp'))
            with Image.open(self.woman.profile.avatar_image.path) as normalized:
                self.assertEqual(normalized.format, 'WEBP')
                self.assertLessEqual(normalized.width, 1024)

            public = self.client.get(f'/api/users/{self.woman.id}/profile/')
            self.assertEqual(public.data['avatar_url'], uploaded.data['avatar_url'])
            self.assertNotIn('avatar_image', public.data)

            removed = self.client.patch('/api/me/profile/', {'remove_avatar': True}, format='multipart')
            self.assertEqual(removed.status_code, 200, removed.data)
            self.assertIsNone(removed.data['avatar_url'])

    def test_profile_upload_rejects_fake_images(self):
        self.client.force_authenticate(self.woman)
        fake = SimpleUploadedFile('foto.png', b'not-a-real-image', content_type='image/png')
        response = self.client.patch('/api/me/profile/', {'avatar_image': fake}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('avatar_image', response.data)

    def test_completed_participants_exchange_badges_and_duplicate_is_rejected(self):
        ride = self.create_ride()
        ride.status = Ride.RideStatus.COMPLETED
        ride.distance_km = 12
        ride.completed_at = timezone.now()
        ride.save(update_fields=['status', 'distance_km', 'completed_at', 'updated_at'])
        RidePassenger.objects.create(ride=ride, passenger_user=self.woman, seat_number=1, completed_at=timezone.now())
        badge = Badge.objects.get(code='gentileza')

        self.client.force_authenticate(self.driver)
        payload = {'ride': str(ride.id), 'receiver_user': str(self.woman.id), 'badge': str(badge.id)}
        first = self.client.post('/api/ride-feedback/', payload, format='json')
        duplicate = self.client.post('/api/ride-feedback/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertEqual(RideBadgeFeedback.objects.filter(receiver_user=self.woman, badge=badge).count(), 1)
        self.assertTrue(Notification.objects.filter(recipient_user=self.woman, title='Você recebeu um badge').exists())

        self.client.force_authenticate(self.man)
        public = self.client.get(f'/api/users/{self.woman.id}/profile/')
        evaluation = next(item for item in public.data['evaluations'] if item['badge']['code'] == 'gentileza')
        self.assertEqual(evaluation['count'], 1)
        self.assertGreaterEqual(public.data['impact']['co2_saved_kg'], 2.3)

    def test_achievement_progress_is_generated_from_real_completed_rides(self):
        ride = self.create_ride()
        ride.status = Ride.RideStatus.COMPLETED
        ride.distance_km = 10
        ride.completed_at = timezone.now()
        ride.save(update_fields=['status', 'distance_km', 'completed_at', 'updated_at'])
        RidePassenger.objects.create(ride=ride, passenger_user=self.woman, seat_number=1, completed_at=timezone.now())

        self.client.force_authenticate(self.woman)
        current = self.client.get('/api/me/')
        first_ride = next(item for item in current.data['badges'] if item['badge']['code'] == 'primeira_carona')
        self.assertIsNotNone(first_ride['achieved_at'])
        self.assertTrue(Notification.objects.filter(recipient_user=self.woman, title='Nova conquista desbloqueada').exists())
        five_rides = next(item for item in current.data['badges'] if item['badge']['code'] == 'cinco_caronas')
        self.assertEqual(five_rides['progress_value'], '1.00')
        self.assertIsNone(five_rides['achieved_at'])
        fifty_rides = next(item for item in current.data['badges'] if item['badge']['code'] == 'cinquenta_caronas')
        self.assertEqual(fifty_rides['target_value'], '50.00')
        self.assertIsNone(fifty_rides['achieved_at'])

        achieved_at = first_ride['achieved_at']
        ride.status = Ride.RideStatus.CANCELLED
        ride.save(update_fields=['status', 'updated_at'])
        refreshed = self.client.get('/api/me/')
        persisted = next(item for item in refreshed.data['badges'] if item['badge']['code'] == 'primeira_carona')
        self.assertEqual(persisted['progress_value'], '0.00')
        self.assertEqual(persisted['achieved_at'], achieved_at)

    def test_public_profiles_are_limited_to_the_same_university(self):
        other_university = University.objects.create(name='Outra Universidade', email_domain='aluno.outra.edu.br')
        other_course = Course.objects.create(university=other_university, name='Direito')
        outsider = User.objects.create_user(
            email='pessoa@aluno.outra.edu.br', password='SenhaForte123!', full_name='Pessoa Externa',
            phone='11999990000', university=other_university, course=other_course,
            ride_visibility_group='woman', account_status=User.AccountStatus.ACTIVE, is_email_verified=True,
        )
        self.client.force_authenticate(self.man)
        response = self.client.get(f'/api/users/{outsider.id}/profile/')
        self.assertEqual(response.status_code, 404)

    def test_password_reset_request_does_not_reveal_account_existence(self):
        existing = self.client.post('/api/auth/password-reset/', {'email': self.man.email}, format='json')
        missing = self.client.post('/api/auth/password-reset/', {'email': 'inexistente@mail.usf.edu.br'}, format='json')
        self.assertEqual(existing.status_code, 200)
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(existing.data['message'], missing.data['message'])

    def test_session_authenticated_writes_require_csrf(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(csrf_client.login(email=self.driver.email, password='SenhaForte123!'))
        response = csrf_client.post('/api/rides/', {
            'origin_text': 'Centro',
            'destination_text': 'USF Sagrado',
            'scheduled_start_at': (timezone.now() + timedelta(days=3)).isoformat(),
            'vehicle_type': 'car',
            'vehicle_model': 'Honda Fit',
            'vehicle_plate': 'ABC1D23',
            'seat_capacity': 4,
            'gender_restriction': 'none',
            'price_amount': '8.00',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_login_endpoint_is_rate_limited(self):
        responses = [
            self.client.post('/api/auth/login/', {'email': self.man.email, 'password': 'incorreta'}, format='json')
            for _ in range(11)
        ]
        self.assertTrue(all(response.status_code == 400 for response in responses[:10]))
        self.assertEqual(responses[-1].status_code, 429)

    def test_notifications_are_private_paginated_and_can_be_marked_read(self):
        for index in range(25):
            create_notification(
                recipient_user=self.man,
                notification_type=Notification.NotificationType.STATUS,
                title=f'Evento {index}',
                body='Atualização da carona.',
                link_path='/acompanhar-carona',
                event_key=f'test:notification:{index}:user:{self.man.id}',
            )
        create_notification(
            recipient_user=self.woman,
            notification_type=Notification.NotificationType.STATUS,
            title='Privada',
            body='Não deve aparecer para outro usuário.',
            link_path='/acompanhar-carona',
            event_key=f'test:private:user:{self.woman.id}',
        )

        self.client.force_authenticate(self.man)
        page = self.client.get('/api/notifications/')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data['count'], 25)
        self.assertEqual(len(page.data['results']), 20)
        self.assertNotIn('Privada', {item['title'] for item in page.data['results']})

        notification_id = page.data['results'][0]['id']
        marked = self.client.post(f'/api/notifications/{notification_id}/mark_read/', {}, format='json')
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.data['is_read'])
        marked_all = self.client.post('/api/notifications/mark_all_read/', {}, format='json')
        self.assertEqual(marked_all.status_code, 200)
        self.assertEqual(Notification.objects.filter(recipient_user=self.man, is_read=False).count(), 0)
        self.assertFalse(Notification.objects.get(recipient_user=self.woman).is_read)

    def test_start_and_completion_notify_only_confirmed_passengers(self):
        ride = self.create_ride()
        confirmed = RideRequest.objects.create(
            ride=ride,
            passenger_user=self.man,
            seat_number=1,
            status=RideRequest.RequestStatus.CONFIRMED,
        )
        RideRequest.objects.create(
            ride=ride,
            passenger_user=self.woman,
            seat_number=2,
            status=RideRequest.RequestStatus.PENDING,
        )
        RidePassenger.objects.create(ride=ride, passenger_user=self.man, seat_number=1)

        self.client.force_authenticate(self.driver)
        started = self.client.post(f'/api/rides/{ride.id}/start/', {}, format='json')
        self.assertEqual(started.status_code, 200)
        completed = self.client.post(f'/api/rides/{ride.id}/complete/', {}, format='json')
        self.assertEqual(completed.status_code, 200)

        self.assertTrue(Notification.objects.filter(recipient_user=self.man, title='A carona começou').exists())
        self.assertTrue(Notification.objects.filter(recipient_user=self.man, title='Carona finalizada').exists())
        self.assertFalse(Notification.objects.filter(recipient_user=self.woman, ride=ride).exists())

    def test_reminders_are_deduplicated_for_driver_and_confirmed_passengers(self):
        ride = self.create_ride(starts_in=timedelta(hours=6))
        RideRequest.objects.create(
            ride=ride,
            passenger_user=self.man,
            seat_number=1,
            status=RideRequest.RequestStatus.CONFIRMED,
        )
        RideRequest.objects.create(
            ride=ride,
            passenger_user=self.woman,
            seat_number=2,
            status=RideRequest.RequestStatus.APPROVED,
        )

        self.assertEqual(send_upcoming_ride_reminders(), 2)
        self.assertEqual(send_upcoming_ride_reminders(), 0)
        recipients = set(Notification.objects.filter(
            ride=ride,
            type=Notification.NotificationType.REMINDER,
        ).values_list('recipient_user_id', flat=True))
        self.assertEqual(recipients, {self.driver.id, self.man.id})

    def test_match_notifications_use_search_affinity_and_are_deduplicated(self):
        unmatched = self.create_user('sem-match@mail.usf.edu.br', 'woman', 'Sem Match')
        RideSearch.objects.create(
            user=self.woman,
            origin_text='Centro',
            destination_text='USF Sagrado',
        )
        ride = self.create_ride(restriction='women_only')

        first = notify_matching_users(ride)
        second = notify_matching_users(ride)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(Notification.objects.filter(recipient_user=self.woman, type='match', ride=ride).count(), 1)
        self.assertFalse(Notification.objects.filter(recipient_user=unmatched, type='match', ride=ride).exists())
        self.assertFalse(Notification.objects.filter(recipient_user=self.man, type='match', ride=ride).exists())

    def test_search_signals_are_deduplicated_within_five_minutes(self):
        self.client.force_authenticate(self.man)
        payload = {
            'origin_text': 'Centro',
            'destination_text': 'USF Sagrado',
            'requested_date': (timezone.localdate() + timedelta(days=2)).isoformat(),
            'requested_time': '18:00',
            'gender_restriction': 'none',
        }
        first = self.client.post('/api/ride-searches/', payload, format='json')
        second = self.client.post('/api/ride-searches/', payload, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(RideSearch.objects.filter(user=self.man).count(), 1)

    def test_smart_match_returns_recommendations_and_keeps_other_active_options(self):
        matching_ride = self.create_ride()
        matching_ride.origin_lat = '-22.906700'
        matching_ride.origin_lng = '-47.061500'
        matching_ride.destination_lat = '-22.952000'
        matching_ride.destination_lng = '-47.010000'
        matching_ride.save(update_fields=['origin_lat', 'origin_lng', 'destination_lat', 'destination_lng'])
        other_ride = self.create_ride()
        other_ride.origin_text = 'Valinhos'
        other_ride.destination_text = 'Centro de Jundiaí'
        other_ride.save(update_fields=['origin_text', 'destination_text'])

        local_start = timezone.localtime(matching_ride.scheduled_start_at)
        self.client.force_authenticate(self.man)
        response = self.client.get('/api/rides/', {
            'status': 'scheduled',
            'smart_match': 'true',
            'origin': 'Centro, Campinas',
            'destination': 'USF Sagrado',
            'origin_lat': '-22.9068',
            'origin_lng': '-47.0616',
            'destination_lat': '-22.9521',
            'destination_lng': '-47.0101',
            'date': local_start.date().isoformat(),
            'time': local_start.strftime('%H:%M'),
            'max_distance_km': '5',
            'time_flexibility_minutes': '60',
        })

        self.assertEqual(response.status_code, 200, response.data)
        results = {item['id']: item for item in response.data}
        self.assertEqual(set(results), {str(matching_ride.id), str(other_ride.id)})
        self.assertTrue(results[str(matching_ride.id)]['is_recommended'])
        self.assertGreaterEqual(results[str(matching_ride.id)]['match_score'], 90)
        self.assertLess(results[str(matching_ride.id)]['match_details']['origin_distance_km'], 1)
        self.assertFalse(results[str(other_ride.id)]['is_recommended'])

    def test_smart_match_respects_time_flexibility(self):
        ride = self.create_ride()
        local_start = timezone.localtime(ride.scheduled_start_at)
        requested_minutes = (local_start.hour * 60 + local_start.minute + 120) % 1440
        requested_time = f'{requested_minutes // 60:02d}:{requested_minutes % 60:02d}'
        self.client.force_authenticate(self.man)
        response = self.client.get('/api/rides/', {
            'status': 'scheduled', 'smart_match': 'true',
            'origin': ride.origin_text, 'destination': ride.destination_text,
            'date': local_start.date().isoformat(), 'time': requested_time,
            'time_flexibility_minutes': '60',
        })
        item = next(item for item in response.data if item['id'] == str(ride.id))
        self.assertFalse(item['is_recommended'])
        self.assertEqual(item['match_details']['departure_time_difference_minutes'], 120)

    def test_matching_helpers_normalize_accents_and_calculate_distance(self):
        self.assertGreaterEqual(text_similarity('USF Sagrado', 'Campus USF Sagrado'), 0.9)
        self.assertGreaterEqual(text_similarity('Jundiaí', 'Jundiai - Centro'), 0.9)
        self.assertAlmostEqual(haversine_km(-22.9068, -47.0616, -22.9068, -47.0616), 0, places=3)


class NotificationRealtimeTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        university = University.objects.create(name='USF Sagrado', email_domain='mail.usf.edu.br')
        course = Course.objects.create(university=university, name='Engenharia')
        self.user = User.objects.create_user(
            email='realtime@mail.usf.edu.br',
            password='SenhaForte123!',
            full_name='Tempo Real',
            phone='11999990000',
            university=university,
            course=course,
            ride_visibility_group='woman',
            account_status=User.AccountStatus.ACTIVE,
            is_email_verified=True,
        )

    def test_authenticated_websocket_receives_change_signal(self):
        session = SessionStore()
        session[SESSION_KEY] = str(self.user.pk)
        session[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
        session[HASH_SESSION_KEY] = self.user.get_session_auth_hash()
        session.save()

        async def scenario():
            communicator = WebsocketCommunicator(
                application,
                '/ws/notifications/',
                headers=[
                    (b'host', b'testserver'),
                    (b'origin', b'http://testserver'),
                    (b'cookie', f'sessionid={session.session_key}'.encode()),
                ],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await sync_to_async(create_notification, thread_sensitive=True)(
                recipient_user=self.user,
                notification_type=Notification.NotificationType.STATUS,
                title='Atualização em tempo real',
                body='Sinal emitido após persistir a notificação.',
                link_path='/acompanhar-carona',
                event_key=f'realtime:test:user:{self.user.id}',
            )
            message = await communicator.receive_json_from(timeout=1)
            self.assertEqual(message, {'type': 'notification.changed'})
            await communicator.disconnect()

        async_to_sync(scenario)()
