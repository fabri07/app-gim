"""
Link de invitación para que el dueño aprobado defina su contraseña.

Reusa el token de reset de contraseña de Django: `default_token_generator` +
`uidb64`, apuntando a `password_reset_confirm` (que en este proyecto resuelve a
`StaffPasswordResetConfirmView`, con `post_reset_login=True`). El token se
genera del lado del servidor y se valida con `check_token(user, token)`, así que
NO pasa por `ResetPasswordStaffForm.get_users()` -- funciona aunque el usuario
recién creado nunca haya pedido un reset. Caduca a `PASSWORD_RESET_TIMEOUT`
(3 días por default); por eso el panel ofrece reenviar.
"""

from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def path_invitacion(usuario):
    """Path (relativo) al `password_reset_confirm` de ese usuario. La vista lo
    envuelve con `request.build_absolute_uri` para el email."""
    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = default_token_generator.make_token(usuario)
    return reverse("password_reset_confirm", args=[uid, token])
