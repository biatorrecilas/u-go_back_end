from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BadgeViewSet,
    CsrfTokenView,
    CourseViewSet,
    CurrentUserProfileView,
    CurrentUserView,
    EngagedPublicProfileView,
    LoginView,
    NotificationViewSet,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    PublicPlatformStatsView,
    ResendVerificationView,
    RideBadgeFeedbackViewSet,
    RideRequestViewSet,
    RideSearchCreateView,
    RideViewSet,
    SignupView,
    UniversityViewSet,
    VerifyEmailView,
)

router = DefaultRouter()
router.register('universities', UniversityViewSet, basename='university')
router.register('courses', CourseViewSet, basename='course')
router.register('badges', BadgeViewSet, basename='badge')
router.register('rides', RideViewSet, basename='ride')
router.register('ride-requests', RideRequestViewSet, basename='ride-request')
router.register('notifications', NotificationViewSet, basename='notification')
router.register('ride-feedback', RideBadgeFeedbackViewSet, basename='ride-feedback')

urlpatterns = [
    path('auth/csrf/', CsrfTokenView.as_view(), name='auth-csrf'),
    path('auth/signup/', SignupView.as_view(), name='auth-signup'),
    path('auth/login/', LoginView.as_view(), name='auth-login'),
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('auth/password-reset/', PasswordResetRequestView.as_view(), name='auth-password-reset'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='auth-password-reset-confirm'),
    path('auth/verify-email/', VerifyEmailView.as_view(), name='auth-verify-email'),
    path('auth/resend-verification/', ResendVerificationView.as_view(), name='auth-resend-verification'),
    path('me/', CurrentUserView.as_view(), name='current-user'),
    path('me/profile/', CurrentUserProfileView.as_view(), name='current-user-profile'),
    path('users/<uuid:user_id>/profile/', EngagedPublicProfileView.as_view(), name='engaged-public-profile'),
    path('public/stats/', PublicPlatformStatsView.as_view(), name='public-platform-stats'),
    path('ride-searches/', RideSearchCreateView.as_view(), name='ride-search-create'),
]

urlpatterns += router.urls
