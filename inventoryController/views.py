import io
import os
import pprint
import re
from django.http import HttpRequest
import requests
from scrapy.http import HtmlResponse
from datetime import datetime, timedelta
import xlrd
from CCPDController.proxy_request import getRandomHeader, parallelRequest, request_with_proxy, request_with_proxy_admin
from inventoryController.models import AuctionItem, AuctionRecord, InstockInventory, InventoryItem
from CCPDController.scrape_utils import extract_urls, getCurrency, getImageUrl, getMsrp, getTitle
from CCPDController.utils import (
    convertToAmountPerDayData, decodeJSON, 
    get_db_client, getBlobTimeString, getImageContainerClient, 
    getIsoFormatInv, 
    getNDayBeforeToday, getShelfLocationRegex, getTimeRangeFil, makeCSVRowFromItem, 
    populateSetData, sanitizeBoolean, 
    sanitizeNumber, 
    sanitizeSku, 
    convertToTime, 
    getIsoFormatNow, 
    qa_inventory_db_name, 
    getIsoFormatNow, 
    sanitizeString,
    full_iso_format,
    findObjectInArray,
    processInstock,
)
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError
from CCPDController.permissions import IsQAPermission, IsAdminPermission, IsSuperAdminPermission
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from fake_useragent import UserAgent
from bson.objectid import ObjectId
from collections import Counter
from CCPDController.chat_gpt_utils import generate_description, generate_title
from inventoryController.unpack_filter import unpackInstockFilter
import pymongo
import pandas as pd
from bs4 import BeautifulSoup
import random
from django.views.decorators.csrf import csrf_exempt
from adrf.decorators import api_view as adrf_view

# pymongo
db = get_db_client()
qa_collection = db[qa_inventory_db_name]
instock_collection = db['InstockInventory']
user_collection = db['User']
auction_collection = db['AuctionHistory']
restock_collection = db['RestockRecords']
remaining_collection = db['RemainingHistory']
admin_settings_collection = db['AdminSettings']
ua = UserAgent()


'''
QA Inventory stuff
'''
# query param sku for inventory db row
# sku: string
@api_view(['POST'])
@permission_classes([IsQAPermission | IsAdminPermission])
def getInventoryBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(int(body['sku']))
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)

    # find the Q&A record
    try:
        res = qa_collection.find_one({'sku': sku}, {'_id': 0})
    except:
        return Response('Cannot Fetch From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    if not res:
        return Response('Record Not Found', status.HTTP_400_BAD_REQUEST)
    
    # replace owner field in response
    return Response(res, status.HTTP_200_OK)

# get all inventory of owner by page
# id: string
@api_view(['POST'])
@permission_classes([IsQAPermission | IsAdminPermission])
def getInventoryByOwnerId(request: HttpRequest, page):
    try:
        body = decodeJSON(request.body)
        ownerId = str(ObjectId(body['id']))
        
        # TODO: make limit a path parameter
        # get targeted page
        limit = 10
        skip = page * limit
    except:
        return Response('Invalid Id', status.HTTP_400_BAD_REQUEST)
     
    # return all inventory from owner in array
    arr = []
    skip = page * limit
    cursor = qa_collection.find({ 'owner': ownerId }).sort('sku', pymongo.DESCENDING).skip(skip).limit(limit)
    for inventory in cursor:
        inventory['_id'] = str(inventory['_id'])
        arr.append(inventory)
    cursor.close()
    return Response(arr, status.HTTP_200_OK)

# for charts and overview data
# id: string
@api_view(['POST'])
@permission_classes([IsQAPermission | IsAdminPermission])
def getInventoryInfoByOwnerId(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        ownerId = str(ObjectId(body['id']))
    except:
        return Response('Invalid Id', status.HTTP_400_BAD_REQUEST)
     
    # array of all inventory
    arr = []
    cursor = qa_collection.find({ 'owner': ownerId }, { 'itemCondition': 1 })
    for inventory in cursor:
        # inventory['_id'] = str(inventory['_id'])
        arr.append(inventory['itemCondition'])
    cursor.close()
    
    itemCount = Counter()
    for condition in arr:
        itemCount[condition] += 1 

    return Response(dict(itemCount), status.HTTP_200_OK)

# get all qa inventory by qa name
# ownerName: string
@api_view(['POST'])
@permission_classes([IsQAPermission | IsAdminPermission])
def getInventoryByOwnerName(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        name = sanitizeString(body['ownerName'])
        currPage = sanitizeNumber(body['page'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
        
    # get all qa inventory
    # default item per page is 10
    skip = currPage * 10
    res = qa_collection.find(
        { 'ownerName': name }, 
        { '_id': 0 }
    ).sort('time', pymongo.DESCENDING).skip(skip).limit(10)
    if not res:
        return Response('No Inventory Found', status.HTTP_200_OK)
    
    # make array of items
    arr = []
    for item in res:
        arr.append(item)
    res.close()
    return Response(arr, status.HTTP_200_OK)

# get bar charts and pie charts data for my inventory page in qa app
# ownerName: string
@api_view(['POST'])
@permission_classes([IsQAPermission | IsAdminPermission])
def getQAInfoByOwnerName(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    name = sanitizeString(body['ownerName'])
    # except:
    #     return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # array of all inventory
    arr = []
    condition = []
    con = qa_collection.find({ 'ownerName': name }, { 'itemCondition': 1 })
    if not con:
        return Response('No Inventory Found', status.HTTP_404_NOT_FOUND)
    
    # make inventory array
    for inventory in con:
        arr.append(inventory['itemCondition'])
    con.close()
    itemCount = Counter()
    for condition in arr:
        itemCount[condition] += 1
    
    # make data object for pie charts
    # get all inventory from target user recorded in past 7 days
    startTime = getNDayBeforeToday(7)
    past7Days = qa_collection.find(
        {
            'ownerName': name,
            'time': {
                '$gte': startTime
            }
        }, 
        {'_id': 0, 'sku': 1, 'time': 1}
    ).sort('time', pymongo.DESCENDING)
    if not past7Days:
        return Response('No Inventory Found', status.HTTP_404_NOT_FOUND)
    
    # make array for all inventories
    past7DaysArr = []
    for inventory in past7Days:
        past7DaysArr.append(inventory)
    past7Days.close()
    # populate date keys first, to include days with zero inventory
    all7Dates = [(datetime.fromisoformat(startTime) + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(8)]
    past7DaysCounter = Counter({date: 0 for date in all7Dates})
    
    # count the results
    for item in past7DaysArr:
        date = datetime.fromisoformat(item['time']).strftime('%Y-%m-%d')
        past7DaysCounter[date] += 1
    return Response({'pieData': dict(itemCount), 'barData': dict(past7DaysCounter)}, status.HTTP_200_OK)

# create Q&A inventory record
@api_view(['PUT'])
@permission_classes([IsQAPermission | IsAdminPermission])
def createInventory(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(body['sku'])
        shelfLocation = sanitizeString(body['shelfLocation'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)

    # if sku exist return conflict
    inv = qa_collection.find_one({'sku': body['sku']})
    if inv:
        return Response('SKU Already Existed', status.HTTP_409_CONFLICT)
    
    # check if shelf location matches admin requirements
    locationArr = admin_settings_collection.find_one(
        {'type': 'adminSettings'},
        {'_id': 0, 'shelfLocationsDef': 1}
    )
    
    if not bool(re.match(getShelfLocationRegex(locationArr['shelfLocationsDef']), shelfLocation)):
        return Response('Shelf Location Invalid', status.HTTP_400_BAD_REQUEST)
    
    # construct new inventory
    # try:
    newInventory = InventoryItem(
        time = getIsoFormatNow(),
        sku = sku,
        itemCondition = body['itemCondition'],
        comment = body['comment'],
        link = body['link'],
        platform = body['platform'],
        shelfLocation = shelfLocation,
        amount = body['amount'],
        owner = body['owner'],
        ownerName = body['ownerName'],
        marketplace = body['marketplace']
    )
    # pymongo need dict or bson object
    qa_collection.insert_one(newInventory.__dict__)
    # except:
    #     return Response('Invalid Inventory Information', status.HTTP_400_BAD_REQUEST)
    return Response('Inventory Created', status.HTTP_200_OK)

# add scraped data to qa database record upon QA submission
@csrf_exempt
@adrf_view(['POST'])
@permission_classes([IsQAPermission | IsAdminPermission])
async def scrapeIntoDb(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        owner = sanitizeString(body['owner'])
        ownerName = sanitizeString(body['ownerName'])
        url = sanitizeString(body['url'])
        sku = sanitizeNumber(int(body['sku']))
        if url == "" or url == "No Link":
            return Response('Invalid Body', status.HTTP_200_OK)
    except:
        return Response('Invalid Body', status.HTTP_200_OK)
    
    # return error if not amazon link or not http
    if 'https' not in url and '.ca' not in url and '.com' not in url:
        return Response('Invalid URL', status.HTTP_200_OK)
    elif 'a.co' not in url and 'amazon' not in url and 'amzn' not in url:
        return Response('Invalid URL, Not Amazon URL', status.HTTP_200_OK)
    
    # send parallel request
    # try:
    res = await parallelRequest(extract_urls(url))
    # except:
        # return Response('Cannot Scrape', status.HTTP_200_OK)
    # extract link with regex

    # blocked by amazon
    if res == None:
        return Response('Blocked by Amazon bot detection', status.HTTP_200_OK)
    if not str(res.body) or 'Sorry, we just need to make sure you\'re not a robot' in str(res.body) or 'To discuss automated access to Amazon data please contact' in str(res.body):
        return Response('Blocked by Amazon bot detection', status.HTTP_200_OK)
    
    # get raw html and parse it with scrapy
    payload = {
        'title': '',
        'msrp': '',
        'imgUrl': '',
        'currency':''
    }
    
    # get components from amazon
    try:
        payload['title'] = getTitle(res)
        payload['msrp'] = getMsrp(res)
        payload['imgUrl'] = getImageUrl(res)
        payload['currency'] = getCurrency(res)
    except:
        return Response('Failed to Get Data', status.HTTP_200_OK)

    # push to db if result
    update = qa_collection.update_one(
        { 'sku': sku },
        {
            '$set': {
                'scrapedData': payload
            }
        }
    )
    
    # upload image to azure
    if not update:
        return Response("Cannot Add Scraped Data to Record", status.HTTP_200_OK)

    # get image by request
    imgUrl = payload['imgUrl']
    if imgUrl:
        try:
            res = requests.get(imgUrl, headers=getRandomHeader())
            print(f'{sku} uploading scraped photos...')
        except:
            return Response('Cannot GET From Provided URL', status.HTTP_200_OK)
    else:
        return Response('No Scraped Image Url', status.HTTP_200_OK)
    if res.status_code != 200:
        return Response(f'Cannot Get From URL: {res.status_code}')
    if len(res.content) < 1:
        return Response(f'Empty Image', status.HTTP_200_OK)
    
    # get bytes
    img_bytes = io.BytesIO(res.content)

    # construct tags
    tag = {
        "sku": str(sku), 
        "time": getBlobTimeString(), # format: 2024-02-06
        "owner": owner,
        "ownerName": ownerName
    }
    # construct name
    extension = imgUrl.split('.')[-1].split('?')[0]
    imageName = f"{sku}/__{sku}_{sku}.{extension}"
    try:
        image_container_client = getImageContainerClient()
        res = image_container_client.upload_blob(imageName, img_bytes.getvalue(), tags=tag)
    except ResourceExistsError:
        return Response(imageName + ' Already Exist!', status.HTTP_200_OK)
    return Response(payload, status.HTTP_200_OK)
    
    
# update qa record by sku
# sku: string
# newInventory: Inventory
"""
{
    sku: xxxxx,
    newInv: {
        sku,
        itemCondition,
        comment,
        link,
        platform,
        shelfLocation,
        amount
    }
}
"""
@api_view(['PUT'])
@permission_classes([IsQAPermission | IsAdminPermission])
def updateInventoryBySku(request: HttpRequest, sku: str):
    try:
        # convert to object id
        body = decodeJSON(request.body)
        sku = int(sanitizeString(sku))
        newInv = body['newInventoryInfo']
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)

    # check if inventory exists
    oldInv = qa_collection.find_one({ 'sku': sku })
    if not oldInv:
        return Response('Inventory Not Found', status.HTTP_404_NOT_FOUND)
    
    # unpack inventory
    try:
        # construct $set object
        setObj = {}
        newSku = 0
        if 'time' in newInv:
            setObj['time'] = sanitizeString(newInv['time'])
        if 'sku' in newInv:
            setObj['sku'] = sanitizeNumber(newInv['sku'])
            if setObj['sku'] != sku:
                newSku = setObj['sku']
        if 'itemCondition' in newInv:
            setObj['itemCondition'] = sanitizeString(newInv['itemCondition'])
        if 'comment' in newInv:
            setObj['comment'] = sanitizeString(newInv['comment'])
        if 'link' in newInv:
            setObj['link'] = sanitizeString(newInv['link'])
        if 'platform' in newInv:
            setObj['platform'] = sanitizeString(newInv['platform'])
        if 'shelfLocation' in newInv:
            setObj['shelfLocation'] = sanitizeString(newInv['shelfLocation'])
        if 'amount' in newInv:
            setObj['amount'] = sanitizeNumber(newInv['amount'])
        if 'marketplace' in newInv:
            setObj['marketplace'] = sanitizeString(newInv['marketplace'])
    except:
        return Response('Invalid Inventory Info', status.HTTP_406_NOT_ACCEPTABLE)
    
    # try:
    # if sku changed, change blob tags
    if newSku != 0:
        image_container_client = getImageContainerClient()
        # check if blob with that sku exist
        queryTag = f"sku = '{newSku}'" 
        target_blob_list = image_container_client.find_blobs_by_tags(filter_expression=queryTag)
        if sum(1 for _ in target_blob_list) > 0:
            return Response('Target Blob Exist', status.HTTP_409_CONFLICT)
        
        # update blob tags (rename)
        queryTag = f"sku = '{sku}'" 
        blob_list = image_container_client.find_blobs_by_tags(filter_expression=queryTag)
        image_container_client.close()
        newTag = {}
        
        blob_service_client = BlobServiceClient.from_connection_string(os.getenv('SAS_KEY'))
        # copy the blobs to new sku destination
        for item in blob_list:
            source_blob = blob_service_client.get_blob_client(container='product-image', blob=item.name)
            tags = source_blob.get_blob_tags()
            if newTag == {}:
                newTag = {
                    **tags,
                    'sku': newSku,
                }
            
            # make new blob name
            length = len(str(sku)) * 2 + 1
            newBlobName = f'{newSku}/{newSku}_{item.name[length:]}'
            
            # copy and delete
            destination_blob_client = blob_service_client.get_blob_client(container='product-image', blob=newBlobName)
            operation = destination_blob_client.start_copy_from_url(source_blob.url)
            while True:
                props = destination_blob_client.get_blob_properties()
                copy_stats = props.copy.status
                if copy_stats == "success":
                    break
                elif copy_stats == "pending":
                    continue
                else:
                    break
            
            # add tags to new blobs
            if copy_stats == "success":
                source_blob.delete_blob()
                destination_blob_client.set_blob_tags(newTag)
            else:
                return Response('Failed to Update Related Photos', status.HTTP_200_OK)
        blob_service_client.close()

    # update inventory
    res = qa_collection.update_one(
        { 'sku': sku },
        { '$set': setObj }
    )
    if not res:
        return Response('Update Failed', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # except:
    #     return Response('Update Photos Failed', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Update Success', status.HTTP_200_OK)

# delete inventory by sku
# QA personal can only delete records within certain time after creating them
# sku: string
@api_view(['DELETE'])
@permission_classes([IsQAPermission])
def deleteInventoryBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(int(body['sku']))
        time = sanitizeString(str(body['time']))
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    if not sku:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
    
    # pull time created
    res = qa_collection.find_one({'sku': sku}, {'time': 1})
    if not res:
        return Response('Inventory Not Found', status.HTTP_404_NOT_FOUND)
    
    # check if the created time is within 2 days (175000 seconds)
    timeCreated = convertToTime(res['time'])
    createdTimestamp = datetime.timestamp(timeCreated)
    # print(f'Created: {createdTimestamp}')
    todayTimestamp = datetime.timestamp(datetime.now())
    # print(f'Today: {todayTimestamp}')
    two_days = 86400 * 2
    delta = todayTimestamp - createdTimestamp
    canDel = delta < two_days

    # perform deletion or throw error
    if canDel:
        # delete record from mongo
        qa_collection.delete_one({'sku': sku, 'time': time})
        
        image_container_client = getImageContainerClient()
        # list blob by sku
        tag_filter = f"sku = '{str(sku)}'"
        blob_list = image_container_client.find_blobs_by_tags(filter_expression=tag_filter)

        # delete each blob 
        try:
            for blob in blob_list:
                image_container_client.delete_blob(blob.name)
            image_container_client.close()
        except:
            return Response('Failed to Delete', status.HTTP_500_INTERNAL_SERVER_ERROR)
        image_container_client.close()
        return Response('Inventory Deleted', status.HTTP_200_OK)
    return Response('Cannot Delete Inventory After 24H, Please Contact Admin', status.HTTP_403_FORBIDDEN)

# get all QA shelf location
@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getAllQAShelfLocations(request: HttpRequest):
    try:
        arr = qa_collection.distinct('shelfLocation')
    except:
        return Response('Cannot Fetch From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(arr, status.HTTP_200_OK)

# get all qa record today plus 7 days prior's record
@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getDailyQARecordData(request: HttpRequest):
    # get owners of qa record in 7 days time range
    time = datetime.now() - timedelta(days=7)
    cursor = qa_collection.find({
        'time': {
            '$gte': time.replace(hour=0, minute=0, second=0, microsecond=0).strftime(full_iso_format),
            '$lt': datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999).strftime(full_iso_format)        
        }
    })
    owners = cursor.distinct('ownerName')
    
    # for all owner get past 7days qa record count array
    res = []
    dates = []
    for owner in owners:
        # skip if not active
        if not user_collection.find_one({'name': owner, 'userActive': True}):
            continue
        # get 7 days count
        counts = []
        days = 7
        for x in range(days):
            counts.append(qa_collection.count_documents({
                'time': getTimeRangeFil(x), 
                'ownerName': owner
            }))
            times = datetime.now() - timedelta(days=x)
            if len(dates) < days:
                dates.append(f'{times.month}/{times.day}')
        res.append({owner: counts})
    cursor.close()
    return Response({'res': res, 'dates': dates})

# get todays shelf location sheet by user name
@api_view(['POST'])
@permission_classes([IsQAPermission])
def getShelfSheetByUser(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        owner = body['ownerName']
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # res = qa_collection.find({'ownerName': owner, 'time': getTimeRangeFil()}, {'_id': 0, 'sku': 1, 'shelfLocation': 1, 'amount': 1, 'ownerName': 1, 'time': 1})
    
    # get todays inventory, return type is QARecord
    res = qa_collection.find(
        {'ownerName': owner, 'time': getTimeRangeFil()}, 
        {'_id': 0}
    )
    if not res:
        return Response('No Record Found', status.HTTP_200_OK)
    arr = []
    for item in res:
        arr.append(item)
    res.close()
    return Response(arr, status.HTTP_200_OK)

# get end of the day shelf location sheet for all records submitted today
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getAllShelfSheet(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        daysAgo = sanitizeNumber(body["daysAgo"])
    except:
        return Response("Invalid Body", status.HTTP_400_BAD_REQUEST)
    
    # construct filter
    fil = { 'time': getTimeRangeFil(daysAgo) }
    # look for all items entries from that day
    res = qa_collection.find(
        fil, 
        {'_id': 0, 'sku': 1, 'shelfLocation': 1, 'amount': 1, 'ownerName': 1, 'time': 1}
    ).sort('shelfLocation', pymongo.ASCENDING)
    if not res:
        return Response('No Record Found', status.HTTP_404_NOT_FOUND)
    
    # load results into array
    arr = []
    for item in res:
        arr.append(item)
    res.close()
    if len(arr) < 1:
        return Response('No Records Found', status.HTTP_404_NOT_FOUND)
    
    # construct pandas dataframe from mongodb data
    resData = pd.DataFrame(
        arr,
        columns=['sku', 'shelfLocation', 'amount', 'ownerName', 'time'],
    )
    
    # respond csv to front end
    csv = resData.to_csv(index=False)
    response = Response(csv, status=status.HTTP_200_OK, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="shelfSheet.csv"'
    return response

# if item was returned, a new instock record will be created, old record remains out-of-stock
@api_view(['POST'])
@permission_classes([IsQAPermission])
def restock(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        oldSku = sanitizeString((body['oldSku']))
        newSku = sanitizeNumber(body['newSku'])
        ownerName = sanitizeString(body['ownerName'])
        newShelfLocation = sanitizeString(body['newShelfLocation'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # pull old inventory with old sku
    oldInv = instock_collection.find_one({'sku': int(oldSku)})
    if not oldInv:
        return Response('Inventory Not Found', status.HTTP_404_NOT_FOUND)
    
    # update instock inventory
    res = instock_collection.update_one(
        { 'sku': int(oldSku) },
        { 
            '$inc': { 'quantityInstock': 1 },
            '$set': {
                'sku': newSku, 
                'shelfLocation': newShelfLocation,  # 2024-01-26 12:50:00
                'time': getIsoFormatInv(),
                'qaName': ownerName
            }
        }
    )
    qaUpdate = qa_collection.update_one(
        { 'sku': int(oldSku) },
        {
            '$inc': {
                'restocked': 1
            }
        }
    )
    if not res or not qaUpdate:
        return Response('Failed to Update', status.HTTP_404_NOT_FOUND)
    
    # change the id on the blob tag 
    sku = f"sku = '{oldSku}'" 
    image_container_client = getImageContainerClient()
    blob_list = image_container_client.find_blobs_by_tags(filter_expression=sku)
    image_container_client.close()
    newTime = getBlobTimeString()
    blob_service_client = BlobServiceClient.from_connection_string(os.getenv('SAS_KEY'))
    for item in blob_list:
        blob_client = blob_service_client.get_blob_client(container='product-image', blob=item.name)
        tags = blob_client.get_blob_tags()
        updated_tags = {
            'sku': newSku, 
            'time': newTime,
            'ownerName': ownerName
        }
        tags.update(updated_tags)
        blob_client.set_blob_tags(tags)
        
    # add it into restock records
    insert = restock_collection.insert_one({
        'oldSku': int(oldSku),
        'newSku': newSku,
        'oldTime': oldInv['time'],
        'newTime': newTime,
        'oldOwner': oldInv['qaName'],
        'newOwner': ownerName
    })
    if not insert:
        return Response('Failed to Insert Re-stock Record', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(f'Re-stocked {oldSku} to {newSku}', status.HTTP_200_OK)


'''
In-stock stuff
'''
# currPage: number
# itemsPerPage: number
# filter: { 
#   timeRangeFilter: { from: string, to: string }, 
#   conditionFilter: string, 
#   platformFilter: string,
#   marketplaceFilter: string,
#   ownerFilter: string,
#   shelfLocationFilter: string[],
#   keywordFilter: string[],
# }
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getInstockByPage(request: HttpRequest):
    body = decodeJSON(request.body)
    sanitizeNumber(body['page'])
    sanitizeNumber(body['itemsPerPage'])
    query_filter = body['filter']
    fil = {}
    fil = unpackInstockFilter(query_filter, fil)
    print(fil)

    # try:
    arr = []
    skip = body['page'] * body['itemsPerPage']
    
    # see if filter is applied to determine the query
    if fil == {}:
        cursor = instock_collection.find()
        query = cursor.sort('time', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
        count = instock_collection.count_documents({})
    else:
        cursor = instock_collection.find(fil)
        query = cursor.sort('time', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
        count = instock_collection.count_documents(fil)
    
    # get rid of object id
    for inventory in query:
        inventory['_id'] = str(inventory['_id'])
        arr.append(inventory)
    query.close()
    cursor.close()
    
    # if pulled array empty return no content
    if len(arr) == 0:
        return Response([], status.HTTP_200_OK)

    # make and return chart data
    res = instock_collection.find(
        {
            'time': {
                '$gte': getNDayBeforeToday(10, True)
            }
        }, 
        {'_id': 0}
    )
    
    chart_arr = []
    for item in res:
        chart_arr.append(item)
    res.close()
    output = convertToAmountPerDayData(chart_arr)
    # except:
    #     return Response(chart_arr, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response({ "arr": arr, "count": count, "chartData": output }, status.HTTP_200_OK)

@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getInstockBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeSku(body['sku'])
    except:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
    
    try:
        res = instock_collection.find_one({'sku': sku}, {'_id': 0})
    except:
        return Response('Cannot Fetch From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    if not res:
        return Response('No Instock Record Found', status.HTTP_404_NOT_FOUND)
    return Response(res, status.HTTP_200_OK)

@api_view(['PUT'])
@permission_classes([IsAdminPermission])
def updateInstockBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(body['sku'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)

    oldInv = instock_collection.find_one({ 'sku': sku })
    if not oldInv:
        return Response('Instock Inventory Not Found', status.HTTP_404_NOT_FOUND)
    
    # construct $set data according to body
    setData = {}
    populateSetData(body, 'sku', setData, sanitizeNumber)
    populateSetData(body, 'time', setData, sanitizeString)
    populateSetData(body, 'condition', setData, sanitizeString)
    populateSetData(body, 'platform', setData, sanitizeString)
    populateSetData(body, 'marketplace', setData, sanitizeString)
    populateSetData(body, 'shelfLocation', setData, sanitizeString)
    populateSetData(body, 'comment', setData, sanitizeString)
    populateSetData(body, 'url', setData, sanitizeString)
    populateSetData(body, 'quantityInstock', setData, sanitizeNumber)
    populateSetData(body, 'quantitySold', setData, sanitizeNumber)
    populateSetData(body, 'qaName', setData, sanitizeString)
    populateSetData(body, 'adminName', setData, sanitizeString)

    populateSetData(body, 'msrp', setData, sanitizeNumber)
    populateSetData(body, 'lead', setData, sanitizeString)
    populateSetData(body, 'description', setData, sanitizeString)
    
    
    # update inventory
    res = instock_collection.update_one(
        { 'sku': sku },
        { '$set': setData }
    )
    
    # return update status 
    if not res:
        return Response('Update Failed', status.HTTP_404_NOT_FOUND)
    return Response('Update Success', status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsSuperAdminPermission])
def deleteInstockBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeSku(body['sku'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    oldInv = instock_collection.find_one({ 'sku': sku })
    if not oldInv:
        return Response('Instock Inventory Not Found', status.HTTP_404_NOT_FOUND)
    
    try:
        instock_collection.delete_one({ 'sku': sku })
    except:
        return Response('Cannot Delete Instock Inventory', status.HTTP_500_INTERNAL_SERVER_ERROR)
    # set qa record record recorded to false
    
    qa_collection.update_one(
        {'sku': sku},
        {
            '$set': {
                'recorded': False
            }
        }
    )
    
    return Response('Instock Inventory Deleted', status.HTTP_200_OK)

# get all in-stock shelf location
@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getAllShelfLocations(request: HttpRequest):
    try:
        arr = instock_collection.distinct('shelfLocation')
    except:
        return Response('Cannot Fetch From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(arr, status.HTTP_200_OK)

# converts qa record to inventory
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def createInstockInventory(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(body['sku'])
        res = instock_collection.find_one({'sku': sku})
        if res:
            return Response(f'Inventory {sku} Already Instock', status.HTTP_409_CONFLICT)
        msrp = 0
        if 'msrp' in body:
            msrp = sanitizeNumber(float(body['msrp']))
        shelfLocation = sanitizeString(body['shelfLocation'])
        condition = sanitizeString(body['condition'])
        platform = sanitizeString(body['platform'])
        marketplace = sanitizeString(body['marketplace']) if 'marketplace' in body else 'Hibid'
        comment = sanitizeString(body['comment'])
        lead = sanitizeString(body['lead'])
        description = sanitizeString(body['description'])
        url = sanitizeString(body['url'])
        quantityInstock = sanitizeNumber(body['quantityInstock'])
        quantitySold = sanitizeNumber(body['quantitySold'])
        adminName = sanitizeString(body['adminName'])
        qaName = sanitizeString(body['qaName'])
        time = getIsoFormatInv()
        
        newInv: InstockInventory = InstockInventory(
            sku=sku,
            time=time,
            shelfLocation=shelfLocation,
            condition=condition,
            comment=comment,
            lead=lead,
            description=description,
            url=url,
            marketplace=marketplace,
            platform=platform,
            adminName=adminName,
            qaName=qaName,
            quantityInstock=quantityInstock,
            quantitySold=quantitySold,
            msrp=msrp
        )
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)

    instock_collection.insert_one(newInv.__dict__)

    try:
        qa_collection.update_one(
            {'sku': sku, 'ownerName': qaName}, 
            {'$set': { 'recorded': True }}
        )
    except:
        return Response('Cannot Set QA Record Stats', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response('Inventory Created', status.HTTP_200_OK)

# get all filtered instock inventory with no lead or description
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getAbnormalInstockInventory(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    fil = {}
    unpackInstockFilter(body['filter'], fil)
    fil['$and'].append({'or': [{'lead': ''}, {'description': ''}]})
    # except:
    #     return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    res = instock_collection.find(fil, {'_id': 0})
    arr = []
    for item in res:
        arr.append(item)
    res.close()
    return Response([], status.HTTP_200_OK)


'''
Auction Stuff
'''
# generate instock inventory csv file competible with hibid
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def getAuctionCsv(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        lot = sanitizeNumber(body['lot'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)

    record = auction_collection.find_one({'lot': lot}, {'_id': 0})
    if not record:
        return Response('Auction Record Not Found', status.HTTP_404_NOT_FOUND)
    
    # make top row inventory array
    topRow = []
    if 'topRow' in record:
        topRowArr = record['topRow']
        for item in topRowArr:
            row = makeCSVRowFromItem(item)
            topRow.append(row)
    
    # make array for bottom rows inventory
    itemsArrData = []
    imageArrData = []
    itemsArr = record['itemsArr']
    image_container_client = getImageContainerClient()
    for item in itemsArr:
        row = makeCSVRowFromItem(item)
        itemsArrData.append(row)
        # build blob filter tag 
        sku = f"sku = '{item['sku']}'" 
        # get blob list by tag
        blob_list = image_container_client.find_blobs_by_tags(filter_expression=sku)
        # all images names by auction lot 
        images = []
        # get images count per item
        imageCount = sum(1 for _ in blob_list)
        # imageCount = 0
        # for _ in blob_list:
        #     imageCount += 1
        # item lot number in auction
        itemLot = sanitizeNumber(item['lot'])
        for x in range(imageCount):
            name = f"{itemLot}_{x + 1}.jpg"  # image name starts with lot_1.jpg
            images.append(name)
        imageArrData.append(images)
    image_container_client.close()
    
    # make array for previously unsold
    allUnsoldArr = []
    image_container_client2 = getImageContainerClient()
    for obj in record['previousUnsoldArr']:
        itemsArr = obj['items']
        for item in itemsArr:
            # item info
            itemLot = sanitizeNumber(item['lot'])
            # make row using utility function
            row = makeCSVRowFromItem(item)
            allUnsoldArr.append(row)
            # image info
            images = []
            # azure query tag
            sku = f"sku = '{item['sku']}'" 
            # list all blob name for each sku
            blob_list = image_container_client2.find_blobs_by_tags(filter_expression=sku)
            # count image and push them into array
            imageCount = sum(1 for _ in blob_list)
            # imageCount = 0
            # for _ in blob_list:
            #     imageCount += 1
            for x in range(imageCount):
                name = f"{itemLot}_{x + 1}.jpg"
                images.append(name)
            imageArrData.append(images)
    image_container_client2.close()

    # column head
    columns = [
        'Lot',
        'Lead',        # original lead from recording
        'Description', # original description from recording
        'MSRP:$',      
        'Price',       # original scraped msrp  
        'Location',    # original shelfLocation
        'item',
        'vendor',
        'start bid',
        'reserve',
        'Est',
    ]

    # construct data frame for top row + items 
    df = pd.DataFrame(
        data=(topRow + itemsArrData + allUnsoldArr),
        columns=columns
    )
    
    # if toprow exist, make space for top row
    if len(topRow) > 0:
        for x in range(len(topRowArr)):
            imageArrData.insert(0, [])
    # create df for images
    image_df = pd.DataFrame(imageArrData)
    
    
    # add empty column head for image columns to make space at the end
    col = len(image_df.columns)
    for x in range(col):
        columns.append('')
    
    # outer joins the image part of csv
    joined_df = df.join(image_df, how='outer')
    
    # export csv
    csv = joined_df.to_csv(index=False, header=columns)
    response = Response(csv, status=status.HTTP_200_OK, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="shelfSheet.csv"'
    return response

@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getAuctionRemainingRecord(request: HttpRequest):
    # get everything
    # TODO: make it paged
    res = auction_collection.find({}, { '_id': 0 }).sort({ 'lot': -1 })
    auctions = []
    for item in res:
        auctions.append(item)
    res.close()
    res = remaining_collection.find({}, { '_id': 0 }).sort({ 'timeClosed': -1 })
    remaining = []
    for item in res:
        remaining.append(item)
    res.close()
    return Response({'auctions': auctions, 'remaining': remaining}, status.HTTP_200_OK)

@api_view(['POST'])
@permission_classes([IsAdminPermission])
def addTopRowItem(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        auctionLot = sanitizeNumber(body['auctionLot'])
        item = body['newItem']
        
        # pass through the model
        newTopRowItem = AuctionItem(
            lot=sanitizeNumber(item['lot']),
            sku=sanitizeNumber(item['sku']),
            lead=sanitizeString(item['lead']),
            description=sanitizeString(item['description']),
            msrp=sanitizeNumber(item['msrp']),
            shelfLocation=sanitizeString(item['shelfLocation']),
            startBid=sanitizeNumber(item['startBid']),
            reserve=sanitizeNumber(item['reserve']),
        )
    except Exception as e:
        return Response(e, status.HTTP_400_BAD_REQUEST)

    res = auction_collection.update_one(
        { 'lot': auctionLot },
        {
            '$push': { 'topRow': newTopRowItem.__dict__ },
            '$inc': { 'totalItems': 1 }
        }
    )
    if not res:
        return Response('Cannot Insert Top Row Item', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Item Inserted', status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsAdminPermission])
def deleteTopRowItem(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(body['sku'])
        itemLotNum = sanitizeNumber(body['itemLotNumber'])
        auctionLotNum = sanitizeNumber(body['auctionLotNumber'])
    except Exception as e:
        return Response(e, status.HTTP_400_BAD_REQUEST)
    
    res = auction_collection.update_one(
        {
            'lot':  auctionLotNum,
            'topRow': { '$elemMatch': { 'sku': sku, 'lot': itemLotNum }}
        },
        {
            '$pull': { 'topRow': { 'sku': sku, 'lot': itemLotNum }},
            '$inc': { 'totalItems': -1 }
        }
    )

    if not res:
        return Response('Cannot Delete Item', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Item Deleted', status.HTTP_200_OK)

@api_view(['POST'])
@permission_classes([IsAdminPermission])
def createAuctionRecord(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        lot = sanitizeNumber(body['lot'])
        duplicate = sanitizeBoolean(body['duplicate'])
        exist = auction_collection.find_one({'lot': lot})
        if exist:
            return Response('Lot Exist', status.HTTP_409_CONFLICT)
        itemLotStart = sanitizeNumber(body['itemLotStart'])
        endDate = sanitizeString(body['endDate'])
    except:
        return Response('InvalidBody', status.HTTP_400_BAD_REQUEST)
        
    # construct auction record fields
    title = ''
    description = ''
    minMSRP = 0
    maxMSRP = 0
    minSku = 0
    maxSku = 0
    
    # unpack body 
    if 'title' in body:
        title = sanitizeString(body['title'])
    if 'description' in body:
        description = sanitizeString(body['description'])
    if 'filter' in body:
        if 'minMSRP' in body['filter']:
            minMSRP = sanitizeNumber(body['filter']['minMSRP'])
        if 'maxMSRP' in body['filter']:
            maxMSRP = sanitizeNumber(body['filter']['maxMSRP'])
        if 'sku' in body['filter']:
            if 'gte' in body['filter']['sku'] and body['filter']['sku']['gte'] != '':
                minSku = sanitizeNumber(int(body['filter']['sku']['gte']))
            if 'lte' in body['filter']['sku'] and body['filter']['sku']['lte'] != '':
                maxSku = sanitizeNumber(int(body['filter']['sku']['lte']))
        fil = {}
        unpackInstockFilter(body['filter'], fil)
    
    # construct itemsArr inside auction record
    # sort by mrsp
    itemsArr = []
    instock = instock_collection.find(
        fil, 
        { '_id': 0, 'sku': 1, 'lead': 1, 'msrp': 1, 'description': 1, 'shelfLocation': 1, 'condition': 1, 'quantityInstock': 1 }
    ).sort('msrp', -1)
    
    # loading mongo result into itemsArr with or without duplicating items
    itemsArr = processInstock(itemsArr, instock, duplicate)
    count = len(itemsArr)
    instock.close()
    
    # append item lot number on to the object
    itemLotNumbersArr = []
    for x in range(itemLotStart, itemLotStart + count + 1):
        itemLotNumbersArr.append({ 'lot': x })
    merged_list = [{ **d1, **d2 } for d1, d2 in zip(itemLotNumbersArr, itemsArr)]

    # path through model
    auctionRecord = AuctionRecord(
        lot=lot,
        totalItems=count,
        openTime=getIsoFormatNow(),
        closeTime=endDate,
        closed=False,
        title=title,
        description=description,
        minMSRP=minMSRP,
        maxMSRP=maxMSRP,
        remainingResolved=False,
        minSku=minSku,
        maxSku=maxSku,
        itemLotStart=itemLotStart,
    )
    
    # create the auction record
    try: 
        auction = auction_collection.insert_one({**auctionRecord.__dict__, 'itemsArr': merged_list})
        if not auction:
            return Response('Cannot Push To DB', status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # sort by msrp
        auction_collection.update_one(
            { 'lot': auctionRecord.lot },
            {
                '$push': {
                    'itemsArr': {
                        '$each': [],
                        '$sort': { 'msrp': -1 }
                    }
                }
            }
        )
    except: 
        return Response('Cannot Push To DB', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(f'Auction Record {lot} Created', status.HTTP_200_OK)

@api_view(['POST'])
@permission_classes([IsAdminPermission])
def updateRemainingToDB(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    remainingLotNumber = sanitizeNumber(body['lot'])

    remainingRecord = remaining_collection.find_one_and_update(
        { 'lot': remainingLotNumber },
        { '$set' : { 'updatedDB' : True } },
    )
    if not res:
        return Response('Remaining Record Not Found', status.HTTP_200_OK)
    # except:
    #     return Response('Invalid Body', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # error array for item not instock or not found
    errArr = []
    soldArr = remainingRecord['soldItems']
    for item in soldArr:
        # reduce instock amount by sold item sku
        res = instock_collection.update_one(
            { 'sku': item['sku'], 'quantityInstock': { '$gt': 0 }},
            { '$inc': { 'quantityInstock': -1 }} 
        )
        if not res:
            errArr.append(item['sku'])
    return Response({ 
        'updatedDB': True, 
        'errorItems': errArr, 
        'updatedCount': len(soldArr) - len(errArr)
    }, status.HTTP_200_OK)

# takes XLS file from Hibid and creat remaining record in DB
# default remaining sheet is XLS
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def createRemainingRecord(request: HttpRequest):
    # get xls file from request (hibid default exports xls)
    try:
        xls = request.FILES.get('xls')
    except:
        xls = request.FILES.get('xlsx')
    if not xls: 
        return Response('No File Uploaded', status.HTTP_400_BAD_REQUEST)
    
    # try:
    # get remaining lot number from form in request
    lot_number = sanitizeNumber(float(request.data.get('lot')))
    
    # # check if remaining record exists
    # res = remaining_collection.find_one({'lot': lot_number})
    # if res:
    #     return Response('Remaining Record Existed', status.HTTP_409_CONFLICT)
    
    # find auction record by lot number
    auctionRecord = auction_collection.find_one(
        {'lot': lot_number}, 
        {'_id': 0}
    )
    if not auctionRecord:
        return Response(f'Auction {lot_number} Not Found', status.HTTP_404_NOT_FOUND)
    
    # make array for all items in auction
    targetAuctionItemsArr = auctionRecord['itemsArr'] if 'itemsArr' in auctionRecord else []
    
    # array for all top row items
    targetAuctionTopRow = auctionRecord['topRow'] if 'topRow' in auctionRecord else []
    
    # array for all imported unsold
    targetAuctionUnsold = auctionRecord['previousUnsoldArr'] if 'previousUnsoldArr' in auctionRecord else []
    allUnsold = []
    if targetAuctionUnsold != []:
        for obj in targetAuctionUnsold:
            for unsold in obj['items']:
                allUnsold.append(unsold)

    # append all the unsold into bottom row
    targetAuctionItemsArr = targetAuctionItemsArr + allUnsold
    
    if targetAuctionItemsArr == []:
        return Response('No Items on Auction', status.HTTP_404_NOT_FOUND)
    
    # item lot start 
    itemLotStart = auctionRecord['itemLotStart'] if 'itemLotStart' in auctionRecord else 100

    try:
        # load the xls file
        file_in_memory = io.BytesIO(xls.read())
        # create work book and get data array
        workbook = xlrd.open_workbook_xls(
            file_contents=file_in_memory.getvalue(), 
            encoding_override='utf-8'
        )
        sheet = workbook.sheet_by_index(0)
        header = sheet.row_values(0)
        # get all datas from worksheet
        data = [sheet.row_values(row) for row in range(1, sheet.nrows)]
        # make panda dataframe
        df = pd.DataFrame(data, columns=header)
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # make remaining init datas
    soldTopRow = []
    unsoldTopRow = []
    soldItems = []
    unsoldItems = []
    errorItems = []
    notInAuction = []
    totalBidAmount = 0
    
    allRemainingSku = []
    # loop all rows in xls file, populate sold, unsold, not in auction
    for _, row in df.iterrows():
        row = row.to_dict()
        # continue if lot number contains letters (is not a integer)
        try:
            lot = sanitizeNumber(int(row.get('clotnum'))) # lot number might not be int, could be '1a' '1f' 'ff'
        except:
            continue
        
        # pull datas from xls file row
        sold = sanitizeString(row.get('soldstatus'))
        lead = sanitizeString(row.get('lead'))
        bid = sanitizeNumber(float(row.get('bidamount')))
        reserve = sanitizeNumber(float(row.get('bidreserve')))

        # top rows inventory
        if lot < itemLotStart:
            try:
                item = findObjectInArray(targetAuctionTopRow, 'lot', lot)
            except:
                # cannot find item in auction toprow
                # item name is not information row, store in not found list
                if lead != 'Welcome':
                    notInAuction.append({
                        'lot': lot,
                        'sold': sold,
                        'lead': lead,
                        'bid': bid,
                        'reserve': reserve
                    })
                continue

            # pull toprow from item in auction record
            shelf = sanitizeString(item['shelfLocation'])
            sku = sanitizeNumber(item['sku'])
            
            # top row item sold
            if sold == 'S':
                newTopRowSold = {
                    'soldStatus': sold,
                    'bidAmount': bid,
                    'clotNumber': lot,
                    'sku': sku,
                    'lead': lead,
                    'reserve': reserve,
                    'shelfLocation': shelf,
                    'quantityInstock': 1           # ? Implement quantity check for top row ?
                }
                soldTopRow.append(newTopRowSold)
                # add top row to total bid amount
                totalBidAmount += bid
            # top row item not sold
            elif sold == 'NS':
                newTopRowUnsold = {
                    'lot': lot,
                    'sku': sku,
                    'lead': lead,
                    'msrp': sanitizeNumber(float(item['msrp'])) if 'msrp' in item else 0,
                    'shelfLocation': shelf,
                    'description': sanitizeString(row.get('shortdesc')),
                    'reserve': reserve,
                    'startBid': sanitizeNumber(float(item['startBid'])) if 'startBid' in item else 0,
                }
                unsoldTopRow.append(newTopRowUnsold)
        # bottom inventory
        elif len(targetAuctionItemsArr) > 0:
            try:
                # pull info from item in auction record
                item = findObjectInArray(targetAuctionItemsArr, 'lot', lot)
                shelf = sanitizeString(item['shelfLocation'])
                sku = sanitizeNumber(item['sku'])
                # store sku in all remaining sku array
                allRemainingSku.append(sku)
            except:
                # push into not in auction if lot number not found in auction record
                notInAuction.append({
                    'lot': lot,
                    'sold': sold,
                    'lead': lead,
                    'bid': bid
                })
                continue

            # find bottom item in instock database collection
            try:
                instock = instock_collection.find_one(
                    { 'sku': sku }, 
                    { '_id': 0 }
                )
                # get quantity for that item
                quantity = int(instock['quantityInstock'])
            except:
                # if not found in inventory, add it to error item, goto next row
                errorItems.append(item)
                continue
            if not instock or not quantity:
                errorItems.append(item)
                continue
            
            # if bottom item sold
            if sold == 'S':
                # construct sold item object
                soldItem = {
                    'soldStatus': sold,
                    'bidAmount': bid,
                    'clotNumber': lot,
                    'sku': sku,
                    'lead': lead,
                    'reserve': reserve,
                    'shelfLocation': shelf,
                    'quantityInstock': quantity
                }
                
                # if sold item in stock push into sold, if not push into error item
                if quantity > 0:
                    soldItems.append(soldItem)
                    totalBidAmount += bid
                else:
                    errorItems.append(soldItem)
                    
            # if not sold push into unsold array
            elif sold == 'NS':
                remainingItem = {
                    'lot': lot,
                    'sku': sku,
                    'lead': lead,
                    'msrp': sanitizeNumber(float(item['msrp'])) if 'msrp' in item else 0,
                    'shelfLocation': shelf,
                    'description': sanitizeString(row.get('shortdesc')),
                    'reserve': reserve,
                    'startBid': sanitizeNumber(float(item['startBid'])) if 'startBid' in item else 0,
                }
                unsoldItems.append(remainingItem)

    # make auction sku array
    auctionSkuList = [item['sku'] for item in targetAuctionItemsArr]
    
    # populate not in remaining XLS array
    notInRemaining = []
    for sku in auctionSkuList:
        if sku not in allRemainingSku:
            notInRemaining.append([x for x in targetAuctionItemsArr if x['sku'] == sku ][0])
    
    # construct remaining record info
    RemainingInfo = {
        'lot': lot_number,
        'totalItems': len(soldItems) + len(unsoldItems) + len(soldTopRow) + len(unsoldTopRow),
        'soldCount': len(soldItems),
        'unsoldCount': len(unsoldItems),
        'isProcessed': False,
        'timeClosed': getIsoFormatNow(),
        'soldItems': soldItems,
        'unsoldItems': unsoldItems,
        'errorItems': errorItems,
        'notInAuction': notInAuction,
        'notInRemaining': notInRemaining,
        'soldTopRow': soldTopRow,
        'unsoldTopRow': unsoldTopRow,
        'totalBidAmount': round(totalBidAmount, 2),
    }
    remaining_collection.insert_one(RemainingInfo)
    return Response('Remaining Record Created', status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsAdminPermission])
def deleteAuctionRecord(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        auctionLotNumber = sanitizeNumber(body['auctionLotNumber'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    res = auction_collection.delete_one({'lot': auctionLotNumber})
    if not res:
        return Response('Cannot Delete From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(f'Delete Auction {auctionLotNumber}', status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsAdminPermission])
def deleteRemainingRecord(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        remainingLotNumber = sanitizeNumber(float(body['remainingLotNumber']))
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    res = remaining_collection.delete_one({ 'lot': remainingLotNumber })
    if not res:
        return Response('Cannot Delete From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(f'Delete Remaining Record {remainingLotNumber}', status.HTTP_200_OK)

# remove item from auction' itemArr and adjust the lot number
@api_view(['DELETE'])
@permission_classes([IsAdminPermission])
def deleteItemInAuction(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        auctionLot = sanitizeNumber(body['auctionLot'])
        itemLot = sanitizeNumber(body['itemLot'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    deleted = auction_collection.update_one(
        { 'lot': auctionLot }, 
        {
            '$pull': {
                'itemsArr': { 'lot': itemLot }
            }, 
            '$inc': { 
                'totalItems': -1
            }
        }
    )
    if not deleted:
        return Response(f'Cannot Delete Item {itemLot}', status.HTTP_200_OK)
    return Response('Item Deleted', status.HTTP_200_OK)

@api_view(['PUT'])
@permission_classes([IsAdminPermission])
def updateItemInAuction(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        auctionLot = sanitizeNumber(int(body['auctionLot']))
        itemLot = sanitizeNumber(int(body['itemLot']))
        newItem = body['newItem']
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    updated = auction_collection.update_one(
        { 'lot': auctionLot },
        {
            '$set': {
                'itemsArr.$[elem].lot': sanitizeNumber(newItem['lot']) if 'lot' in newItem else '',
                'itemsArr.$[elem].msrp': sanitizeNumber(newItem['msrp']) if 'msrp' in newItem else '',
                'itemsArr.$[elem].lead': sanitizeString(newItem['lead']) if 'lead' in newItem else '',
                'itemsArr.$[elem].sku': sanitizeNumber(newItem['sku']) if 'sku' in newItem else '',
                'itemsArr.$[elem].shelfLocation': sanitizeString(newItem['shelfLocation']) if 'shelfLocation' in newItem else '',
                'itemsArr.$[elem].description': sanitizeString(newItem['description']) if 'description' in newItem else '',
                'itemsArr.$[elem].startBid': sanitizeNumber(newItem['startBid']) if 'startBid' in newItem else '',
                'itemsArr.$[elem].reserve': sanitizeNumber(newItem['reserve']) if 'reserve' in newItem else '',
            },
        },
        array_filters=[{ "elem.lot": itemLot }]
    )
    
    # sort by msrp
    auction_collection.update_one(
        { 'lot': auctionLot },
        {
            '$push': {
                'itemsArr': {
                    '$each': [],
                    '$sort': { 'msrp': -1 }
                }
            }
        }
    )
    if not updated:
        return Response(f'Cannot Update Item {itemLot} in Auction {auctionLot}', status.HTTP_200_OK)
    return Response(f'Updated Item {itemLot} in Auction {auctionLot}')

@api_view(['PUT'])
@permission_classes([IsAdminPermission])
def addSelectionToAuction(request: HttpRequest):
    fil = {}
    # try:
    body = decodeJSON(request.body)
    auctionLot = sanitizeNumber(int(body['auctionLot']))
    duplicate = sanitizeNumber(body['duplicate'])
    unpackInstockFilter(body['filter'], fil)
    # except:
    #     return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # check if auction record exist
    auction = auction_collection.find_one(
        {'lot': auctionLot}, 
        {'_id': 0}
    )
    if not auction:
        return Response(f'Auction {auctionLot} not Found', status.HTTP_404_NOT_FOUND)
    
    # determine gap between itemsArr and first item in first unsold object
    lastItemsLot = max(auction['itemsArr'], key=lambda x: x['lot'])['lot'] if 'itemsArr' in auction and len(auction['itemsArr']) > 0 else auction['itemLotStart']
    # print(f'Last Lot number in itemArr: {lastItemsLot}')
    firstUnsoldLot = auction['previousUnsoldArr'][0]['items'][0]['lot'] if 'previousUnsoldArr' in auction and len(auction['previousUnsoldArr']) > 0 else None
    # print(f'First Lot number in First Unsold Object: {firstUnsoldLot}')
    
    # check for unsold object
    if firstUnsoldLot != None:
        gap = firstUnsoldLot - lastItemsLot - 1
        # if no gap between bottom row and unsold, return error
        if gap == 0:
            return Response(f'No Gap Between Bottom Rows and Unsold Array', status.HTTP_400_BAD_REQUEST)
    
    # check gap between    
    # get all selected items
    itemsArr = []
    instock = instock_collection.find(
        fil, 
        {
            '_id': 0, 
            'sku': 1, 
            'lead': 1, 
            'msrp': 1, 
            'description': 1, 
            'shelfLocation': 1, 
            'condition': 1, 
            'quantityInstock': 1 
        }
    ).sort({ 'msrp': -1 })
    # populate instock array for selected items
    itemsArr = processInstock(itemsArr, instock, duplicate, auction['itemsArr'])
    instock.close()
    
    # count howmany items selected
    count = len(itemsArr)
    if firstUnsoldLot != None:
        # if selection have more item than gap
        if count > gap:
            return Response(f'Too Many Items ({count}) to Insert, Gap Size = {gap}', status.HTTP_400_BAD_REQUEST)

    # join old auction and new array
    newItemsArr = auction['itemsArr'] + itemsArr

    # sort by msrp desc
    newItemsArr = sorted(newItemsArr, key=lambda x: x['msrp'], reverse=True)

    # remove all lots for new lot index
    for item in newItemsArr:
        if 'lot' in item:
            item.pop('lot')

    # make index array from lot start in auction record
    indexArr = []
    start = auction['itemLotStart']
    for index in range(start, start + len(newItemsArr)):
        indexArr.append({ 'lot': index })
    newList = [{ **d1, **d2 } for d1, d2 in zip(indexArr, newItemsArr)]
    
    # set itemsArr: jointArr
    update = auction_collection.update_one(
        { 'lot': auctionLot }, 
        {
            '$set': {
                'itemsArr':newList,  
                'totalItems': len(newList)
            },
        },
    )

    # make index array and zip it with imported items
    # indexArr = []
    # lastLot = lastItemsLot + 1
    # for index in range(lastLot, lastLot + count):
    #     indexArr.append({ 'lot': index })
    # newList = [{ **d1, **d2 } for d1, d2 in zip(indexArr, itemsArr)]
    
    # sort the array by msrp
    # auction_collection.update_one(
    #     { 'lot': auctionLot },
    #     {
    #        '$push': {
    #            'itemsArr': {
    #                 '$each': [],
    #                 '$sort': { 'msrp': -1 }
    #            }
    #        }
    #     }
    # )
    if not update:
        return Response('Cannot Add Item to Auction', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(f'Selection Added to Auction {auctionLot}', status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getRemainingLotNumbers(request: HttpRequest):
    # grab remaining record if unsold items exist
    cursor = remaining_collection.find({}, { '_id': 0, 'lot': 1, 'unsoldCount': { '$gt': 0 }})
    res = cursor.distinct('lot')
    cursor.close()
    
    # pull all remaining lot
    arr = []
    for item in res:
        arr.append(item)
    arr.sort(reverse=True)
    return Response(arr, status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getAuctionLotNumbers(request: HttpRequest):
    # grab remaining record if unsold items exist
    cursor = auction_collection.find({}, { '_id': 0, 'lot': 1})
    res = cursor.distinct('lot')
    cursor.close()
    arr= []
    for item in res:
        arr.append(item)
    arr.sort(reverse=True)
    return Response(arr ,status.HTTP_200_OK)

# add unsold items to auction record 
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def importUnsoldItems(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    auctionLotNumber = sanitizeNumber(float(body['auctionLotNumber']))
    remainingLotNumber = sanitizeNumber(float(body['remainingLotNumber']))
    gapSize = sanitizeNumber(body['gapSize'])

    # find auction record without the unsold in the prev unsold array
    auction = auction_collection.find_one(
        {
            'lot': auctionLotNumber,
            'previousUnsoldArr': {
                '$not': {
                    "$elemMatch": { "lot": remainingLotNumber }
                }
            },
        },
        {
            '_id': 0,
            'totalItems': 1, 
            'itemsArr': 1,
            'previousUnsoldArr': 1
        }
    )
    # check for existing data
    if not auction:
        return Response('Already Imported', status.HTTP_409_CONFLICT)
    
    # check if unsold lots exist in this record
    # get largest lot number to start appending unsold items
    if ('previousUnsoldArr' not in auction or len(auction['previousUnsoldArr']) < 1):
        # find object with the largest lot value (bottom ones)
        lot_largest = max(auction['itemsArr'], key=lambda x: x["lot"]) if len(auction['itemsArr']) > 0 else {'lot': 100}
    else:
        # get the last object in unsold array
        arr = auction['previousUnsoldArr'][-1]['items']
        lot_largest = max(arr, key=lambda x: x['lot'])
    unsoldLotStart = lot_largest['lot'] + gapSize + 1
    
    # except:
    #     return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # get unsold items array from targeted remaining record
    remaining = remaining_collection.find_one(
        { 'lot': remainingLotNumber }, 
        {
            '_id': 0, 
            'unsoldItems': 1
        }
    )
    if not remaining:
        return Response('Remaining Record Not Found', status.HTTP_404_NOT_FOUND)
    
    # get all unsold items in an array
    remainingItemsArr = []
    resultArr = []
    for unsold in remaining['unsoldItems']:
        remainingItemsArr.append(unsold)
    # if no unsold items return not found
    if len(remainingItemsArr) < 1:
        return Response(f'No Unsold Items Found for Lot {remainingLotNumber}', status.HTTP_404_NOT_FOUND)
    
    # randomly sort the unsold items
    random.shuffle(remainingItemsArr)
    for unsold in remainingItemsArr:
        resultArr.append({**unsold, 'lot': unsoldLotStart})
        unsoldLotStart += 1

    # create new object in unsold array
    # set the total items count
    auction = auction_collection.find_one_and_update(
        { 
            'lot': auctionLotNumber,
        },
        {
            '$set': {
                'totalItems': auction['totalItems'] + len(remainingItemsArr)
            },
            '$push': {
                'previousUnsoldArr': { 'lot': remainingLotNumber, 'items': resultArr }
            }
        }
    )
    if not auction:
        return Response('Auction Not Found, Failed to Update', status.HTTP_404_NOT_FOUND)
    return Response(f'Unsold Items Imported to Auction {auctionLotNumber}', status.HTTP_200_OK)

# delete unsold items inside auction record
@api_view(['DELETE'])
@permission_classes([IsAdminPermission])
def deleteUnsoldItems(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        auctionLotNumber = sanitizeNumber(float(body['auctionLotNumber']))
        lotToDelete = sanitizeNumber(float(body['remainingLotNumber']))
        remaining = remaining_collection.find_one(
            {'lot': lotToDelete}, 
            {'_id': 0, 'unsoldCount': 1}
        )
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # unset the key value set
    auction = auction_collection.find_one_and_update(
        {'lot': auctionLotNumber},
        {
            '$pull': { 
                'previousUnsoldArr': {'lot': lotToDelete},
            },
            '$inc':{
                'totalItems': -(remaining['unsoldCount'])
            }
        }
    )
    if not auction:
        return Response('Cannot Delete Unsold from Auction', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(f'Deleted Remaining Lot {lotToDelete} In Auction {auctionLotNumber}', status.HTTP_200_OK)

# this will update sold items to database
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def auditRemainingRecord(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    lot = sanitizeNumber(body['remainingLotNumber'])
    # get sold and unsold and processed status
    remaining = remaining_collection.find_one(
        { 'lot': lot }, 
        { '_id': 0, 'soldItems': 1, 'unsoldItems': 1, 'isProcessed': 1 }
    )
    if not remaining:
        return Response('Remaining Record Not Found', status.HTTP_404_NOT_FOUND)
    if remaining['isProcessed'] == True:
        return Response('Remaining Record Already Processed', status.HTTP_409_CONFLICT)
    # except:
    #     return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)

    deducted = []
    outOfStock = []
    for soldItem in remaining['soldItems']:
        # sold in one array and out of stock in one array
        fil = { 'sku': soldItem['sku'] }
        res = instock_collection.find_one(fil, { '_id': 0, 'quantityInstock': 1 })
        
        # check if it is still instock
        if int(res['quantityInstock']) < 1:
            outOfStock.append(soldItem)
        else:
            res = instock_collection.update_one(
                fil,
                {
                    '$inc': {
                        'quantityInstock': -1,
                        'quantitySold': 1
                    }
                }
            )
            if res:
                deducted.append(soldItem)
            else:
                return Response(f'Cannot deduct {soldItem['sku']} from database', status.HTTP_500_INTERNAL_SERVER_ERROR)

    # append info to remaining record
    res = remaining_collection.update_one(
        { 'lot': lot }, 
        {
            '$set': {
                'deducted': deducted,
                'outOfStock': outOfStock,
                'isProcessed': True
            }
        }
    )
    return Response({'deducted': deducted, 'outOfStock': outOfStock}, status.HTTP_200_OK)


'''
Scraping stuff 
'''
# description: string
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def generateDescriptionBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        condition = sanitizeString(body['condition'])
        comment = sanitizeString(body['comment'])
        title = sanitizeString(body['title'])
        titleTemplate = sanitizeString(body['titleTemplate'])
        descTemplate = sanitizeString(body['descTemplate'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # call chat gpt to generate description
    lead = generate_title(title, titleTemplate)
    desc = generate_description(condition, comment, title, descTemplate)
    return Response({ 'lead': lead, 'desc': desc }, status.HTTP_200_OK)

# return info from amazon for given sku
# sku: string
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def scrapeInfoBySkuAmazon(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(int(body['sku']))
    except:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
    
    # find target inventory
    target = qa_collection.find_one({ 'sku': sku })
    if not target:
        return Response('No Such Inventory', status.HTTP_404_NOT_FOUND)

    # extract link with regex
    # return error if not amazon link or not http
    link = extract_urls(target['link'])
    if 'https' not in link and '.ca' not in link and '.com' not in link:
        return Response('Invalid URL', status.HTTP_400_BAD_REQUEST)
    elif 'a.co' not in link and 'amazon' not in link and 'amzn' not in link:
        return Response('Invalid URL, Not Amazon URL', status.HTTP_400_BAD_REQUEST)

    # get raw html and parse it with scrapy
    payload = {
        'title': '',
        'msrp': '',
        'imgUrl': '',
        'currency':''
    }
    
    # # request the raw html from Amazon
    # headers = {
    #     'User-Agent': f'user-agent={ua.random}',
    #     'Accept-Language': 'en-US,en;q=0.9',
    # }
    
    # rawHTML = requests.get(url=link, headers=headers).text
    # rawHTML = request_with_proxy(link).text
    response = request_with_proxy_admin(link)
    # response = HtmlResponse(url=link, body=rawHTML, encoding='utf-8')
        
    if 'Sorry, we just need to make sure you\'re not a robot' in str(response.body) or 'To discuss automated access to Amazon data please contact' in str(response.body):
        return Response('Blocked by Amazon bot detection', status.HTTP_502_BAD_GATEWAY)
    
    try:
        payload['title'] = getTitle(response)
    except:
        return Response('Failed to Get Title', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    try:
        payload['msrp'] = getMsrp(response)
    except:
        return Response('Failed to Get MSRP', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    try:
        payload['imgUrl'] = getImageUrl(response)
    except:
        return Response('No Image URL Found', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    try:
        payload['currency'] = getCurrency(response)
    except:
        return Response('No Currency Info Found', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(payload, status.HTTP_200_OK)

# return msrp from home depot for given sku
# sku: string
@api_view(['GET'])
@permission_classes([IsAdminPermission])
def scrapePriceBySkuHomeDepot(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(int(body['sku']))
    except:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
    
    # find target inventory
    target = qa_collection.find_one({ 'sku': sku })
    if not target:
        return Response('No Such Inventory', status.HTTP_404_NOT_FOUND)

    # check if url is home depot
    url = target['link']
    if 'homedepot' not in url or 'http' not in url:
        return Response('Invalid URL', status.HTTP_400_BAD_REQUEST)
    
    # extract url incase where the link includes title
    start_index = target['link'].find("https://")
    if start_index != -1:
        url = target['link'][start_index:]
        print("Extracted URL:", url)

    # generate header with random user agent
    headers = {
        'User-Agent': f'user-agent={ua.random}',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    # get raw html and parse it with scrapy
    # TODO: purchase and implement proxy service
    rawHTML = requests.get(url=url, headers=headers).text
    response = HtmlResponse(url=url, body=rawHTML, encoding='utf-8')
    
    # HD Canada className = hdca-product__description-pricing-price-value
    # HD Canada itemprop="price"
    # <span itemprop="price">44.98</span>
    # HD US className = ????

    # grab the fist span element encountered tagged with class 'a-price-whole' and extract the text
    price = response.selector.xpath('//span/text()').extract()
    # price = price[0].replace('$', '')
    
    if not price:
        return Response('No Result', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(price, status.HTTP_200_OK)


'''
Migration stuff 
'''
# instock record csv migrated from SQL processing to Mongo compatible csv
# removes row from csv result if sku existed in database
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def sendInstockCSV(request: HttpRequest):
    body = decodeJSON(request.body)
    path = body['path']

    # joint file location with relative path
    dirName = os.path.dirname(__file__)
    fileName = os.path.join(dirName, path)
    # parse csv to pandas data frame
    data = pd.read_csv(filepath_or_buffer=fileName)
    # indicies to remove after looping
    to_remove = []
    # loop pandas dataframe
    for index in data.index:
        # if time is malformed set to empty string
        if len(str(data['time'][index])) < 18 or '0000-00-00 00:00:00' in str(data['time'][index]):
            data.loc[index, 'time'] = ''
        else:
            # time convert to iso format
            # original: 2023-08-03 17:47:00
            # targeted: 2024-01-03T05:00:00.000
            time = datetime.strptime(str(data['time'][index]), "%Y-%m-%d %H:%M:%S").isoformat()
            data.loc[index, 'time'] = time.replace('T', ' ')
        
        # check url is http
        if 'http' not in str(data['url'][index]) or len(str(data['url'][index])) < 15 or '<' in str(data['url'][index]):
            data.loc[index, 'url'] = ''
        
        # condition
        condition = str(data['condition'][index]).title().strip()
        if 'A-B' in condition:
            data.loc[index, 'condition'] = 'A-B'
        elif 'API' in condition:
            data.loc[index, 'condition'] = 'New'
        elif 'NO MANUAL' in condition:
            data.loc[index, 'condition'] = 'New'
        else:
            # item condition set to capitalized
            data.loc[index, 'condition'] = condition
            
        # remove $ inside msrp price
        try:
            if 'NA' in str(data['msrp'][index]) or '***Need Price***' in str(data['msrp'][index]):
                data.loc[index, 'msrp'] = ''
            else:
                msrp = str(data['msrp'][index]).replace('$', '')
                msrp = msrp.replace(',', '')
                data.loc[index, 'msrp'] = float(msrp)
        except:
            data.loc[index, 'msrp'] = ''
        
        sku = int(data.loc[index, 'sku'])
        
        # update the instock quantity if sku found in database
        exist = instock_collection.find_one({'sku': sku})
        if exist:    
            quant = int(data.loc[index, 'quantityInstock'])
            res = instock_collection.find_one_and_update(
                { 'sku': sku },
                {
                    '$set': {
                        'quantityInstock': quant,
                    }
                }
            )
            if res:
                print(f'updated {sku} instock from {exist['quantityInstock']} to {quant}')
                to_remove.append(index)
        else:
            print(sku)
    
    # drop all existed rows
    data = data.drop(to_remove)
    # set output copy path
    data.to_csv(path_or_buf='./output.csv', encoding='utf-8', index=False)
    return Response(str(data), status.HTTP_200_OK)

# for qa record csv processing to mongo db
# detects and removes existing sku in QARecords
@api_view(['POST'])
@permission_classes([IsAdminPermission])
def sendQACSV(request: HttpRequest):
    body = decodeJSON(request.body)
    path = body['path']

    # joint file location with relative path
    dirName = os.path.dirname(__file__)
    fileName = os.path.join(dirName, path)
    
    # parse csv to pandas data frame
    data = pd.read_csv(filepath_or_buffer=fileName)
    
    existedSKU = []
    
    # loop pandas dataframe
    for index in data.index:
        res = qa_collection.find_one({'sku': int(data['sku'][index])})
        if res:
            existedSKU.append(data['sku'][index])
            print(f'{int(data['sku'][index])} exist in DB')
            continue
        else:
            print(f'{int(data['sku'][index])}')
        
        # time convert to iso format
        # original: 2023-08-03 17:47:00 OR 02/20/2024 11:42am
        # targeted: 2024-01-03T05:00:00.000   optional time zone: -05:00 (EST is -5)
        try:
            time = datetime.strptime(data['time'][index], "%m/%d/%Y %I:%M %p").isoformat()
        except:
            time = datetime.strptime(data['time'][index], "%m/%d/%Y %I:%M%p").isoformat()
        data.loc[index, 'time'] = time
        
        # remove all html tags
        # if link containes '<'
        if '<' in data['link'][index]:
            cleanLink = BeautifulSoup(data['link'][index], "lxml").text
            data.loc[index, 'link'] = cleanLink
        
        # item condition set to capitalized
        condition = str(data['itemCondition'][index]).title()
        data.loc[index, 'itemCondition'] = condition
        
        # platform other capitalize
        if data['platform'][index] == 'other':
            data.loc[index, 'platform'] = 'Other'

    # drop existed sku
    filtered_df = data[~data['sku'].isin(existedSKU)]

    # set output copy path
    filtered_df.to_csv(path_or_buf='./output.csv', encoding='utf-8', index=False)
    return Response(str(data), status.HTTP_200_OK)

@api_view(['POST'])
@permission_classes([IsAdminPermission])
def fillPlatform(request: HttpRequest):
    # find
    # myquery = {
    #    '$nor': [
    #        {'url': {"$regex": "ebay"}}, 
    #        {'url': {"$regex": "homedepot"}}, 
    #        {'url': {"$regex": "amazon"}}, 
    #        {'url': {"$regex": "a.co"}}, 
    #        {'url': {"$regex": "amzn"}}, 
    #        {'url': {"$regex": "ebay"}}, 
    #        {'url': {"$regex": "aliexpress"}},
    #        {'url': {"$regex": "walmart"}}
    #     ], 
    # }

    # # set
    # newvalues = { "$set": { "platform": "Other" }}
    # res = instock_collection.update_many(myquery, newvalues)
    
    # replace T in time string
    res = instock_collection.find({'time': {'$regex': 'T'}})
    for item in res:
        time = item['time'].replace('T', ' ')
        res = instock_collection.update_one(
            {'sku': item['sku'], 'time': item['time']},
            {
                '$set': { 'time': time }
            }
        )
        if res:
            print(time)
    res.close()
    return Response('Platform Filled', status.HTTP_200_OK)

# for database fixing
@api_view(['POST'])
def fixAuctionRecord(request: HttpRequest):
    # auction = auction_collection.find_one(
    #     {'lot': 212},
    #     {'_id': 0}
    # )
    lotArr = [
        805,
        806,
        807,
        811,
        812,
        813,
        814,
        815,
        819,
        822,
        825,
        826,
        845,
        846,
        847,
        848,
        849,
        850,
        851,
        852,
        853,
        855,
        858,
        859,
        867,
        868,
        869,
        870,
        885,
        886,
        887,
        888,
        890,
        891,
        893,
        895,
        899,
        907,
        913,
        914,
        915,
        916,
        917,
        918,
        919,
        924,
        925,
        926,
        928,
        930,
        936,
        937,
        946,
        947,
        948,
        949,
        953,
        954,
        956,
        959,
        960,
        961,
        964,
        965,
        970,
        972,
        973,
        974,
        975,
        976,
        977,
        978,
        980,
        985,
        986,
        987,
        988,
        989,
        991,
        993,
        994,
        995,
        996,
        999,
        1000,
        1032,
        1033,
        1034,
        1035,
        1036,
        1037,
        1044,
        1047,
        1060,
        1061,
        1063,
        1064,
        1109,
        1110,
        1111,
        1112,
        1113,
        1114,
        1115,
        1116,
        1117,
        1118,
        1119,
        1120,
        1121,
        1122,
        1123,
        1124,
        1125,
        1126,
        1127,
        1128,
        1129,
        1130,
        1131,
        1132,
        1133,
        1134,
        1135,
        1144,
        1145,
        1146,
        1149,
        1155,
        1156,
        1157,
        1158,
        1162,
        1170,
        1171,
        1173,
        1175,
        1176,
        1179,
        1180,
        1184,
        1187,
        1191,
        1193,
        1194,
        1205,
        1206,
        1207,
        1208,
        1216,
        1217,
        1219,
        1230,
        1231,
        1233,
        1235,
    ]
    skuArrFull = [
        7320,
        7381,
        7329,
        7380,
        6879,
        8043,
        7051,
        7323,
        7322,
        7325,
        7318,
        7327,
        6878,
        7691,
        7044,
        6875,
        7319,
        7874,
        8100,
        7030,
        8232,
        7378,
        7375,
        7830,
        7043,
        7047,
        7062,
        7050,
        7871,
        7869,
        8449,
        8041,
        8778,
        7935,
        7866,
        7933,
        7690,
        7057,
        7040,
        7311,
        7317,
        7688,
        7923,
        7032,
        7055,
        7665,
        7944,
        7924,
        7879,
        7448,
        8157,
        7674,
        8102,
        7439,
        7312,
        8311,
        7911,
        7889,
        7393,
        7675,
        7848,
        7596,
        8054,
        8294,
        8155,
        6876,
        7597,
        7052,
        7053,
        7054,
        7056,
        7379,
        7061,
        7326,
        7065,
        9013,
        8103,
        7880,
        8235,
        8779,
        8807,
        8452,
        7033,
        7618,
        7664,
        8044,
        7564,
        8042,
        7714,
        8163,
        9015,
        7600,
        6877,
        8050,
        7617,
        8290,
        8800,
        7663,
        7833,
        7859,
        7934,
        7505,
        7506,
        8165,
        7314,
        7725,
        7870,
        8289,
        7560,
        8040,
        8451,
        8047,
        8291,
        7568,
        7613,
        7861,
        9016,
        7504,
        7847,
        7846,
        7478,
        7477,
        7598,
        8316,
        8317,
        7510,
        7585,
        7586,
        7587,
        7588,
        7589,
        7316,
        7968,
        7969,
        7426,
        7429,
        7497,
        7715,
        7385,
        7328,
        7526,
        8815,
        7035,
        7060,
        8131,
        7907,
        7324,
        7417,
        7444,
        7445,
        7966,
        7887,
        7865,
        7739,
        7882,
        8295,
        8048,
        8146,
        7515,
        7893,
        7850,
        8286,
        7841,
        7729,
        7425,
        6547,
        8450,
        7624,
        8193,
        7034,
        8812,
        8313,
        8282,
        7972,
        7971,
        8167,
        7594,
        8046,
        7720,
        7670,
        7313,
        7451,
        7936,
        7854,
        8814,
        7894,
        7831,
        7906,
        7837,
        7844,
        7031,
        8140,
        8156,
        8130,
        7901,
        8143,
        7735,
        8775,
        7063,
        7528,
        8141,
        8233,
        7908,
        7629,
        8273,
        7856,
        7028,
        7041,
        7712,
        7499,
        7989,
        7514,
        7721,
        7730,
        7392,
        7549,
        7524,
        7512,
        7027,
        7310,
        7489,
        7488,
        7474,
        7473,
        7428,
        7427,
        7516,
        7892,
        8312,
        7970,
        8142,
        8194,
        7449,
        8099,
        8315,
        7918,
        8777,
        7621,
        9011,
        7898,
        7731,
        8090,
        8093,
        8104,
        7391,
        7590,
        7396,
        7711,
        7470,
        7547,
        7555,
        8318,
        8319,
        7863,
        7666,
        7067,
        8049,
        7839,
        7834,
        8106,
        8181,
        7580,
        7518,
        8810,
        8283,
        7370,
        7628,
        7967,
        8114,
        7546,
        7076,
        7912,
        7394,
        7418,
        7421,
        7443,
        8279,
        7565,
        7525,
        7419,
        8149,
        8115,
        8116,
        7961,
        7962,
        7964,
        7965,
        7963,
        7059,
        7468,
        7447,
        8147,
        7728,
        7612,
        7668,
        7538,
        7046,
        7466,
        7465,
        7592,
        7069,
        7066,
        8770,
        7554,
        7734,
        7584,
        8148,
        8276,
        9012,
        7543,
        7042,
        8278,
        7058,
        8805,
        7851,
        7872,
        7899,
        7070,
        8091,
        7462,
        7469,
        7563,
        7389,
        7672,
        8162,
        8107,
        7461,
        7460,
        8097,
        7595,
        7838,
        7667,
        8275,
        8129,
        7886,
        7885,
        7884,
        7576,
        7566,
        7562,
        7615,
        8195,
        7315,
        7374,
        9018,
        8808,
        7855,
        7398,
        7593,
        7974,
        8120,
        7496,
        7495,
        8145,
        7494,
        7723,
        8234,
        7726,
        8123,
        7620,
        7868,
        7867,
        8285,
        7919,
        7491,
        7492,
        7661,
        8292,
        8271,
        8811,
        8169,
        8161,
        7395,
        7078,
        7072,
        7399,
        7548,
        7436,
        7321,
        8284,
        8280,
        7888,
        8801,
        8287,
        7558,
        7559,
        7864,
        7359,
        7357,
        7356,
        7355,
        8144,
        7535,
        7845,
        7905,
        7873,
        7358,
        7414,
        7415,
        7416,
        7420,
        7455,
        7456,
        7457,
        7458,
        7459,
        7983,
        7413,
        7534,
        7533,
        7607,
        8809,
        7382,
        7579,
        7619,
        8293,
        8096,
        7937,
        7849,
        7853,
        7878,
        7843,
        7945,
        8184,
        7523,
        7897,
        8571,
        8570,
        7452,
        8813,
        8105,
        8098,
        8134,
        8159,
        7412,
        8199,
        7438,
        7603,
        7581,
        8189,
        7434,
        7435,
        7432,
        7433,
        7521,
        7852,
        8806,
        8453,
        7561,
        7717,
        8056,
        7424,
        7476,
        7500,
        7501,
        7502,
        7503,
        7623,
        7036,
        7440,
        7373,
        7902,
        7860,
        8118,
        8802,
        7556,
        7049,
        7376,
        7883,
        8281,
        8272,
        7536,
        7939,
        7940,
        7896,
        7377,
        8183,
        8198,
        7531,
        7716,
        7973,
        8168,
        7605,
        8288,
        7383,
        7835,
        7387,
        7738,
        7539,
        7877,
        7941,
        8124,
        7423,
        8197,
        7545,
        8122,
        8819,
        8182,
        7532,
        7687,
        7572,
        7616,
        7857,
        8125,
        8277,
        8095,
        8108,
        7074,
        7064,
        7917,
        8192,
        7858,
        8816,
        7519,
        7544,
        7569,
        7574,
        7475,
        7938,
        7075,
        8052,
        8045,
        7490,
        7446,
        7582,
        7977,
        7571,
        7570,
        8774,
        8817,
        7073,
        7371,
        7513,
        7875,
        9014,
        7430,
        7832,
        8053,
        7575,
        8771,
        7622,
        7891,
        7985,
        7471,
        7472,
        7484,
        7909,
        7915,
        8818,
        7039,
        7614,
        7540,
        7552,
        7573,
        7736,
        7719,
        7727,
        8055,
        7431,
        7450,
        7453,
        7454,
        7480,
        7481,
        7482,
        7483,
        7671,
        7498,
        7975,
        7976,
        8132,
        8196,
        8314,
        8772,
        8051,
        7984,
        7037,
        7925,
        8180,
        7606,
        7583,
        7511,
        6548,
        9017,
        8299,
        7517,
        8776,
        8094,
        7550,
        7551,
        7591,
        7553,
        7921,
        7732,
        7397,
        7390,
        7578,
        7386,
        7384,
        7557,
        7542,
        7541,
        8110,
        7077,
        7025,
        7024,
        7842,
        7836,
        6549,
        7903,
        7900,
        7895,
        7942,
        7916,
        7913,
        7733,
        7599,
        8117,
        7960,
        7509,
        7508,
        7507,
        7673,
        7479,
        7467,
        7442,
        7354,
        7353,
        7352,
        7351,
        7718,
        7713,
        7710,
        7737,
        7602,
        7422,
        7724,
        7529,
        7029,
        8773,
        7464,
        7441,
        7493,
        8121,
        7038,
        8190,
        7045,
        7627,
        7626,
        8133,
        7463,
        8101,
        8298,
        8296,
        7522,
        7876,
        7910,
        7862,
        7026,
        6546,
        7722,
        8803,
        7669,
        7920,
        7914,
        8297,
        8119,
        7610,
        8164,
        7372,
        7486,
        7485,
        7487,
        7048,
        8158,
        7520,
        7068,
        7904,
        7881,
        7922,
        7071,
        8191,
        8274,
        7609,
        7530,
        8092,
        7079,
        8160,
        7527,
        7662,
        7943,
        7601,
        7840,
        8166,
        8109,
        7625,
        7537,
        7577,
        7611,
        7437,
        8154,
        8804,
        7608,
        7676,
        8606,
        9049,
        9048,
        8137,
        8981,
        9142,
        9024,
        8702,
        8995,
        8967,
        8601,
        8889,
        9073,
        9060,
        8581,
        8552,
        8542,
        8521,
        8677,
        8674,
        8608,
        8994,
        8558,
        8019,
        8673,
        8989,
        9061,
        8138,
        9059,
        9041,
        9025,
        8671,
        8966,
        9144,
        9058,
        8557,
        9021,
        8973,
        8826,
        8825,
        9052,
        9051,
        9050,
        8721,
        9038,
        8872,
        8522,
        9000,
        9032,
        9075,
        8717,
        8993,
        8503,
        8502,
        8706,
        8678,
        8711,
        8695,
        8554,
        8603,
        8599,
        8513,
        8969,
        9030,
        8237,
        8729,
        8623,
        8622,
        8621,
        8520,
        8519,
        8518,
        8629,
        9152,
        9035,
        8701,
        8700,
        8526,
        8821,
        9045,
        9002,
        9128,
        8867,
        9068,
        8597,
        8988,
        8987,
        8965,
        8577,
        9034,
        8718,
        8549,
        8556,
        9153,
        9072,
        8716,
        8724,
        9070,
        8616,
        8992,
        8978,
        8625,
        8624,
        8618,
        8617,
        8593,
        8592,
        8589,
        9127,
        9125,
        8881,
        8545,
        8544,
        8543,
        8540,
        8539,
        8538,
        8537,
        8525,
        8524,
        8523,
        9149,
        9044,
        8707,
        8548,
        8035,
        8034,
        9047,
        8878,
        8877,
        8560,
        8979,
        8670,
        8829,
        8555,
        9146,
        9067,
        8876,
        9126,
        8696,
        8025,
        8976,
        9124,
        8977,
        8968,
        9031,
        8712,
        9057,
        8689,
        8723,
        8705,
        9036,
        8971,
        8703,
        9143,
        8567,
    ]
    skuArr = [
        8606,
        9049,
        9048,
        8137,
        8981,
        9142,
        9024,
        8702,
        8995,
        8967,
        8601,
        8889,
        9073,
        9060,
        8581,
        8552,
        8542,
        8521,
        8677,
        8674,
        8608,
        8994,
        8558,
        8019,
        8673,
        8989,
        9061,
        8138,
        9059,
        9041,
        9025,
        8671,
        8966,
        9144,
        9058,
        8557,
        9021,
        8973,
        8826,
        8825,
        9052,
        9051,
        9050,
        8721,
        9038,
        8872,
        8522,
        9000,
        9032,
        9075,
        8717,
        8993,
        8503,
        8502,
        8706,
        8678,
        8711,
        8695,
        8554,
        8603,
        8599,
        8513,
        8969,
        9030,
        8237,
        8729,
        8623,
        8622,
        8621,
        8520,
        8519,
        8518,
        8629,
        9152,
        9035,
        8701,
        8700,
        8526,
        8821,
        9045,
        9002,
        9128,
        8867,
        9068,
        8597,
        8988,
        8987,
        8965,
        8577,
        9034,
        8718,
        8549,
        8556,
        9153,
        9072,
        8716,
        8724,
        9070,
        8616,
        8992,
        8978,
        8625,
        8624,
        8618,
        8617,
        8593,
        8592,
        8589,
        9127,
        9125,
        8881,
        8545,
        8544,
        8543,
        8540,
        8539,
        8538,
        8537,
        8525,
        8524,
        8523,
        9149,
        9044,
        8707,
        8548,
        8035,
        8034,
        9047,
        8878,
        8877,
        8560,
        8979,
        8670,
        8829,
        8555,
        9146,
        9067,
        8876,
        9126,
        8696,
        8025,
        8976,
        9124,
        8977,
        8968,
        9031,
        8712,
        9057,
        8689,
        8723,
        8705,
        9036,
        8971,
        8703,
        9143,
        8567,
    ]
    lotArrFull = [
        100,
        101,
        103,
        102,
        104,
        105,
        106,
        107,
        108,
        109,
        110,
        111,
        112,
        113,
        114,
        115,
        116,
        117,
        118,
        119,
        120,
        121,
        122,
        123,
        127,
        126,
        124,
        125,
        128,
        129,
        130,
        131,
        132,
        133,
        134,
        135,
        136,
        137,
        138,
        139,
        140,
        141,
        142,
        143,
        144,
        145,
        148,
        147,
        146,
        149,
        150,
        151,
        152,
        153,
        154,
        155,
        159,
        158,
        157,
        156,
        160,
        161,
        162,
        163,
        164,
        165,
        166,
        169,
        168,
        167,
        170,
        171,
        172,
        173,
        174,
        175,
        176,
        177,
        179,
        184,
        183,
        182,
        181,
        180,
        178,
        185,
        186,
        187,
        188,
        189,
        190,
        191,
        192,
        193,
        194,
        195,
        196,
        197,
        198,
        199,
        200,
        202,
        201,
        203,
        204,
        205,
        206,
        207,
        208,
        209,
        210,
        211,
        214,
        212,
        213,
        215,
        216,
        217,
        218,
        219,
        220,
        221,
        222,
        230,
        229,
        228,
        227,
        226,
        225,
        224,
        223,
        231,
        233,
        232,
        235,
        234,
        236,
        237,
        238,
        239,
        240,
        241,
        242,
        243,
        244,
        250,
        249,
        248,
        247,
        246,
        245,
        251,
        252,
        253,
        255,
        256,
        254,
        257,
        258,
        259,
        264,
        263,
        262,
        261,
        260,
        265,
        266,
        267,
        268,
        269,
        270,
        271,
        272,
        273,
        274,
        275,
        278,
        276,
        277,
        279,
        280,
        282,
        293,
        292,
        291,
        290,
        289,
        288,
        287,
        286,
        285,
        284,
        283,
        281,
        294,
        295,
        296,
        301,
        300,
        299,
        297,
        298,
        302,
        303,
        310,
        309,
        308,
        307,
        306,
        305,
        304,
        311,
        312,
        313,
        314,
        315,
        316,
        317,
        318,
        319,
        321,
        322,
        323,
        324,
        325,
        326,
        327,
        328,
        329,
        320,
        330,
        331,
        332,
        333,
        334,
        335,
        336,
        337,
        349,
        348,
        340,
        347,
        346,
        345,
        343,
        342,
        341,
        339,
        338,
        344,
        350,
        352,
        351,
        353,
        354,
        355,
        356,
        357,
        358,
        359,
        360,
        361,
        362,
        363,
        364,
        366,
        365,
        367,
        368,
        369,
        370,
        371,
        372,
        373,
        375,
        374,
        376,
        377,
        378,
        379,
        380,
        387,
        386,
        385,
        384,
        382,
        381,
        383,
        388,
        389,
        390,
        391,
        392,
        393,
        394,
        395,
        396,
        397,
        398,
        399,
        400,
        401,
        402,
        403,
        404,
        406,
        405,
        410,
        409,
        407,
        408,
        411,
        412,
        413,
        418,
        420,
        419,
        417,
        416,
        415,
        414,
        421,
        422,
        423,
        424,
        425,
        426,
        427,
        428,
        429,
        430,
        431,
        432,
        433,
        434,
        435,
        436,
        448,
        449,
        450,
        451,
        452,
        453,
        454,
        455,
        456,
        457,
        446,
        447,
        437,
        438,
        439,
        440,
        442,
        441,
        445,
        443,
        444,
        458,
        459,
        460,
        461,
        462,
        463,
        464,
        465,
        466,
        467,
        468,
        469,
        470,
        471,
        472,
        476,
        477,
        474,
        475,
        473,
        478,
        479,
        480,
        481,
        482,
        483,
        484,
        485,
        507,
        497,
        499,
        500,
        501,
        502,
        503,
        504,
        505,
        506,
        498,
        495,
        494,
        493,
        492,
        491,
        490,
        489,
        488,
        487,
        486,
        496,
        509,
        510,
        508,
        511,
        512,
        513,
        514,
        515,
        518,
        525,
        524,
        523,
        522,
        520,
        519,
        517,
        516,
        521,
        526,
        527,
        528,
        529,
        530,
        531,
        532,
        533,
        534,
        535,
        536,
        537,
        538,
        539,
        541,
        540,
        543,
        542,
        553,
        556,
        555,
        554,
        552,
        551,
        550,
        549,
        548,
        547,
        546,
        545,
        544,
        557,
        558,
        559,
        560,
        561,
        562,
        563,
        565,
        564,
        566,
        567,
        568,
        569,
        570,
        573,
        580,
        579,
        578,
        577,
        576,
        575,
        574,
        572,
        571,
        581,
        582,
        583,
        584,
        585,
        586,
        587,
        588,
        589,
        590,
        591,
        592,
        593,
        594,
        595,
        598,
        597,
        596,
        599,
        600,
        601,
        602,
        603,
        604,
        605,
        606,
        607,
        608,
        609,
        615,
        617,
        616,
        614,
        613,
        612,
        611,
        610,
        618,
        619,
        620,
        621,
        622,
        623,
        625,
        624,
        626,
        627,
        628,
        629,
        630,
        631,
        632,
        635,
        634,
        633,
        636,
        637,
        638,
        639,
        640,
        641,
        642,
        644,
        643,
        650,
        670,
        669,
        668,
        667,
        666,
        665,
        664,
        663,
        662,
        661,
        660,
        659,
        658,
        657,
        656,
        655,
        654,
        653,
        652,
        651,
        649,
        648,
        647,
        646,
        645,
        671,
        673,
        672,
        674,
        675,
        676,
        677,
        678,
        679,
        680,
        681,
        682,
        683,
        684,
        688,
        690,
        689,
        687,
        686,
        685,
        691,
        692,
        714,
        715,
        716,
        717,
        718,
        719,
        720,
        721,
        722,
        723,
        724,
        725,
        726,
        727,
        728,
        729,
        730,
        731,
        732,
        733,
        734,
        735,
        712,
        713,
        693,
        694,
        695,
        696,
        697,
        698,
        699,
        700,
        701,
        703,
        704,
        705,
        706,
        707,
        708,
        709,
        710,
        711,
        702,
        736,
        737,
        738,
        739,
        742,
        743,
        741,
        740,
        744,
        745,
        746,
        747,
        748,
        749,
        750,
        751,
        752,
        753,
        754,
        755,
        760,
        759,
        757,
        758,
        756,
        761,
        762,
        763,
        764,
        765,
        766,
        767,
        768,
        769,
        771,
        772,
        770,
        773,
        774,
        775,
        776,
        777,
        778,
        779,
        780,
        781,
        783,
        782,
        784,
        785,
        786,
        787,
        788,
        789,
        790,
        791,
        792,
        793,
        797,
        796,
        795,
        794,
        798,
        799,
        800,
        801,
        802,
        803,
        805,
        806,
        807,
        811,
        812,
        813,
        814,
        815,
        819,
        822,
        825,
        826,
        845,
        846,
        847,
        848,
        849,
        850,
        851,
        852,
        853,
        855,
        858,
        859,
        867,
        868,
        869,
        870,
        885,
        886,
        887,
        888,
        890,
        891,
        893,
        895,
        899,
        907,
        913,
        914,
        915,
        916,
        917,
        918,
        919,
        924,
        925,
        926,
        928,
        930,
        936,
        937,
        946,
        947,
        948,
        949,
        953,
        954,
        956,
        959,
        960,
        961,
        964,
        965,
        970,
        972,
        973,
        974,
        975,
        976,
        977,
        978,
        980,
        985,
        986,
        987,
        988,
        989,
        991,
        993,
        994,
        995,
        996,
        999,
        1000,
        1032,
        1033,
        1034,
        1035,
        1036,
        1037,
        1044,
        1047,
        1060,
        1061,
        1063,
        1064,
        1109,
        1110,
        1111,
        1112,
        1113,
        1114,
        1115,
        1116,
        1117,
        1118,
        1119,
        1120,
        1121,
        1122,
        1123,
        1124,
        1125,
        1126,
        1127,
        1128,
        1129,
        1130,
        1131,
        1132,
        1133,
        1134,
        1135,
        1144,
        1145,
        1146,
        1149,
        1155,
        1156,
        1157,
        1158,
        1162,
        1170,
        1171,
        1173,
        1175,
        1176,
        1179,
        1180,
        1184,
        1187,
        1191,
        1193,
        1194,
        1205,
        1206,
        1207,
        1208,
        1216,
        1217,
        1219,
        1230,
        1231,
        1233,
        1235,
    ]
    
    lot213Sku = [
        8699,
        8013,
        8012,
        8011,
        8010,
        8009,
        8008,
        8007,
        8006,
        8004,
        8003,
        8002,
        8001,
        8000,
        7999,
        7998,
        8005,
        8580,
        8579,
        8578,
        8135,
        8014,
        8720,
        8865,
        8864,
        9042,
        9040,
        8824,
        8607,
        8562,
        8561,
        8884,
        9120,
        8611,
        8610,
        8136,
        8559,
        7988,
        7987,
        7986,
        9132,
        8890,
        8713,
        8679,
        8529,
        9043,
        8719,
        8681,
        8827,
        9063,
        8883,
        8885,
        8685,
        8031,
        8682,
        8873,
        8714,
        8563,
        8605,
        9039,
        8680,
        9006,
        9028,
        9003,
        9010,
        9053,
        8871,
        8964,
        8996,
        8974,
        8863,
        8985,
        8501,
        8500,
        8037,
        8036,
        9156,
        8018,
        8726,
        8038,
        8861,
        8531,
        8032,
        7996,
        7995,
        7994,
        7993,
        7992,
        7991,
        7990,
        8874,
        8823,
        9078,
        8517,
        9023,
        8882,
        8868,
        8870,
        8970,
        8575,
        8573,
        8572,
        8564,
        8016,
        8015,
        8030,
        8029,
        8017,
        8574,
        8698,
        8728,
        8983,
        9019,
        8697,
        8687,
        9064,
        8886,
        8828,
        9062,
        9007,
        9020,
        8505,
        8504,
        8866,
        8568,
        8566,
        8033,
        9130,
        8688,
        8684,
        8676,
        9009,
        8023,
        8022,
        8725,
        8609,
        8600,
        8694,
        8527,
        8975,
        8541,
        8530,
        8516,
        8515,
        9066,
        8615,
        9129,
        8972,
        9141,
        8991,
        8582,
        8715,
        8822,
        8962,
        8614,
        8613,
        9148,
        9140,
        8709,
        8528,
        8690,
        8612,
        8591,
        8590,
        9056,
        8722,
        8595,
        8594,
        8569,
        8514,
        8021,
        8020,
        8683,
        8598,
        8550,
        8547,
        9079,
        9027,
        9076,
        8239,
        8869,
        7997,
        9147,
        9065,
        9055,
        8536,
        9145,
        9033,
        8708,
        8727,
        9026,
        9004,
        8692,
        8691,
        8628,
        8627,
        8588,
        8587,
        8586,
        8585,
        8584,
        8583,
        8546,
        8710,
        8686,
        9054,
        8604,
        8980,
        9121,
        8887,
        8879,
        9069,
        9037,
        8672,
        9008,
        8139,
        9151,
        9150,
        8999,
        8986,
        8963,
        8961,
        8596,
        8875,
        8553,
        8551,
        8675,
        9133,
        8270,
        9046,
        8576,
        8693,
        9077,
        8862,
        8626,
        8602,
        8024,
        9005,
        8236,
        9154,
        9074,
        9122,
        8997,
        8880,
        9022,
        9071,
        9001,
        9029,
        8998,
        8533,
        8535,
        8534,
        8532,
        8512,
        8039,
        8028,
        8027,
        8026,
        8511,
        8510,
        8509,
        8508,
        8507,
        8506,
        9155,
        8620,
        8619,
        9157,
        8984,
        8990,
        8238,
        8982,
        9123,
        8565,
        9131,
        8704,

    ]
    
    # trying to fix lot 213.01
    index = 2606
    lot213Lot = []
    for x in range(len(lot213Sku)):
        lot213Lot.append(index)
        index += 1
    print(lot213Lot)
    # return Response('succeess', status.HTTP_200_OK)

    lotObjArr = []
    for i in lot213Lot:
        lotObjArr.append({'lot': i})
    
    columns = [
        'Lot',
        'Lead',        # original lead from recording
        'Description', # original description from recording
        'MSRP:$',      
        'Price',       # original scraped msrp  
        'Location',    # original shelfLocation
        'item',
        'vendor',
        'start bid',
        'reserve',
        'Est',
    ]

    # get all instock inventory
    auctionItemArr = []
    for sku in lot213Sku:
        res = instock_collection.find_one(
            {'sku': sku}, 
            { '_id': 0, 'sku': 1, 'lead': 1, 'msrp': 1, 'description': 1, 'shelfLocation': 1, 'condition': 1, 'quantityInstock': 1 }
        )
        auctionItemArr.append(res)
    
    # zip lot number and item array
    itemsArr = []
    itemsArr = processInstock(itemsArr, auctionItemArr, duplicate=False)
    itemsArr = [{ **d1, **d2 } for d1, d2 in zip(lotObjArr, itemsArr)]
    # pprint.pprint(itemsArr)
    
    auction_collection.update_one(
        {'lot': 213.02},
        {'$set': {'itemsArr': itemsArr}}
    )
    
    return Response('Fixed', status.HTTP_200_OK)
    # make image array and joint it with item array
    imageArrData = []
    itemsArrData = []
    image_container_client = getImageContainerClient()
    for item in itemsArr:
        row = makeCSVRowFromItem(item)
        itemsArrData.append(row)
        # build blob filter tag 
        sku = f"sku = '{item['sku']}'" 
        # get blob list by tag
        blob_list = image_container_client.find_blobs_by_tags(filter_expression=sku)
        # all images names by auction lot 
        images = []
        # get images count per item
        imageCount = sum(1 for _ in blob_list)
        # imageCount = 0
        # for _ in blob_list:
        #     imageCount += 1
        # item lot number in auction
        itemLot = sanitizeNumber(item['lot'])
        for x in range(imageCount):
            name = f"{itemLot}_{x + 1}.jpg"  # image name starts with lot_1.jpg
            images.append(name)
        imageArrData.append(images)
    image_container_client.close()
    
    # construct data frame for top row + items 
    item_df = pd.DataFrame(
        data=itemsArrData,
        columns=columns
    )
    
    # create df for images
    image_df = pd.DataFrame(imageArrData)
    
    # add empty column head for image columns to make space at the end
    col = len(image_df.columns)
    for x in range(col):
        columns.append('')
    
    # outer joins the image part of csv
    joined_df = item_df.join(image_df, how='outer')
    
    # export csv
    csv = joined_df.to_csv(index=False, header=columns)
    response = Response(csv, status=status.HTTP_200_OK, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="shelfSheet.csv"'
    return response
    return Response('fixed', status.HTTP_200_OK)
    # auctionArr = []
    # for item in auction['itemsArr']:
    #     auctionArr.append(item)
    #     # print(item['sku'])
    #     sku = item['sku']
    #     resItem = instock_collection.find_one({'sku': item['sku']})
    #     # if (resItem['quantitySold'] > 0):
    #     #     res = instock_collection.update_one(
    #     #         {'sku': item['sku']},
    #     #         {
    #     #             '$inc': {
    #     #                 'quantityInstock': 1,
    #     #                 'quantitySold': -1
    #     #             }
    #     #         }
    #     #     )
    #     #     print(f'{resItem['sku']} {resItem}')
        
    #     # if resItem['quantityInstock'] > 1 or resItem['quantityInstock'] < 0:
    #     print(f'{sku} - instock: {resItem['quantityInstock']}')
    #     print(f'{sku} - sold: {resItem['quantitySold']}')
    
    # return Response('', status.HTTP_200_OK)
    
