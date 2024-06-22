from rest_framework import permissions
from CCPDController.utils import get_db_client, getIsWorkingHourEST

# pymongo
db = get_db_client()
collection = db['User']

# user object will be pass into here from authentication 
# request.auth = ROLE
# request.user = {
#   '_id': ObjectId(xxx),
#   'userActive': xxx,
#   'role': xxx
# }

# QA personal permission
class IsQAPermission(permissions.BasePermission):
    message = 'Permission Denied, QAPersonal Only!'
    def has_permission(self, request, view):
        # if not in work hours, return false
        if getIsWorkingHourEST() == False:
            return False
        
        # mongo db query
        # grant if user is qa personal and user is active
        if request.auth == 'QAPersonal' and bool(request.user['userActive']) == True:
            return True

# admin permission
class IsAdminPermission(permissions.BasePermission):
    message = 'Permission Denied, Admin Only!'
    def has_permission(self, request, view):
        if request.auth == 'Admin' and bool(request.user['userActive']) == True :
            return True
        elif request.auth == 'Super Admin':
            return True

class IsSuperAdminPermission(permissions.BasePermission):
    message = 'Permission Denied, Super Admin Only!'
    def has_permission(self, request, view):
        if request.auth == 'Super Admin':
            return True

# user blocked by IP black list
# class BlockedPermission(permissions.BasePermission):
#     message = 'You Are Blocked From Our Service'
    
#     def has_permission(self, request, view):
#         ipAddress = request.META['REMOTE_ADDR']

