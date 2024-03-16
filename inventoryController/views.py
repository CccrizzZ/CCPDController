import csv
from ctypes import Array
import os
from urllib import response
from uu import decode
from django.http import HttpRequest
from numpy import NaN
import requests
from scrapy.http import HtmlResponse
from datetime import datetime, timedelta
from inventoryController.models import AuctionItem, AuctionRecord, InstockInventory, InventoryItem
from CCPDController.scrape_utils import extract_urls, getCurrency, getImageUrl, getMsrp, getTitle
from CCPDController.utils import (
    convertToAmountPerDayData, decodeJSON, 
    get_db_client, 
    getIsoFormatInv, 
    getNDayBeforeToday, getTodayTimeRangeFil, 
    populateSetData, 
    sanitizeNumber, 
    sanitizeSku, 
    convertToTime, 
    getIsoFormatNow, 
    qa_inventory_db_name, 
    getIsoFormatNow, 
    sanitizeString,
    full_iso_format,
    findObjectInArray,
    getBidReserve,
    product_image_container_client,
    inv_iso_format
)
from CCPDController.permissions import IsQAPermission, IsAdminPermission, IsSuperAdminPermission
from CCPDController.authentication import JWTAuthentication
from rest_framework.decorators import api_view, permission_classes, authentication_classes
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
vendor_name = 'B0000'
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
remaining_collection = db['RemainingHistory']
ua = UserAgent()


'''
QA Inventory stuff
'''
# query param sku for inventory db row
# sku: string
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
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
@authentication_classes([JWTAuthentication])
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
@authentication_classes([JWTAuthentication])
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
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission | IsAdminPermission])
def getInventoryByOwnerName(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    name = sanitizeString(body['ownerName'])
    currPage = sanitizeNumber(body['page'])
    # except:
    #     return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
        
    # get all qa inventory
    # default item per page is 10
    skip = currPage * 10
    res = qa_collection.find({ 'ownerName': name }, { '_id': 0 }).sort('sku', pymongo.DESCENDING).skip(skip).limit(10)
    if not res:
        return Response('No Inventory Found', status.HTTP_200_OK)
    
    # make array of items
    arr = []
    for item in res:
        arr.append(item)
 
    return Response(arr, status.HTTP_200_OK)

# get all qa inventory condition stats for graph by qa name
# ownerName: string
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission | IsAdminPermission])
def getQAConditionInfoByOwnerName(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    name = sanitizeString(body['ownerName'])
    # except:
    #     return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    arr = []
    # array of all inventory
    condition = []
    con = qa_collection.find({ 'ownerName': name }, { 'itemCondition': 1 })
    if not con:
        return Response('No Inventory Found', status.HTTP_204_NO_CONTENT)
    for inventory in con:
        arr.append(inventory['itemCondition'])
    
    # make data object for charts
    itemCount = Counter()
    for condition in arr:
        itemCount[condition] += 1   
    return Response(dict(itemCount))

# create single inventory Q&A record
@api_view(['PUT'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission | IsAdminPermission])
def createInventory(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeSku(body['sku'])
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
        res = qa_collection.insert_one(newInventory.__dict__)
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
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission | IsAdminPermission])
def updateInventoryBySku(request: HttpRequest, sku: str):
    try:
        # convert to object id
        body = decodeJSON(request.body)
        sku = sanitizeSku(int(sku))
        if not sku:
            return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
        
        # check body
        newInv = body['newInventoryInfo']
        newInventory = InventoryItem(
            time = newInv['time'],
            sku = newInv['sku'],
            itemCondition = newInv['itemCondition'],
            comment = newInv['comment'],
            link = newInv['link'],
            platform = newInv['platform'],
            shelfLocation = newInv['shelfLocation'],
            amount = newInv['amount'],
            owner =  newInv['owner'] if 'owner' in newInv else '',
            ownerName = newInv['ownerName'],
            marketplace = newInv['marketplace'] if 'marketplace' in newInv else ''
        )
    except:
        return Response('Invalid Inventory Info', status.HTTP_406_NOT_ACCEPTABLE)
    
    # check if inventory exists
    oldInv = qa_collection.find_one({ 'sku': sku })
    if not oldInv:
        return Response('Inventory Not Found', status.HTTP_404_NOT_FOUND)
    
    # update inventory
    res = qa_collection.update_one(
        { 'sku': sku },
        {
            '$set': 
            {
                'sku': newInventory.sku,
                'amount': newInventory.amount,
                'itemCondition': newInventory.itemCondition,
                'platform': newInventory.platform,
                'shelfLocation': newInventory.shelfLocation,
                'comment': newInventory.comment,
                'link': newInventory.link,
                'marketplace': newInventory.marketplace
            }
        }
    )
    
    # return update status 
    if not res:
        return Response('Update Failed', status.HTTP_404_NOT_FOUND)
    return Response('Update Success', status.HTTP_200_OK)

# delete inventory by sku
# QA personal can only delete record created within 24h
# sku: string
@api_view(['DELETE'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission])
def deleteInventoryBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeSku(body['sku'])
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
    todayTimestamp = datetime.timestamp(datetime.now())
    
    two_days = 175000
    canDel = (todayTimestamp- createdTimestamp) < two_days
    print(todayTimestamp)
    print(createdTimestamp)
    print(todayTimestamp - createdTimestamp)
    print(two_days)
    print(canDel)
    
    
    # perform deletion or throw error
    if canDel:
        qa_collection.delete_one({'sku': sku})
        return Response('Inventory Deleted', status.HTTP_200_OK)
    return Response('Cannot Delete Inventory After 24H, Please Contact Admin', status.HTTP_403_FORBIDDEN)

# get all QA shelf location
@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def getAllQAShelfLocations(request: HttpRequest):
    try:
        arr = qa_collection.distinct('shelfLocation')
    except:
        return Response('Cannot Fetch From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(arr, status.HTTP_200_OK)

# get all qa record today plus 7 days prior's record
@api_view(['GET'])
@authentication_classes([JWTAuthentication])
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
                'time': getTodayTimeRangeFil(x), 
                'ownerName': owner
            }))
            times = datetime.now() - timedelta(days=x)
            if len(dates) < days:
                dates.append(f'{times.month}/{times.day}')
        res.append({owner: counts})
    return Response({'res': res, 'dates': dates})

# get todays shelf location sheet by user name
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission])
def getShelfSheetByUser(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        owner = body['ownerName']
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # get from db
    res = qa_collection.find({'ownerName': owner, 'time': getTodayTimeRangeFil()}, {'_id': 0, 'sku': 1, 'shelfLocation': 1, 'amount': 1, 'ownerName': 1, 'time': 1})
    if not res:
        return Response('No Record Found', status.HTTP_200_OK)
    arr = []
    for item in res:
        arr.append(item)
    return Response(arr, status.HTTP_200_OK)

# get end of the day shelf location sheet for all records submitted today
@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def getAllShelfSheet(request: HttpRequest):
    con = { 'time': getTodayTimeRangeFil() }
    res = qa_collection.find(con, {'_id': 0, 'sku': 1, 'shelfLocation': 1, 'amount': 1, 'ownerName': 1, 'time': 1}).sort('shelfLocation', pymongo.ASCENDING)
    if not res:
        return Response('No Record Found', status.HTTP_204_NO_CONTENT)

    arr = []
    for item in res:
        arr.append(item)
    if len(arr) < 1:
        return Response('No Records Found', status.HTTP_204_NO_CONTENT)
    
    # mongo data array to pandas dataframe
    resData = pd.DataFrame(
        arr,
        columns=['sku', 'shelfLocation', 'amount', 'ownerName', 'time'],
    )
    csv = resData.to_csv(index=False)
    response = Response(csv, status=status.HTTP_200_OK, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="shelfSheet.csv"'
    return response


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
@authentication_classes([JWTAuthentication])
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
        query = instock_collection.find().sort('sku', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
        count = instock_collection.count_documents({})
    else:
        query = instock_collection.find(fil).sort('sku', pymongo.DESCENDING).skip(skip).limit(body['itemsPerPage'])
        count = instock_collection.count_documents(fil)

    # get rid of object id
    for inventory in query:
        inventory['_id'] = str(inventory['_id'])
        arr.append(inventory)
    
    # if pulled array empty return no content
    if len(arr) == 0:
        return Response([], status.HTTP_200_OK)

    # make and return chart data
    res = instock_collection.find({'time': {'$gte': getNDayBeforeToday(10)}}, {'_id': 0})
    chart_arr = []
    for item in res:
        chart_arr.append(item)
    output = convertToAmountPerDayData(chart_arr)
    # except:
    #     return Response(chart_arr, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response({ "arr": arr, "count": count, "chartData": output }, status.HTTP_200_OK)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
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
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def updateInstockBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeSku(body['sku'])
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
@authentication_classes([JWTAuthentication])
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
    return Response('Instock Inventory Deleted', status.HTTP_200_OK)

# get all in-stock shelf location
@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def getAllShelfLocations(request: HttpRequest):
    try:
        arr = instock_collection.distinct('shelfLocation')
    except:
        return Response('Cannot Fetch From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(arr, status.HTTP_200_OK)

# converts qa record to inventory
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def createInstockInventory(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        sku = sanitizeNumber(body['sku'])
        res = instock_collection.find_one({'sku': sku})
        if res:
            return Response(f'Inventory {sku} Already Instock', status.HTTP_409_CONFLICT)
        msrp = sanitizeNumber(body['msrp']) if 'msrp' in body else ''
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

    try:
        instock_collection.insert_one(newInv.__dict__)
    except:
        return Response('Cannot Add to Database', status.HTTP_500_INTERNAL_SERVER_ERROR)

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
@authentication_classes([JWTAuthentication])
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
@authentication_classes([JWTAuthentication])
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
    
    # process the top row array
    topRow = []
    if 'topRow' in record:
        topRowArr = record['topRow']
        for item in topRowArr:
            if 'msrp' in item:
                msrp = float(sanitizeNumber(item['msrp']))
            else:
                msrp = ''
            if 'description' in item:
                if msrp != '' and msrp < 80:
                    desc = desc_under_80 + ' '+ sanitizeString(item['description'])
                else:
                    desc = sanitizeString(item['description'])
            else: 
                desc = ''
            if 'lead' in item:
                lead = sanitizeString(item['lead'])
            else:
                lead = ''
            if 'startBid' in item:
                startBid = sanitizeNumber(item['startBid'])
            else:
                startBid = ''
            if 'reserve' in item:
                reserve = sanitizeNumber(item['reserve'])
            else:
                reserve = ''
        
            row = {
                'Lot': sanitizeNumber(item['lot']), 
                'Lead': lead,
                'Description': desc,
                'MSRP:$': 'MSRP:$',
                'Price': msrp,
                'Location': sanitizeString(item['shelfLocation']),
                'item': sanitizeNumber(item['sku']),
                'vendor': vendor_name,
                'start bid': startBid,
                'reserve': reserve,
                'Est': msrp,
            }
            topRow.append(row)
    
    # make inventory csv rows
    itemsArrData = []
    imageArrData = []
    itemsArr = record['itemsArr']
    for item in itemsArr:
        # get float msrp
        if 'msrp' in item:
            msrp = float(sanitizeNumber(item['msrp']))
        else:
            msrp = ''
            
        # description adjusted according to msrp
        if 'description' in item:
            if msrp != '' and msrp < 80:
                desc = desc_under_80 + ' '+ sanitizeString(item['description'])
            else:
                desc = sanitizeString(item['description'])
        else: 
            desc = ''
        
        # get title
        if 'lead' in item:
            lead = sanitizeString(item['lead'])
        else:
            lead = ''
        
        sku = sanitizeNumber(item['sku'])
        itemLot = sanitizeNumber(item['lot'])
        # create csv row
        row = {
            'Lot': itemLot, 
            'Lead': lead,
            'Description': desc,
            'MSRP:$': 'MSRP:$',
            'Price': msrp,
            'Location': sanitizeString(item['shelfLocation']),
            'item': sku,
            'vendor': vendor_name,
            'start bid': default_start_bid,
            'reserve': reserve_default,
            'Est': msrp,
        }
        itemsArrData.append(row)
        
        # populate photo names array
        sku = f"sku = '{sku}'" 
        blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=sku)
        imageCount = sum(1 for _ in blob_list)
        images = []
        for x in range(imageCount):
            name = f"{itemLot}_{x + 1}.jpg"  # image name starts with lot_1.jpg
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
        data=(topRow + itemsArrData),
        columns=columns
    )
    
    # create df for images
    image_df = pd.DataFrame(imageArrData)
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
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def getAuctionRemainingRecord(request: HttpRequest):
    # get everything
    # TODO: make it paged
    res = auction_collection.find({}, { '_id': 0 }).sort({ 'lot': -1 })
    auctions = []
    for item in res:
        auctions.append(item)
    res = remaining_collection.find({}, { '_id': 0 }).sort({ 'lot': -1 })
    remaining = []
    for item in res:
        remaining.append(item)
    return Response({'auctions': auctions, 'remaining': remaining}, status.HTTP_200_OK)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
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
            '$push': {
                'topRow': newTopRowItem.__dict__
            }
        }
    )
    if not res:
        return Response('Cannot Insert Top Row Item', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Item Inserted', status.HTTP_200_OK)

@api_view(['DELETE'])
@authentication_classes([JWTAuthentication])
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
            '$pull': { 'topRow': { 'sku': sku, 'lot': itemLotNum }}
        }
    )

    if not res:
        return Response('Cannot Delete Item', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response('Item Deleted', status.HTTP_200_OK)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def createAuctionRecord(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    lot = sanitizeNumber(body['lot'])
    exist = auction_collection.find_one({'lot': lot})
    if exist:
        return Response('Lot Exist', status.HTTP_409_CONFLICT)
    itemLotStart = sanitizeNumber(body['itemLotStart'])
    
    # construct auction record fields
    endDate = sanitizeString(body['endDate'])
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
    itemsArr = []
    res = instock_collection.find(fil, { '_id': 0, 'sku': 1, 'lead': 1, 'msrp': 1, 'description': 1, 'shelfLocation': 1, 'condition': 1 }).sort({ 'msrp': -1 })
    count = instock_collection.count_documents(fil)
    for item in res:
        priceObj = getBidReserve(
            item['description'] if 'description' in item else '', 
            item['msrp'] if 'msrp' in item else 0, 
            item['condition'] if 'condition' in item else 'New'
        )
        if 'startBid' in item and 'reserve' in item:
            priceObj = {
                'reserve': sanitizeNumber(item['reserve']),
                'startBid': sanitizeNumber(item['startBid'])
            }
        
        itemsArr.append({
            **item, 
            'startBid': priceObj['startBid'] if 'startBid' not in item else item['startBid'], 
            'reserve': priceObj['reserve'] if 'reserve' not in item else item['reserve'],
        }) # start bid and reserve is calculated at getBidReserveEst

    # append item lot number inside
    itemLotNumbersArr = []
    for x in range(itemLotStart, itemLotStart + count + 1):
        itemLotNumbersArr.append({'lot': x})
    merged_list = [{**d1, **d2} for d1, d2 in zip(itemLotNumbersArr, itemsArr)]

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
    )
    try: 
        # create auction record in mongo db
        res = auction_collection.insert_one({**auctionRecord.__dict__, 'itemsArr': merged_list})
        if not res:
            return Response('Cannot Push To DB', status.HTTP_500_INTERNAL_SERVER_ERROR)
    except: 
        return Response('Cannot Push To DB', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(f'Auction Record {lot} Created', status.HTTP_200_OK)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
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

# default remaining sheet is XLS
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def processRemaining(request: HttpRequest):
    try:
        # destruct from formData
        lot_number = sanitizeNumber(int(request.data.get('lot')))
        res = remaining_collection.find_one({'lot': lot_number})
        if res:
            return Response('Remaining Record Existed', status.HTTP_409_CONFLICT)
        
        # get itemArr in auction record
        targetAuctionItemsArr = auction_collection.find_one({'lot': lot_number})['itemsArr']
        if not targetAuctionItemsArr:
            return Response(f'Auction {lot_number} Not Found', status.HTTP_404_NOT_FOUND)
        
        # get xls file from request (hibid default exports xls)
        xls = request.FILES.get('xls')
        if not xls: 
            return Response('No File Uploaded', status.HTTP_400_BAD_REQUEST)
        
        # convert it into pandas dataframe
        df = pd.DataFrame(pd.read_excel(xls))
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # grab all neccesary columns from remaining xls (df)
    soldItems = []
    unsoldItems = []
    for index, row in df.iterrows():
        row = row.to_dict()
        try:
            # lot number might not be int, could be '1a' '1f' 'ff'
            lot = sanitizeNumber(int(row.get('clotnum')))
            # find item in matching auction record
            item = findObjectInArray(targetAuctionItemsArr, 'lot', lot)
        except:
            # item not found or lot number not integer
            continue
        
        # TODO: Add bid amount to retail manager as sells record 
        # sanitize input
        bid = sanitizeNumber(float(row.get('bidamount')))
        sold = sanitizeString(row.get('soldstatus'))
        lead = sanitizeString(row.get('lead'))
        sku = sanitizeNumber(item['sku'])
        reserve = sanitizeNumber(float(item['reserve']))
        shelf = sanitizeString(item['shelfLocation'])
        
        # determin if it is sold or not
        if sold == 'S' and bid > 0:
            soldItem = {
                'soldStatus': sold,
                'bidAmount': bid,
                'clotNumber': lot,
                'sku': sku,
                'lead': lead,
                'reserve': reserve,
                'shelfLocation': shelf
            }
            soldItems.append(soldItem)
            
            # TODO: create retail record
        elif sold == 'NS':
            remainingItem = {
                'lot': lot,
                'sku': sku,
                'lead': lead,
                'msrp': sanitizeNumber(float(item['msrp'])),
                'shelfLocation': shelf,
                'description': sanitizeString(row.get('shortdesc')),
                'reserve': reserve,
                'startBid': sanitizeNumber(float(item['startBid']))
            }
            unsoldItems.append(remainingItem)

    # construct remaining info
    RemainingInfo = {
        'lot': lot_number,
        'totalItems': len(soldItems) + len(unsoldItems),
        'soldCount': len(soldItems),
        'unsoldCount': len(unsoldItems),
        'soldItems': soldItems,
        'unsoldItems': unsoldItems,
        'timeClosed': getIsoFormatNow(),
    }
    remaining_collection.insert_one(RemainingInfo)
    # construct csv and send to front end
    # csv = df.to_csv(index=False)
    # response = Response(csv, status=status.HTTP_200_OK, content_type='text/csv')
    # response['Content-Disposition'] = 'attachment; filename="shelfSheet.csv"'
    return Response('Remaining Record Created', status.HTTP_200_OK)

@api_view(['DELETE'])
@authentication_classes([JWTAuthentication])
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
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def deleteRemainingRecord(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        remainingLotNumber = sanitizeNumber(body['remainingLotNumber'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    res = remaining_collection.delete_one({ 'lot': remainingLotNumber })
    if not res:
        return Response('Cannot Delete From Database', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(f'Delete Remaining Record {remainingLotNumber}', status.HTTP_200_OK)

@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def getRemainingLotNumbers(request: HttpRequest):
    # grab remaining record if unsold items exist
    res = remaining_collection.find({}, { '_id': 0, 'lot': 1, 'unsoldCount': { '$gt': 0 }}).distinct('lot')
    arr= []
    for item in res:
        arr.append(item)
    return Response(arr, status.HTTP_200_OK)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def importUnsoldItems(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        auctionLotNumber = sanitizeNumber(int(body['auctionLotNumber']))
        remainingLotNumber = sanitizeNumber(int(body['remainingLotNumber']))
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # look for remaining record
    remainingItems = remaining_collection.find({ 'lot': remainingLotNumber }, {'_id': 0, 'unsoldItems': 1})
    if not remainingItems:
        return Response('Remaining Record Not Found', status.HTTP_404_NOT_FOUND)
    remainingItemsArr = []
    for item in remainingItems:
        for i in item['unsoldItems']:
            remainingItemsArr.append(i)
    if len(remainingItemsArr) < 1:
        return Response('No Unsold Items Found', status.HTTP_404_NOT_FOUND)
    
    # random sort unsold
    random.shuffle(remainingItemsArr)
    
    # look for auction record
    # and insert previous remaining record into the 
    auction = auction_collection.find_one_and_update(
        { 'lot': auctionLotNumber },
        {
            '$set': {
                f'previousUnsoldArr.{remainingLotNumber}': remainingItemsArr
            }
        }
    )
    if not auction:
        return Response('Auction Not Found, Failed to Update', status.HTTP_404_NOT_FOUND)
    return Response(f'Unsold Items Imported to Auction {auctionLotNumber}', status.HTTP_200_OK)


'''
Scraping stuff 
'''
# description: string
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def generateDescriptionBySku(request: HttpRequest):
    try:
        body = decodeJSON(request.body)
        condition = sanitizeString(body['condition'])
        comment = sanitizeString(body['comment'])
        title = sanitizeString(body['title'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # call chat gpt to generate description
    lead = generate_title(title)
    desc = generate_description(condition, comment, title)
    return Response({ 'lead': lead, 'desc': desc }, status.HTTP_200_OK)

# return info from amazon for given sku
# sku: string
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
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
    if 'a.co' not in link and 'amazon' not in link and 'amzn' not in link:
        return Response('Invalid URL, Not Amazon URL', status.HTTP_400_BAD_REQUEST)
    
    # generate header with random user agent
    headers = {
        'User-Agent': f'user-agent={ua.random}',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    # get raw html and parse it with scrapy
    # TODO: use 10 proxy service to incraese scraping speed
    payload = {
        'title': '',
        'msrp': '',
        'imgUrl': '',
        'currency':''
    }
    
    # request the raw html from Amazon
    rawHTML = requests.get(url=link, headers=headers).text
    response = HtmlResponse(url=link, body=rawHTML, encoding='utf-8')
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
@authentication_classes([JWTAuthentication])
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
# for instock record csv processing to mongo db
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def sendInstockCSV(request: HttpRequest):
    body = decodeJSON(request.body)
    path = body['path']

    # joint file location with relative path
    dirName = os.path.dirname(__file__)
    fileName = os.path.join(dirName, path)
    # parse csv to pandas data frame
    data = pd.read_csv(filepath_or_buffer=fileName)
    
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
            data.loc[index, 'time'] = time
        
        # check url is http
        if 'http' not in str(data['url'][index]) or len(str(data['url'][index])) < 15 or '<' in str(data['url'][index]):
            data.loc[index, 'url'] = ''
        
        condition = str(data['condition'][index]).title().strip()
        
        # condition
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

    # set output copy path
    data.to_csv(path_or_buf='./output.csv', encoding='utf-8', index=False)
    return Response(str(data), status.HTTP_200_OK)

# for qa record csv processing to mongo db
# detects and removes existing sku in QARecords
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
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
            continue
        
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
    # data.drop(index=existedSKU, inplace=True)
    filtered_df = data[~data['sku'].isin(existedSKU)]

    # set output copy path
    filtered_df.to_csv(path_or_buf='./output.csv', encoding='utf-8', index=False)
    return Response(str(data), status.HTTP_200_OK)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
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