from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import permission_required

from util import add_member_permission
from util import remove_member_permission
from util import check_if_user_has_permission
from forms import UpdateKeyForm
from managers import EveManager
from authentication.managers import AuthServicesInfoManager
from services.managers.eve_api_manager import EveApiManager
from util.common_task import add_user_to_group
from util.common_task import remove_user_from_group
from util.common_task import deactivate_services
from util.common_task import generate_corp_group_name
from eveonline.models import EveCorporationInfo
from eveonline.models import EveCharacter
from eveonline.models import EveApiKeyPair
from authentication.models import AuthServicesInfo
from celerytask.tasks import determine_membership_by_user
from celerytask.tasks import set_state
from celerytask.tasks import refresh_api

import logging

logger = logging.getLogger(__name__)


def disable_member(user, char_id):
    logger.debug("Disabling user %s with character id %s" % (user, char_id))
    remove_member_permission(user, 'member')
    remove_user_from_group(user, settings.DEFAULT_AUTH_GROUP)
    remove_user_from_group(user,
                           generate_corp_group_name(
                               EveManager.get_character_by_id(char_id).corporation_name))
    deactivate_services(user)
    logger.info("Disabled member %s" % user)


def disable_blue_member(user):
    logger.debug("Disabling blue user %s" % user)
    remove_member_permission(user, 'blue_member')
    remove_user_from_group(user, settings.DEFAULT_BLUE_GROUP)
    deactivate_services(user)
    logger.info("Disabled blue user %s" % user)

@login_required
def add_api_key(request):
    logger.debug("add_api_key called by user %s" % request.user)
    user_state = determine_membership_by_user(request.user)
    if request.method == 'POST':
        form = UpdateKeyForm(request.POST)
        form.user_state=user_state
        logger.debug("Request type POST with form valid: %s" % form.is_valid())
        if form.is_valid():
            EveManager.create_api_keypair(form.cleaned_data['api_id'],
                                          form.cleaned_data['api_key'],
                                          request.user)

            # Grab characters associated with the key pair
            characters = EveApiManager.get_characters_from_api(form.cleaned_data['api_id'],
                                                               form.cleaned_data['api_key'])
            EveManager.create_characters_from_list(characters, request.user, form.cleaned_data['api_id'])
            logger.info("Successfully processed api add form for user %s" % request.user)
            return HttpResponseRedirect("/api_key_management/")
        else:
            logger.debug("Form invalid: returning to form.")
    else:
        logger.debug("Providing empty update key form for user %s" % request.user)
        form = UpdateKeyForm()
        form.user_state = user_state
    context = {'form': form, 'apikeypairs': EveManager.get_api_key_pairs(request.user.id)}
    return render_to_response('registered/addapikey.html', context,
                              context_instance=RequestContext(request))


@login_required
def api_key_management_view(request):
    logger.debug("api_key_management_view called by user %s" % request.user)
    context = {'apikeypairs': EveManager.get_api_key_pairs(request.user.id)}

    return render_to_response('registered/apikeymanagment.html', context,
                              context_instance=RequestContext(request))


@login_required
def api_key_removal(request, api_id):
    logger.debug("api_key_removal called by user %s for api id %s" % (request.user, api_id))
    authinfo = AuthServicesInfoManager.get_auth_service_info(request.user)
    # Check if our users main id is in the to be deleted characters
    characters = EveManager.get_characters_by_owner_id(request.user.id)
    if characters is not None:
        for character in characters:
            if character.character_id == authinfo.main_char_id:
                if character.api_id == api_id:
                    # TODO: Remove services also
                    if authinfo.is_blue:
                        logger.debug("Blue user %s deleting api for main character. Disabling." % request.user)
                        disable_blue_member(request.user)
                    else:
                        logger.debug("User %s deleting api for main character. Disabling." % request.user)
                        disable_member(request.user, authinfo.main_char_id)

    EveManager.delete_api_key_pair(api_id, request.user.id)
    EveManager.delete_characters_by_api_id(api_id, request.user.id)
    logger.info("Succesfully processed api delete request by user %s for api %s" % (request.user, api_id))
    return HttpResponseRedirect("/api_key_management/")


@login_required
def characters_view(request):
    logger.debug("characters_view called by user %s" % request.user)
    render_items = {'characters': EveManager.get_characters_by_owner_id(request.user.id),
                    'authinfo': AuthServicesInfoManager.get_auth_service_info(request.user)}
    return render_to_response('registered/characters.html', render_items, context_instance=RequestContext(request))


@login_required
def main_character_change(request, char_id):
    logger.debug("main_character_change called by user %s for character id %s" % (request.user, char_id))
    if EveManager.check_if_character_owned_by_user(char_id, request.user):
        AuthServicesInfoManager.update_main_char_Id(char_id, request.user)
        set_state(request.user)
        return HttpResponseRedirect("/characters/")
    return HttpResponseRedirect("/characters/")



@login_required
def user_refresh_api(request, api_id):
    logger.debug("user_refresh_api called by user %s for api id %s" % (request.user, api_id))
    if EveApiKeyPair.objects.filter(api_id=api_id).exists():
        api_key_pair = EveApiKeyPair.objects.get(api_id=api_id)
        if api_key_pair.user == request.user:
            refresh_api(api_key_pair)
            set_state(request.user)
        else:
            logger.warn("User %s not authorized to refresh api id %s" % (request.user, api_id))
    else:
        logger.warn("User %s unable to refresh api id %s - api key not found" % (request.user, api_id))
    return HttpResponseRedirect("/api_key_management/")
