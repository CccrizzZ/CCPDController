from email.mime import image
import io
from operator import index, le
import os
import pprint
from re import L
from django.http import HttpRequest
from numpy import sort
import requests
import scrapy
from scrapy.http import HtmlResponse
from datetime import datetime, timedelta
import xlrd
from CCPDController.proxy_request import request_with_proxy
from inventoryController.models import AuctionItem, AuctionRecord, InstockInventory, InventoryItem
from CCPDController.scrape_utils import extract_urls, getCurrency, getImageUrl, getMsrp, getTitle
from CCPDController.utils import (
    convertToAmountPerDayData, decodeJSON, 
    get_db_client, getBlobTimeString, 
    getIsoFormatInv, 
    getNDayBeforeToday, getTimeRangeFil, makeCSVRowFromItem, 
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
    product_image_container_client,
    inv_iso_format,
    processInstock,
    azure_blob_client
)
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

# append this in front of description for item msrp lte 80$
desc_under_80 = 'READ NEW TERMS OF USE BEFORE YOU BID!'
default_start_bid = 5
default_start_bid_mystery_box = 5
aliexpress_mystery_box_closing = 25
reserve_default = 0

# pymongo
db = get_db_client()
qa_collection = db[qa_inventory_db_name]
instock_collection = db['InstockInventory']
user_collection = db['User']
auction_collection = db['AuctionHistory']
restock_collection = db['RestockRecords']
remaining_collection = db['RemainingHistory']
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
    for inventory in qa_collection.find({ 'owner': ownerId }).sort('sku', pymongo.DESCENDING).skip(skip).limit(limit):
        inventory['_id'] = str(inventory['_id'])
        arr.append(inventory)
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
    res = qa_collection.find({ 'ownerName': name }, { '_id': 0 }).sort('time', pymongo.DESCENDING).skip(skip).limit(10)
    if not res:
        return Response('No Inventory Found', status.HTTP_200_OK)
    
    # make array of items
    arr = []
    for item in res:
        arr.append(item)
 
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
    
    # populate date keys first, to include days with zero inventory
    all7Dates = [(datetime.fromisoformat(startTime) + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(8)]
    past7DaysCounter = Counter({date: 0 for date in all7Dates})
    
    # count the results
    for item in past7DaysArr:
        date = datetime.fromisoformat(item['time']).strftime('%Y-%m-%d')
        past7DaysCounter[date] += 1
    return Response({'pieData': dict(itemCount), 'barData': dict(past7DaysCounter)}, status.HTTP_200_OK)

# create single inventory Q&A record
@api_view(['PUT'])
@permission_classes([IsQAPermission | IsAdminPermission])
def createInventory(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(body['sku'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)

    # if sku exist return conflict
    inv = qa_collection.find_one({'sku': body['sku']})
    if inv:
        return Response('SKU Already Existed', status.HTTP_409_CONFLICT)
    try:
        # construct new inventory
        newInventory = InventoryItem(
            time = getIsoFormatNow(),
            sku = sku,
            itemCondition = body['itemCondition'],
            comment = body['comment'],
            link = body['link'],
            platform = body['platform'],
            shelfLocation = body['shelfLocation'],
            amount = body['amount'],
            owner = body['owner'],
            ownerName = body['ownerName'],
            marketplace = body['marketplace']
        )
        # pymongo need dict or bson object
        qa_collection.insert_one(newInventory.__dict__)
    except:
        return Response('Invalid Inventory Information', status.HTTP_400_BAD_REQUEST)
    return Response('Inventory Created', status.HTTP_200_OK)

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
        # check if blob with that sku exist
        queryTag = f"sku = '{newSku}'" 
        target_blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=queryTag)
        if sum(1 for _ in target_blob_list) > 0:
            return Response('Target Blob Exist', status.HTTP_409_CONFLICT)
        
        # update blob tags (rename)
        queryTag = f"sku = '{sku}'" 
        blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=queryTag)
        newTag = {}
        
        # copy the blobs to new sku destination
        for item in blob_list:
            source_blob = azure_blob_client.get_blob_client(container='product-image', blob=item.name)
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
            destination_blob_client = azure_blob_client.get_blob_client(container='product-image', blob=newBlobName)
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
        sku = sanitizeSku(body['sku'])
        time = sanitizeString(body['time'])
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
        
        # list blob by sku
        tag_filter = "sku = '" + str(sku) + "'"
        blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=tag_filter)

        # delete each blob 
        try:
            for blob in blob_list:
                blob_name = blob.name
                product_image_container_client.delete_blob(blob_name)
        except:
            return Response('Failed to Delete', status.HTTP_500_INTERNAL_SERVER_ERROR)
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
    owners = qa_collection.find({
        'time': {
            '$gte': time.replace(hour=0, minute=0, second=0, microsecond=0).strftime(full_iso_format),
            '$lt': datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999).strftime(full_iso_format)        
        }
    }).distinct('ownerName')

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
    blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=sku)
    newTime = getBlobTimeString()
    for item in blob_list:
        blob_client = azure_blob_client.get_blob_client(container='product-image', blob=item.name)
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

    # try:
    arr = []
    skip = body['page'] * body['itemsPerPage']
    
    # see if filter is applied to determine the query
    if fil == {}:
        query = instock_collection.find().sort('time', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
        count = instock_collection.count_documents({})
    else:
        query = instock_collection.find(fil).sort('time', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
        count = instock_collection.count_documents(fil)

    # get rid of object id
    for inventory in query:
        inventory['_id'] = str(inventory['_id'])
        arr.append(inventory)
    
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
    for item in itemsArr:
        row = makeCSVRowFromItem(item)
        itemsArrData.append(row)
        # build blob filter tag 
        sku = f"sku = '{item['sku']}'" 
        # get blob list by tag
        blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=sku)
        # all images names by auction lot 
        images = []
        # get images count per item
        imageCount = sum(1 for _ in blob_list)
        # item lot number in auction
        itemLot = sanitizeNumber(item['lot'])
        for x in range(imageCount):
            name = f"{itemLot}_{x + 1}.jpg"  # image name starts with lot_1.jpg
            images.append(name)
        imageArrData.append(images)
    
    # make array for previously unsold
    allUnsoldArr = []
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
            # list all blob name for each sku
            blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=sku)
            # count image and push them into array
            count = sum(1 for _ in blob_list)
            for x in range(count):
                name = f"{itemLot}_{x + 1}.jpg"
                images.append(name)
            imageArrData.append(images)
    
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
    res = remaining_collection.find({}, { '_id': 0 }).sort({ 'timeClosed': -1 })
    remaining = []
    for item in res:
        remaining.append(item)
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
    if len(targetAuctionUnsold) > 0:
        for obj in targetAuctionUnsold:
            for unsold in obj['items']:
                allUnsold.append(unsold)

    # append all the unsold into bottom row
    targetAuctionItemsArr = targetAuctionItemsArr + allUnsold
    
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
        else:
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
    res = remaining_collection.find({}, { '_id': 0, 'lot': 1, 'unsoldCount': { '$gt': 0 }}).distinct('lot')
    arr= []
    for item in res:
        arr.append(item)
    arr.sort(reverse=True)
    return Response(arr, status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAdminPermission])
def getAuctionLotNumbers(request: HttpRequest):
    # grab remaining record if unsold items exist
    res = auction_collection.find({}, { '_id': 0, 'lot': 1}).distinct('lot')
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
    
    # check for unsold sets
    if 'previousUnsoldArr' not in auction or len(auction['previousUnsoldArr']) < 1:
        # find object with the largest lot value (bottom ones)
        largest = max(auction['itemsArr'], key=lambda x: x["lot"])
    else:
        # get the last object in unsold array
        arr = auction['previousUnsoldArr'][-1]['items']
        largest = max(arr, key=lambda x: x['lot'])
    unsoldLotStart = largest['lot'] + gapSize + 1
    
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
    print(remaining)
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
    
    # webdriver
    # res = webDriverGet(link)
    
    # request the raw html from Amazon
    # rawHTML = requests.get(url=link, headers=headers).text
    rawHTML = request_with_proxy(link).text
    response = HtmlResponse(url=link, body=rawHTML, encoding='utf-8')
     
        
    if 'Sorry, we just need to make sure you\'re not a robot' in str(response.body) or 'To discuss automated access to Amazon data please contact' in str(response.body):
        return Response('Blocked by Amazon bot detection', status.HTTP_502_BAD_GATEWAY)
    
    # try:
    payload['title'] = getTitle(response)
    # except:
    #     return Response('Failed to Get Title', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # try:
    payload['msrp'] = getMsrp(response)
    # except:
    #     return Response('Failed to Get MSRP', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # try:
    payload['imgUrl'] = getImageUrl(response)
    # except:
    #     return Response('No Image URL Found', status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # try:
    payload['currency'] = getCurrency(response)
    # except:
    #     return Response('No Currency Info Found', status.HTTP_500_INTERNAL_SERVER_ERROR)
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
    return Response('Platform Filled', status.HTTP_200_OK)