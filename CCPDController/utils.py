import datetime
import os
import json
import jwt
from django.conf import settings
from rest_framework.response import Response
from rest_framework import exceptions
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv()

# construct mongoDB client
client = MongoClient(os.getenv('DATABASE_URL'), maxPoolSize=2)
db_handle = client[os.getenv('DB_NAME')]
def get_db_client():
    return db_handle

# decode body from json to object
decodeJSON = lambda body : json.loads(body.decode('utf-8'))\

# get client ip address
def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

# limit variables
max_name = 40
min_name = 3
max_email = 45
min_email = 6
max_password = 70
min_password = 8
max_sku = 7
min_sku = 4

# convert from string to datetime
# example: Thu Oct 12 18:48:49 2023
time_format = "%a %b %d %H:%M:%S %Y"
def convertToTime(time_str):
    return datetime.strptime(time_str, time_format)

# check if body contains valid user registration information
def checkBody(body):
    if not inRange(body['name'], min_name, max_name):
        return False
    elif not inRange(body['email'], min_email, max_email) or '@' not in body['email']:
        return False
    elif not inRange(body['password'], min_password, max_password):
        return False
    return body

# check input length
# if input is in range return true else return false
def inRange(input, minLen, maxLen):
    if len(str(input)) < minLen or len(str(input)) > maxLen:
        return False
    else: 
        return True

# sanitize mongodb strings
def removeStr(input):
    input.replace('$', '')
    return input

# skuy can be from 3 chars to 40 chars
def sanitizeSku(sku):
    # type check
    if not isinstance(sku, int):
        return False
    
    # len check
    if not inRange(sku, min_sku, max_sku):
        return False
    return sku

# name can be from 3 chars to 40 chars
def sanitizeName(name):
    # type check
    if not isinstance(name, str):
        return False
    
    # remove danger chars
    clean_name = removeStr(name)
    
    # len check
    if not inRange(clean_name, min_name, max_name):
        return False
    return clean_name

# email can be from 7 chars to 40 chars
def sanitizeEmail(email):
    # type and format check
    if not isinstance(email, str) or '@' not in email:
        return False
    
    # len check
    if not inRange(email, min_email, max_email):
        return False
    return email

# password can be from 8 chars to 40 chars
def sanitizePassword(password):
    if not isinstance(password, str):
        return False
    if not inRange(password, min_password, max_password):
        return False
    return password

# platfrom can only be these
def sanitizePlatform(platform):
    if platform not in ['Amazon', 'eBay', 'Official Website', 'Other']:
        return False

# shelf location sanitize
def sanitizeShelfLocation(shelfLocation):
    if not isinstance(shelfLocation, str):
        return False