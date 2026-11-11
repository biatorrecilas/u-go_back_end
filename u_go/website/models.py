import uuid
from pathlib import Path
from django.contrib.auth.base_user import BaseUserManager
from django.db import models
from django.db.models import Q
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# ==========================================
# 1. CORE & UNIVERSIDADE
# ==========================================

class University(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    email_domain = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name='courses')
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('university', 'name')

# ==========================================
# 2. USUÁRIOS E AUTENTICAÇÃO
# ==========================================

class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('The email field must be set.')

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('account_status', User.AccountStatus.PENDING)
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('account_status', User.AccountStatus.ACTIVE)
        extra_fields.setdefault('is_email_verified', True)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('ride_visibility_group', User.VisibilityGroup.MAN)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user(email, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    class VisibilityGroup(models.TextChoices):
        WOMAN = 'woman', _('Woman')
        MAN = 'man', _('Man')
        NON_BINARY = 'non_binary', _('Non-binary')
        OTHER = 'other', _('Other')
        PREFER_NOT_TO_SAY = 'prefer_not_to_say', _('Prefer not to say')
        NONE = 'none', _('None')

    class AccountStatus(models.TextChoices):
        PENDING = 'pending_verification', _('Pending Verification')
        ACTIVE = 'active', _('Active')
        SUSPENDED = 'suspended', _('Suspended')
        DISABLED = 'disabled', _('Disabled')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    university = models.ForeignKey(University, on_delete=models.PROTECT, related_name='users')
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='users')
    
    full_name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20)
    
    ride_visibility_group = models.CharField(max_length=30, choices=VisibilityGroup.choices, default=VisibilityGroup.NONE)
    custom_gender = models.CharField(max_length=50, null=True, blank=True)
    account_status = models.CharField(max_length=30, choices=AccountStatus.choices, default=AccountStatus.PENDING)
    is_email_verified = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name', 'phone', 'university', 'course']

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)

        if self.course_id and self.university_id and self.course.university_id != self.university_id:
            raise ValidationError({'course': _('Course must belong to the selected university.')})

    @property
    def is_active(self):
        return self.account_status == self.AccountStatus.ACTIVE

def profile_avatar_upload_to(instance, filename):
    return f'profiles/{instance.user_id}/avatar/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


def profile_cover_upload_to(instance, filename):
    return f'profiles/{instance.user_id}/cover/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True, related_name='profile')
    avatar_url = models.URLField(max_length=500, null=True, blank=True)
    cover_photo_url = models.URLField(max_length=500, null=True, blank=True)
    avatar_image = models.ImageField(upload_to=profile_avatar_upload_to, null=True, blank=True)
    cover_photo_image = models.ImageField(upload_to=profile_cover_upload_to, null=True, blank=True)
    bio = models.TextField(null=True, blank=True)
    address_text = models.CharField(max_length=255, null=True, blank=True)
    semester = models.SmallIntegerField(null=True, blank=True)
    avatar_config = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(semester__isnull=True) | Q(semester__gte=1, semester__lte=12),
                name='user_profile_semester_range',
            ),
        ]

class UserTermsAcceptance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='terms_acceptances')
    terms_version = models.CharField(max_length=20)
    privacy_version = models.CharField(max_length=20)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'terms_version', 'privacy_version')

class EmailVerificationCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='verification_codes')
    code_hash = models.TextField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

class UserSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    refresh_token_hash = models.TextField()
    remember_me = models.BooleanField(default=False)
    user_agent = models.TextField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

# ==========================================
# 3. CARONAS (OFERTA)
# ==========================================

class VehicleType(models.TextChoices):
    CAR = 'car', _('Car')
    MOTORCYCLE = 'motorcycle', _('Motorcycle')

class GenderRestriction(models.TextChoices):
    NONE = 'none', _('None')
    WOMEN_ONLY = 'women_only', _('Women Only')
    MEN_ONLY = 'men_only', _('Men Only')
    NON_BINARY_ONLY = 'non_binary_only', _('Non-binary Only')
    OTHER_ONLY = 'other_only', _('Other Only')

class RideSeries(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_series')
    
    origin_text = models.CharField(max_length=150)
    origin_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    origin_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    
    destination_text = models.CharField(max_length=150)
    destination_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    destination_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    departure_time = models.TimeField()
    
    vehicle_type = models.CharField(max_length=20, choices=VehicleType.choices)
    vehicle_model = models.CharField(max_length=100)
    vehicle_plate = models.CharField(max_length=10)
    seat_capacity = models.SmallIntegerField()
    
    driver_contact_phone = models.CharField(max_length=20)
    gender_restriction = models.CharField(max_length=30, choices=GenderRestriction.choices, default=GenderRestriction.NONE)
    price_amount = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(null=True, blank=True)
    accept_deviation = models.BooleanField(default=False)
    deviation_distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    deviation_time_min = models.SmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(vehicle_type=VehicleType.MOTORCYCLE, seat_capacity=1)
                    | Q(vehicle_type=VehicleType.CAR, seat_capacity__gte=4, seat_capacity__lte=7)
                ),
                name='ride_series_valid_seat_capacity',
            ),
        ]

class RideSeriesWeekday(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride_series = models.ForeignKey(RideSeries, on_delete=models.CASCADE, related_name='weekdays')
    weekday = models.SmallIntegerField() # ISO weekday: 1 (Monday) to 7 (Sunday)

    class Meta:
        unique_together = ('ride_series', 'weekday')
        constraints = [
            models.CheckConstraint(
                condition=Q(weekday__gte=1, weekday__lte=7),
                name='ride_series_weekday_range',
            ),
        ]

class Ride(models.Model):
    class RideStatus(models.TextChoices):
        SCHEDULED = 'scheduled', _('Scheduled')
        IN_PROGRESS = 'in_progress', _('In Progress')
        COMPLETED = 'completed', _('Completed')
        CANCELLED = 'cancelled', _('Cancelled')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride_series = models.ForeignKey(RideSeries, on_delete=models.SET_NULL, null=True, blank=True, related_name='rides')
    driver_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rides_driven')
    
    origin_text = models.CharField(max_length=150)
    origin_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    origin_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    
    destination_text = models.CharField(max_length=150)
    destination_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    destination_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    
    scheduled_start_at = models.DateTimeField()
    
    vehicle_type = models.CharField(max_length=20, choices=VehicleType.choices)
    vehicle_model = models.CharField(max_length=100)
    vehicle_plate = models.CharField(max_length=10)
    seat_capacity = models.SmallIntegerField()
    
    driver_contact_phone = models.CharField(max_length=20)
    gender_restriction = models.CharField(max_length=30, choices=GenderRestriction.choices, default=GenderRestriction.NONE)
    price_amount = models.DecimalField(max_digits=10, decimal_places=2)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    duration_minutes = models.SmallIntegerField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    accept_deviation = models.BooleanField(default=False)
    deviation_distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    deviation_time_min = models.SmallIntegerField(null=True, blank=True)
    
    status = models.CharField(max_length=20, choices=RideStatus.choices, default=RideStatus.SCHEDULED)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['driver_user', 'status', 'scheduled_start_at']),
            models.Index(fields=['gender_restriction', 'scheduled_start_at']),
            models.Index(fields=['status', 'scheduled_start_at'], name='ride_status_start_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(vehicle_type=VehicleType.MOTORCYCLE, seat_capacity=1)
                    | Q(vehicle_type=VehicleType.CAR, seat_capacity__gte=4, seat_capacity__lte=7)
                ),
                name='ride_valid_seat_capacity',
            ),
        ]

class RideEdit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='edits')
    edited_by_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_edits')
    changed_fields_jsonb = models.JSONField() # JSONB no PostgreSQL
    created_at = models.DateTimeField(auto_now_add=True)

# ==========================================
# 4. ASSENTOS E SOLICITAÇÕES (DEMANDA)
# ==========================================

class RideRequest(models.Model):
    class RequestStatus(models.TextChoices):
        PENDING = 'pending_driver', _('Pending Driver')
        APPROVED = 'approved_by_driver', _('Approved by Driver')
        CONFIRMED = 'confirmed', _('Confirmed')
        REJECTED = 'rejected_by_driver', _('Rejected by Driver')
        DECLINED = 'declined_by_passenger', _('Declined by Passenger')
        CANCELLED = 'cancelled_by_passenger', _('Cancelled by Passenger')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='requests')
    passenger_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_requests')
    seat_number = models.SmallIntegerField()
    status = models.CharField(max_length=30, choices=RequestStatus.choices, default=RequestStatus.PENDING)
    
    requested_at = models.DateTimeField(auto_now_add=True)
    driver_decision_at = models.DateTimeField(null=True, blank=True)
    passenger_decision_at = models.DateTimeField(null=True, blank=True)
    contact_unlocked_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def active_statuses(cls):
        """Statuses that still consume one of the ride's passenger slots."""
        return [
            cls.RequestStatus.PENDING,
            cls.RequestStatus.APPROVED,
            cls.RequestStatus.CONFIRMED,
        ]

    class Meta:
        unique_together = ('ride', 'passenger_user')
        indexes = [
            models.Index(fields=['ride', 'status']),
            models.Index(fields=['passenger_user', 'status']),
        ]
        # O ÍNDICE PARCIAL OBRIGATÓRIO DO POSTGRES
        constraints = [
            models.UniqueConstraint(
                fields=['ride', 'seat_number'],
                condition=Q(status__in=['pending_driver', 'approved_by_driver', 'confirmed']),
                name='ux_ride_requests_active_seat'
            )
        ]

    def clean(self):
        super().clean()

        if self.ride_id and self.passenger_user_id and self.ride.driver_user_id == self.passenger_user_id:
            raise ValidationError({'passenger_user': _('Driver cannot request their own ride.')})

        if self.ride_id and self.seat_number and not 1 <= self.seat_number <= self.ride.seat_capacity:
            raise ValidationError({'seat_number': _('Seat number must be within the ride seat capacity.')})

class RideSeat(models.Model):
    class SeatStatus(models.TextChoices):
        AVAILABLE = 'available', _('Available')
        UNAVAILABLE = 'unavailable', _('Unavailable')
        RESERVED = 'reserved', _('Reserved')
        OCCUPIED = 'occupied', _('Occupied')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='seats')
    seat_number = models.SmallIntegerField()
    seat_status = models.CharField(max_length=20, choices=SeatStatus.choices, default=SeatStatus.AVAILABLE)
    
    reserved_for_request = models.OneToOneField(RideRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name='reserved_seat')
    occupant_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='occupied_seats')
    
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('ride', 'seat_number')

    def clean(self):
        super().clean()

        if self.reserved_for_request_id and self.reserved_for_request.ride_id != self.ride_id:
            raise ValidationError({'reserved_for_request': _('Reserved request must belong to the same ride.')})

        if self.occupant_user_id and self.ride.driver_user_id == self.occupant_user_id:
            raise ValidationError({'occupant_user': _('Driver cannot occupy a passenger seat.')})

class RidePassenger(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='passengers')
    passenger_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rides_as_passenger')
    seat_number = models.SmallIntegerField()
    
    confirmed_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('ride', 'passenger_user'), ('ride', 'seat_number')]

    def clean(self):
        super().clean()

        if self.ride_id and self.passenger_user_id and self.ride.driver_user_id == self.passenger_user_id:
            raise ValidationError({'passenger_user': _('Driver cannot be registered as a passenger.')})

        if self.ride_id and self.seat_number and not 1 <= self.seat_number <= self.ride.seat_capacity:
            raise ValidationError({'seat_number': _('Seat number must be within the ride seat capacity.')})

# ==========================================
# 5. HISTÓRICOS (LOGS)
# ==========================================

class RideStatusHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='status_history')
    from_status = models.CharField(max_length=20, choices=Ride.RideStatus.choices, null=True, blank=True)
    to_status = models.CharField(max_length=20, choices=Ride.RideStatus.choices)
    changed_by_user = models.ForeignKey(User, on_delete=models.CASCADE)
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(null=True, blank=True)

class RideRequestStatusHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride_request = models.ForeignKey(RideRequest, on_delete=models.CASCADE, related_name='status_history')
    from_status = models.CharField(max_length=30, choices=RideRequest.RequestStatus.choices, null=True, blank=True)
    to_status = models.CharField(max_length=30, choices=RideRequest.RequestStatus.choices)
    changed_by_user = models.ForeignKey(User, on_delete=models.CASCADE)
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(null=True, blank=True)

# ==========================================
# 6. NOTIFICAÇÕES E SOCIAL (GAMIFICAÇÃO)
# ==========================================

class Notification(models.Model):
    class NotificationType(models.TextChoices):
        REQUEST = 'request', _('Request')
        APPROVAL = 'approval', _('Approval')
        REMINDER = 'reminder', _('Reminder')
        PROFILE = 'profile', _('Profile')
        RIDE_UPDATE = 'ride_update', _('Ride update')
        STATUS = 'status', _('Status change')
        MATCH = 'match', _('Match recommendation')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    type = models.CharField(max_length=20, choices=NotificationType.choices)
    title = models.CharField(max_length=120)
    body = models.TextField()
    link_path = models.CharField(max_length=255)
    
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, null=True, blank=True)
    ride_request = models.ForeignKey(RideRequest, on_delete=models.CASCADE, null=True, blank=True)
    event_key = models.CharField(max_length=255, null=True, blank=True, unique=True)
    
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['recipient_user', 'is_read', '-created_at']),
        ]


class RideSearch(models.Model):
    """A privacy-minimized search signal used for future ride recommendations."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_searches')
    origin_text = models.CharField(max_length=150, blank=True)
    destination_text = models.CharField(max_length=150, blank=True)
    requested_date = models.DateField(null=True, blank=True)
    requested_time = models.TimeField(null=True, blank=True)
    origin_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    origin_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    destination_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    destination_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    vehicle_type = models.CharField(max_length=20, choices=VehicleType.choices, blank=True)
    prefer_deviation = models.BooleanField(null=True, blank=True)
    max_distance_km = models.DecimalField(max_digits=5, decimal_places=1, default=8)
    time_flexibility_minutes = models.SmallIntegerField(default=90)
    gender_restriction = models.CharField(
        max_length=30,
        choices=GenderRestriction.choices,
        default=GenderRestriction.NONE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

class Badge(models.Model):
    class BadgeKind(models.TextChoices):
        FEEDBACK = 'feedback', _('Feedback')
        ACHIEVEMENT = 'achievement', _('Achievement')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField()
    badge_kind = models.CharField(max_length=20, choices=BadgeKind.choices)
    display_color = models.CharField(max_length=50, null=True, blank=True)
    is_active = models.BooleanField(default=True)

class UserBadge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='badges')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE)
    progress_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    target_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    achieved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'badge')

class RideBadgeFeedback(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='feedbacks')
    giver_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='given_feedbacks')
    receiver_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_feedbacks')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('ride', 'giver_user', 'receiver_user', 'badge')
