"""Reliable delivery helpers for account verification and password recovery."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from rest_framework.exceptions import APIException


logger = logging.getLogger(__name__)


class EmailDeliveryUnavailable(APIException):
    status_code = 503
    default_detail = 'O serviço de e-mail está temporariamente indisponível. Tente novamente mais tarde.'
    default_code = 'email_delivery_unavailable'


def _can_deliver_email():
    """Return whether this environment has a usable delivery backend."""
    if settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
        return True
    return bool(settings.EMAIL_HOST)


def require_email_delivery():
    """Fail closed in production while preserving debug-only local flows."""
    if not _can_deliver_email() and not settings.DEBUG:
        raise EmailDeliveryUnavailable()


def send_verification_email(user, code):
    """Deliver a six-digit verification code, or roll back signup in production."""
    if not _can_deliver_email():
        require_email_delivery()
        return False
    try:
        send_mail(
            subject='Seu código de verificação U-Go',
            message=(
                f'Seu código de verificação é: {code}\n\n'
                'Ele expira em 15 minutos. Se você não solicitou este cadastro, ignore esta mensagem.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception as exc:
        logger.exception('Falha ao enviar e-mail de verificação.', extra={'event': 'verification_email_failed'})
        if not settings.DEBUG:
            raise EmailDeliveryUnavailable() from exc
        return False


def send_password_reset_email(user, reset_url):
    """Deliver a single-use reset link without exposing provider errors to clients."""
    if not _can_deliver_email():
        return False
    try:
        send_mail(
            subject='Redefinição de senha U-Go',
            message=(
                f'Acesse este link para criar uma nova senha: {reset_url}\n\n'
                f'O link expira em {settings.PASSWORD_RESET_TIMEOUT // 60} minutos e só pode ser usado uma vez. '
                'Se você não solicitou a alteração, ignore esta mensagem.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception:
        # The public response stays generic to avoid account enumeration.
        logger.exception('Falha ao enviar recuperação de senha.', extra={'event': 'password_reset_email_failed'})
        return False
