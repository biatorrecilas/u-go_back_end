"""Privacy-safe public platform metrics.

Only aggregates are returned. Distances are counted once for each passenger on a
completed ride because that represents one individual trip avoided. The CO2
estimate is intentionally configurable: it is an estimate, not telemetry.
"""

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from ..models import Ride, RideBadgeFeedback, RidePassenger, RideSeat, University, User


TREE_EQUIVALENCE_SOURCE_URL = (
	'https://www.epa.gov/energy/'
	'greenhouse-gas-equivalencies-calculator-calculations-and-references'
)


def build_platform_stats(*, current_time=None):
	"""Return the canonical public metrics payload without personal data."""
	now = current_time or timezone.now()
	shared_distance = RidePassenger.objects.filter(
		ride__status=Ride.RideStatus.COMPLETED,
		ride__distance_km__isnull=False,
	).aggregate(total=Sum('ride__distance_km'))['total'] or 0
	shared_distance_km = round(float(shared_distance), 2)
	co2_saved_kg = round(shared_distance_km * settings.CO2_PER_AVOIDED_KM_KG, 2)
	tree_factor_kg = settings.TREE_CO2_SEQUESTRATION_KG_PER_YEAR
	urban_tree_equivalent_count = round(co2_saved_kg / tree_factor_kg, 2)

	return {
		'active_users': User.objects.filter(
			account_status=User.AccountStatus.ACTIVE,
			is_email_verified=True,
			is_staff=False,
		).count(),
		'completed_rides': Ride.objects.filter(status=Ride.RideStatus.COMPLETED).count(),
		'active_rides': Ride.objects.filter(
			status=Ride.RideStatus.SCHEDULED,
			scheduled_start_at__gte=now,
			seats__seat_status=RideSeat.SeatStatus.AVAILABLE,
		).distinct().count(),
		'confirmed_matches': RidePassenger.objects.count(),
		'badge_feedback_count': RideBadgeFeedback.objects.count(),
		'universities_count': University.objects.count(),
		'shared_distance_km': shared_distance_km,
		'co2_saved_kg': co2_saved_kg,
		'co2_methodology': (
			f'{settings.CO2_PER_AVOIDED_KM_KG:.3f} kg de CO₂ por km '
			'de deslocamento individual evitado'
		),
		'urban_tree_equivalent_count': urban_tree_equivalent_count,
		'urban_tree_equivalent_methodology': (
			f'CO₂ potencialmente evitado dividido por {tree_factor_kg:g} kg de CO₂ '
			'capturados, em média, por uma árvore urbana em um ano; fator anual '
			'baseado em árvores mantidas por 10 anos'
		),
		'urban_tree_equivalent_source_url': TREE_EQUIVALENCE_SOURCE_URL,
		'updated_at': now,
	}
