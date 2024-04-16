from datetime import datetime, timedelta, timezone
import os
import json
import pytz
from pymongo import MongoClient
from collections import Counter
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
load_dotenv()

# construct mongoDB client
# ssl hand shake error because ip not whitelisted
client = MongoClient(
    os.getenv('DATABASE_URL'), 
    maxPoolSize=2
)
db_handle = client[os.getenv('DB_NAME')]
def get_db_client():
    return db_handle

qa_inventory_db_name = 'QAInventory'

# Azure stuff
account_name = 'CCPD'
container_name = 'product-image'
# blob client object from azure access keys
azure_blob_client = BlobServiceClient.from_connection_string(os.getenv('SAS_KEY'))
# container handle for product image
product_image_container_client = azure_blob_client.get_container_client(container_name)

# decode body from json to object
decodeJSON = lambda body : json.loads(body.decode('utf-8'))

# get client ip address
def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

# limit variables
max_name = 50
min_name = 3
max_email = 45
min_email = 6
max_password = 70
min_password = 8
max_sku = 7
min_sku = 4
max_inv_code = 100
min_inv_code = 10
max_role = 12
min_role = 4

# user registration date format
user_time_format = "%b %-d %Y"

# instock inventory format
inv_iso_format = '%Y-%m-%d %H:%M:%S'

# iso format
# for QA inventory, table filters,
iso_format = "%Y-%m-%dT%H:%M:%S.%f"
full_iso_format = "%Y-%m-%dT%H:%M:%S.%fZ"
# image blob date format
# 2023-11-30
# the date have to be 1 digit
# blob_date_format = "%a %b %d %Y"
blob_date_format = "%Y-%m-%d"

# return blob time string with format of blob date format
def getBlobTimeString() -> str:
    eastern_timezone = pytz.timezone('America/Toronto')
    current_time = datetime.now(eastern_timezone)
    return current_time.strftime(blob_date_format)

# return N days before time_str in blob date format
def getNDayBefore(days_before, time_str) -> str:
    blob_time = datetime.strptime(time_str, blob_date_format)
    blob_time = blob_time - timedelta(days=days_before)
    return blob_time.strftime(blob_date_format)

def getNDayBeforeToday(days_before) -> str:
    blob_time = datetime.now() - timedelta(days=days_before)
    return blob_time.strftime(iso_format)

# convert from string to iso time
def convertToTime(time_str):
    try:
        return datetime.strptime(time_str, iso_format)
    except:
        try:
            return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
        except:
            return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%f%z")

# inventory time format in eastern timezone
def getIsoFormatNow():
    eastern_timezone = pytz.timezone('America/Toronto')
    current_time = datetime.now(eastern_timezone)
    now = current_time.isoformat()
    return now

def getIsoFormatInv():
    eastern_timezone = pytz.timezone('America/Toronto')
    current_time = datetime.now(eastern_timezone)
    return current_time.strftime(inv_iso_format)

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

# role length from 4 to 12
def sanitizeRole(role):
    if not isinstance(role, str):
        return False
    if not inRange(role, min_role, max_role):
        return False
    return role

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
    return platform

# shelf location sanitize
def sanitizeShelfLocation(shelfLocation):
    if not isinstance(shelfLocation, str):
        return False
    return shelfLocation

# invitation code should be a string
def sanitizeInvitationCode(code):
    if not isinstance(code, str):
        return False
    if not inRange(code, min_inv_code, max_inv_code):
        return False
    return code


# these below will raise type error instead of returning false
# make sure string is type str and no $ included 
def sanitizeString(field):
    if not isinstance(field, str):
        raise TypeError('Invalid String')
    if len(field) > 600:
        raise TypeError('Input Too Long')
    return field.replace('$', '')

# makesure number is int and no $
def sanitizeNumber(num):
    if not isinstance(num, int) and not isinstance(num, float):
        raise TypeError('Invalid Number')
    return num

# make sure bool is actually bool
def sanitizeBoolean(bool_input):
    if not isinstance(bool_input, bool):
        raise TypeError('Invalid Boolean')
    return bool_input

# sanitize all field in user info body
# make sure user is active and remove $
def sanitizeUserInfoBody(body):
    for attr, value in body.items():
        # if hit user active field set the field to bool type
        # if not sanitize string and remove '$'
        if attr == 'userActive':
            body[attr] = bool(value == 'true')
        else:
            body[attr] = sanitizeString(value)
            
# get is current time working hours (EST)
def getIsWorkingHourEST() -> bool:
    eastern_timezone = pytz.timezone('America/Toronto')
    current_time = datetime.now(eastern_timezone)
    hour = current_time.hour
    minute = current_time.minute
    print(hour)
    print(minute)
    if hour < 10 and minute < 30:
        return False
    elif hour > 19 and minute > 0:
        return False
    return True

# for instock inventory  
def populateSetData(body, key, setData, sanitizationMethod):
    if key in body:
        setData[key] = sanitizationMethod(body[key])

def convertToAmountPerDayData(arr):
    # try:
    formatted_dates = [datetime.strptime(item['time'], inv_iso_format).strftime('%b %d') for item in arr]
    date_counts = Counter(formatted_dates)
    # except:
    #     formatted_dates = [datetime.strptime(item['time'], '%Y-%m-%d %H:%M:%S').strftime('%b %d') for item in arr]
    #     date_counts = Counter(formatted_dates)
    
    out = []
    for date, count in date_counts.items():
        out.append({'date': date, 'Recorded Inventory': count})
    return out

def getTodayTimeRangeFil(deltaDays=0):
    time = datetime.now() - timedelta(days=deltaDays)
    return {
        '$gte': time.replace(hour=0, minute=0, second=0, microsecond=0).strftime(full_iso_format),
        '$lt': time.replace(hour=23, minute=59, second=59, microsecond=999999).strftime(full_iso_format)
    }

def findObjectInArray(array_of_objects, key, value):
    return [obj for obj in array_of_objects if obj.get(key) == value][0]

default_start_bid = 5
low_value_start_bid = 2
reserve_default = 0
# generate reserve price according to description and condition
# Arthur: @ouyangxue-0407
def process_numbers(number,factor):
    multiplied = number * factor
    rounded = round(multiplied / 5) * 5
    return rounded
def getBidReserve(description, msrp, condition):
    price = round(float(sanitizeNumber(msrp)), 2)
    reserve = reserve_default
    startbid = default_start_bid
    
    # determine reserve price
    if price > 79.99:
        match condition:
            case "Sealed":
                reserve = process_numbers(price, 0.375)
            case "New":
                if "MISSING PART" in description.upper():
                    reserve = process_numbers(price, 0.30)
                else:
                    reserve = process_numbers(price, 0.35)
            case "Used Like New":
                reserve = process_numbers(price, 0.30)
            case "Used":
                reserve = process_numbers(price, 0.25)
    
    # determine start bid
    if price < 11:
        startbid = low_value_start_bid
    return {'startBid': startbid, 'reserve': reserve}


def processInstock(itemArr, instockRes, duplicate):
    # loop all filtered instock items
    for item in instockRes:
        quantity = item['quantityInstock']
        item.pop('quantityInstock')
        
        # get bid and reserve price
        priceObj = getBidReserve(
            item['description'] if 'description' in item else '', 
            item['msrp'] if 'msrp' in item else 0, 
            item['condition'] if 'condition' in item else 'New'
        )
        # if specified startbid and reserve, pull from item 
        if 'startBid' in item and 'reserve' in item:
            priceObj = {
                'reserve': sanitizeNumber(item['reserve']),
                'startBid': sanitizeNumber(item['startBid'])
            }
        
        # final object
        auctionItem = {
            **item, 
            'startBid': priceObj['startBid'] if 'startBid' not in item else item['startBid'], 
            'reserve': priceObj['reserve'] if 'reserve' not in item else item['reserve'],
            'msrp': item['msrp'] if 'msrp' in item else 0,
        } # start bid and reserve is calculated at getBidReserveEst

        # create empty field if these dont exist when pulling from instock db
        if 'description' not in auctionItem:
            auctionItem = {**auctionItem, 'description': ''}
        if 'lead' not in auctionItem:
            auctionItem = {**auctionItem, 'lead': ''}

        # if duplication option, duplicate the row x times
        if duplicate and quantity > 1:
            for x in range(quantity):
                itemArr.append(auctionItem)
        else:
            itemArr.append(auctionItem)
    return itemArr
