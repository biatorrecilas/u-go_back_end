"""Explainable ride matching used by search results and recommendations."""

from dataclasses import dataclass
from datetime import date, time
from math import asin, cos, radians, sin, sqrt
import re
import unicodedata

from django.utils import timezone

from ..models import Ride, RideRequest, RideSearch


RECOMMENDED_SCORE = 65
HISTORY_RECOMMENDED_SCORE = 45


def _normalized(value):
    value = unicodedata.normalize('NFKD', (value or '').casefold())
    value = ''.join(character for character in value if not unicodedata.combining(character))
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def text_similarity(left, right):
    """Return a conservative 0..1 similarity suitable for short addresses."""
    left_value = _normalized(left)
    right_value = _normalized(right)
    if not left_value or not right_value:
        return 0.0
    if left_value == right_value:
        return 1.0
    if left_value in right_value or right_value in left_value:
        return 0.9
    left_tokens = set(left_value.split())
    right_tokens = set(right_value.split())
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def haversine_km(lat1, lng1, lat2, lng2):
    """Calculate the surface distance between two latitude/longitude pairs."""
    lat1, lng1, lat2, lng2 = map(float, (lat1, lng1, lat2, lng2))
    delta_lat = radians(lat2 - lat1)
    delta_lng = radians(lng2 - lng1)
    value = sin(delta_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lng / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(value))


def _float(value, minimum=None, maximum=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def _integer(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _time(value):
    try:
        return time.fromisoformat(value[:5]) if value else None
    except (TypeError, ValueError):
        return None


def _boolean(value):
    if value in (True, 'true', '1', 'yes'):
        return True
    if value in (False, 'false', '0', 'no'):
        return False
    return None


@dataclass(frozen=True)
class MatchCriteria:
    origin: str = ''
    destination: str = ''
    requested_date: date | None = None
    requested_time: time | None = None
    gender_restriction: str = 'none'
    vehicle_type: str = ''
    prefer_deviation: bool | None = None
    origin_lat: float | None = None
    origin_lng: float | None = None
    destination_lat: float | None = None
    destination_lng: float | None = None
    max_distance_km: float = 8.0
    time_flexibility_minutes: int = 90

    @property
    def has_explicit_filters(self):
        return bool(
            self.origin or self.destination or self.requested_date or self.requested_time
            or self.gender_restriction != 'none' or self.vehicle_type or self.prefer_deviation is not None
        )


def criteria_from_query_params(params):
    return MatchCriteria(
        origin=(params.get('origin') or '').strip(),
        destination=(params.get('destination') or '').strip(),
        requested_date=_date(params.get('date')),
        requested_time=_time(params.get('time')),
        gender_restriction=(params.get('gender_restriction') or 'none').strip(),
        vehicle_type=(params.get('vehicle_type') or '').strip(),
        prefer_deviation=_boolean(params.get('prefer_deviation')),
        origin_lat=_float(params.get('origin_lat'), -90, 90),
        origin_lng=_float(params.get('origin_lng'), -180, 180),
        destination_lat=_float(params.get('destination_lat'), -90, 90),
        destination_lng=_float(params.get('destination_lng'), -180, 180),
        max_distance_km=_float(params.get('max_distance_km'), 0.5, 50) or 8.0,
        time_flexibility_minutes=_integer(params.get('time_flexibility_minutes'), 90, 15, 240),
    )


def build_user_matching_context(user):
    if user is None or not user.is_authenticated:
        return {'recent_searches': [], 'prior_routes': [], 'profile_address': ''}
    return {
        'recent_searches': list(RideSearch.objects.filter(user=user).order_by('-created_at')[:20]),
        'prior_routes': list(Ride.objects.filter(
            requests__passenger_user=user,
            requests__status=RideRequest.RequestStatus.CONFIRMED,
        ).values_list(
            'origin_text', 'destination_text', 'scheduled_start_at',
            'vehicle_type', 'accept_deviation',
        ).order_by('-scheduled_start_at')[:20]),
        'profile_address': getattr(getattr(user, 'profile', None), 'address_text', '') or '',
    }


def _endpoint_score(search_text, search_lat, search_lng, ride_text, ride_lat, ride_lng, max_distance):
    if search_lat is not None and search_lng is not None and ride_lat is not None and ride_lng is not None:
        distance = haversine_km(search_lat, search_lng, ride_lat, ride_lng)
        similarity = max(0.0, 1.0 - distance / max(max_distance * 1.5, 0.1))
        return similarity, distance, distance <= max_distance
    similarity = text_similarity(search_text, ride_text)
    return similarity, None, similarity >= 0.55


def _history_affinity(ride, context):
    best = 0.0
    ride_start = timezone.localtime(ride.scheduled_start_at)
    for search in context['recent_searches']:
        signals = []
        if search.origin_text:
            signals.append(text_similarity(search.origin_text, ride.origin_text))
        if search.destination_text:
            signals.append(text_similarity(search.destination_text, ride.destination_text))
        if search.requested_time:
            ride_minutes = ride_start.hour * 60 + ride_start.minute
            search_minutes = search.requested_time.hour * 60 + search.requested_time.minute
            difference = min(abs(ride_minutes - search_minutes), 1440 - abs(ride_minutes - search_minutes))
            signals.append(max(0.0, 1.0 - difference / 180))
        if search.requested_date:
            signals.append(1.0 if search.requested_date.weekday() == ride_start.date().weekday() else 0.0)
        if search.vehicle_type:
            signals.append(1.0 if search.vehicle_type == ride.vehicle_type else 0.0)
        if search.prefer_deviation is not None:
            signals.append(1.0 if search.prefer_deviation == ride.accept_deviation else 0.0)
        if signals:
            best = max(best, sum(signals) / len(signals))
    for prior_origin, prior_destination, prior_start, vehicle_type, accepted_deviation in context['prior_routes']:
        prior_local_start = timezone.localtime(prior_start)
        signals = [
            text_similarity(prior_origin, ride.origin_text),
            text_similarity(prior_destination, ride.destination_text),
            1.0 if prior_local_start.weekday() == ride_start.weekday() else 0.0,
            1.0 if vehicle_type == ride.vehicle_type else 0.0,
            1.0 if accepted_deviation == ride.accept_deviation else 0.0,
        ]
        best = max(best, sum(signals) / len(signals))
    profile_address = context['profile_address']
    if profile_address:
        best = max(best, text_similarity(profile_address, ride.origin_text) * 0.8)
    return min(best, 1.0)


def calculate_match(ride, criteria, context=None):
    """Return score, compatibility, reasons and measurable differences."""
    context = context or {'recent_searches': [], 'prior_routes': [], 'profile_address': ''}
    weighted_signals = []
    reasons = []
    compatible = True
    details = {
        'origin_distance_km': None,
        'destination_distance_km': None,
        'departure_time_difference_minutes': None,
    }

    if criteria.origin:
        similarity, distance, endpoint_matches = _endpoint_score(
            criteria.origin, criteria.origin_lat, criteria.origin_lng,
            ride.origin_text, ride.origin_lat, ride.origin_lng, criteria.max_distance_km,
        )
        weighted_signals.append((25, similarity))
        compatible = compatible and endpoint_matches
        details['origin_distance_km'] = round(distance, 1) if distance is not None else None
        if endpoint_matches:
            reasons.append(f'Origem a {distance:.1f} km' if distance is not None else 'Origem compatível')

    if criteria.destination:
        similarity, distance, endpoint_matches = _endpoint_score(
            criteria.destination, criteria.destination_lat, criteria.destination_lng,
            ride.destination_text, ride.destination_lat, ride.destination_lng, criteria.max_distance_km,
        )
        weighted_signals.append((25, similarity))
        compatible = compatible and endpoint_matches
        details['destination_distance_km'] = round(distance, 1) if distance is not None else None
        if endpoint_matches:
            reasons.append(f'Destino a {distance:.1f} km' if distance is not None else 'Destino compatível')

    local_start = timezone.localtime(ride.scheduled_start_at)
    if criteria.requested_date:
        date_matches = local_start.date() == criteria.requested_date
        weighted_signals.append((20, 1.0 if date_matches else 0.0))
        compatible = compatible and date_matches
        if date_matches:
            reasons.append('Data solicitada')

    if criteria.requested_time:
        ride_minutes = local_start.hour * 60 + local_start.minute
        requested_minutes = criteria.requested_time.hour * 60 + criteria.requested_time.minute
        difference = abs(ride_minutes - requested_minutes)
        difference = min(difference, 1440 - difference)
        details['departure_time_difference_minutes'] = difference
        time_matches = difference <= criteria.time_flexibility_minutes
        time_similarity = max(0.0, 1.0 - difference / max(criteria.time_flexibility_minutes, 1)) if time_matches else 0.0
        weighted_signals.append((15, time_similarity))
        compatible = compatible and time_matches
        if time_matches:
            reasons.append('Horário exato' if difference == 0 else f'{difference} min do horário desejado')

    if criteria.gender_restriction != 'none':
        gender_matches = ride.gender_restriction == criteria.gender_restriction
        weighted_signals.append((10, 1.0 if gender_matches else 0.0))
        compatible = compatible and gender_matches
        if gender_matches:
            reasons.append('Exclusividade solicitada')

    if criteria.vehicle_type:
        vehicle_matches = ride.vehicle_type == criteria.vehicle_type
        weighted_signals.append((5, 1.0 if vehicle_matches else 0.0))
        if vehicle_matches:
            reasons.append('Veículo preferido')

    if criteria.prefer_deviation is not None:
        deviation_matches = ride.accept_deviation == criteria.prefer_deviation
        weighted_signals.append((5, 1.0 if deviation_matches else 0.0))
        if deviation_matches and criteria.prefer_deviation:
            reasons.append('Motorista aceita desvio')

    history = _history_affinity(ride, context)
    if history >= 0.55:
        reasons.append('Combina com seu histórico')

    if weighted_signals:
        weight = sum(item_weight for item_weight, _ in weighted_signals)
        explicit_score = sum(item_weight * value for item_weight, value in weighted_signals) / weight * 90
        score = round(min(100, explicit_score + history * 10))
        is_recommended = compatible and score >= RECOMMENDED_SCORE
    else:
        score = round(history * 100)
        is_recommended = score >= HISTORY_RECOMMENDED_SCORE

    return {
        'score': score,
        'is_recommended': is_recommended,
        'reasons': reasons[:4],
        'details': details,
    }
