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
    GetSessionReviewersAPI,
    UpdateUserStatusAPI,
    DisableCheckAPI,
    ChairReplacementRequestAPI
)

urlpatterns = [
    path("register/", UserRegisterAPI.as_view(), name="register-user"),
    path('register-admin/', AdminRegisterAPI.as_view(), name='register-admin'),
    path("login/", LoginAPI.as_view(), name="login"),
    path('getUser/', GetUserIdAPI.as_view(), name='user-current'),
    path('user-full-data/', GetUserFullDataAPI.as_view(), name='user-full-data'),
    path('getUsers/', GetUserListAPI.as_view(), name='users-list'), # Creado por el Grupo 1 para traer todos los usuarios de la base de datos. Grupo 3 (2026): solo para admin
    path('getCommonUsers/', GetUsersNoAdminAPI.as_view(), name='common-users-list'), # Creado por Grupo 3 para obtener usuarios comunes
    path('<int:user_id>/status/', UpdateUserStatusAPI.as_view(), name='update-user-status'), # Creado por Grupo 3 (2026): habilitar/deshabilitar usuarios, solo para admin
    path('<int:user_id>/disable-check/', DisableCheckAPI.as_view(), name='user-disable-check'), # Creado por Grupo 3 (2026): qué pasaría al deshabilitar un usuario, solo para admin
    path('<int:user_id>/chair-requests/', ChairReplacementRequestAPI.as_view(), name='user-chair-requests'), # Creado por Grupo 3 (2026): pedir al chair de conferencia que reemplace al único chair de una sesión, solo para admin
    path('update/', UpdateUserDataAPI.as_view(), name='update-user-data'),
    path('update-password/', UpdateUserPasswordAPI.as_view(), name='update-user-password'),
    path('sessions/<int:session_id>/reviewers/', GetSessionReviewersAPI.as_view(), name='session-reviewers'),
]

