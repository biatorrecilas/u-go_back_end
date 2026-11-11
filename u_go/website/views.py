import hashlib
import secrets
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.tokens import default_token_generator
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import generics, permissions, serializers, status, views, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.throttling import ScopedRateThrottle

from .models import (
	Badge,
	Course,
	EmailVerificationCode,
	Notification,
	Ride,
	RideEdit,
	RideSeries,
	RideSeriesWeekday,
	RideBadgeFeedback,
	RidePassenger,
	RideRequest,
	RideSearch,
	RideRequestStatusHistory,
	RideSeat,
	RideStatusHistory,
	University,
	User,
	UserSession,
	UserProfile,
)
from .serializers import (
	BadgeSerializer,
	CourseSerializer,
	CurrentUserSerializer,
	CurrentUserProfileSerializer,
	EmailVerificationSerializer,
	EngagedPublicProfileSerializer,
	LoginSerializer,
	NotificationSerializer,
	PasswordResetConfirmSerializer,
	PasswordResetRequestSerializer,
	ResendVerificationSerializer,
	RideBadgeFeedbackSerializer,
	RidePublicSerializer,
	RideRequestSerializer,
	RideSearchSerializer,
	RideTrackingSerializer,
	RideWriteSerializer,
	SignupSerializer,
	UniversitySerializer,
	UserProfileSerializer,
)
from .services.platform_stats import build_platform_stats
from .services.email_delivery import (
	require_email_delivery,
	send_password_reset_email,
	send_verification_email,
)
from .services.notifications import (
	broadcast_notification_change,
	create_notification,
	notify_matching_users,
	notify_ride_participants,
)
from .services.matching import build_user_matching_context, criteria_from_query_params
from .services.profiles import sync_user_achievements


VERIFICATION_CODE_TTL_MINUTES = 15
VERIFICATION_CODE_MAX_ATTEMPTS = 5
RIDE_CHANGE_DEADLINE = timedelta(days=1)


def hash_session_key(session_key):
	return hashlib.sha256(session_key.encode('utf-8')).hexdigest()


def generate_verification_code():
	return ''.join(str(secrets.randbelow(10)) for _ in range(6))


def issue_verification_code(user):
	now = timezone.now()
	EmailVerificationCode.objects.filter(
		user=user,
		consumed_at__isnull=True,
		expires_at__gte=now,
	).update(consumed_at=now)

	code = generate_verification_code()
	verification = EmailVerificationCode.objects.create(
		user=user,
		code_hash=make_password(code),
		expires_at=now + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES),
	)
	return verification, code


def build_auth_response_payload(user, *, message, code=None):
	payload = {
		'message': message,
		'csrf_token': get_token(user._request) if hasattr(user, '_request') else None,
		'user': CurrentUserSerializer(user).data,
	}
	if code and settings.DEBUG:
		payload['debug_verification_code'] = code
	return payload


def create_user_session_record(request, user, remember_me=False):
	if request.session.session_key is None:
		request.session.save()

	session_key = request.session.session_key
	return UserSession.objects.create(
		user=user,
		refresh_token_hash=hash_session_key(session_key),
		remember_me=remember_me,
		user_agent=request.META.get('HTTP_USER_AGENT'),
		ip_address=request.META.get('REMOTE_ADDR'),
		expires_at=request.session.get_expiry_date(),
	)


def revoke_user_session_record(request):
	session_key = request.session.session_key
	if not session_key:
		return

	UserSession.objects.filter(
		refresh_token_hash=hash_session_key(session_key),
		revoked_at__isnull=True,
	).update(revoked_at=timezone.now())


def ride_restriction_for_user(user):
	if user is None or not user.is_authenticated:
		return None
	return {
		User.VisibilityGroup.WOMAN: 'women_only',
		User.VisibilityGroup.MAN: 'men_only',
		User.VisibilityGroup.NON_BINARY: 'non_binary_only',
		User.VisibilityGroup.OTHER: 'other_only',
	}.get(user.ride_visibility_group)


def ride_can_be_changed(ride):
	return ride.status == Ride.RideStatus.SCHEDULED and ride.scheduled_start_at >= timezone.now() + RIDE_CHANGE_DEADLINE


def get_ride_queryset():
	return Ride.objects.select_related('driver_user', 'ride_series').prefetch_related(
		'seats',
		Prefetch('passengers', queryset=RidePassenger.objects.select_related('passenger_user')),
		Prefetch('requests', queryset=RideRequest.objects.select_related('passenger_user')),
	)


class UniversityViewSet(viewsets.ReadOnlyModelViewSet):
	queryset = University.objects.order_by('name')
	serializer_class = UniversitySerializer
	permission_classes = [permissions.AllowAny]


class CourseViewSet(viewsets.ReadOnlyModelViewSet):
	serializer_class = CourseSerializer
	permission_classes = [permissions.AllowAny]

	def get_queryset(self):
		queryset = Course.objects.filter(is_active=True).select_related('university').order_by('name')
		university_id = self.request.query_params.get('university')
		if university_id:
			queryset = queryset.filter(university_id=university_id)
		return queryset


class BadgeViewSet(viewsets.ReadOnlyModelViewSet):
	serializer_class = BadgeSerializer
	permission_classes = [permissions.AllowAny]

	def get_queryset(self):
		queryset = Badge.objects.filter(is_active=True).order_by('name')
		badge_kind = self.request.query_params.get('badge_kind')
		if badge_kind:
			queryset = queryset.filter(badge_kind=badge_kind)
		return queryset


@method_decorator(cache_page(60), name='dispatch')
class PublicPlatformStatsView(views.APIView):
	"""Aggregated, non-identifying metrics used by the public home page."""

	permission_classes = [permissions.AllowAny]
	authentication_classes = []

	def get(self, request):
		return Response(build_platform_stats())


class CurrentUserView(generics.RetrieveAPIView):
	serializer_class = CurrentUserSerializer
	permission_classes = [permissions.IsAuthenticated]

	def get_object(self):
		sync_user_achievements(self.request.user)
		return User.objects.select_related('profile', 'course', 'university').get(pk=self.request.user.pk)


class CurrentUserProfileView(generics.RetrieveUpdateAPIView):
	serializer_class = CurrentUserProfileSerializer
	permission_classes = [permissions.IsAuthenticated]

	def get_object(self):
		profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
		return profile


class EngagedPublicProfileView(generics.RetrieveAPIView):
	serializer_class = EngagedPublicProfileSerializer
	permission_classes = [permissions.IsAuthenticated]
	lookup_url_kwarg = 'user_id'

	def get_queryset(self):
		return User.objects.select_related('profile', 'course', 'university').filter(
			university=self.request.user.university,
			account_status=User.AccountStatus.ACTIVE,
			is_email_verified=True,
		)


class CsrfTokenView(views.APIView):
	permission_classes = [permissions.AllowAny]
	authentication_classes = []

	def get(self, request):
		return Response({'csrfToken': get_token(request)})


class SignupView(views.APIView):
	permission_classes = [permissions.AllowAny]
	authentication_classes = []
	throttle_classes = [ScopedRateThrottle]
	throttle_scope = 'signup'

	@transaction.atomic
	def post(self, request):
		serializer = SignupSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		user = serializer.save()
		user._request = request
		_, code = issue_verification_code(user)
		send_verification_email(user, code)
		return Response(
			build_auth_response_payload(user, message='Cadastro realizado. Verifique seu e-mail para concluir o acesso.', code=code),
			status=status.HTTP_201_CREATED,
		)


class VerifyEmailView(views.APIView):
	permission_classes = [permissions.AllowAny]
	authentication_classes = []
	throttle_classes = [ScopedRateThrottle]
	throttle_scope = 'verification'

	@transaction.atomic
	def post(self, request):
		serializer = EmailVerificationSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)

		email = serializer.validated_data['email']
		code = serializer.validated_data['code']
		try:
			user = User.objects.get(email=email)
		except User.DoesNotExist:
			return Response({'detail': 'Usuário não encontrado.'}, status=status.HTTP_404_NOT_FOUND)

		verification = user.verification_codes.filter(
			consumed_at__isnull=True,
			expires_at__gte=timezone.now(),
		).order_by('-created_at').first()

		if verification is None:
			return Response({'detail': 'Nenhum código de verificação ativo foi encontrado.'}, status=status.HTTP_400_BAD_REQUEST)

		if verification.attempts >= VERIFICATION_CODE_MAX_ATTEMPTS:
			verification.consumed_at = timezone.now()
			verification.save(update_fields=['consumed_at'])
			return Response({'detail': 'O código expirou por excesso de tentativas.'}, status=status.HTTP_400_BAD_REQUEST)

		if not check_password(code, verification.code_hash):
			verification.attempts += 1
			update_fields = ['attempts']
			if verification.attempts >= VERIFICATION_CODE_MAX_ATTEMPTS:
				verification.consumed_at = timezone.now()
				update_fields.append('consumed_at')
			verification.save(update_fields=update_fields)
			return Response({'detail': 'Código inválido.'}, status=status.HTTP_400_BAD_REQUEST)

		verification.consumed_at = timezone.now()
		verification.save(update_fields=['consumed_at'])

		user.is_email_verified = True
		user.account_status = User.AccountStatus.ACTIVE
		user.save(update_fields=['is_email_verified', 'account_status', 'updated_at'])

		login(request, user)
		request.session.set_expiry(0)
		create_user_session_record(request, user, remember_me=False)
		user._request = request

		return Response(build_auth_response_payload(user, message='E-mail verificado com sucesso.'))


class ResendVerificationView(views.APIView):
	permission_classes = [permissions.AllowAny]
	authentication_classes = []
	throttle_classes = [ScopedRateThrottle]
	throttle_scope = 'verification'

	@transaction.atomic
	def post(self, request):
		serializer = ResendVerificationSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)

		email = serializer.validated_data['email']
		try:
			user = User.objects.get(email=email)
		except User.DoesNotExist:
			return Response({'detail': 'Usuário não encontrado.'}, status=status.HTTP_404_NOT_FOUND)

		if user.is_email_verified:
			return Response({'detail': 'Este e-mail já foi verificado.'}, status=status.HTTP_400_BAD_REQUEST)

		_, code = issue_verification_code(user)
		send_verification_email(user, code)
		payload = {'message': 'Um novo código de verificação foi gerado.'}
		if settings.DEBUG:
			payload['debug_verification_code'] = code
		return Response(payload)


class LoginView(views.APIView):
	permission_classes = [permissions.AllowAny]
	authentication_classes = []
	throttle_classes = [ScopedRateThrottle]
	throttle_scope = 'login'

	@transaction.atomic
	def post(self, request):
		serializer = LoginSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)

		email = serializer.validated_data['email']
		password = serializer.validated_data['password']
		remember_me = serializer.validated_data['remember_me']

		user = authenticate(request, email=email, password=password)
		if user is None:
			candidate = User.objects.filter(email=email).first()
			if candidate and candidate.check_password(password):
				if not candidate.is_email_verified:
					return Response({'detail': 'Sua conta ainda não foi verificada.'}, status=status.HTTP_400_BAD_REQUEST)
				if candidate.account_status != User.AccountStatus.ACTIVE:
					return Response({'detail': 'Sua conta não está ativa para login.'}, status=status.HTTP_400_BAD_REQUEST)
			return Response({'detail': 'E-mail ou senha inválidos.'}, status=status.HTTP_400_BAD_REQUEST)

		login(request, user)
		request.session.set_expiry(1209600 if remember_me else 0)
		create_user_session_record(request, user, remember_me=remember_me)
		user._request = request

		return Response(build_auth_response_payload(user, message='Login realizado com sucesso.'))


class LogoutView(views.APIView):
	permission_classes = [permissions.IsAuthenticated]

	def post(self, request):
		revoke_user_session_record(request)
		logout(request)
		return Response({'message': 'Logout realizado com sucesso.'})


class PasswordResetRequestView(views.APIView):
	permission_classes = [permissions.AllowAny]
	authentication_classes = []
	throttle_classes = [ScopedRateThrottle]
	throttle_scope = 'password_reset'

	def post(self, request):
		require_email_delivery()
		serializer = PasswordResetRequestSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		user = User.objects.filter(email=serializer.validated_data['email'], is_email_verified=True).first()
		payload = {'message': 'Se o e-mail estiver cadastrado, enviaremos as instruções de recuperação.'}
		if user:
			uid = urlsafe_base64_encode(force_bytes(user.pk))
			token = default_token_generator.make_token(user)
			frontend_url = getattr(settings, 'FRONTEND_URL', 'http://127.0.0.1:5173').rstrip('/')
			reset_url = f'{frontend_url}/login?reset_uid={uid}&reset_token={token}'
			send_password_reset_email(user, reset_url)
			if settings.DEBUG:
				payload.update({'debug_reset_uid': uid, 'debug_reset_token': token})
		return Response(payload)


class PasswordResetConfirmView(views.APIView):
	permission_classes = [permissions.AllowAny]
	authentication_classes = []
	throttle_classes = [ScopedRateThrottle]
	throttle_scope = 'password_reset'

	@transaction.atomic
	def post(self, request):
		serializer = PasswordResetConfirmSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		try:
			user_id = force_str(urlsafe_base64_decode(serializer.validated_data['uid']))
			user = User.objects.get(pk=user_id)
		except (TypeError, ValueError, OverflowError, User.DoesNotExist):
			return Response({'detail': 'Link de recuperação inválido ou expirado.'}, status=status.HTTP_400_BAD_REQUEST)
		if not default_token_generator.check_token(user, serializer.validated_data['token']):
			return Response({'detail': 'Link de recuperação inválido ou expirado.'}, status=status.HTTP_400_BAD_REQUEST)
		user.set_password(serializer.validated_data['password'])
		user.save(update_fields=['password', 'updated_at'])
		UserSession.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
		return Response({'message': 'Senha redefinida com sucesso. Faça login com a nova senha.'})


class RideViewSet(viewsets.ModelViewSet):
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_serializer_context(self):
		context = super().get_serializer_context()
		if self.action == 'list':
			context['match_criteria'] = criteria_from_query_params(self.request.query_params)
			context['user_matching_context'] = build_user_matching_context(self.request.user)
		return context

	def get_queryset(self):
		queryset = get_ride_queryset().order_by('scheduled_start_at')
		user = self.request.user

		if self.action != 'tracking':
			allowed_restriction = ride_restriction_for_user(user)
			visibility_filter = Q(gender_restriction='none')
			if allowed_restriction:
				visibility_filter |= Q(gender_restriction=allowed_restriction)
			if user.is_authenticated:
				visibility_filter |= Q(driver_user=user)
			queryset = queryset.filter(visibility_filter)

		status_filter = self.request.query_params.get('status')
		if status_filter:
			queryset = queryset.filter(status=status_filter)

		smart_match = self.request.query_params.get('smart_match') in {'1', 'true'}
		gender_restriction = self.request.query_params.get('gender_restriction')
		if gender_restriction and not smart_match:
			queryset = queryset.filter(gender_restriction=gender_restriction)

		driver_user_id = self.request.query_params.get('driver_user')
		if driver_user_id:
			queryset = queryset.filter(driver_user_id=driver_user_id)

		if self.action == 'list':
			if user.is_authenticated:
				queryset = queryset.exclude(driver_user=user)
			if status_filter == Ride.RideStatus.SCHEDULED:
				queryset = queryset.filter(scheduled_start_at__gte=timezone.now())

		origin = self.request.query_params.get('origin')
		destination = self.request.query_params.get('destination')
		date_filter = self.request.query_params.get('date')
		time_filter = self.request.query_params.get('time')
		if origin and not smart_match:
			queryset = queryset.filter(origin_text__icontains=origin.strip())
		if destination and not smart_match:
			queryset = queryset.filter(destination_text__icontains=destination.strip())
		if date_filter and not smart_match:
			queryset = queryset.filter(scheduled_start_at__date=date_filter)
		if time_filter and not smart_match:
			queryset = queryset.filter(scheduled_start_at__time=time_filter[:5])

		return queryset

	def get_serializer_class(self):
		if self.action in {'create', 'update', 'partial_update'}:
			return RideWriteSerializer
		if self.action in {'tracking', 'start', 'complete', 'cancel'}:
			return RideTrackingSerializer
		return RidePublicSerializer

	@transaction.atomic
	def perform_create(self, serializer):
		data = dict(serializer.validated_data)

		# If this is a recurring series, create RideSeries and multiple Ride instances
		if data.get('is_recurring'):
			# create series
			# parse departure_time if provided (expected 'HH:MM')
			dep_time_val = None
			if data.get('departure_time'):
				parts = str(data.get('departure_time')).split(':')
				if len(parts) >= 2:
					try:
						dep_time_val = time(int(parts[0]), int(parts[1]))
					except Exception:
						dep_time_val = None
			# fallback to scheduled_start_at time if present
			if dep_time_val is None and data.get('scheduled_start_at'):
				dep_time_val = data.get('scheduled_start_at').time()

			series = RideSeries.objects.create(
				driver_user=self.request.user,
				origin_text=data.get('origin_text'),
				origin_lat=data.get('origin_lat'),
				origin_lng=data.get('origin_lng'),
				destination_text=data.get('destination_text'),
				destination_lat=data.get('destination_lat'),
				destination_lng=data.get('destination_lng'),
				start_date=data.get('recurring_start_date'),
				end_date=data.get('recurring_end_date'),
				departure_time=dep_time_val,
				vehicle_type=data.get('vehicle_type'),
				vehicle_model=data.get('vehicle_model'),
				vehicle_plate=data.get('vehicle_plate'),
				seat_capacity=data.get('seat_capacity'),
				driver_contact_phone=data.get('driver_contact_phone') or self.request.user.phone,
				gender_restriction=data.get('gender_restriction'),
				price_amount=data.get('price_amount'),
				notes=data.get('notes'),
				accept_deviation=data.get('accept_deviation', False),
				deviation_distance_km=data.get('deviation_distance_km'),
				deviation_time_min=data.get('deviation_time_min'),
			)

			# Weekdays use ISO numbering: Monday=1 through Sunday=7.
			rdays = data.get('recurring_days') or []
			mapping_label_to_int = {'seg':1,'ter':2,'qua':3,'qui':4,'sex':5,'sab':6,'dom':7}
			weekdays_int = []
			for d in rdays:
				if isinstance(d, int):
					weekdays_int.append(d)
				elif isinstance(d, str) and d in mapping_label_to_int:
					weekdays_int.append(mapping_label_to_int[d])

			for wd in sorted(set(weekdays_int)):
				RideSeriesWeekday.objects.create(ride_series=series, weekday=wd)

			# generate rides for each date in range matching weekdays
			start = data.get('recurring_start_date')
			end = data.get('recurring_end_date')
			created_rides = []
			if start and end:
				cur = start
				while cur <= end:
					if cur.isoweekday() in weekdays_int:
						# construct scheduled_start_at as datetime from cur + departure_time
						departure_time = series.departure_time
						scheduled_dt = timezone.make_aware(datetime.combine(cur, departure_time), timezone.get_current_timezone())
						ride = Ride.objects.create(
							ride_series=series,
							driver_user=self.request.user,
							origin_text=series.origin_text,
							origin_lat=series.origin_lat,
							origin_lng=series.origin_lng,
							destination_text=series.destination_text,
							destination_lat=series.destination_lat,
							destination_lng=series.destination_lng,
							scheduled_start_at=scheduled_dt,
							vehicle_type=series.vehicle_type,
							vehicle_model=series.vehicle_model,
							vehicle_plate=series.vehicle_plate,
							seat_capacity=series.seat_capacity,
							driver_contact_phone=series.driver_contact_phone,
							gender_restriction=series.gender_restriction,
							price_amount=series.price_amount,
							distance_km=data.get('distance_km'),
							duration_minutes=data.get('duration_minutes'),
							notes=series.notes,
							accept_deviation=series.accept_deviation,
							deviation_distance_km=series.deviation_distance_km,
							deviation_time_min=series.deviation_time_min,
						)
						# create seats
						RideSeat.objects.bulk_create([
							RideSeat(ride=ride, seat_number=i+1, seat_status=(RideSeat.SeatStatus.AVAILABLE if (i+1) in (data.get('available_seat_numbers') or list(range(1, series.seat_capacity+1))) else RideSeat.SeatStatus.UNAVAILABLE))
							for i in range(series.seat_capacity)
						])
						RideStatusHistory.objects.create(
							ride=ride,
							from_status=None,
							to_status=ride.status,
							changed_by_user=self.request.user,
							reason='Recurring ride instance created.',
						)
						created_rides.append(ride)
					cur = cur + timedelta(days=1)

			serializer.instance = created_rides[0]
			notify_matching_users(created_rides[0], occurrence_count=len(created_rides))
			return

		# Non recurring: create a single ride
		ride = serializer.save(
			driver_user=self.request.user,
			driver_contact_phone=serializer.validated_data.get('driver_contact_phone') or self.request.user.phone,
		)
		RideStatusHistory.objects.create(
			ride=ride,
			from_status=None,
			to_status=ride.status,
			changed_by_user=self.request.user,
			reason='Ride created.',
		)
		notify_matching_users(ride)

	@transaction.atomic
	def perform_update(self, serializer):
		ride = Ride.objects.select_for_update().get(pk=serializer.instance.pk)
		serializer.instance = ride
		if ride.driver_user_id != self.request.user.id:
			raise PermissionDenied('Somente o motorista pode editar esta carona.')
		if not ride_can_be_changed(ride):
			raise serializers.ValidationError({'detail': 'A edição só é permitida com pelo menos 1 dia de antecedência.'})
		changed_fields = {
			field: {'from': getattr(ride, field, None), 'to': value}
			for field, value in serializer.validated_data.items()
			if field not in {'available_seat_numbers'} and getattr(ride, field, None) != value
		}
		updated_ride = serializer.save()
		if changed_fields or 'available_seat_numbers' in serializer.validated_data:
			ride_edit = RideEdit.objects.create(
				ride=updated_ride,
				edited_by_user=self.request.user,
				changed_fields_jsonb={key: {'from': str(value['from']), 'to': str(value['to'])} for key, value in changed_fields.items()},
			)
			notify_ride_participants(
				updated_ride,
				title='Carona atualizada',
				body=f'O motorista alterou informações da carona {updated_ride.origin_text} → {updated_ride.destination_text}. Confira os novos dados.',
				notification_type=Notification.NotificationType.RIDE_UPDATE,
				event_name=f'edit:{ride_edit.id}',
			)

	def destroy(self, request, *args, **kwargs):
		return Response({'detail': 'Use a ação de cancelamento para preservar o histórico da carona.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	@transaction.atomic
	def cancel(self, request, pk=None):
		ride = Ride.objects.select_for_update().get(pk=self.get_object().pk)
		if ride.driver_user_id != request.user.id:
			return Response({'detail': 'Somente o motorista pode cancelar esta carona.'}, status=status.HTTP_403_FORBIDDEN)
		if not ride_can_be_changed(ride):
			return Response({'detail': 'O cancelamento só é permitido com pelo menos 1 dia de antecedência.'}, status=status.HTTP_400_BAD_REQUEST)
		previous_status = ride.status
		ride.status = Ride.RideStatus.CANCELLED
		ride.save(update_fields=['status', 'updated_at'])
		RideStatusHistory.objects.create(
			ride=ride,
			from_status=previous_status,
			to_status=ride.status,
			changed_by_user=request.user,
			reason='Ride cancelled by driver.',
		)
		notify_ride_participants(
			ride,
			title='Carona cancelada',
			body=f'A carona {ride.origin_text} → {ride.destination_text} foi cancelada pelo motorista.',
			event_name='cancelled',
		)
		return Response(RideTrackingSerializer(ride, context=self.get_serializer_context()).data)

	@action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
	def tracking(self, request):
		queryset = self.get_queryset().filter(
			Q(driver_user=request.user) | Q(requests__passenger_user=request.user)
		).distinct()

		ride_type = request.query_params.get('type')
		if ride_type == 'driver':
			queryset = queryset.filter(driver_user=request.user)
		elif ride_type == 'passenger':
			queryset = queryset.filter(requests__passenger_user=request.user)

		serializer = self.get_serializer(queryset, many=True)
		return Response(serializer.data)

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	@transaction.atomic
	def start(self, request, pk=None):
		ride = Ride.objects.select_for_update().get(pk=self.get_object().pk)
		if ride.driver_user_id != request.user.id:
			return Response({'detail': 'Only the driver can start this ride.'}, status=status.HTTP_403_FORBIDDEN)
		if ride.status != Ride.RideStatus.SCHEDULED:
			return Response({'detail': 'Only scheduled rides can be started.'}, status=status.HTTP_400_BAD_REQUEST)

		ride.status = Ride.RideStatus.IN_PROGRESS
		ride.started_at = timezone.now()
		ride.save(update_fields=['status', 'started_at', 'updated_at'])

		RideStatusHistory.objects.create(
			ride=ride,
			from_status=Ride.RideStatus.SCHEDULED,
			to_status=Ride.RideStatus.IN_PROGRESS,
			changed_by_user=request.user,
			reason='Ride started.',
		)
		notify_ride_participants(
			ride,
			title='A carona começou',
			body=f'O motorista iniciou a carona {ride.origin_text} → {ride.destination_text}.',
			statuses=[RideRequest.RequestStatus.CONFIRMED],
			event_name='started',
		)

		serializer = RideTrackingSerializer(ride, context=self.get_serializer_context())
		return Response(serializer.data)

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	@transaction.atomic
	def complete(self, request, pk=None):
		ride = Ride.objects.select_for_update().get(pk=self.get_object().pk)
		if ride.driver_user_id != request.user.id:
			return Response({'detail': 'Only the driver can complete this ride.'}, status=status.HTTP_403_FORBIDDEN)
		if ride.status != Ride.RideStatus.IN_PROGRESS:
			return Response({'detail': 'Only rides in progress can be completed.'}, status=status.HTTP_400_BAD_REQUEST)

		now = timezone.now()
		ride.status = Ride.RideStatus.COMPLETED
		ride.completed_at = now
		ride.save(update_fields=['status', 'completed_at', 'updated_at'])
		ride.passengers.filter(completed_at__isnull=True).update(completed_at=now)
		participants = [ride.driver_user, *(item.passenger_user for item in ride.passengers.select_related('passenger_user'))]
		for participant in participants:
			sync_user_achievements(participant)

		RideStatusHistory.objects.create(
			ride=ride,
			from_status=Ride.RideStatus.IN_PROGRESS,
			to_status=Ride.RideStatus.COMPLETED,
			changed_by_user=request.user,
			reason='Ride completed.',
		)
		notify_ride_participants(
			ride,
			title='Carona finalizada',
			body='A carona foi finalizada. Sua avaliação por badges já está disponível.',
			statuses=[RideRequest.RequestStatus.CONFIRMED],
			event_name='completed',
		)

		serializer = RideTrackingSerializer(ride, context=self.get_serializer_context())
		return Response(serializer.data)


class RideRequestViewSet(viewsets.ModelViewSet):
	serializer_class = RideRequestSerializer
	permission_classes = [permissions.IsAuthenticated]

	def get_queryset(self):
		queryset = RideRequest.objects.select_related('ride', 'passenger_user', 'ride__driver_user').order_by('-requested_at')
		role = self.request.query_params.get('role')
		if role == 'driver':
			queryset = queryset.filter(ride__driver_user=self.request.user)
		else:
			queryset = queryset.filter(Q(passenger_user=self.request.user) | Q(ride__driver_user=self.request.user)).distinct()

		ride_id = self.request.query_params.get('ride')
		if ride_id:
			queryset = queryset.filter(ride_id=ride_id)

		status_filter = self.request.query_params.get('status')
		if status_filter:
			queryset = queryset.filter(status=status_filter)

		return queryset

	def _get_locked_request(self, pk):
		"""Lock a request row so concurrent decisions cannot double-transition it."""
		get_object_or_404(self.filter_queryset(self.get_queryset()), pk=pk)
		return get_object_or_404(
			RideRequest.objects.select_related('ride', 'passenger_user', 'ride__driver_user').select_for_update(),
			pk=pk,
		)

	@transaction.atomic
	def perform_create(self, serializer):
		ride = Ride.objects.select_for_update().get(pk=serializer.validated_data['ride'].pk)
		serializer.validated_data['ride'] = ride
		if ride.driver_user_id == self.request.user.id:
			raise serializers.ValidationError({'ride': 'Driver cannot request their own ride.'})
		if RideRequest.objects.filter(ride=ride, passenger_user=self.request.user).exists():
			raise serializers.ValidationError({'ride': 'Você já possui uma solicitação para esta carona.'})

		ride_request = serializer.save(passenger_user=self.request.user)
		RideRequestStatusHistory.objects.create(
			ride_request=ride_request,
			from_status=None,
			to_status=ride_request.status,
			changed_by_user=self.request.user,
			reason='Ride request created.',
		)
		create_notification(
			recipient_user=ride_request.ride.driver_user,
			notification_type=Notification.NotificationType.REQUEST,
			title='Nova solicitação de carona',
			body=f'{self.request.user.full_name} solicitou uma vaga na sua carona.',
			link_path='/acompanhar-carona',
			ride=ride_request.ride,
			ride_request=ride_request,
			event_key=f'ride-request:{ride_request.id}:created',
		)

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	@transaction.atomic
	def approve(self, request, pk=None):
		ride_request = self._get_locked_request(pk)
		if ride_request.ride.driver_user_id != request.user.id:
			return Response({'detail': 'Only the driver can approve this request.'}, status=status.HTTP_403_FORBIDDEN)
		if ride_request.status != RideRequest.RequestStatus.PENDING:
			return Response({'detail': 'Only pending requests can be approved.'}, status=status.HTTP_400_BAD_REQUEST)

		seat = ride_request.ride.seats.filter(seat_number=ride_request.seat_number).first()
		if seat is None or seat.seat_status != RideSeat.SeatStatus.AVAILABLE:
			return Response({'detail': 'Selected seat is no longer available.'}, status=status.HTTP_400_BAD_REQUEST)

		ride_request.status = RideRequest.RequestStatus.APPROVED
		ride_request.driver_decision_at = timezone.now()
		ride_request.save(update_fields=['status', 'driver_decision_at'])

		seat.seat_status = RideSeat.SeatStatus.RESERVED
		seat.reserved_for_request = ride_request
		seat.save(update_fields=['seat_status', 'reserved_for_request', 'updated_at'])

		RideRequestStatusHistory.objects.create(
			ride_request=ride_request,
			from_status=RideRequest.RequestStatus.PENDING,
			to_status=RideRequest.RequestStatus.APPROVED,
			changed_by_user=request.user,
			reason='Request approved by driver.',
		)
		create_notification(
			recipient_user=ride_request.passenger_user,
			notification_type=Notification.NotificationType.APPROVAL,
			title='Solicitação aprovada',
			body=f'{request.user.full_name} aprovou sua solicitação. Veja o perfil e confirme para liberar o WhatsApp.',
			link_path='/acompanhar-carona',
			ride=ride_request.ride,
			ride_request=ride_request,
			event_key=f'ride-request:{ride_request.id}:approved',
		)

		return Response(self.get_serializer(ride_request).data)

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	@transaction.atomic
	def reject(self, request, pk=None):
		ride_request = self._get_locked_request(pk)
		if ride_request.ride.driver_user_id != request.user.id:
			return Response({'detail': 'Only the driver can reject this request.'}, status=status.HTTP_403_FORBIDDEN)
		if ride_request.status != RideRequest.RequestStatus.PENDING:
			return Response({'detail': 'Only pending requests can be rejected.'}, status=status.HTTP_400_BAD_REQUEST)

		ride_request.status = RideRequest.RequestStatus.REJECTED
		ride_request.driver_decision_at = timezone.now()
		ride_request.save(update_fields=['status', 'driver_decision_at'])

		RideRequestStatusHistory.objects.create(
			ride_request=ride_request,
			from_status=RideRequest.RequestStatus.PENDING,
			to_status=RideRequest.RequestStatus.REJECTED,
			changed_by_user=request.user,
			reason='Request rejected by driver.',
		)
		create_notification(
			recipient_user=ride_request.passenger_user,
			notification_type=Notification.NotificationType.STATUS,
			title='Solicitação recusada',
			body=f'O motorista recusou sua solicitação para {ride_request.ride.origin_text} → {ride_request.ride.destination_text}.',
			link_path='/acompanhar-carona',
			ride=ride_request.ride,
			ride_request=ride_request,
			event_key=f'ride-request:{ride_request.id}:rejected',
		)

		return Response(self.get_serializer(ride_request).data)

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	@transaction.atomic
	def confirm(self, request, pk=None):
		ride_request = self._get_locked_request(pk)
		if ride_request.passenger_user_id != request.user.id:
			return Response({'detail': 'Only the passenger can confirm this request.'}, status=status.HTTP_403_FORBIDDEN)
		if ride_request.status != RideRequest.RequestStatus.APPROVED:
			return Response({'detail': 'Only approved requests can be confirmed.'}, status=status.HTTP_400_BAD_REQUEST)

		seat = ride_request.ride.seats.select_for_update().filter(seat_number=ride_request.seat_number).first()
		if seat is None or seat.seat_status != RideSeat.SeatStatus.RESERVED or seat.reserved_for_request_id != ride_request.id:
			return Response({'detail': 'Reserved seat state is invalid for confirmation.'}, status=status.HTTP_400_BAD_REQUEST)

		now = timezone.now()
		ride_request.status = RideRequest.RequestStatus.CONFIRMED
		ride_request.passenger_decision_at = now
		ride_request.contact_unlocked_at = now
		ride_request.save(update_fields=['status', 'passenger_decision_at', 'contact_unlocked_at'])

		seat.seat_status = RideSeat.SeatStatus.OCCUPIED
		seat.occupant_user = ride_request.passenger_user
		seat.save(update_fields=['seat_status', 'occupant_user', 'updated_at'])

		RidePassenger.objects.get_or_create(
			ride=ride_request.ride,
			passenger_user=ride_request.passenger_user,
			defaults={'seat_number': ride_request.seat_number},
		)

		RideRequestStatusHistory.objects.create(
			ride_request=ride_request,
			from_status=RideRequest.RequestStatus.APPROVED,
			to_status=RideRequest.RequestStatus.CONFIRMED,
			changed_by_user=request.user,
			reason='Request confirmed by passenger.',
		)
		create_notification(
			recipient_user=ride_request.ride.driver_user,
			notification_type=Notification.NotificationType.STATUS,
			title='Match confirmado',
			body=f'{ride_request.passenger_user.full_name} confirmou a vaga. O contato por WhatsApp foi liberado para os dois.',
			link_path='/acompanhar-carona',
			ride=ride_request.ride,
			ride_request=ride_request,
			event_key=f'ride-request:{ride_request.id}:confirmed',
		)

		return Response(self.get_serializer(ride_request).data)

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	@transaction.atomic
	def decline(self, request, pk=None):
		ride_request = self._get_locked_request(pk)
		if ride_request.passenger_user_id != request.user.id:
			return Response({'detail': 'Only the passenger can decline this request.'}, status=status.HTTP_403_FORBIDDEN)
		if ride_request.status != RideRequest.RequestStatus.APPROVED:
			return Response({'detail': 'Only approved requests can be declined.'}, status=status.HTTP_400_BAD_REQUEST)

		seat = ride_request.ride.seats.select_for_update().filter(seat_number=ride_request.seat_number, reserved_for_request=ride_request).first()
		if seat is not None:
			seat.seat_status = RideSeat.SeatStatus.AVAILABLE
			seat.reserved_for_request = None
			seat.save(update_fields=['seat_status', 'reserved_for_request', 'updated_at'])

		ride_request.status = RideRequest.RequestStatus.DECLINED
		ride_request.passenger_decision_at = timezone.now()
		ride_request.save(update_fields=['status', 'passenger_decision_at'])

		RideRequestStatusHistory.objects.create(
			ride_request=ride_request,
			from_status=RideRequest.RequestStatus.APPROVED,
			to_status=RideRequest.RequestStatus.DECLINED,
			changed_by_user=request.user,
			reason='Request declined by passenger.',
		)
		create_notification(
			recipient_user=ride_request.ride.driver_user,
			notification_type=Notification.NotificationType.STATUS,
			title='Solicitante desistiu',
			body=f'{ride_request.passenger_user.full_name} desistiu da vaga antes de confirmar.',
			link_path='/acompanhar-carona',
			ride=ride_request.ride,
			ride_request=ride_request,
			event_key=f'ride-request:{ride_request.id}:declined',
		)

		return Response(self.get_serializer(ride_request).data)

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	@transaction.atomic
	def cancel(self, request, pk=None):
		ride_request = self._get_locked_request(pk)
		if ride_request.passenger_user_id != request.user.id:
			return Response({'detail': 'Somente o passageiro pode cancelar sua participação.'}, status=status.HTTP_403_FORBIDDEN)
		if ride_request.status not in {
			RideRequest.RequestStatus.PENDING,
			RideRequest.RequestStatus.APPROVED,
			RideRequest.RequestStatus.CONFIRMED,
		}:
			return Response({'detail': 'Esta participação não pode mais ser cancelada.'}, status=status.HTTP_400_BAD_REQUEST)
		if not ride_can_be_changed(ride_request.ride):
			return Response({'detail': 'O cancelamento só é permitido com pelo menos 1 dia de antecedência.'}, status=status.HTTP_400_BAD_REQUEST)

		previous_status = ride_request.status
		seat = ride_request.ride.seats.select_for_update().filter(seat_number=ride_request.seat_number).first()
		if seat:
			seat.seat_status = RideSeat.SeatStatus.AVAILABLE
			seat.reserved_for_request = None
			seat.occupant_user = None
			seat.save(update_fields=['seat_status', 'reserved_for_request', 'occupant_user', 'updated_at'])
		RidePassenger.objects.filter(ride=ride_request.ride, passenger_user=request.user).delete()
		ride_request.status = RideRequest.RequestStatus.CANCELLED
		ride_request.passenger_decision_at = timezone.now()
		ride_request.contact_unlocked_at = None
		ride_request.save(update_fields=['status', 'passenger_decision_at', 'contact_unlocked_at'])
		RideRequestStatusHistory.objects.create(
			ride_request=ride_request,
			from_status=previous_status,
			to_status=ride_request.status,
			changed_by_user=request.user,
			reason='Participation cancelled by passenger.',
		)
		create_notification(
			recipient_user=ride_request.ride.driver_user,
			notification_type=Notification.NotificationType.STATUS,
			title='Participação cancelada',
			body=f'{request.user.full_name} cancelou a participação; a vaga já voltou a ficar disponível.',
			link_path='/acompanhar-carona',
			ride=ride_request.ride,
			ride_request=ride_request,
			event_key=f'ride-request:{ride_request.id}:cancelled',
		)
		return Response(self.get_serializer(ride_request).data)


class RideSearchCreateView(generics.CreateAPIView):
	serializer_class = RideSearchSerializer
	permission_classes = [permissions.IsAuthenticated]

	@transaction.atomic
	def perform_create(self, serializer):
		data = serializer.validated_data
		recent = RideSearch.objects.filter(
			user=self.request.user,
			origin_text=data.get('origin_text', ''),
			destination_text=data.get('destination_text', ''),
			requested_date=data.get('requested_date'),
			requested_time=data.get('requested_time'),
			origin_lat=data.get('origin_lat'),
			origin_lng=data.get('origin_lng'),
			destination_lat=data.get('destination_lat'),
			destination_lng=data.get('destination_lng'),
			gender_restriction=data.get('gender_restriction', 'none'),
			vehicle_type=data.get('vehicle_type', ''),
			prefer_deviation=data.get('prefer_deviation'),
			max_distance_km=data.get('max_distance_km', 8),
			time_flexibility_minutes=data.get('time_flexibility_minutes', 90),
			created_at__gte=timezone.now() - timedelta(minutes=5),
		).first()
		if recent:
			serializer.instance = recent
			return
		serializer.save(user=self.request.user)


class NotificationPagination(PageNumberPagination):
	page_size = 20
	page_size_query_param = 'page_size'
	max_page_size = 50


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
	serializer_class = NotificationSerializer
	permission_classes = [permissions.IsAuthenticated]
	pagination_class = NotificationPagination

	def get_queryset(self):
		return Notification.objects.filter(recipient_user=self.request.user).order_by('-created_at')

	@action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	def mark_read(self, request, pk=None):
		notification = self.get_object()
		if not notification.is_read:
			notification.is_read = True
			notification.read_at = timezone.now()
			notification.save(update_fields=['is_read', 'read_at'])
			broadcast_notification_change(request.user.id)
		return Response(self.get_serializer(notification).data)

	@action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated])
	def mark_all_read(self, request):
		now = timezone.now()
		updated = self.get_queryset().filter(is_read=False).update(is_read=True, read_at=now)
		if updated:
			broadcast_notification_change(request.user.id)
		return Response({'updated': updated})


class RideBadgeFeedbackViewSet(viewsets.ModelViewSet):
	serializer_class = RideBadgeFeedbackSerializer
	permission_classes = [permissions.IsAuthenticated]

	def get_queryset(self):
		queryset = RideBadgeFeedback.objects.select_related('badge', 'ride', 'giver_user', 'receiver_user').order_by('-created_at')
		ride_id = self.request.query_params.get('ride')
		if ride_id:
			queryset = queryset.filter(ride_id=ride_id)
		return queryset.filter(Q(giver_user=self.request.user) | Q(receiver_user=self.request.user)).distinct()

	@transaction.atomic
	def perform_create(self, serializer):
		feedback = serializer.save(giver_user=self.request.user)
		sync_user_achievements(feedback.receiver_user)
		create_notification(
			recipient_user=feedback.receiver_user,
			notification_type=Notification.NotificationType.PROFILE,
			title='Você recebeu um badge',
			body=f'{self.request.user.full_name} reconheceu você com o badge “{feedback.badge.name}”.',
			link_path='/perfil',
			ride=feedback.ride,
			event_key=f'feedback:{feedback.id}:received',
		)

