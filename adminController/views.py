from curses import newpad
import email
from urllib import response
from django.http import HttpRequest
import jwt
import uuid
import pymongo
from django.conf import settings
from django.views.decorators.csrf import csrf_protect
from django.middleware.csrf import get_token
from bson.objectid import ObjectId
from datetime import datetime, timedelta, date, timezone
from inventoryController.unpack_filter import unpackQARecordFilter
from userController.models import User
from .models import InvitationCode, RetailRecord
from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from CCPDController.throttles import AppIDThrottle
from CCPDController.permissions import  IsAdminPermission, IsQAPermission, IsSuperAdminPermission
from CCPDController.utils import (
    decodeJSON,
    get_db_client,
    sanitizeArrayOfString,
    sanitizeBoolean, 
    sanitizeEmail, 
    sanitizePassword, 
    sanitizeString, 
    sanitizeUserInfoBody, 
    user_time_format, 
    sanitizeNumber,
    qa_inventory_db_name,
)
from firebase_admin import auth

# pymongo
db = get_db_client()
user_collection = db['User']
qa_collection = db[qa_inventory_db_name]
inv_code_collection = db['Invitations']
instock_collection = db['InstockInventory']
retail_collection = db['Retail']
return_collection = db['Return']
admin_settings_collection = db['AdminSettings']

# admin jwt token expiring time
admin_expire_days = 90

# login admins (replaced by firebase)
# check admin token
@csrf_protect
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def checkAdminToken(request: HttpRequest):
    # get token from cookie, token is 100% set because of permission
    token = request.COOKIES.get('token')
    
    # decode and return user id
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms='HS256')
    except jwt.DecodeError or UnicodeError:
        raise AuthenticationFailed('Invalid token')
    except jwt.ExpiredSignatureError:
        raise AuthenticationFailed('Token has expired')

    if token:
        user = user_collection.find_one({'_id': ObjectId(payload['id'])}, {'name': 1, 'role': 1})
        if user:
            return Response({ 'id': str(ObjectId(user['_id'])), 'name': user['name'], 'role': user['role']}, status.HTTP_200_OK)
    return Response('Token Not Found, Please Login Again', status.HTTP_100_CONTINUE)

# login admins (replaced by firebase)
@csrf_protect
@api_view(['POST'])
@throttle_classes([AppIDThrottle])
@permission_classes([AllowAny])
def adminLogin(request: HttpRequest):
    body = decodeJSON(request.body)

    # sanitize
    email = sanitizeEmail(body['email'])
    password = sanitizePassword(body['password'])
    if email == False or password == False:
        return Response('Invalid Login Information', status.HTTP_400_BAD_REQUEST)
    
    # check if user exist
    # only retrive user status and role
    user = user_collection.find_one({
        'email': email.lower(),
        'password': password
    }, { 'userActive': 1, 'role': 1, 'name': 1 })
    
    # check user status
    if not user:
        return Response('Login Failed', status.HTTP_404_NOT_FOUND)
    if bool(user['userActive']) == False:
        return Response('User Inactive', status.HTTP_401_UNAUTHORIZED)
    if user['role'] != 'Admin' and user['role'] != 'Super Admin':
        return Response('Permission Denied', status.HTTP_403_FORBIDDEN)

    try:
        # construct payload
        expire = datetime.now(tz=timezone.utc) + timedelta(days=admin_expire_days)
        payload = {
            'id': str(ObjectId(user['_id'])),
            'exp': expire,
            'iat': datetime.now(tz=timezone.utc)
        }
        
        # construct tokent and return it
        token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")
    except:
        return Response('Failed to Generate Token', status.HTTP_500_INTERNAL_SERVER_ERROR)

    # return the id and name
    info = {
        'id': str(ObjectId(user['_id'])),
        'name': user['name'],
        'role': user['role']
    }

    # construct response store jwt token in http only cookie
    response = Response(info, status.HTTP_200_OK)
    response.set_cookie('token', token, httponly=True, expires=expire, samesite="None", secure=True)
    response.set_cookie('csrftoken', get_token(request), httponly=True, expires=expire, samesite="None", secure=True)
    return response


'''
User manager stuff
'''
# create user with custom roles 
# update: added firebase authentication with mongodb info storing
@csrf_protect
@api_view(['POST'])
@permission_classes([IsSuperAdminPermission])
def createUser(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sanitizeUserInfoBody(body)
        lowercase_user_email = str(body['email']).lower()
        newUser = User (
            name=body['name'],
            email=lowercase_user_email,
            password=body['password'],
            role=body['role'],
            registrationDate=date.today().strftime(user_time_format),
            userActive=True
        )
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # register with firebase
    try:
        fb_res = auth.create_user(email=lowercase_user_email, password=body['password'])
    except:
        return Response('Firebase Error, Cannot Create User', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # register in mongodb
    if fb_res:
        try:
            user_collection.insert_one(newUser.__dict__)
        except:
            return Response('Unable to Create User', status.HTTP_400_BAD_REQUEST)
    else:
        return Response('Unable to Create User', status.HTTP_400_BAD_REQUEST)
    return Response('User Created', status.HTTP_200_OK)

# delete user by id
# id: string
@api_view(['DELETE'])
@permission_classes([IsSuperAdminPermission])
def deleteUserById(request: HttpRequest):
    # try:
    # convert to BSON
    body = decodeJSON(request.body)
    uid = ObjectId(sanitizeString(body['id']))
    fid = sanitizeString(body['fid'])
    # except:
    #     return Response('Invalid User ID', status.HTTP_400_BAD_REQUEST)
    
    # try delete user from db
    res = user_collection.delete_one({'_id': uid})
    
    # if deleted from mongo DB, call firebase
    if res:
        deleted = auth.delete_user(uid=fid)
        if deleted:
            return Response('User Deleted', status.HTTP_200_OK)
        else:
            return Response('Failed to Delete From Firebase', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('User Not Found', status.HTTP_404_NOT_FOUND)

# update user information by id
# id: string
# body: UserDetail
@api_view(['PUT'])
@permission_classes([IsSuperAdminPermission])
def updateUserById(request: HttpRequest, uid):
    try:
        # convert string to ObjectId
        userId = ObjectId(uid)
        
        # if user not in db throw 404
        user = user_collection.find_one({ '_id': userId })
        if not user:
            return Response('User Not Found', status.HTTP_404_NOT_FOUND)
        body = decodeJSON(request.body)
        
        # loop body obeject and remove $
        sanitizeUserInfoBody(body)
    except:
        return Response('Invalid User Info', status.HTTP_400_BAD_REQUEST)
    
    # update user information
    try:
        user_collection.update_one(
            { '_id': userId }, 
            { '$set': body }
        )
    except:
        return Response('Update User Infomation Failed', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Password Updated', status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getAllUserInfo(request: HttpRequest):
    userArr = []
    cursor = user_collection.find({}, {'password': 0})
    for item in cursor:
        item['_id'] = str(item['_id'])
        userArr.append(item)
    cursor.close()
    return Response(userArr, status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getAllInvitationCode(request: HttpRequest): 
    codeArr = []
    cursor = inv_code_collection.find({}, {'_id': 0})
    for item in cursor:
        codeArr.append(item)
    cursor.close()
    return Response(codeArr, status.HTTP_200_OK)

# admin generate invitation code for newly hired QA personal to join
@api_view(['POST'])
@permission_classes([IsSuperAdminPermission])
def issueInvitationCode(request: HttpRequest):
    # generate a uuid for invitation code
    inviteCode = uuid.uuid4()
    expireTime = (datetime.now() + timedelta(days=1)).timestamp()
    newCode = InvitationCode(
        code = str(inviteCode),
        exp = expireTime,
    )
    
    try:
        res = inv_code_collection.insert_one(newCode.__dict__)
    except:
        return Response('Server Error', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Invitation Code Created', status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsSuperAdminPermission])
def deleteInvitationCode(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        code = body['code']
    except:
        return Response('Invalid Body: ', status.HTTP_400_BAD_REQUEST)
    
    try:
        res = inv_code_collection.delete_one({'code': code})
    except:
        return Response('Delete Failed', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Code Deleted!', status.HTTP_200_OK)

# get distinct field from instock db
# get all distinct admin name in instock db
# get all distinct QA name in instock db
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getInstockDistinct(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    dist = sanitizeString(body['distinct'])
    # if dist == 'qaName':
    #     res = instock_collection.distinct(str(dist))
    #     for item in res:
    #         users = user_collection.find({'name': item, 'userActive': True}, {'_id': 0,'name': 1})
    #     arr = []
    #     for user in users:
    #         arr.append(user['name'])
    #     return Response(arr, status.HTTP_200_OK)
    # else:
    res = instock_collection.distinct(str(dist))
    
    # except:
    #     return Response('Cannot Pull From Database', status.HTTP_400_BAD_REQUEST)
    return Response(res, status.HTTP_200_OK)

'''
QA inventory stuff
'''
# currPage: number
# itemsPerPage: number
# filter: { 
#   timeRangeFilter: { from: str, to: str }, 
#   conditionFilter: str, 
#   platformFilter: str, 
#   marketplaceFilter: str 
# }
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getQARecordsByPage(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sanitizeNumber(body['page'])
        sanitizeNumber(body['itemsPerPage'])
        query_filter = body['filter']
        # sortingOption = sanitizeNumber(body['sortingOption']) if 'sortingOption' in body else ''

        # sort_by = 'time'
        # direction = -1
        
        # if sortingOption == 'Time ASC':
        #     direction = 1
        # elif sortingOption == 'SKU ASC':
        #     sort_by = 'sku'
        #     direction = 1
        # elif sortingOption == 'SKU DESC':
        #     sort_by = 'sku'
        #     direction = 1

        # strip the ilter into mongoDB query object in fil
        fil = {}
        unpackQARecordFilter(query_filter, fil)
        print(fil)
    except:
        return Response('Invalid Body: ', status.HTTP_400_BAD_REQUEST)
    
    # sort by sku
    # if sortSku > 0:
    #     sortSku = pymongo.DESCENDING
    # else: 
    #     sortSku = pymongo.ASCENDING
        
    try:
        arr = []
        skip = body['page'] * body['itemsPerPage']
        
        if fil == {}:
            query = qa_collection.find().sort('time', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
            count = qa_collection.count_documents({})
            recorded = qa_collection.count_documents({'recorded': True})
        else:
            query = qa_collection.find(fil).sort('time', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
            count = qa_collection.count_documents(fil)
            recorded = qa_collection.count_documents({**fil, 'recorded': True})

        for inventory in query:
                inventory['_id'] = str(inventory['_id'])
                arr.append(inventory)
        query.close()
                
        # if pulled array empty return no content
        if len(arr) == 0:
            return Response([], status.HTTP_200_OK)
    except:
        return Response('Cannot Fetch From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response({"arr": arr, "count": count, "recorded": recorded}, status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsAdminPermission])
def deleteQARecordsBySku(request: HttpRequest, sku): 
    try:
        sku = sanitizeNumber(int(sku))
    except:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
    
    try:
        res = qa_collection.delete_one({'sku': sku})
        if not res:
            return Response('Inventory SKU Not Found', status.HTTP_404_NOT_FOUND)
    except:
        return Response('Failed Deleting From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Inventory Deleted', status.HTTP_200_OK)

# sku: str
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getQARecordBySku(request: HttpRequest, sku):
    sku = int(sku)
    try:
        sanitizeNumber(sku)
    except:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
    
    try:
        res = qa_collection.find_one({'sku': sku}, {'_id': 0})
        if not res:
            return Response('Inventory SKU Not Found', status.HTTP_404_NOT_FOUND)
    except:
        return Response('Failed Querying Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(res, status.HTTP_200_OK)

# currPage: str
# itemsPerPage: str
@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getProblematicRecords(request: HttpRequest):
    arr = []
    cursor = qa_collection.find({ 'problem': True }).sort('sku', pymongo.DESCENDING)
    for item in cursor:
        item['_id'] = str(item['_id'])
        arr.append(item)
    cursor.close()
    return Response(arr, status.HTTP_200_OK)

# set problem to true for qa records
# isProblem: bool
@api_view(['PATCH'])
@permission_classes([IsAdminPermission])
def setProblematicBySku(request: HttpRequest, sku):
    try:
        body = decodeJSON(request.body)
        sanitizeNumber(int(sku))
        isProblem = bool(body['isProblem'])
    except:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)

    # update record
    res = qa_collection.update_one(
        { 'sku': int(sku) },
        { '$set': {'problem': isProblem} },
    )
    if not res:
        return Response('Cannot Modify Records', status.HTTP_500_INTERNAL_SERVER_ERROR)    
    return Response('Record Set', status.HTTP_200_OK)


'''
Retail and return stuff
'''
# page: number
# itemsPerPage: number
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getSalesRecordsByPage(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        currPage = sanitizeNumber(int(body['currPage']))
        itemsPerPage = sanitizeNumber(int(body['itemsPerPage']))
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    arr = []
    skip = currPage * itemsPerPage
    cursor = retail_collection.find().sort('sku', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
    for record in cursor:
        # convert ObjectId
        record['_id'] = str(record['_id'])
        arr.append(record)
    cursor.close()

    # if pulled array empty return no content
    if len(arr) == 0:
        return Response('No Result', status.HTTP_204_NO_CONTENT)
    return Response(arr, status.HTTP_200_OK)


# RetailRecord: RetailRecord
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def createSalesRecord(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        newRecord = RetailRecord (
            sku=body['sku'],
            time=body['time'],
            amount=body['amount'],
            quantity=body['quantity'],
            marketplace=body['marketplace'],
            paymentMethod=body['paymentMethod'],
            buyerName=body['buyerName'],
            adminName=body['adminName'],
            # is this redundent
            invoiceNumber=body['invoiceNumber'] if body['invoiceNumber'] else '',
            adminId=body['adminId'] if body['adminId'] else '',
        )
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    res = retail_collection.insert_one(newRecord)
    if not res:
        return Response('Cannot Insert Into DB', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Sales Record Created', status.HTTP_200_OK)

# one SKU could have multiple retail records with different info
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getSalesRecordsBySku(request: HttpRequest, sku):
    try:
        sku = sanitizeNumber(int(sku))
    except:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
    
    # get all sales records associated with this sku
    arr = []
    cursor = retail_collection.find({'sku': sku})
    for inventory in cursor:
        # convert ObjectId to string prevent error
        inventory['_id'] = str(inventory['_id'])
        arr.append(inventory)
    if len(arr) < 1:
        return Response('No Records Found', status.HTTP_404_NOT_FOUND)
    cursor.close()
    return Response(arr, status.HTTP_200_OK)

# retailRecordId: string
# returnRecord: ReturnRecord
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def createReturnRecord(request: HttpRequest):
    return Response('Return Record')


'''
Admin Settings's stuff
'''
@api_view(['GET'])
@permission_classes([IsAdminPermission | IsQAPermission])
def getAdminSettings(request: HttpRequest):
    res = admin_settings_collection.find_one({'type': 'adminSettings'}, {'_id': 0})
    if not res:
        return Response('Cannot Get Admin Settings', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(res, status.HTTP_200_OK)
    
@api_view(['POST'])
@permission_classes([IsSuperAdminPermission])
def updateAdminSettings(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        setObject = {}
        # deconstruct request and make $set obect for database action
        if 'daysQACanDeleteRecord' in body:
            setObject['daysQACanDeleteRecord'] = sanitizeNumber(int(body['daysQACanDeleteRecord']))
        if 'isQAPermittedAfterHours' in body:
            setObject['isQAPermittedAfterHours'] = sanitizeBoolean(bool(body['isQAPermittedAfterHours']))
        if 'shelfLocationsDef' in body:
            setObject['shelfLocationsDef'] = sanitizeArrayOfString(body['shelfLocationsDef'])
    except:
        return Response('No Records Found', status.HTTP_404_NOT_FOUND)
    
    # update data to database
    res = admin_settings_collection.update_one(
        {'type': 'adminSettings'},
        {'$set': setObject}
    )
    if not res:
        return Response('Cannot Update Admin Settings', status.HTTP_500_INTERNAL_SERVER_ERROR)    
    return Response('Updated Admin Settings', status.HTTP_200_OK)

# update admin password from settings panel
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def updateAdminPassword(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        fid = sanitizeString(body['fid'])
        uid = sanitizeString(body['uid'])
        newPass = sanitizeString(body['newPass'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    try:
        # update password in mongo db
        user_collection.update_one(
            {'_id': ObjectId(uid)},
            {
                '$set': {
                    'password': newPass
                }
            }
        )
    except:
        return Response('Cannot Update User Info in MongoDB', status.HTTP_500_INTERNAL_SERVER_ERROR)

    # update password in firebase
    try:
        auth.update_user(uid=fid, password=newPass)
    except:
        return Response('Cannot Update User Info in Firebase', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Password Updated! Please Login!', status.HTTP_200_OK)
