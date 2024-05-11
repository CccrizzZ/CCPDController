import os
import io
import re
from django.http import HttpRequest
import requests
import pillow_heif
from PIL import Image
from azure.core.exceptions import ResourceExistsError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from CCPDController.utils import (
    decodeJSON, 
    getNDayBefore, 
    sanitizeNumber, 
    sanitizeString, 
    getBlobTimeString, 
    get_db_client,
    product_image_container_client,
)
from CCPDController.authentication import JWTAuthentication
from CCPDController.permissions import IsQAPermission, IsAdminPermission
from dotenv import load_dotenv
from urllib import parse
load_dotenv()
from django.views.decorators.cache import never_cache

# Mongo DB
db = get_db_client()
qa_collection = db['Inventory']

# return array of all image url from owner
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission | IsAdminPermission])
def getUrlsByOwner(request):
    try:
        body = decodeJSON(request.body)
        sanitizeString(body['ownerName'])
    except:
        return Response('Invalid Owner', status.HTTP_400_BAD_REQUEST)
    
    # format: 2024-02-06
    # filter image created within 2 days
    time = f"\"time\">='{getNDayBefore(2, getBlobTimeString())}'"
    # search by owner ID
    # search by owner name
    owner = "\"ownerName\"='" + body['ownerName'] + "'"
    query = owner + " AND " + time
    
    # collection of blobs
    blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=query)
    
    arr = []
    for blob in blob_list:
        blob_client = product_image_container_client.get_blob_client(blob.name)
        arr.append(blob_client.url)

    if len(arr) < 1:
        return Response('No Images Found for Owner', status.HTTP_200_OK)
    return Response(arr, status.HTTP_200_OK)

# sku: str
# returns an array of image uri (for public access)
@never_cache
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def getUrlsBySku(request):
    try:
        body = decodeJSON(request.body)
        sanitizeNumber(int(body['sku']))
    except:
        return Response('Invalid SKU', status.HTTP_400_BAD_REQUEST)
    sku = "sku = '" + body['sku'] + "'"
    blob_list = product_image_container_client.find_blobs_by_tags(filter_expression=sku)
    
    arr = []
    for blob in blob_list:
        blob_client = product_image_container_client.get_blob_client(blob.name)
        arr.append(blob_client.url)
        
    if len(arr) < 1:
        return Response('No images found for sku', status.HTTP_404_NOT_FOUND)
    return Response(arr, status.HTTP_200_OK)

# single image upload
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission | IsAdminPermission])
def uploadImage(request, ownerId, owner, sku):
    # request body content type is file form therefore only binary data allowed
    # sku will be in the path parameter
    # request.FILES looks like this and is a multi-value dictionary
    # {
    #     'IMG_20231110_150642.jpg': [<InMemoryUploadedFile: IMG_20231110_150642.jpg (image/jpeg)>], 
    #     'IMG_20231110_150000.jpg': [<InMemoryUploadedFile: IMG_20231110_150000.jpg (image/jpeg)>]
    # }
    
    # azure allow tags on each blob
    inventory_tags = {
        "sku": sku, 
        "time": getBlobTimeString(), # format: 2024-02-06
        "owner": ownerId,
        "ownerName": owner
    }
    print(len(request.FILES))
    if len(request.FILES) < 1:
        return Response('No images to upload', status.HTTP_200_OK)
    res = {}
    # loop the files in the request
    for name, value in request.FILES.items():
        # images will be uploaded to the folder named after their sku
        img = value
        imageName = sku + '/' + sku + '_' + name
        
        # process apples photo format
        if 'heic' in name or 'HEIC' in name:
            # convert image to jpg
            heicFile = pillow_heif.read_heif(value)
            byteImage = Image.frombytes (
                heicFile.mode,
                heicFile.size,
                heicFile.data,
                "raw"
            )
            buf = io.BytesIO()
            byteImage.save(buf, format="JPEG")
            img = buf.getvalue()
            # change extension to jpg
            base_name = os.path.splitext(name)[0]
            imageName = sku + '/' + base_name + '.' + 'jpg'
        try:
            res = product_image_container_client.upload_blob(imageName, img, tags=inventory_tags)
            if not res: 
                return Response('Failed to upload', status.HTTP_500_INTERNAL_SERVER_ERROR)
        except ResourceExistsError:
            return Response(imageName + 'Already Exist!', status.HTTP_409_CONFLICT)
    return Response('Upload success', status.HTTP_200_OK)

@api_view(['DELETE'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission | IsAdminPermission])
def deleteImageByName(request):
    body = decodeJSON(request.body)
    sku = sanitizeNumber(int(body['sku']))
    name = sanitizeString(body['name'])
    
    # azure automatically unquote all % in url
    imageName = parse.unquote(f'{str(sku)}/{name}')
    try:
        product_image_container_client.delete_blob(imageName)
    except:
        return Response('No Such Image', status.HTTP_404_NOT_FOUND)
    return Response('Image Deleted', status.HTTP_200_OK)

# when recording qa inventory records
# admin upload 1st stock image scraped from amazon or homedepot as {sku}.jpg
# url: string
# sku: string
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsQAPermission | IsAdminPermission])
def uploadScrapedImage(request):
    try:
        body = decodeJSON(request.body)
        url = body['url']
        sku = sanitizeString(str(body['sku']))
        owner = sanitizeString(body['owner']['id'])
        ownerName = sanitizeString(body['owner']['name'])
    except:
        return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # try request the image
    try:
        res = requests.get(url)
    except:
        return Response('Cannot GET From Provided URL', status.HTTP_404_NOT_FOUND)
    if res.status_code != 200:
        return Response(f'Cannot Get From URL: {res.status_code}')
    if len(res.content) < 1:
        return Response(f'Empty Image', status.HTTP_404_NOT_FOUND)

    # compress image size to 50 percent off
    img_bytes = io.BytesIO(res.content)
    # print(f'before compress: {len(img_bytes.read())}')
    img = Image.open(img_bytes)
    compressed_img = img.resize((img.width // 2, img.height // 2))
    compressed_image_stream = io.BytesIO()
    compressed_img.save(compressed_image_stream, format='JPEG', quality=50)
    # print(f'after compress: {len(compressed_image_stream.getvalue())}')
    
    # construct tags
    inventory_tags = {
        "sku": sku, 
        "time": getBlobTimeString(), # format: 2024-02-06
        "owner": owner,
        "ownerName": ownerName
    }
    extension = url.split('.')[-1].split('?')[0]
    
    # if body have image name set it to that
    if 'imageName' in body:
        imageName = f"{sku}/{sku}_{body['imageName']}.{extension}"
    else:
        imageName = f"{sku}/{sku}_{sku}.{extension}"
    try:
        res = product_image_container_client.upload_blob(imageName, compressed_image_stream.getvalue(), tags=inventory_tags)
    except ResourceExistsError:
        return Response(imageName + ' Already Exist!', status.HTTP_409_CONFLICT)
    return Response('found', status.HTTP_200_OK)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminPermission])
def rotateImage(request: HttpRequest):
    # try:
    body = decodeJSON(request.body)
    sku = sanitizeNumber(int(body['sku']))
    name = sanitizeString(body['name'])
    rotationIndex = sanitizeNumber(body['rotationIndex'])
    # except:
    # return Response('Invalid Body', status.HTTP_400_BAD_REQUEST)
    
    # update image in azure blob
    imageName = parse.unquote(f'{str(sku)}/{name}')
    print(imageName)
    blob_client = product_image_container_client.get_blob_client(imageName)
    tags = blob_client.get_blob_tags()
    print(blob_client.blob_name)
    print(blob_client.blob_name.format)
    blob_data = blob_client.download_blob()
    image_stream = io.BytesIO(blob_data.readall())
    
    
    print('rotate: ')
    print(rotationIndex * 90)
    # rotate
    image = Image.open(image_stream)
    rotated_image = image.rotate(90 * rotationIndex)
    
    print(rotated_image.width)
    print(rotated_image.height)
    output_stream = io.BytesIO()
    rotated_image.save(output_stream, format='JPEG')
    output_stream.seek(0)
    
    blob_client.upload_blob(output_stream, blob_type="BlockBlob", overwrite=True)
    blob_client.set_blob_tags(tags)
    return Response('', status.HTTP_200_OK)