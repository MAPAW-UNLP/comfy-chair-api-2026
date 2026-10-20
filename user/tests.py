from datetime import date, datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from article.models import Article
from conference.models import Conference
from conference_session.models import Session
from reviewer.models import Bid
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


class UpdateUserStatusAPITests(UserTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin@example.com', role='admin')
        self.user = self.create_user('user@example.com')
        self.authenticate(self.admin)

    def url(self, user):
        return reverse('update-user-status', args=[user.id])

    def test_admin_disables_user(self):
        response = self.client.patch(self.url(self.user), {'is_active': False}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['user']['is_active'])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_admin_enables_user(self):
        self.user.is_active = False
        self.user.save()

        response = self.client.patch(self.url(self.user), {'is_active': True}, format='json')

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_admin_cannot_disable_own_account(self):
        response = self.client.patch(self.url(self.admin), {'is_active': False}, format='json')

        self.assertEqual(response.status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_missing_or_invalid_is_active_returns_bad_request(self):
        for body in ({}, {'is_active': 'false'}, {'is_active': 0}):
            with self.subTest(body=body):
                response = self.client.patch(self.url(self.user), body, format='json')

                self.assertEqual(response.status_code, 400)

    def test_unknown_user_returns_not_found(self):
        response = self.client.patch(
            reverse('update-user-status', args=[9999]), {'is_active': False}, format='json'
        )

        self.assertEqual(response.status_code, 404)

    def test_common_user_gets_forbidden(self):
        self.authenticate(self.create_user('other@example.com'))

        response = self.client.patch(self.url(self.user), {'is_active': False}, format='json')

        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)


class DisabledUserAccessTests(UserTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user('user@example.com')
        self.user.set_password('clave-de-prueba-123')
        self.user.save()

    def login(self, password='clave-de-prueba-123'):
        return self.client.post(
            reverse('login'), {'email': 'user@example.com', 'password': password}, format='json'
        )

    def disable_user(self):
        self.user.is_active = False
        self.user.save()

    def test_open_session_is_rejected_after_disabling(self):
        self.authenticate(self.user)
        self.assertEqual(self.client.get(reverse('user-current')).status_code, 200)

        self.disable_user()
        response = self.client.get(reverse('user-current'))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'account_disabled')
        self.assertEqual(response.json()['error'], 'Su cuenta se encuentra deshabilitada')

    def test_token_of_deleted_user_is_rejected(self):
        self.authenticate(self.user)
        self.user.delete()

        response = self.client.get(reverse('user-current'))

        self.assertEqual(response.status_code, 401)
        self.assertNotIn('code', response.json())

    def test_disabled_user_with_correct_password_gets_disabled_message(self):
        self.disable_user()

        response = self.login()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'account_disabled')
        self.assertEqual(response.json()['error'], 'Su cuenta se encuentra deshabilitada')

    def test_disabled_user_with_wrong_password_gets_generic_error(self):
        self.disable_user()

        response = self.login(password='otra-clave')

        self.assertEqual(response.status_code, 401)
        self.assertNotIn('code', response.json())

    def test_re_enabled_user_can_log_in(self):
        self.disable_user()
        self.user.is_active = True
        self.user.save()

        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertIn('token', response.json())


class UserSelectorsTests(UserTestCase):
    def setUp(self):
        super().setUp()
        self.active = self.create_user('active@example.com')
        self.inactive = self.create_user('inactive@example.com', is_active=False)
        self.authenticate(self.create_user('requester@example.com'))

    def emails(self, response):
        return [user['email'] for user in response.json()]

    def create_article(self):
        conference = Conference.objects.create(
            title='Conference',
            description='Conference',
            start_date=date(2030, 1, 1),
            end_date=date(2030, 1, 10),
        )
        session = Session.objects.create(
            title='Session', deadline=conference.end_date, capacity=10, conference=conference
        )
        return Article.objects.create(
            title='Article',
            main_file='articles/example.pdf',
            type='regular',
            abstract='Abstract',
            corresponding_author=self.active,
            session=session,
        )

    def test_common_users_excludes_inactive_users(self):
        response = self.client.get(reverse('common-users-list'))

        self.assertIn('active@example.com', self.emails(response))
        self.assertNotIn('inactive@example.com', self.emails(response))

    def test_session_reviewers_excludes_inactive_users(self):
        response = self.client.get(reverse('session-reviewers', args=[1]))

        self.assertIn('active@example.com', self.emails(response))
        self.assertNotIn('inactive@example.com', self.emails(response))

    def test_available_reviewers_excludes_inactive_users(self):
        article = self.create_article()
        inactive_bidder = self.create_user('inactive-bidder@example.com', is_active=False)
        Bid.objects.create(reviewer=inactive_bidder, article=article, choice='Interesado')

        response = self.client.get(reverse('available-reviewers', args=[article.id]))

        emails = self.emails(response)
        self.assertIn('active@example.com', emails)
        self.assertNotIn('inactive@example.com', emails)
        self.assertNotIn('inactive-bidder@example.com', emails)
