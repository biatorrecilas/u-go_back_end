"""Notification creation, deduplication, recommendations and reminders."""

import logging
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from ..models import Notification, Ride, RideRequest, RideSearch, User


logger = logging.getLogger(__name__)


def broadcast_notification_change(user_id):
    """Signal connected clients after the surrounding transaction commits."""
    def send_signal():
        try:
            channel_layer = get_channel_layer()
            if channel_layer is None:
                return
            async_to_sync(channel_layer.group_send)(
                f'notifications.{user_id}',
                {'type': 'notification.changed'},
            )
        except Exception:
            # Persistence is authoritative; clients retain a polling fallback.
            logger.exception('Falha ao emitir sinal de notificação.', extra={'event': 'notification_signal_failed'})

    transaction.on_commit(send_signal)


def create_notification(
    *, recipient_user, notification_type, title, body, link_path,
    ride=None, ride_request=None, event_key=None,
):
    """Create one notification, idempotently when an event key is supplied."""
    values = {
        'recipient_user': recipient_user,
        'type': notification_type,
        'title': title,
        'body': body,
        'link_path': link_path,
        'ride': ride,
        'ride_request': ride_request,
    }
    if event_key:
        notification, created = Notification.objects.get_or_create(
            event_key=event_key,
            defaults=values,
        )
        if created:
            broadcast_notification_change(recipient_user.id)
        return notification
    notification = Notification.objects.create(**values)
    broadcast_notification_change(recipient_user.id)
    return notification


def notify_ride_participants(
    ride, *, title, body, notification_type=Notification.NotificationType.STATUS,
    exclude_user=None, statuses=None, event_name=None,
):
    """Notify the selected request states without leaking data to unrelated users."""
    allowed_statuses = statuses or RideRequest.active_statuses()
    user_ids = set(ride.requests.filter(
        status__in=allowed_statuses,
    ).values_list('passenger_user_id', flat=True))
    if exclude_user is not None:
        user_ids.discard(exclude_user.id)

    notifications = []
    for user in User.objects.filter(id__in=user_ids):
        event_key = f'ride:{ride.id}:{event_name}:user:{user.id}' if event_name else None
        notifications.append(create_notification(
            recipient_user=user,
            notification_type=notification_type,
            title=title,
            body=body,
            link_path='/acompanhar-carona',
            ride=ride,
            event_key=event_key,
        ))
    return notifications


def _contains_route(search_value, ride_value):
    search = (search_value or '').strip().casefold()
    ride = (ride_value or '').strip().casefold()
    return bool(search and ride and (search in ride or ride in search))


def recommendation_score(user, ride):
    """Score stable, explainable route affinity for proactive notifications."""
    score = 0
    profile = getattr(user, 'profile', None)
    if profile and _contains_route(profile.address_text, ride.origin_text):
        score += 35

    recent_searches = RideSearch.objects.filter(user=user).order_by('-created_at')[:20]
    local_start = timezone.localtime(ride.scheduled_start_at)
    for search in recent_searches:
        candidate = 0
        if _contains_route(search.origin_text, ride.origin_text):
            candidate += 30
        if _contains_route(search.destination_text, ride.destination_text):
            candidate += 30
        if search.requested_date and search.requested_date == local_start.date():
            candidate += 15
        if search.requested_time:
            ride_minutes = local_start.hour * 60 + local_start.minute
            search_minutes = search.requested_time.hour * 60 + search.requested_time.minute
            if abs(ride_minutes - search_minutes) <= 90:
                candidate += 10
        if search.gender_restriction != 'none' and search.gender_restriction == ride.gender_restriction:
            candidate += 35
        score = max(score, candidate)

    prior_routes = Ride.objects.filter(
        requests__passenger_user=user,
        requests__status=RideRequest.RequestStatus.CONFIRMED,
    ).values_list('origin_text', 'destination_text').order_by('-scheduled_start_at')[:20]
    for prior_origin, prior_destination in prior_routes:
        candidate = 0
        if _contains_route(prior_origin, ride.origin_text):
            candidate += 30
        if _contains_route(prior_destination, ride.destination_text):
            candidate += 30
        score = max(score, candidate)
    return min(score, 100)


def notify_matching_users(ride, *, occurrence_count=1):
    """Notify only eligible users with a meaningful route affinity."""
    restriction_to_group = {
        'none': None,
        'women_only': User.VisibilityGroup.WOMAN,
        'men_only': User.VisibilityGroup.MAN,
        'non_binary_only': User.VisibilityGroup.NON_BINARY,
        'other_only': User.VisibilityGroup.OTHER,
    }
    users = User.objects.select_related('profile').filter(
        university=ride.driver_user.university,
        account_status=User.AccountStatus.ACTIVE,
        is_email_verified=True,
    ).exclude(id=ride.driver_user_id)
    required_group = restriction_to_group.get(ride.gender_restriction)
    if required_group:
        users = users.filter(ride_visibility_group=required_group)

    local_start = timezone.localtime(ride.scheduled_start_at)
    recurring_text = f' Há {occurrence_count} datas disponíveis nesta recorrência.' if occurrence_count > 1 else ''
    created = []
    series_or_ride = f'series:{ride.ride_series_id}' if ride.ride_series_id else f'ride:{ride.id}'
    for user in users.iterator():
        score = recommendation_score(user, ride)
        if score < 35:
            continue
        created.append(create_notification(
            recipient_user=user,
            notification_type=Notification.NotificationType.MATCH,
            title='Carona nova compatível com você',
            body=(
                f'{ride.origin_text} → {ride.destination_text}, em {local_start:%d/%m às %H:%M}. '
                f'Compatibilidade estimada: {score}%.{recurring_text}'
            ),
            link_path='/caronas',
            ride=ride,
            event_key=f'{series_or_ride}:match:user:{user.id}',
        ))
    return created


@transaction.atomic
def send_upcoming_ride_reminders(*, current_time=None):
    """Create one reminder per confirmed participant and driver within 24 hours."""
    now = current_time or timezone.now()
    rides = Ride.objects.select_related('driver_user').filter(
        status=Ride.RideStatus.SCHEDULED,
        scheduled_start_at__gt=now,
        scheduled_start_at__lte=now + timedelta(hours=24),
    )
    created = 0
    for ride in rides.iterator():
        local_start = timezone.localtime(ride.scheduled_start_at)
        recipients = {ride.driver_user_id: ride.driver_user}
        for passenger in User.objects.filter(
            ride_requests__ride=ride,
            ride_requests__status=RideRequest.RequestStatus.CONFIRMED,
        ).distinct():
            recipients[passenger.id] = passenger
        for user in recipients.values():
            event_key = f'ride:{ride.id}:reminder:24h:user:{user.id}'
            _, was_created = Notification.objects.get_or_create(
                event_key=event_key,
                defaults={
                    'recipient_user': user,
                    'type': Notification.NotificationType.REMINDER,
                    'title': 'Sua carona está chegando',
                    'body': f'{ride.origin_text} → {ride.destination_text}, em {local_start:%d/%m às %H:%M}.',
                    'link_path': '/acompanhar-carona',
                    'ride': ride,
                },
            )
            if was_created:
                broadcast_notification_change(user.id)
            created += int(was_created)
    return created
