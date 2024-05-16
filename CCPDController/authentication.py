import jwt
from firebase_admin import auth
from rest_framework.authentication import CSRFCheck, TokenAuthentication, BaseAuthentication
from bson.objectid import ObjectId
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from CCPDController.utils import get_db_client

# pymongo
db = get_db_client()
collection = db['User']

# check for csrf token in request
def enforce_csrf(request):
    check = CSRFCheck(request)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise PermissionDenied('CSRF Failed: %s' % reason)

# customized authentication class used in settings
class JWTAuthentication(TokenAuthentication):
    # run query against database to verify user info by querying user id
    def authenticate_credentials(self, id):
        # if id cannot convert into ObjectId, throw error
        try:
            uid = ObjectId(id)
        except:
            raise AuthenticationFailed('Invalid ID')
        
        # get only id status and role
        user = collection.find_one({'_id': uid}, {'userActive': 1, 'role': 1})
        
        # check user status
        if not user:
            raise AuthenticationFailed('User Not Found')
        if user['userActive'] == False:
            raise AuthenticationFailed('User Inactive')
          
        # return type have to be tuple
        return (user, user['role'])
        
    # called everytime when accessing restricted router
    def authenticate(self, request):
        try:
            # check for http-only cookies
            raw_token = request.COOKIES.get('token') or None
            if not raw_token:
                raise AuthenticationFailed('No token provided')
            
            # decode jwt and retrive user id
            payload = jwt.decode(raw_token, settings.JWT_SECRET_KEY, algorithms='HS256')
        
        except jwt.DecodeError or UnicodeError:
            raise AuthenticationFailed('Invalid token')
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        
        # TODO
        # check the reason why csrf token cannot be fetch on logout
        # enforce_csrf(request)
        return self.authenticate_credentials(payload['id'])

class FirebaseAuthentication(BaseAuthentication):
    def authenticate(self, request):
        # read token in header
        auth_header = request.META.get("HTTP_AUTHORIZATION")
        if not auth_header:
            print("No auth token provided")
            raise PermissionDenied("No auth token provided")
    
        # decode token
        id_token = auth_header.split(" ").pop()
        decoded_token = None
        try:
            decoded_token = auth.verify_id_token(id_token)
        except Exception:
            print("Invalid Auth Token")
            raise PermissionDenied("Invalid Auth Token")
        
        if not id_token or not decoded_token:
            return None

        # pull user form mongo
        user = collection.find_one(
            {'email': decoded_token.get('email')}, 
            {'userActive': 1, 'role': 1}
        )
        
        # check user status
        if not user:
            print("User Not Found")
            raise PermissionDenied('User Not Found')
        if user['userActive'] == False:
            print("User Inactive")
            raise PermissionDenied('User Inactive')

        try:
            uid = decoded_token.get("uid")
        except Exception:
            raise PermissionDenied("Firebase Server Error")

        return (uid, user['role'])