from django import template

from notificaciones.icons import icono_pwa_url as _icono_pwa_url

register = template.Library()


@register.simple_tag
def icono_pwa_url(gimnasio, size=192):
    """URL versionada del ícono PWA del gimnasio (`apple-touch-icon`)."""
    return _icono_pwa_url(gimnasio, size)
