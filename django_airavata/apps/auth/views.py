import logging
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import mail_admins, send_mail
from django.forms import ValidationError
from django.shortcuts import redirect, render, resolve_url
from django.urls import reverse
from requests_oauthlib import OAuth2Session

from . import forms, iam_admin_client, models

logger = logging.getLogger(__name__)


def start_login(request):
    return render(request, 'django_airavata_auth/login.html', {
        'next': request.GET.get('next', None),
        'options': settings.AUTHENTICATION_OPTIONS,
    })


def redirect_login(request, idp_alias):
    _validate_idp_alias(idp_alias)
    client_id = settings.KEYCLOAK_CLIENT_ID
    base_authorize_url = settings.KEYCLOAK_AUTHORIZE_URL
    redirect_uri = request.build_absolute_uri(
        reverse('django_airavata_auth:callback'))
    if 'next' in request.GET:
        redirect_uri += "?next=" + quote(request.GET['next'])
    oauth2_session = OAuth2Session(
        client_id, scope='openid', redirect_uri=redirect_uri)
    authorization_url, state = oauth2_session.authorization_url(
        base_authorize_url)
    authorization_url += '&kc_idp_hint=' + quote(idp_alias)
    # Store state in session for later validation (see backends.py)
    request.session['OAUTH2_STATE'] = state
    request.session['OAUTH2_REDIRECT_URI'] = redirect_uri
    return redirect(authorization_url)


def _validate_idp_alias(idp_alias):
    external_auth_options = settings.AUTHENTICATION_OPTIONS['external']
    valid_idp_aliases = [ext['idp_alias'] for ext in external_auth_options]
    if idp_alias not in valid_idp_aliases:
        raise Exception("idp_alias is not valid")


def handle_login(request):
    username = request.POST['username']
    password = request.POST['password']
    user = authenticate(username=username, password=password, request=request)
    logger.debug("authenticated user: {}".format(user))
    try:
        if user is not None:
            login(request, user)
            next_url = request.POST.get('next', settings.LOGIN_REDIRECT_URL)
            return redirect(next_url)
        else:
            # TODO: add error message that login failed
            return render(request, 'django_airavata_auth/login.html', {
                'username': username
            })
    except Exception as err:
        logger.exception("An error occurred while logging in with "
                         "username and password")
        return redirect(reverse('django_airavata_auth:error'))


def start_logout(request):
    logout(request)
    redirect_url = request.build_absolute_uri(
        resolve_url(settings.LOGOUT_REDIRECT_URL))
    return redirect(settings.KEYCLOAK_LOGOUT_URL +
                    "?redirect_uri=" + quote(redirect_url))


def callback(request):
    try:
        user = authenticate(request=request)
        login(request, user)
        next_url = request.GET.get('next', settings.LOGIN_REDIRECT_URL)
        return redirect(next_url)
    except Exception as err:
        logger.exception("An error occurred while processing OAuth2 "
                         "callback: {}".format(request.build_absolute_uri()))
        return redirect(reverse('django_airavata_auth:error'))


def auth_error(request):
    return render(request, 'django_airavata_auth/auth_error.html', {
        'login_url': settings.LOGIN_URL
    })


def create_account(request):
    if request.method == 'POST':
        form = forms.CreateAccountForm(request.POST)
        if form.is_valid():
            try:
                username = form.cleaned_data['username']
                email = form.cleaned_data['email']
                first_name = form.cleaned_data['first_name']
                last_name = form.cleaned_data['last_name']
                password = form.cleaned_data['password']
                success = iam_admin_client.register_user(
                    username, email, first_name, last_name, password)
                if not success:
                    form.add_error(None, ValidationError(
                        "Failed to register user with IAM service"))
                else:
                    _create_and_send_email_verification_link(
                        request, username, email, first_name, last_name)
                    # TODO: success message
                    return redirect(
                        reverse('django_airavata_auth:create_account'))
            except Exception as e:
                logger.exception(
                    "Failed to create account for user", exc_info=e)
                form.add_error(None, ValidationError(e.message))
    else:
        form = forms.CreateAccountForm()
    return render(request, 'django_airavata_auth/create_account.html', {
        'options': settings.AUTHENTICATION_OPTIONS,
        'form': form
    })


def verify_email(request, code):

    try:
        email_verification = models.EmailVerification.objects.get(
            verification_code=code)
        email_verification.verified = True
        email_verification.save()
        # TODO: test what happens if calling iam_admin_client fails
        # Check if user is enabled, if so redirect to login page
        username = email_verification.username
        logger.debug("Email address verified for {}".format(username))
        if iam_admin_client.is_user_enabled(username):
            logger.debug("User {} is already enabled".format(username))
            # TODO: add success message
            return redirect(reverse('django_airavata_auth:login'))
        else:
            logger.debug("Enabling user {}".format(username))
            # enable user and inform admins
            iam_admin_client.enable_user(username)
            # TODO: use proper template for new user email
            mail_admins(
                'New User Created',
                'New user: {}'.format(username)
            )
            # TODO: add success message
            return redirect(reverse('django_airavata_auth:login'))
    except ObjectDoesNotExist as e:
        # TODO: if doesn't exist, give user a form where they can enter their
        # username to resend verification code
        return redirect(reverse('django_airavata_auth:resend_email_link'))


def resend_email_link(request):

    # TODO: if the user is already verified their email, then redirect to login page with message
    if request.method == 'POST':
        form = forms.ResendEmailVerificationLinkForm(request.POST)
        if form.is_valid():
            try:
                username = form.cleaned_data['username']
                if iam_admin_client.is_user_exist(username):
                    user_profile = iam_admin_client.get_user(username)
                    _create_and_send_email_verification_link(
                        request,
                        username,
                        user_profile.emails[0],
                        user_profile.firstName,
                        user_profile.lastName)
                # TODO: success message
                return redirect(
                    reverse('django_airavata_auth:resend_email_link'))
            except Exception as e:
                logger.exception(
                    "Failed to resend email verification link", exc_info=e)
                form.add_error(None, ValidationError(str(e)))
    else:
        form = forms.ResendEmailVerificationLinkForm()
    return render(request, 'django_airavata_auth/verify_email.html', {
        'form': form
    })


def _create_and_send_email_verification_link(
        request, username, email, first_name, last_name):

    email_verification = models.EmailVerification(
        username=username)
    email_verification.save()

    verification_uri = request.build_absolute_uri(
        reverse(
            'django_airavata_auth:verify_email', kwargs={
                'code': email_verification.verification_code}))
    logger.debug(
        "verification_uri={}".format(verification_uri))

    # TODO: need a better template, customization
    # TODO: add email settings documentation to
    # settings_local.py
    send_mail(
        'Please verify your email address',
        "Verification link: {}".format(verification_uri),
        "Django Portal <pga.airavata@gmail.com>",
        ["{} {} <{}>".format(first_name, last_name, email)]
    )
