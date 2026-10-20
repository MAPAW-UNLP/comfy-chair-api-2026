from django.urls import path

from .api import (
    AdminRegisterAPI, 
    GetUserIdAPI, 
    GetUserListAPI, 
    LoginAPI, 
    UserRegisterAPI, 
    GetUsersNoAdminAPI, 
    UpdateUserDataAPI, 
    UpdateUserPasswordAPI, 
    GetUserFullDataAPI, 
    GetSessionReviewersAPI
)

urlpatterns = [
    path("register/", UserRegisterAPI.as_view(), name="register-user"),
    path('register-admin/', AdminRegisterAPI.as_view(), name='register-admin'),
    path("login/", LoginAPI.as_view(), name="login"),
    path('getUser/', GetUserIdAPI.as_view(), name='user-current'),
    path('user-full-data/', GetUserFullDataAPI.as_view(), name='user-full-data'),
    path('getUsers/', GetUserListAPI.as_view(), name='users-list'), # Creado por el Grupo 1 para traer todos los usuarios de la base de datos. Grupo 3 (2026): solo para admin
    path('getCommonUsers/', GetUsersNoAdminAPI.as_view(), name='common-users-list'), # Creado por Grupo 3 para obtener usuarios comunes
    path('update/', UpdateUserDataAPI.as_view(), name='update-user-data'),
    path('update-password/', UpdateUserPasswordAPI.as_view(), name='update-user-password'),
    path('sessions/<int:session_id>/reviewers/', GetSessionReviewersAPI.as_view(), name='session-reviewers'),
]

