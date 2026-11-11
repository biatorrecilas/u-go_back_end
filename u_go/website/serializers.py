from datetime import timedelta
from decimal import Decimal
from io import BytesIO
import re
import uuid

from django.contrib.auth.password_validation import validate_password
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import serializers

from .models import EmailVerificationCode
from .models import (
    Badge,
    Course,
    Notification,
    Ride,
    RideBadgeFeedback,
    RidePassenger,
    RideRequest,
    RideSearch,
    RideSeat,
    University,
    User,
    UserBadge,
    UserProfile,
    UserTermsAcceptance,
)
from .services.matching import (
    build_user_matching_context,
    calculate_match,
    criteria_from_query_params,
)
from .services.profiles import (
    build_feedback_summary,
    build_user_impact,
    engagement_title,
    sync_user_achievements,
)


PROFILE_IMAGE_LIMITS = {
    'avatar': {'max_bytes': 5 * 1024 * 1024, 'max_size': (1024, 1024), 'min_size': (64, 64)},
    'cover': {'max_bytes': 8 * 1024 * 1024, 'max_size': (2400, 1350), 'min_size': (320, 100)},
}
PROFILE_IMAGE_CONTENT_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
PROFILE_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}


def profile_asset_url(profile, image_field, fallback_field, request=None):
    if profile is None:
        return None
    image = getattr(profile, image_field, None)
    value = image.url if image else getattr(profile, fallback_field, None)
    if value and request is not None and value.startswith('/'):
        return request.build_absolute_uri(value)
    return value


def normalize_profile_image(uploaded_file, kind):
    limits = PROFILE_IMAGE_LIMITS[kind]
    if uploaded_file.size > limits['max_bytes']:
        max_mb = limits['max_bytes'] // (1024 * 1024)
        raise serializers.ValidationError(f'A imagem deve ter no máximo {max_mb} MB.')
    if getattr(uploaded_file, 'content_type', None) not in PROFILE_IMAGE_CONTENT_TYPES:
        raise serializers.ValidationError('Envie uma imagem JPG, PNG ou WebP.')
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as source:
            source.verify()
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as source:
            if source.format not in PROFILE_IMAGE_FORMATS:
                raise serializers.ValidationError('O conteúdo do arquivo não corresponde a uma imagem permitida.')
            width, height = source.size
            minimum_width, minimum_height = limits['min_size']
            if width < minimum_width or height < minimum_height:
                raise serializers.ValidationError(
                    f'A imagem precisa ter pelo menos {minimum_width}×{minimum_height} pixels.'
                )
            if width * height > 30_000_000:
                raise serializers.ValidationError('A imagem possui resolução excessiva.')
            image = ImageOps.exif_transpose(source)
            if image.mode not in {'RGB', 'RGBA'}:
                image = image.convert('RGBA' if 'transparency' in image.info else 'RGB')
            image.thumbnail(limits['max_size'], Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format='WEBP', quality=85, method=6)
    except serializers.ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise serializers.ValidationError('O arquivo enviado não é uma imagem válida.')
    return ContentFile(output.getvalue(), name=f'{kind}-{uuid.uuid4().hex}.webp')


class UniversitySerializer(serializers.ModelSerializer):
    class Meta:
        model = University
        fields = ['id', 'name', 'email_domain']


class CourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ['id', 'university', 'name', 'is_active']


class PublicUserSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'full_name', 'ride_visibility_group', 'avatar_url']

    def get_avatar_url(self, obj):
        profile = getattr(obj, 'profile', None)
        return profile_asset_url(profile, 'avatar_image', 'avatar_url', self.context.get('request'))


class UserProfileSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    cover_photo_url = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = ['avatar_url', 'cover_photo_url', 'bio', 'address_text', 'semester', 'avatar_config', 'updated_at']
        read_only_fields = ['updated_at']

    def get_avatar_url(self, obj):
        return profile_asset_url(obj, 'avatar_image', 'avatar_url', self.context.get('request'))

    def get_cover_photo_url(self, obj):
        return profile_asset_url(obj, 'cover_photo_image', 'cover_photo_url', self.context.get('request'))


class CurrentUserProfileSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    cover_photo_url = serializers.SerializerMethodField()
    avatar_image = serializers.ImageField(write_only=True, required=False)
    cover_photo_image = serializers.ImageField(write_only=True, required=False)
    remove_avatar = serializers.BooleanField(write_only=True, required=False, default=False)
    remove_cover = serializers.BooleanField(write_only=True, required=False, default=False)
    course = serializers.PrimaryKeyRelatedField(source='user.course', queryset=Course.objects.filter(is_active=True), required=False)
    course_name = serializers.CharField(source='user.course.name', read_only=True)
    phone = serializers.CharField(source='user.phone', required=False, max_length=20)

    class Meta:
        model = UserProfile
        fields = [
            'avatar_url', 'cover_photo_url', 'bio', 'address_text', 'semester',
            'course', 'course_name', 'phone', 'avatar_image', 'cover_photo_image',
            'remove_avatar', 'remove_cover', 'avatar_config', 'updated_at',
        ]
        read_only_fields = ['updated_at']
        extra_kwargs = {
            'bio': {'max_length': 500},
            'semester': {'min_value': 1, 'max_value': 12},
        }

    def validate_course(self, course):
        if course.university_id != self.instance.user.university_id:
            raise serializers.ValidationError('O curso precisa pertencer à sua universidade.')
        return course

    def validate_phone(self, value):
        digits = ''.join(character for character in value if character.isdigit())
        if not 10 <= len(digits) <= 15:
            raise serializers.ValidationError('Informe um telefone válido com DDD.')
        return value.strip()

    def validate_avatar_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('A personalização do avatar precisa ser um objeto válido.')
        allowed = {
            'enabled': {True, False},
            'skin': {'fair', 'light', 'warm', 'tan', 'brown', 'deep'},
            'hairColor': {'black', 'dark_brown', 'brown', 'light_brown', 'blonde', 'red', 'gray', 'white'},
            'hairStyle': {'straight', 'wavy', 'curly', 'coily', 'afro', 'braids', 'locs', 'bun', 'buzz', 'bald'},
            'hairLength': {'short', 'medium', 'long'},
            'facialHair': {'none', 'stubble', 'mustache', 'goatee', 'short_beard', 'beard'},
            'facialHairColor': {'black', 'dark_brown', 'brown', 'light_brown', 'blonde', 'red', 'gray', 'white'},
            'outfitType': {
                'hoodie', 'jacket', 'tee', 'sweater', 'polo', 'varsity',
                'overalls', 'jersey', 'turtleneck',
            },
            'outfitColor': {'green', 'blue', 'coral', 'purple', 'yellow', 'charcoal'},
            'headwear': {'none', 'cap', 'beanie', 'headscarf'},
            'eyewear': {'none', 'round_glasses', 'square_glasses', 'sunglasses'},
            'earAccessory': {'none', 'headphones', 'earrings', 'hearing_aid'},
            'outfitAccessory': {'none', 'leaf_pin', 'animal_pin', 'course_pin'},
            'animalPin': {'cat', 'dog', 'bird', 'rabbit', 'fish', 'turtle'},
            'expression': {'happy', 'calm', 'bored', 'wink'},
        }
        defaults = {
            'enabled': False,
            'skin': 'warm',
            'hairColor': 'black',
            'hairStyle': 'straight',
            'hairLength': 'short',
            'facialHair': 'none',
            'facialHairColor': 'black',
            'outfitType': 'hoodie',
            'outfitColor': 'green',
            'headwear': 'none',
            'eyewear': 'none',
            'earAccessory': 'none',
            'outfitAccessory': 'none',
            'animalPin': 'cat',
            'coursePin': 'Curso',
            'pinColor': '#1cb0f6',
            'expression': 'happy',
        }
        legacy_keys = {'hair', 'outfit', 'accessory'}
        custom_fields = {'coursePin', 'pinColor'}
        unknown = set(value) - set(allowed) - legacy_keys - custom_fields
        if unknown:
            raise serializers.ValidationError('A personalização contém campos desconhecidos.')
        normalized = {**defaults, **value}
        if normalized['outfitAccessory'] == 'campus_pin':
            normalized['outfitAccessory'] = 'animal_pin'
        legacy_hair = {
            'short': ('straight', 'short'), 'medium': ('straight', 'medium'),
            'long': ('straight', 'long'), 'curly': ('curly', 'medium'),
            'waves': ('wavy', 'medium'), 'bun': ('bun', 'long'),
        }.get(value.get('hair'))
        if 'hair' in value and legacy_hair is None:
            raise serializers.ValidationError('O cabelo legado informado não é válido.')
        if legacy_hair:
            if 'hairStyle' not in value:
                normalized['hairStyle'] = legacy_hair[0]
            if 'hairLength' not in value:
                normalized['hairLength'] = legacy_hair[1]
        legacy_outfit = {
            'green_hoodie': ('hoodie', 'green'), 'blue_jacket': ('jacket', 'blue'),
            'coral_tee': ('tee', 'coral'), 'purple_sweater': ('sweater', 'purple'),
        }.get(value.get('outfit'))
        if 'outfit' in value and legacy_outfit is None:
            raise serializers.ValidationError('A roupa legada informada não é válida.')
        if legacy_outfit:
            if 'outfitType' not in value:
                normalized['outfitType'] = legacy_outfit[0]
            if 'outfitColor' not in value:
                normalized['outfitColor'] = legacy_outfit[1]
        legacy_accessory = value.get('accessory')
        if legacy_accessory not in {None, 'none', 'cap', 'glasses', 'headphones', 'leaf_pin'}:
            raise serializers.ValidationError('O acessório legado informado não é válido.')
        if legacy_accessory == 'cap' and 'headwear' not in value:
            normalized['headwear'] = 'cap'
        if legacy_accessory == 'glasses' and 'eyewear' not in value:
            normalized['eyewear'] = 'square_glasses'
        if legacy_accessory == 'headphones' and 'earAccessory' not in value:
            normalized['earAccessory'] = 'headphones'
        if legacy_accessory == 'leaf_pin' and 'outfitAccessory' not in value:
            normalized['outfitAccessory'] = 'leaf_pin'
        if value.get('expression') == 'excited':
            normalized['expression'] = 'bored'
        if normalized['hairStyle'] in {'buzz', 'bald'}:
            normalized['hairLength'] = 'short'
        normalized.pop('hair', None)
        normalized.pop('outfit', None)
        normalized.pop('accessory', None)
        if type(normalized['enabled']) is not bool:
            raise serializers.ValidationError('A opção de exibir o avatar precisa ser verdadeira ou falsa.')
        if not isinstance(normalized['coursePin'], str) or not normalized['coursePin'].strip() or len(normalized['coursePin']) > 120:
            raise serializers.ValidationError('O curso do broche precisa ter um nome válido.')
        normalized['coursePin'] = normalized['coursePin'].strip()
        if not isinstance(normalized['pinColor'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', normalized['pinColor']):
            raise serializers.ValidationError('A cor do broche precisa estar no formato hexadecimal.')
        color_fields = {'hairColor', 'facialHairColor', 'outfitColor'}
        has_invalid_option = any(
            normalized[key] not in choices
            and not (
                key in color_fields
                and isinstance(normalized[key], str)
                and re.fullmatch(r'#[0-9a-fA-F]{6}', normalized[key])
            )
            for key, choices in allowed.items()
        )
        if has_invalid_option:
            raise serializers.ValidationError('Uma das opções de personalização não é válida.')
        return normalized

    def get_avatar_url(self, obj):
        return profile_asset_url(obj, 'avatar_image', 'avatar_url', self.context.get('request'))

    def get_cover_photo_url(self, obj):
        return profile_asset_url(obj, 'cover_photo_image', 'cover_photo_url', self.context.get('request'))

    def validate_avatar_image(self, value):
        return normalize_profile_image(value, 'avatar')

    def validate_cover_photo_image(self, value):
        return normalize_profile_image(value, 'cover')

    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        course = user_data.get('course')
        remove_avatar = validated_data.pop('remove_avatar', False)
        remove_cover = validated_data.pop('remove_cover', False)
        old_files = []
        if (remove_avatar or 'avatar_image' in validated_data) and instance.avatar_image:
            old_files.append((instance.avatar_image.storage, instance.avatar_image.name))
        if (remove_cover or 'cover_photo_image' in validated_data) and instance.cover_photo_image:
            old_files.append((instance.cover_photo_image.storage, instance.cover_photo_image.name))
        if remove_avatar:
            validated_data['avatar_image'] = None
            validated_data['avatar_url'] = None
        elif 'avatar_image' in validated_data:
            validated_data['avatar_url'] = None
        if remove_cover:
            validated_data['cover_photo_image'] = None
            validated_data['cover_photo_url'] = None
        elif 'cover_photo_image' in validated_data:
            validated_data['cover_photo_url'] = None
        profile = super().update(instance, validated_data)
        update_fields = []
        if course is not None and profile.user.course_id != course.id:
            profile.user.course = course
            update_fields.append('course')
        if 'phone' in user_data and profile.user.phone != user_data['phone']:
            profile.user.phone = user_data['phone']
            update_fields.append('phone')
        if update_fields:
            update_fields.append('updated_at')
            profile.user.save(update_fields=update_fields)
        for storage, name in old_files:
            transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))
        return profile


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    confirm_password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    address_text = serializers.CharField(required=False, allow_blank=True, write_only=True)
    terms_version = serializers.CharField(required=False, default='2026.1', write_only=True)
    privacy_version = serializers.CharField(required=False, default='2026.1', write_only=True)
    accept_terms = serializers.BooleanField(write_only=True)
    custom_gender = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            'id',
            'full_name',
            'email',
            'phone',
            'ride_visibility_group',
            'custom_gender',
            'university',
            'course',
            'password',
            'confirm_password',
            'address_text',
            'terms_version',
            'privacy_version',
            'accept_terms',
        ]
        read_only_fields = ['id']

    def validate_email(self, value):
        return value.lower().strip()

    def validate(self, attrs):
        if not attrs.get('accept_terms'):
            raise serializers.ValidationError({'accept_terms': 'Você precisa aceitar os Termos de Uso e a Política de Privacidade.'})
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'As senhas não coincidem.'})

        validate_password(attrs['password'])

        university = attrs['university']
        email_domain = attrs['email'].split('@')[-1]
        if email_domain.lower() != university.email_domain.lower():
            raise serializers.ValidationError({'email': 'Use um e-mail institucional compatível com a universidade selecionada.'})

        if attrs['course'].university_id != university.id:
            raise serializers.ValidationError({'course': 'O curso precisa pertencer à universidade selecionada.'})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop('password')
        validated_data.pop('confirm_password')
        address_text = validated_data.pop('address_text', '')
        terms_version = validated_data.pop('terms_version')
        privacy_version = validated_data.pop('privacy_version')
        validated_data.pop('accept_terms')

        user = User.objects.create_user(
            password=password,
            **validated_data,
        )

        UserProfile.objects.create(user=user, address_text=address_text or None)
        UserTermsAcceptance.objects.create(
            user=user,
            terms_version=terms_version,
            privacy_version=privacy_version,
        )
        return user


class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6)

    def validate_email(self, value):
        return value.lower().strip()


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower().strip()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    remember_me = serializers.BooleanField(required=False, default=False)

    def validate_email(self, value):
        return value.lower().strip()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower().strip()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    confirm_password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'As senhas não coincidem.'})
        validate_password(attrs['password'])
        return attrs


class BadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Badge
        fields = ['id', 'code', 'name', 'description', 'badge_kind', 'display_color', 'is_active']


class UserBadgeSerializer(serializers.ModelSerializer):
    badge = BadgeSerializer(read_only=True)

    class Meta:
        model = UserBadge
        fields = ['id', 'badge', 'progress_value', 'target_value', 'achieved_at']


class EngagedPublicProfileSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    cover_photo_url = serializers.SerializerMethodField()
    bio = serializers.SerializerMethodField()
    semester = serializers.SerializerMethodField()
    avatar_config = serializers.SerializerMethodField()
    university_name = serializers.CharField(source='university.name', read_only=True)
    course_name = serializers.CharField(source='course.name', read_only=True)
    achievements = serializers.SerializerMethodField()
    evaluations = serializers.SerializerMethodField()
    impact = serializers.SerializerMethodField()
    engagement_title = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'full_name', 'created_at', 'university_name', 'course_name',
            'avatar_url', 'cover_photo_url', 'bio', 'semester', 'avatar_config', 'achievements',
            'evaluations', 'impact', 'engagement_title',
        ]

    def _profile(self, obj):
        return getattr(obj, 'profile', None)

    def get_avatar_url(self, obj):
        profile = self._profile(obj)
        return profile_asset_url(profile, 'avatar_image', 'avatar_url', self.context.get('request'))

    def get_cover_photo_url(self, obj):
        profile = self._profile(obj)
        return profile_asset_url(profile, 'cover_photo_image', 'cover_photo_url', self.context.get('request'))

    def get_bio(self, obj):
        profile = self._profile(obj)
        return profile.bio if profile else None

    def get_semester(self, obj):
        profile = self._profile(obj)
        return profile.semester if profile else None

    def get_avatar_config(self, obj):
        profile = self._profile(obj)
        return profile.avatar_config if profile else {}

    def _impact(self, obj):
        cache = getattr(obj, '_engaged_impact', None)
        if cache is None:
            cache = build_user_impact(obj)
            obj._engaged_impact = cache
        return cache

    def get_impact(self, obj):
        return self._impact(obj)

    def _achievements(self, obj):
        cache = getattr(obj, '_engaged_achievements', None)
        if cache is None:
            cache = list(sync_user_achievements(obj, self._impact(obj)))
            obj._engaged_achievements = cache
        return cache

    def get_achievements(self, obj):
        return UserBadgeSerializer(
            [item for item in self._achievements(obj) if item.achieved_at is not None],
            many=True,
        ).data

    def get_evaluations(self, obj):
        return [
            {'badge': BadgeSerializer(item['badge']).data, 'count': item['count']}
            for item in build_feedback_summary(obj)
        ]

    def get_engagement_title(self, obj):
        achieved_count = sum(item.achieved_at is not None for item in self._achievements(obj))
        return engagement_title(self._impact(obj), achieved_count)


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id',
            'type',
            'title',
            'body',
            'link_path',
            'ride',
            'ride_request',
            'is_read',
            'created_at',
            'read_at',
        ]
        read_only_fields = ['created_at', 'read_at']


class RideSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = RideSearch
        fields = [
            'id', 'origin_text', 'destination_text', 'requested_date',
            'requested_time', 'origin_lat', 'origin_lng', 'destination_lat',
            'destination_lng', 'gender_restriction', 'vehicle_type',
            'prefer_deviation', 'max_distance_km', 'time_flexibility_minutes',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            'max_distance_km': {'min_value': Decimal('0.5'), 'max_value': Decimal('50')},
            'time_flexibility_minutes': {'min_value': 15, 'max_value': 240},
        }

    def validate(self, attrs):
        coordinate_ranges = {
            'origin_lat': (-90, 90), 'destination_lat': (-90, 90),
            'origin_lng': (-180, 180), 'destination_lng': (-180, 180),
        }
        errors = {}
        for field, (minimum, maximum) in coordinate_ranges.items():
            value = attrs.get(field)
            if value is not None and not minimum <= value <= maximum:
                errors[field] = f'Informe um valor entre {minimum} e {maximum}.'
        if errors:
            raise serializers.ValidationError(errors)
        if not any(attrs.get(field) for field in (
            'origin_text', 'destination_text', 'requested_date', 'requested_time',
        )) and attrs.get('gender_restriction', 'none') == 'none':
            raise serializers.ValidationError('Informe ao menos um filtro de busca.')
        return attrs


class CurrentUserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    badges = UserBadgeSerializer(many=True, read_only=True)
    total_rides_driven = serializers.SerializerMethodField()
    total_rides_as_passenger = serializers.SerializerMethodField()
    unread_notifications_count = serializers.SerializerMethodField()
    university_name = serializers.CharField(source='university.name', read_only=True)
    course_name = serializers.CharField(source='course.name', read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'full_name',
            'email',
            'phone',
            'created_at',
            'ride_visibility_group',
            'account_status',
            'is_email_verified',
            'university',
            'university_name',
            'course',
            'course_name',
            'avatar_url',
            'profile',
            'badges',
            'total_rides_driven',
            'total_rides_as_passenger',
            'unread_notifications_count',
        ]

    def get_total_rides_driven(self, obj):
        return obj.rides_driven.count()

    def get_total_rides_as_passenger(self, obj):
        return obj.rides_as_passenger.count()

    def get_unread_notifications_count(self, obj):
        return obj.notifications.filter(is_read=False).count()

    def get_avatar_url(self, obj):
        return profile_asset_url(
            getattr(obj, 'profile', None), 'avatar_image', 'avatar_url', self.context.get('request'),
        )


class RideSeatSerializer(serializers.ModelSerializer):
    occupant_user = PublicUserSerializer(read_only=True)
    reserved_for_request_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = RideSeat
        fields = [
            'id',
            'seat_number',
            'seat_status',
            'reserved_for_request_id',
            'occupant_user',
            'updated_at',
        ]


class RidePassengerSerializer(serializers.ModelSerializer):
    passenger_user = PublicUserSerializer(read_only=True)
    contact_phone = serializers.SerializerMethodField()

    class Meta:
        model = RidePassenger
        fields = ['id', 'passenger_user', 'seat_number', 'confirmed_at', 'completed_at', 'contact_phone']

    def get_contact_phone(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        if obj.ride.driver_user_id != user.id:
            return None
        return obj.passenger_user.phone


class RideRequestSerializer(serializers.ModelSerializer):
    passenger_user = PublicUserSerializer(read_only=True)
    seat_number = serializers.IntegerField(required=False, allow_null=True, default=None)

    class Meta:
        model = RideRequest
        fields = [
            'id',
            'ride',
            'passenger_user',
            'seat_number',
            'status',
            'requested_at',
            'driver_decision_at',
            'passenger_decision_at',
            'contact_unlocked_at',
        ]
        read_only_fields = [
            'status',
            'requested_at',
            'driver_decision_at',
            'passenger_decision_at',
            'contact_unlocked_at',
        ]

    def validate(self, attrs):
        ride = attrs.get('ride') or getattr(self.instance, 'ride', None)
        seat_number = attrs.get('seat_number')

        if ride is None:
            raise serializers.ValidationError({'ride': 'Ride is required.'})

        if ride.status != Ride.RideStatus.SCHEDULED:
            raise serializers.ValidationError({'ride': 'Only scheduled rides can receive requests.'})

        request = self.context.get('request')
        user = getattr(request, 'user', None)
        required_group = {
            'women_only': User.VisibilityGroup.WOMAN,
            'men_only': User.VisibilityGroup.MAN,
            'non_binary_only': User.VisibilityGroup.NON_BINARY,
            'other_only': User.VisibilityGroup.OTHER,
        }.get(ride.gender_restriction)
        if required_group and (user is None or user.ride_visibility_group != required_group):
            raise serializers.ValidationError({'ride': 'Esta carona é exclusiva e não está disponível para o seu perfil.'})

        if seat_number is not None and not 1 <= seat_number <= ride.seat_capacity:
            raise serializers.ValidationError({'seat_number': 'Seat number must be within the ride seat capacity.'})

        return attrs

    def create(self, validated_data):
        ride = validated_data['ride']
        seat_number = validated_data.get('seat_number')

        allocated_seats = RideRequest.objects.filter(
            ride=ride,
            status__in=RideRequest.active_statuses(),
        ).values_list('seat_number', flat=True)

        if seat_number is None:
            seat = ride.seats.select_for_update().filter(
                seat_status=RideSeat.SeatStatus.AVAILABLE,
            ).exclude(seat_number__in=allocated_seats).order_by('seat_number').first()
            if seat is None:
                raise serializers.ValidationError({'seat_number': 'Não há vagas disponíveis para novas solicitações.'})
            validated_data['seat_number'] = seat.seat_number
        elif seat_number in set(allocated_seats):
            raise serializers.ValidationError({'seat_number': 'Esta vaga já está vinculada a outra solicitação.'})

        return super().create(validated_data)


class RidePublicSerializer(serializers.ModelSerializer):
    available_seat_numbers = serializers.SerializerMethodField()
    available_seat_count = serializers.SerializerMethodField()
    match_score = serializers.SerializerMethodField()
    is_recommended = serializers.SerializerMethodField()
    match_reasons = serializers.SerializerMethodField()
    match_details = serializers.SerializerMethodField()

    class Meta:
        model = Ride
        fields = [
            'id',
            'ride_series',
            'origin_text',
            'destination_text',
            'scheduled_start_at',
            'vehicle_type',
            'seat_capacity',
            'gender_restriction',
            'price_amount',
            'distance_km',
            'duration_minutes',
            'notes',
            'accept_deviation',
            'deviation_distance_km',
            'deviation_time_min',
            'status',
            'created_at',
            'updated_at',
            'available_seat_numbers',
            'available_seat_count',
            'match_score',
            'is_recommended',
            'match_reasons',
            'match_details',
        ]

    def get_available_seat_numbers(self, obj):
        cached = getattr(obj, '_public_available_seat_numbers', None)
        if cached is not None:
            return cached
        allocated_seats = {
            request.seat_number for request in obj.requests.all()
            if request.status in RideRequest.active_statuses()
        }
        cached = sorted(
            seat.seat_number for seat in obj.seats.all()
            if seat.seat_status == RideSeat.SeatStatus.AVAILABLE and seat.seat_number not in allocated_seats
        )
        obj._public_available_seat_numbers = cached
        return cached

    def get_available_seat_count(self, obj):
        return len(self.get_available_seat_numbers(obj))

    def _match_result(self, obj):
        cache = getattr(obj, '_public_match_result', None)
        if cache is not None:
            return cache
        request = self.context.get('request')
        if request is None:
            return {'score': 0, 'is_recommended': False, 'reasons': [], 'details': {}}
        criteria = self.context.get('match_criteria') or criteria_from_query_params(request.query_params)
        matching_context = self.context.get('user_matching_context')
        if matching_context is None:
            matching_context = build_user_matching_context(getattr(request, 'user', None))
        cache = calculate_match(obj, criteria, matching_context)
        obj._public_match_result = cache
        return cache

    def get_match_score(self, obj):
        return self._match_result(obj)['score']

    def get_is_recommended(self, obj):
        return self._match_result(obj)['is_recommended']

    def get_match_reasons(self, obj):
        return self._match_result(obj)['reasons']

    def get_match_details(self, obj):
        return self._match_result(obj)['details']


class RideTrackingSerializer(serializers.ModelSerializer):
    driver_user = serializers.SerializerMethodField()
    driver_contact_phone = serializers.SerializerMethodField()
    vehicle_plate = serializers.SerializerMethodField()
    seats = serializers.SerializerMethodField()
    passengers = serializers.SerializerMethodField()
    requests = serializers.SerializerMethodField()
    available_seat_numbers = serializers.SerializerMethodField()
    my_request = serializers.SerializerMethodField()
    can_view_contact = serializers.SerializerMethodField()

    class Meta:
        model = Ride
        fields = [
            'id',
            'ride_series',
            'driver_user',
            'origin_text',
            'origin_lat',
            'origin_lng',
            'destination_text',
            'destination_lat',
            'destination_lng',
            'scheduled_start_at',
            'vehicle_type',
            'vehicle_model',
            'vehicle_plate',
            'seat_capacity',
            'driver_contact_phone',
            'gender_restriction',
            'price_amount',
            'distance_km',
            'duration_minutes',
            'notes',
            'accept_deviation',
            'deviation_distance_km',
            'deviation_time_min',
            'status',
            'started_at',
            'completed_at',
            'created_at',
            'updated_at',
            'available_seat_numbers',
            'seats',
            'passengers',
            'requests',
            'my_request',
            'can_view_contact',
        ]

    def get_available_seat_numbers(self, obj):
        allocated_seats = obj.requests.filter(status__in=RideRequest.active_statuses()).values_list('seat_number', flat=True)
        return list(obj.seats.filter(seat_status=RideSeat.SeatStatus.AVAILABLE).exclude(seat_number__in=allocated_seats).order_by('seat_number').values_list('seat_number', flat=True))

    def _get_request_user(self):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        return user

    def _get_current_user_request(self, obj):
        request_user = self._get_request_user()
        if request_user is None or obj.driver_user_id == request_user.id:
            return None

        for ride_request in obj.requests.all():
            if ride_request.passenger_user_id == request_user.id:
                return ride_request
        return None

    def _can_view_contact(self, obj):
        request_user = self._get_request_user()
        if request_user is None:
            return False
        if obj.driver_user_id == request_user.id:
            return True

        ride_request = self._get_current_user_request(obj)
        return bool(
            ride_request
            and ride_request.status == RideRequest.RequestStatus.CONFIRMED
            and ride_request.contact_unlocked_at
        )

    def get_driver_user(self, obj):
        current_request = self._get_current_user_request(obj)
        if self._can_view_contact(obj) or (
            current_request and current_request.status in {
                RideRequest.RequestStatus.APPROVED,
                RideRequest.RequestStatus.CONFIRMED,
            }
        ):
            return PublicUserSerializer(obj.driver_user).data

        return {
            'id': str(obj.driver_user_id),
            'full_name': 'Motorista protegido',
            'ride_visibility_group': None,
        }

    def get_driver_contact_phone(self, obj):
        if self._can_view_contact(obj):
            return obj.driver_contact_phone
        return None

    def get_vehicle_plate(self, obj):
        if self._can_view_contact(obj):
            return obj.vehicle_plate
        return None

    def get_requests(self, obj):
        request_user = self._get_request_user()
        if request_user is None:
            return []

        if obj.driver_user_id == request_user.id:
            requests = list(obj.requests.all())
        else:
            current_request = self._get_current_user_request(obj)
            requests = [current_request] if current_request else []

        return RideRequestSerializer(requests, many=True).data

    def get_passengers(self, obj):
        request_user = self._get_request_user()
        if request_user is None:
            return []

        if obj.driver_user_id == request_user.id:
            passengers = list(obj.passengers.all())
        else:
            passengers = [
                passenger
                for passenger in obj.passengers.all()
                if passenger.passenger_user_id == request_user.id
            ]

        return RidePassengerSerializer(passengers, many=True, context=self.context).data

    def get_seats(self, obj):
        request_user = self._get_request_user()
        current_request = self._get_current_user_request(obj)
        current_request_id = current_request.id if current_request else None
        current_user_id = request_user.id if request_user else None

        payload = RideSeatSerializer(obj.seats.all(), many=True).data
        if request_user is None or obj.driver_user_id == current_user_id:
            return payload

        sanitized_payload = []
        for seat in payload:
            should_keep_occupant = seat.get('occupant_user') and seat['occupant_user']['id'] == str(current_user_id)
            if not should_keep_occupant:
                seat['occupant_user'] = None

            if seat.get('reserved_for_request_id') != str(current_request_id):
                seat['reserved_for_request_id'] = None

            sanitized_payload.append(seat)

        return sanitized_payload

    def get_my_request(self, obj):
        current_request = self._get_current_user_request(obj)
        if current_request is None:
            return None
        return RideRequestSerializer(current_request).data

    def get_can_view_contact(self, obj):
        return self._can_view_contact(obj)


class RideWriteSerializer(serializers.ModelSerializer):
    available_seat_numbers = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    # Recurrence and deviation helper fields
    is_recurring = serializers.BooleanField(required=False, write_only=True)
    recurring_days = serializers.ListField(child=serializers.CharField(), required=False, write_only=True)
    recurring_start_date = serializers.DateField(required=False, write_only=True)
    recurring_end_date = serializers.DateField(required=False, write_only=True)
    departure_time = serializers.CharField(required=False, write_only=True)
    accept_deviation = serializers.BooleanField(required=False)
    deviation_distance_km = serializers.DecimalField(required=False, max_digits=6, decimal_places=2)
    deviation_time_min = serializers.IntegerField(required=False)

    class Meta:
        model = Ride
        fields = [
            'ride_series',
            'origin_text',
            'origin_lat',
            'origin_lng',
            'destination_text',
            'destination_lat',
            'destination_lng',
            'scheduled_start_at',
            'vehicle_type',
            'vehicle_model',
            'vehicle_plate',
            'seat_capacity',
            'driver_contact_phone',
            'gender_restriction',
            'price_amount',
            'distance_km',
            'duration_minutes',
            'notes',
            'available_seat_numbers',
            # recurrence and deviation (write-only helpers)
            'is_recurring',
            'recurring_days',
            'recurring_start_date',
            'recurring_end_date',
            'departure_time',
            'accept_deviation',
            'deviation_distance_km',
            'deviation_time_min',
        ]
        extra_kwargs = {
            'scheduled_start_at': {'required': False, 'allow_null': True},
            'driver_contact_phone': {'required': False, 'allow_blank': True},
        }

    def validate_available_seat_numbers(self, value):
        normalized = sorted(set(value))
        if len(normalized) != len(value):
            raise serializers.ValidationError('Seat numbers must be unique.')
        return normalized

    def validate(self, attrs):
        seat_capacity = attrs.get('seat_capacity', getattr(self.instance, 'seat_capacity', None))
        available_seat_numbers = attrs.get('available_seat_numbers')

        if seat_capacity is None:
            raise serializers.ValidationError({'seat_capacity': 'Seat capacity is required.'})

        if available_seat_numbers is not None:
            invalid_seats = [seat for seat in available_seat_numbers if seat > seat_capacity]
            if invalid_seats:
                raise serializers.ValidationError({'available_seat_numbers': 'Available seats must be within the ride seat capacity.'})

        is_recurring = attrs.get('is_recurring', False)
        if self.instance is None and is_recurring:
            required = {
                'recurring_days': attrs.get('recurring_days'),
                'recurring_start_date': attrs.get('recurring_start_date'),
                'recurring_end_date': attrs.get('recurring_end_date'),
                'departure_time': attrs.get('departure_time'),
            }
            missing = [field for field, value in required.items() if not value]
            if missing:
                raise serializers.ValidationError({field: 'Campo obrigatório para uma carona recorrente.' for field in missing})

            day_map = {'seg': 1, 'ter': 2, 'qua': 3, 'qui': 4, 'sex': 5, 'sab': 6, 'dom': 7}
            days = {day_map.get(str(day).lower(), day) for day in attrs['recurring_days']}
            if not days or any(not isinstance(day, int) or day < 1 or day > 7 for day in days):
                raise serializers.ValidationError({'recurring_days': 'Selecione dias da semana válidos.'})
            if attrs['recurring_end_date'] < attrs['recurring_start_date']:
                raise serializers.ValidationError({'recurring_end_date': 'A data final deve ser igual ou posterior à data inicial.'})
            if not any(
                current.isoweekday() in days
                for current in (
                    attrs['recurring_start_date'] + timedelta(days=offset)
                    for offset in range((attrs['recurring_end_date'] - attrs['recurring_start_date']).days + 1)
                )
            ):
                raise serializers.ValidationError({'recurring_days': 'O período informado não contém nenhum dos dias selecionados.'})
        elif self.instance is None and not attrs.get('scheduled_start_at'):
            raise serializers.ValidationError({'scheduled_start_at': 'Informe a data e o horário da carona.'})

        if attrs.get('accept_deviation'):
            if not attrs.get('deviation_distance_km') or attrs['deviation_distance_km'] <= 0:
                raise serializers.ValidationError({'deviation_distance_km': 'Informe uma distância de desvio maior que zero.'})
            if not attrs.get('deviation_time_min') or attrs['deviation_time_min'] <= 0:
                raise serializers.ValidationError({'deviation_time_min': 'Informe um tempo de desvio maior que zero.'})
        else:
            attrs['deviation_distance_km'] = None
            attrs['deviation_time_min'] = None

        return attrs

    @staticmethod
    def _pop_helper_fields(validated_data):
        for field in ('is_recurring', 'recurring_days', 'recurring_start_date', 'recurring_end_date', 'departure_time'):
            validated_data.pop(field, None)

    @transaction.atomic
    def create(self, validated_data):
        available_seat_numbers = validated_data.pop('available_seat_numbers', None)
        self._pop_helper_fields(validated_data)
        ride = Ride.objects.create(**validated_data)

        chosen_seats = set(available_seat_numbers or range(1, ride.seat_capacity + 1))
        RideSeat.objects.bulk_create([
            RideSeat(
                ride=ride,
                seat_number=seat_number,
                seat_status=(
                    RideSeat.SeatStatus.AVAILABLE
                    if seat_number in chosen_seats
                    else RideSeat.SeatStatus.UNAVAILABLE
                ),
            )
            for seat_number in range(1, ride.seat_capacity + 1)
        ])

        return ride

    @transaction.atomic
    def update(self, instance, validated_data):
        available_seat_numbers = validated_data.pop('available_seat_numbers', None)
        self._pop_helper_fields(validated_data)
        ride = super().update(instance, validated_data)

        existing_seats = {seat.seat_number: seat for seat in ride.seats.all()}
        occupied_numbers = {
            seat.seat_number for seat in existing_seats.values()
            if seat.seat_status in {RideSeat.SeatStatus.RESERVED, RideSeat.SeatStatus.OCCUPIED}
        }
        if occupied_numbers and max(occupied_numbers) > ride.seat_capacity:
            raise serializers.ValidationError({'seat_capacity': 'A quantidade de vagas não pode remover uma vaga reservada ou ocupada.'})

        for number in range(1, ride.seat_capacity + 1):
            seat = existing_seats.get(number)
            if seat is None:
                RideSeat.objects.create(ride=ride, seat_number=number)
            elif available_seat_numbers is not None and seat.seat_status not in {RideSeat.SeatStatus.RESERVED, RideSeat.SeatStatus.OCCUPIED}:
                seat.seat_status = RideSeat.SeatStatus.AVAILABLE if number in available_seat_numbers else RideSeat.SeatStatus.UNAVAILABLE
                seat.save(update_fields=['seat_status', 'updated_at'])
        ride.seats.filter(seat_number__gt=ride.seat_capacity).exclude(
            seat_status__in=[RideSeat.SeatStatus.RESERVED, RideSeat.SeatStatus.OCCUPIED]
        ).delete()
        return ride


class RideBadgeFeedbackSerializer(serializers.ModelSerializer):
    badge_detail = BadgeSerializer(source='badge', read_only=True)

    class Meta:
        model = RideBadgeFeedback
        fields = ['id', 'ride', 'giver_user', 'receiver_user', 'badge', 'badge_detail', 'created_at']
        read_only_fields = ['giver_user', 'created_at']

    def validate(self, attrs):
        request = self.context.get('request')
        giver = getattr(request, 'user', None)
        ride = attrs['ride']
        receiver = attrs['receiver_user']
        if ride.status != Ride.RideStatus.COMPLETED:
            raise serializers.ValidationError({'ride': 'As avaliações ficam disponíveis somente após a finalização.'})
        participant_ids = {ride.driver_user_id, *ride.passengers.values_list('passenger_user_id', flat=True)}
        if giver.id not in participant_ids or receiver.id not in participant_ids or giver.id == receiver.id:
            raise serializers.ValidationError('A avaliação deve ser entre participantes desta carona.')
        if attrs['badge'].badge_kind != Badge.BadgeKind.FEEDBACK:
            raise serializers.ValidationError({'badge': 'Selecione um badge de avaliação.'})
        if not attrs['badge'].is_active:
            raise serializers.ValidationError({'badge': 'Este badge não está mais disponível.'})
        if RideBadgeFeedback.objects.filter(
            ride=ride,
            giver_user=giver,
            receiver_user=receiver,
            badge=attrs['badge'],
        ).exists():
            raise serializers.ValidationError({'badge': 'Você já enviou este badge para essa pessoa nesta carona.'})
        return attrs
