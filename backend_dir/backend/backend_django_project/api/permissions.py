from rest_framework import permissions
from . import models


class RolePermission(permissions.BasePermission):
    """
    Check if user role has permissions for this endpoint and method.
    Note that token-related endpoints are always allowed.
    """

    def has_permission(self, request, view):
        if role_is_active(request):
            if endpoint_is_always_allowed(request):
                return True
            elif check_has_all_endpoints(request):
                return True
            elif user_is_update_gaps_status(request) and endpoint_is_related_to_gaps_status(request):
                return True
            else:
                return check_has_endpoint(request)

        return False


def user_is_update_gaps_status(request):
    return request.user.username == "update-gaps-status" and request.user.role.name == "update-gaps-status"


def endpoint_is_related_to_gaps_status(request):
    if (request.path == "/api/update-gaps-status" and request.method == "POST") or (request.path == "/api/delete-update-gaps-status-block" and request.method == "POST"):
        return True


def endpoint_is_always_allowed(request):

    if endpoint_is_user_photo(request) and user_is_requesting_its_photo(request):
        return True
    elif endpoint_is_get_user_by_id(request) and user_is_requesting_its_data(request):
        return True
    elif endpoint_is_health_check(request):
        return True

    return False


def endpoint_is_get_user_by_id(request):
    return replace_path_params(request.path) == "/api/users/<PATH_PARAM>" and request.method == "GET"


def user_is_requesting_its_data(request):
    return int(get_path_params_from_endpoint(request.path)[0]) == int(request.user.id)


def endpoint_is_health_check(request):
    return request.path == "/api/health-check" and request.method == "GET"


def endpoint_is_user_photo(request):
    return replace_path_params(request.path) == "/api/users/<PATH_PARAM>/photo" and request.method == "GET"


def user_is_requesting_its_photo(request):
    return int(get_path_params_from_endpoint(request.path)[0]) == int(request.user.id)


def get_path_params_from_endpoint(path):
    path_parts = path.split("/")
    path_params = []

    for i in path_parts:
        if remove_whitespaces(i).isnumeric():
            path_params.append(remove_whitespaces(i))

    return path_params


def role_is_active(request):
    try:
        models.Role.objects.get(
            id=request.user.role.id, is_active=True)
    except models.Role.DoesNotExist:
        return False
    except models.Role.MultipleObjectsReturned:
        return True
    else:
        return True


def check_has_all_endpoints(request):
    try:
        models.Role.objects.get(
            id=request.user.role.id, allow_all=True)
    except models.Role.DoesNotExist:
        return False
    except models.Role.MultipleObjectsReturned:
        return True
    else:
        return True


def check_has_endpoint(request):

    if models.Role.objects.get(
            id=request.user.role.id).role_api:
        return check_has_endpoint_api(request)
    else:
        return check_has_endpoint_frontend(request)


def check_has_endpoint_api(request):
    role = models.Role.objects.get(
        id=request.user.role.id)

    for endpoint_cluster in role.endpoints_clusters.filter(role_type__icontains="API"):
        if endpoint_cluster.endpoints.filter(path=replace_path_params(request.path), method__in=[request.method, "ALL"]).exists():
            return True

    return False


def check_has_endpoint_frontend(request):
    role = models.Role.objects.get(
        id=request.user.role.id)

    for endpoint_cluster in role.endpoints_clusters.filter(role_type__icontains="FRONT"):
        if endpoint_cluster.endpoints.filter(path=replace_path_params(request.path), method__in=[request.method, "ALL"]).exists():
            return True

    return False


def replace_path_params(str):
    path_parts = str.split("/")

    for i in path_parts:
        if remove_whitespaces(i).isnumeric():
            path_parts[path_parts.index(i)] = "<PATH_PARAM>"

    return "/".join(path_parts)


def remove_whitespaces(str):
    return "".join(str.split())
