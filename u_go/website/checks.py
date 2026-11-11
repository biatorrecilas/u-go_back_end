"""Deployment checks for services required by authentication flows."""

from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, register


UNSAFE_EMAIL_BACKENDS = {
    'django.core.mail.backends.console.EmailBackend',
    'django.core.mail.backends.dummy.EmailBackend',
    'django.core.mail.backends.locmem.EmailBackend',
}


@register(Tags.security, deploy=True)
def check_production_email(app_configs, **kwargs):
    """Block deployments that cannot deliver verification and reset messages."""
    if settings.DEBUG:
        return []

    errors = []
    if settings.EMAIL_BACKEND in UNSAFE_EMAIL_BACKENDS:
        errors.append(Error(
            'O backend de e-mail configurado não entrega mensagens reais.',
            hint='Use django.core.mail.backends.smtp.EmailBackend em produção.',
            id='website.E001',
        ))

    if settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend':
        missing = [
            name for name in ('EMAIL_HOST', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD')
            if not getattr(settings, name, '')
        ]
        if missing:
            errors.append(Error(
                f'Configuração SMTP incompleta: {", ".join(missing)}.',
                hint='Preencha as credenciais no ambiente de produção; nunca no código-fonte.',
                id='website.E002',
            ))

    frontend = urlparse(settings.FRONTEND_URL)
    if frontend.scheme != 'https' or not frontend.netloc:
        errors.append(Error(
            'FRONTEND_URL precisa ser uma URL HTTPS pública em produção.',
            hint='Exemplo: https://app.u-go.com.br',
            id='website.E003',
        ))
    if not getattr(settings, 'REDIS_URL', ''):
        errors.append(Error(
            'REDIS_URL é obrigatório para notificações em tempo real em produção.',
            hint='Configure uma instância Redis protegida e acessível apenas pela aplicação.',
            id='website.E004',
        ))
    return errors
