from django.urls import path
from . import views

# define all routes
urlpatterns = [
    path('checkToken', views.checkToken, name="checkToken"),
    path('login', views.login, name="login"),
    path("registerUser", views.registerUser, name="registerUser"),
    path("getUserById", views.getUserById, name="getUserById"),
    path("changeOwnPassword", views.changeOwnPassword, name="changeOwnPassword"),
    path("logout", views.logout, name="logout"),
]
