"""Privacy-safe engaged profile metrics and automatic achievements."""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import Badge, Notification, Ride, RideBadgeFeedback, RidePassenger, UserBadge


ACHIEVEMENT_RULES = {
    'primeira_carona': ('total_rides', Decimal('1')),
    'cinco_caronas': ('total_rides', Decimal('5')),
    'vinte_caronas': ('total_rides', Decimal('20')),
    'cinquenta_caronas': ('total_rides', Decimal('50')),
    'cem_caronas': ('total_rides', Decimal('100')),
    'eco_10kg': ('co2_saved_kg', Decimal('10')),
    'eco_50kg': ('co2_saved_kg', Decimal('50')),
    'eco_100kg': ('co2_saved_kg', Decimal('100')),
    'eco_250kg': ('co2_saved_kg', Decimal('250')),
    'reconhecido_5': ('feedback_count', Decimal('5')),
    'reconhecido_10': ('feedback_count', Decimal('10')),
    'reconhecido_25': ('feedback_count', Decimal('25')),
}


def build_user_impact(user):
    passenger_rides = list(RidePassenger.objects.filter(
        passenger_user=user,
        ride__status=Ride.RideStatus.COMPLETED,
    ).select_related('ride'))
    driven_rides = list(Ride.objects.filter(
        driver_user=user,
        status=Ride.RideStatus.COMPLETED,
    ).prefetch_related('passengers'))

    passenger_distance = sum(Decimal(item.ride.distance_km or 0) for item in passenger_rides)
    driver_shared_distance = sum(
        Decimal(ride.distance_km or 0) * ride.passengers.count()
        for ride in driven_rides
    )
    shared_distance = passenger_distance + driver_shared_distance
    feedback_count = RideBadgeFeedback.objects.filter(receiver_user=user).count()
    return {
        'total_rides': len(passenger_rides) + len(driven_rides),
        'rides_as_driver': len(driven_rides),
        'rides_as_passenger': len(passenger_rides),
        'shared_distance_km': round(float(shared_distance), 1),
        'co2_saved_kg': round(float(shared_distance) * settings.CO2_PER_AVOIDED_KM_KG, 1),
        'feedback_count': feedback_count,
        'co2_methodology': (
            f'{settings.CO2_PER_AVOIDED_KM_KG:.3f} kg de CO₂ por km '
            'de deslocamento individual evitado'
        ),
    }


@transaction.atomic
def sync_user_achievements(user, impact=None):
    impact = impact or build_user_impact(user)
    now = timezone.now()
    for badge in Badge.objects.filter(badge_kind=Badge.BadgeKind.ACHIEVEMENT, is_active=True):
        rule = ACHIEVEMENT_RULES.get(badge.code)
        if not rule:
            continue
        metric, target = rule
        progress = Decimal(str(impact[metric]))
        user_badge, _ = UserBadge.objects.get_or_create(
            user=user,
            badge=badge,
            defaults={'progress_value': progress, 'target_value': target},
        )
        update_fields = []
        if user_badge.progress_value != progress:
            user_badge.progress_value = progress
            update_fields.append('progress_value')
        if user_badge.target_value != target:
            user_badge.target_value = target
            update_fields.append('target_value')
        newly_achieved = progress >= target and user_badge.achieved_at is None
        if newly_achieved:
            user_badge.achieved_at = now
            update_fields.append('achieved_at')
        if update_fields:
            user_badge.save(update_fields=update_fields)
        if newly_achieved:
            from .notifications import create_notification
            create_notification(
                recipient_user=user,
                notification_type=Notification.NotificationType.PROFILE,
                title='Nova conquista desbloqueada',
                body=f'Você conquistou o badge “{badge.name}”. Veja seu progresso no perfil.',
                link_path='/perfil',
                event_key=f'achievement:{user_badge.id}:unlocked',
            )
    return UserBadge.objects.filter(user=user).select_related('badge').order_by('badge__name')


def build_feedback_summary(user):
    summary = {}
    feedback = RideBadgeFeedback.objects.filter(receiver_user=user).select_related('badge')
    for item in feedback:
        current = summary.setdefault(str(item.badge_id), {
            'badge': item.badge,
            'count': 0,
        })
        current['count'] += 1
    return sorted(summary.values(), key=lambda item: (-item['count'], item['badge'].name))


def engagement_title(impact, achieved_count):
    points = impact['total_rides'] * 10 + impact['feedback_count'] * 5 + achieved_count * 20
    if points >= 300:
        return 'Embaixador Verde'
    if points >= 80:
        return 'Engajado'
    if points >= 20:
        return 'Em movimento'
    return 'Começando agora'
