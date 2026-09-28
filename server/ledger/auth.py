"""Sign in with the username in ANY case: "Srinivas", "srinivas", "SRINIVAS".

Phones capitalise the first letter of a text box, so a case-sensitive username
fails for no reason the person can see. Usernames made on the Settings page are
stored lower-case; this backend also finds older mixed-case ones (e.g. made with
createsuperuser) so nobody is locked out.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None
        username = username.strip()
        matches = list(User._default_manager.filter(**{f"{User.USERNAME_FIELD}__iexact": username})[:2])
        if len(matches) != 1:
            # none, or two accounts differing only by case: refuse rather than guess
            User().set_password(password)   # same timing as a real check
            return None
        user = matches[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
