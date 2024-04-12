from django.urls import path
from . import views

# define all routes
urlpatterns = [
    path("uploadImage/<str:ownerId>/<str:owner>/<str:sku>", views.uploadImage, name="uploadImage"),
    path("getUrlsByOwner", views.getUrlsByOwner, name="getUrlsByOwner"),
    path("getUrlsBySku", views.getUrlsBySku, name="getUrlsBySku"),
    path("deleteImageByName", views.deleteImageByName, name="deleteImageByName"),
    path("uploadScrapedImage", views.uploadScrapedImage, name="uploadScrapedImage"),
    path("rotateImage", views.rotateImage, name="rotateImage"),
]
