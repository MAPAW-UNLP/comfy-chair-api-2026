from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from user.models import User


class UserTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def create_user(self, email, role='user', **kwargs):
        return User.objects.create(
            email=email,
            full_name=kwargs.pop('full_name', email),
            affiliation=kwargs.pop('affiliation', 'MAPAW'),
            role=role,
            **kwargs,
        )

    def authenticate(self, user):
        token = jwt.encode(
            {
                'user_id': user.id,
                'exp': datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')


class GetUserListAPITests(UserTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('users-list')
        self.admin = self.create_user('admin@example.com', role='admin', full_name='Admin')

    def test_admin_gets_full_user_list(self):
        self.create_user('ana@example.com', full_name='Ana', affiliation='UNLP')
        self.authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        ana = next(user for user in response.json() if user['email'] == 'ana@example.com')
        self.assertEqual(ana, {
            'id': ana['id'],
            'full_name': 'Ana',
            'email': 'ana@example.com',
            'affiliation': 'UNLP',
            'role': 'user',
            'is_active': True,
        })

    def test_admin_list_includes_inactive_users(self):
        self.create_user('inactive@example.com', is_active=False)
        self.authenticate(self.admin)

        response = self.client.get(self.url)

        inactive = next(user for user in response.json() if user['email'] == 'inactive@example.com')
        self.assertFalse(inactive['is_active'])

    def test_admin_list_is_ordered_by_full_name(self):
        self.create_user('zoe@example.com', full_name='Zoe')
        self.create_user('bruno@example.com', full_name='Bruno')
        self.authenticate(self.admin)

        response = self.client.get(self.url)

        names = [user['full_name'] for user in response.json()]
        self.assertEqual(names, ['Admin', 'Bruno', 'Zoe'])

    def test_common_user_gets_forbidden(self):
        self.authenticate(self.create_user('user@example.com'))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_request_without_token_gets_unauthorized(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)
