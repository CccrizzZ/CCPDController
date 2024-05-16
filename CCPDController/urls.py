from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # path('admin/', admin.site.urls),
    path('imageController/', include('imageController.urls')),
    path('inventoryController/', include('inventoryController.urls')),
    path('userController/', include('userController.urls')),
    path('adminController/', include('adminController.urls')),
]
