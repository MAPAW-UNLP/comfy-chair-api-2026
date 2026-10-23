from datetime import date, datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient
from article.models import Article
from conference.models import Conference
from conference_session.models import Session
from reviewer.models import Bid, Review
from notification.models import Notification
from chair.models import ReviewAssignment
from user.models import ChairReplacementRequest, User


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


class DisableUserChairsTests(UserTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin@example.com', role='admin')
        self.user = self.create_user('chair@example.com')
        self.other = self.create_user('other-chair@example.com')
        self.authenticate(self.admin)
        today = date.today()
        self.conference = self.create_conference('Activa', today, today + timedelta(days=30))
        self.finished_conference = self.create_conference(
            'Terminada', today - timedelta(days=30), today - timedelta(days=1)
        )

    def create_conference(self, title, start, end, *chairs):
        conference = Conference.objects.create(
            title=title, description=title, start_date=start, end_date=end
        )
        conference.chairs.set(chairs)
        return conference

    def create_session(self, conference, title, *chairs):
        session = Session.objects.create(
            title=title, deadline=conference.end_date, capacity=10, conference=conference
        )
        session.chairs.set(chairs)
        return session

    def check(self):
        return self.client.get(reverse('user-disable-check', args=[self.user.id]))

    def disable(self):
        return self.client.patch(
            reverse('update-user-status', args=[self.user.id]), {'is_active': False}, format='json'
        )

    def test_user_without_chair_roles_can_be_disabled(self):
        data = self.check().json()

        self.assertTrue(data['can_disable'])
        self.assertEqual(data['conflicts'], [])
        self.assertEqual(data['chair_removals'], [])
        self.assertEqual(self.disable().status_code, 200)

    def test_sole_conference_chair_is_a_conflict(self):
        self.conference.chairs.set([self.user])

        data = self.check().json()
        response = self.disable()

        self.assertFalse(data['can_disable'])
        self.assertEqual(data['conflicts'], [{'type': 'conference', 'id': self.conference.id, 'title': 'Activa'}])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['conflicts'], data['conflicts'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertIn(self.user, self.conference.chairs.all())

    def test_sole_session_chair_is_a_conflict_even_if_conference_has_chairs(self):
        self.conference.chairs.set([self.other])
        session = self.create_session(self.conference, 'Sesión IA', self.user)

        data = self.check().json()

        self.assertEqual(data['conflicts'], [{
            'type': 'session',
            'id': session.id,
            'title': 'Sesión IA',
            'conference_id': self.conference.id,
            'conference_title': 'Activa',
            'can_request_conference_chair': True,
            'pending_request': None,
        }])
        self.assertEqual(self.disable().status_code, 409)

    def test_shared_chair_is_removed_when_disabled(self):
        self.conference.chairs.set([self.user, self.other])
        session = self.create_session(self.conference, 'Sesión IA', self.user, self.other)

        data = self.check().json()
        response = self.disable()

        self.assertTrue(data['can_disable'])
        self.assertEqual([item['type'] for item in data['chair_removals']], ['conference', 'session'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(self.conference.chairs.all()), [self.other])
        self.assertEqual(list(session.chairs.all()), [self.other])

    def test_inactive_chair_does_not_count_as_other_chair(self):
        inactive = self.create_user('inactive-chair@example.com', is_active=False)
        self.conference.chairs.set([self.user, inactive])

        self.assertFalse(self.check().json()['can_disable'])
        self.assertEqual(self.disable().status_code, 409)

    def test_can_be_disabled_after_resolving_the_conflict(self):
        self.conference.chairs.set([self.user])
        self.assertEqual(self.disable().status_code, 409)

        self.conference.chairs.add(self.other)

        self.assertEqual(self.disable().status_code, 200)
        self.assertEqual(list(self.conference.chairs.all()), [self.other])

    def test_finished_conference_is_kept_as_history(self):
        self.finished_conference.chairs.set([self.user])
        session = self.create_session(self.finished_conference, 'Sesión vieja', self.user)

        data = self.check().json()
        response = self.disable()

        self.assertTrue(data['can_disable'])
        self.assertEqual(data['conflicts'], [])
        self.assertEqual(data['chair_removals'], [])
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.user, self.finished_conference.chairs.all())
        self.assertIn(self.user, session.chairs.all())

    def test_enabling_does_not_check_conflicts(self):
        self.conference.chairs.set([self.user])
        self.user.is_active = False
        self.user.save()

        response = self.client.patch(
            reverse('update-user-status', args=[self.user.id]), {'is_active': True}, format='json'
        )

        self.assertEqual(response.status_code, 200)

    def test_check_lists_pending_reviews_by_session(self):
        session = self.create_session(self.conference, 'Sesión IA', self.other)
        pending = Article.objects.create(
            title='Pendiente', main_file='articles/a.pdf', type='regular',
            abstract='Abstract', corresponding_author=self.other, session=session,
        )
        reviewed = Article.objects.create(
            title='Revisado', main_file='articles/b.pdf', type='regular',
            abstract='Abstract', corresponding_author=self.other, session=session,
        )
        ReviewAssignment.objects.create(reviewer=self.user, article=pending)
        ReviewAssignment.objects.create(reviewer=self.user, article=reviewed, reviewed=True)

        data = self.check().json()

        self.assertEqual(data['pending_reviews'], [{
            'session_id': session.id,
            'session_title': 'Sesión IA',
            'articles': [{'id': pending.id, 'title': 'Pendiente'}],
        }])

    def test_check_requires_admin(self):
        self.authenticate(self.create_user('common@example.com'))

        self.assertEqual(self.check().status_code, 403)

    def test_check_unknown_user_returns_not_found(self):
        response = self.client.get(reverse('user-disable-check', args=[9999]))

        self.assertEqual(response.status_code, 404)


class DisableReviewerTests(UserTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin@example.com', role='admin')
        self.reviewer = self.create_user('reviewer@example.com', full_name='Rita Revisora')
        self.session_chair = self.create_user('session-chair@example.com')
        self.conference_chair = self.create_user('conference-chair@example.com')
        self.author = self.create_user('author@example.com')
        self.authenticate(self.admin)
        today = date.today()
        self.conference = Conference.objects.create(
            title='Activa', description='Activa', start_date=today, end_date=today + timedelta(days=30)
        )
        self.conference.chairs.set([self.conference_chair])
        self.session = self.create_session(self.conference, 'Sesión IA', self.session_chair)

    def create_session(self, conference, title, *chairs):
        session = Session.objects.create(
            title=title, deadline=conference.end_date, capacity=10, conference=conference
        )
        session.chairs.set(chairs)
        return session

    def create_assignment(self, session, title, **kwargs):
        article = Article.objects.create(
            title=title, main_file='articles/a.pdf', type='regular',
            abstract='Abstract', corresponding_author=self.author, session=session,
        )
        return ReviewAssignment.objects.create(reviewer=self.reviewer, article=article, **kwargs)

    def disable(self):
        return self.client.patch(
            reverse('update-user-status', args=[self.reviewer.id]), {'is_active': False}, format='json'
        )

    def disabled_notifications(self, user=None):
        notifications = Notification.objects.filter(title='Revisor deshabilitado')
        return notifications.filter(user=user) if user else notifications

    def test_pending_assignments_are_released(self):
        pending = self.create_assignment(self.session, 'Pendiente')

        response = self.disable()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['removed_assignments'], 1)
        pending.refresh_from_db()
        self.assertTrue(pending.deleted)

    def test_reviewed_assignments_and_reviews_are_kept(self):
        reviewed = self.create_assignment(self.session, 'Revisado', reviewed=True)
        review = Review.objects.create(
            reviewer=self.reviewer, article=reviewed.article, score=2, opinion='Bien', is_published=True
        )

        self.disable()

        reviewed.refresh_from_db()
        self.assertFalse(reviewed.deleted)
        self.assertTrue(Review.objects.filter(pk=review.pk).exists())

    def test_session_chairs_are_notified_with_the_articles(self):
        self.create_assignment(self.session, 'Artículo A')
        self.create_assignment(self.session, 'Artículo B')

        response = self.disable()

        notification = self.disabled_notifications(self.session_chair).get()
        self.assertEqual(notification.type, 'critical')
        self.assertIn('Rita Revisora (reviewer@example.com)', notification.message)
        self.assertIn('"Sesión IA"', notification.message)
        self.assertIn('"Artículo A", "Artículo B"', notification.message)
        self.assertEqual(
            response.json()['notified_sessions'],
            [{'session_id': self.session.id, 'session_title': 'Sesión IA'}],
        )
        self.assertFalse(self.disabled_notifications(self.conference_chair).exists())

    def test_session_without_chairs_notifies_conference_chairs(self):
        session = self.create_session(self.conference, 'Sin chairs')
        self.create_assignment(session, 'Artículo')

        self.disable()

        self.assertEqual(self.disabled_notifications(self.conference_chair).count(), 1)

    def test_inactive_chairs_are_not_notified(self):
        inactive = self.create_user('inactive-chair@example.com', is_active=False)
        self.session.chairs.add(inactive)
        self.create_assignment(self.session, 'Artículo')

        self.disable()

        self.assertFalse(self.disabled_notifications(inactive).exists())
        self.assertTrue(self.disabled_notifications(self.session_chair).exists())

    def test_one_notification_per_session(self):
        other_session = self.create_session(self.conference, 'Sesión Redes', self.session_chair)
        self.create_assignment(self.session, 'Artículo IA')
        self.create_assignment(other_session, 'Artículo Redes')

        response = self.disable()

        messages = sorted(n.message for n in self.disabled_notifications(self.session_chair))
        self.assertEqual(len(messages), 2)
        self.assertIn('"Artículo IA"', messages[0])
        self.assertNotIn('"Artículo Redes"', messages[0])
        self.assertIn('"Artículo Redes"', messages[1])
        self.assertEqual(len(response.json()['notified_sessions']), 2)

    def test_disabled_reviewer_is_not_notified(self):
        self.create_assignment(self.session, 'Artículo')
        before = Notification.objects.filter(user=self.reviewer).count()

        self.disable()

        self.assertEqual(Notification.objects.filter(user=self.reviewer).count(), before)

    def test_without_pending_assignments_nobody_is_notified(self):
        self.create_assignment(self.session, 'Revisado', reviewed=True)

        response = self.disable()

        self.assertEqual(response.json()['removed_assignments'], 0)
        self.assertEqual(response.json()['notified_sessions'], [])
        self.assertFalse(self.disabled_notifications().exists())

    def test_finished_conference_assignments_are_kept(self):
        today = date.today()
        finished = Conference.objects.create(
            title='Terminada', description='Terminada',
            start_date=today - timedelta(days=30), end_date=today - timedelta(days=1),
        )
        session = self.create_session(finished, 'Sesión vieja', self.session_chair)
        assignment = self.create_assignment(session, 'Viejo')

        self.disable()

        assignment.refresh_from_db()
        self.assertFalse(assignment.deleted)
        self.assertFalse(self.disabled_notifications().exists())

    def test_chair_conflict_leaves_assignments_untouched(self):
        self.session.chairs.set([self.reviewer])
        assignment = self.create_assignment(self.session, 'Artículo')

        response = self.disable()

        self.assertEqual(response.status_code, 409)
        assignment.refresh_from_db()
        self.assertFalse(assignment.deleted)
        self.assertFalse(self.disabled_notifications().exists())


class ChairsDetailTests(UserTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin@example.com', role='admin')
        self.zoe = self.create_user('zoe@example.com', full_name='Zoe')
        self.bruno = self.create_user('bruno@example.com', full_name='Bruno', is_active=False)
        self.session_chair = self.create_user('session-chair@example.com', full_name='Sofía')
        self.authenticate(self.admin)
        self.conference = self.create_conference('Activa', self.zoe, self.bruno)
        self.session = self.create_session(self.conference, 'Sesión IA', self.session_chair)

    def create_conference(self, title, *chairs):
        today = date.today()
        conference = Conference.objects.create(
            title=title, description=title, start_date=today, end_date=today + timedelta(days=30)
        )
        conference.chairs.set(chairs)
        return conference

    def create_session(self, conference, title, *chairs):
        session = Session.objects.create(
            title=title, deadline=conference.end_date, capacity=10, conference=conference
        )
        session.chairs.set(chairs)
        return session

    def summary(self, user):
        return {'id': user.id, 'full_name': user.full_name, 'email': user.email}

    def test_conference_detail_includes_chairs_detail(self):
        response = self.client.get(reverse('conference-detail', args=[self.conference.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['chairs_detail'], [self.summary(self.bruno), self.summary(self.zoe)])

    def test_session_detail_includes_chairs_detail(self):
        response = self.client.get(reverse('session-detail', args=[self.session.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['chairs_detail'], [self.summary(self.session_chair)])

    def test_disabled_chair_is_included_without_is_active(self):
        data = self.client.get(reverse('conference-detail', args=[self.conference.id])).json()

        bruno = next(chair for chair in data['chairs_detail'] if chair['id'] == self.bruno.id)
        self.assertNotIn('is_active', bruno)

    def test_chairs_keeps_the_list_of_ids(self):
        data = self.client.get(reverse('conference-detail', args=[self.conference.id])).json()

        self.assertCountEqual(data['chairs'], [self.zoe.id, self.bruno.id])

    def test_create_and_edit_conference_with_chairs(self):
        today = date.today()
        response = self.client.post(reverse('conference-list'), {
            'title': 'Nueva', 'description': 'Nueva', 'start_date': today, 'end_date': today + timedelta(days=10),
            'blind_kind': 'single blind', 'chairs': [self.zoe.id], 'user_id': self.admin.id,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['chairs_detail'], [self.summary(self.zoe)])

        response = self.client.patch(
            reverse('conference-detail', args=[response.json()['id']]),
            {'chairs': [self.session_chair.id], 'user_id': self.admin.id}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['chairs'], [self.session_chair.id])
        self.assertEqual(response.json()['chairs_detail'], [self.summary(self.session_chair)])

    def test_edit_session_with_chairs(self):
        response = self.client.patch(
            reverse('session-detail', args=[self.session.id]), {'chairs': [self.admin.id]}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['chairs_detail'], [self.summary(self.admin)])

    def test_nested_serializers_do_not_include_chairs_detail(self):
        Article.objects.create(
            title='Artículo', main_file='articles/a.pdf', type='regular',
            abstract='Abstract', corresponding_author=self.zoe, session=self.session,
        )

        session = self.client.get(reverse('session-detail', args=[self.session.id])).json()
        articles = self.client.get(reverse('session-articles', args=[self.session.id])).json()

        self.assertNotIn('chairs_detail', session['conference'])
        self.assertNotIn('chairs_detail', articles[0]['session'])
        self.assertNotIn('chairs_detail', articles[0]['session']['conference'])

    def test_lists_do_not_add_queries_per_item(self):
        def count_queries(url):
            with CaptureQueriesContext(connection) as context:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(context)

        conference_queries = count_queries(reverse('conference-list'))
        session_queries = count_queries(reverse('session-list'))
        for index in range(3):
            conference = self.create_conference(f'Otra {index}', self.zoe, self.bruno)
            self.create_session(conference, f'Sesión {index}', self.session_chair)

        self.assertEqual(count_queries(reverse('conference-list')), conference_queries)
        self.assertEqual(count_queries(reverse('session-list')), session_queries)


class ChairReplacementRequestTests(UserTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin@example.com', role='admin')
        self.user = self.create_user('chair@example.com', full_name='Carla Chair')
        self.other = self.create_user('other@example.com', full_name='Otro Chair')
        self.conference_chair = self.create_user('conference-chair@example.com', full_name='Conf Chair')
        self.inactive_conference_chair = self.create_user('inactive@example.com', is_active=False)
        self.authenticate(self.admin)
        today = date.today()
        self.conference = self.create_conference('Activa', today, today + timedelta(days=30))
        self.session = self.create_session(self.conference, 'Sesión IA', self.user)

    def create_conference(self, title, start, end):
        conference = Conference.objects.create(title=title, description=title, start_date=start, end_date=end)
        conference.chairs.set([self.conference_chair, self.inactive_conference_chair])
        return conference

    def create_session(self, conference, title, *chairs):
        session = Session.objects.create(
            title=title, deadline=conference.end_date, capacity=10, conference=conference
        )
        session.chairs.set(chairs)
        return session

    def request_chair(self, session=None, user=None):
        session = session or self.session
        return self.client.post(
            reverse('user-chair-requests', args=[(user or self.user).id]), {'session_id': session.id}, format='json'
        )

    def notifications(self, title, user=None):
        notifications = Notification.objects.filter(title=title)
        return notifications.filter(user=user) if user else notifications

    def session_conflict(self):
        data = self.client.get(reverse('user-disable-check', args=[self.user.id])).json()
        return next(conflict for conflict in data['conflicts'] if conflict['type'] == 'session')

    def test_request_is_created_and_active_conference_chairs_are_notified(self):
        response = self.request_chair()

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['session_id'], self.session.id)
        self.assertEqual(data['conference_id'], self.conference.id)
        self.assertEqual(data['notified_chairs'], ['Conf Chair'])
        self.assertTrue(ChairReplacementRequest.objects.filter(
            id=data['id'], user=self.user, session=self.session, requested_by=self.admin, resolved_at=None
        ).exists())
        notification = self.notifications('Sesión sin chair').get()
        self.assertEqual(notification.user, self.conference_chair)
        self.assertEqual(notification.type, 'critical')
        self.assertIn('Carla Chair', notification.message)
        self.assertIn('Sesión IA', notification.message)

    def test_repeated_request_returns_the_pending_one_without_notifying(self):
        first = self.request_chair().json()

        response = self.request_chair()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], first['id'])
        self.assertEqual(ChairReplacementRequest.objects.count(), 1)
        self.assertEqual(self.notifications('Sesión sin chair').count(), 1)

    def test_request_without_conflict_returns_400(self):
        self.session.chairs.add(self.other)

        response = self.request_chair()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ChairReplacementRequest.objects.exists())

    def test_request_for_finished_conference_returns_400(self):
        today = date.today()
        finished = self.create_conference('Terminada', today - timedelta(days=30), today - timedelta(days=1))
        session = self.create_session(finished, 'Sesión vieja', self.user)

        response = self.request_chair(session)

        self.assertEqual(response.status_code, 400)

    def test_request_without_active_conference_chairs_returns_400(self):
        self.conference.chairs.set([self.inactive_conference_chair])

        response = self.request_chair()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.notifications('Sesión sin chair').exists())

    def test_request_without_session_id_returns_400(self):
        response = self.client.post(reverse('user-chair-requests', args=[self.user.id]), {}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_common_user_and_missing_user_or_session(self):
        url = reverse('user-chair-requests', args=[999])
        self.assertEqual(self.client.post(url, {'session_id': self.session.id}, format='json').status_code, 404)
        url = reverse('user-chair-requests', args=[self.user.id])
        self.assertEqual(self.client.post(url, {'session_id': 999}, format='json').status_code, 404)
        self.authenticate(self.other)
        self.assertEqual(self.request_chair().status_code, 403)

    def test_adding_another_chair_resolves_the_request_and_notifies_the_admin(self):
        self.request_chair()

        response = self.client.patch(
            reverse('session-detail', args=[self.session.id]), {'chairs': [self.user.id, self.other.id]}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertIsNotNone(ChairReplacementRequest.objects.get().resolved_at)
        notification = self.notifications('Conflicto resuelto', self.admin).get()
        self.assertEqual(notification.type, 'info')
        self.assertIn('Carla Chair', notification.message)

    def test_removing_the_user_from_the_session_resolves_the_request(self):
        self.request_chair()

        self.user.session.remove(self.session)

        self.assertIsNotNone(ChairReplacementRequest.objects.get().resolved_at)
        self.assertTrue(self.notifications('Conflicto resuelto', self.admin).exists())

    def test_adding_an_inactive_chair_does_not_resolve_the_request(self):
        self.request_chair()

        self.session.chairs.add(self.inactive_conference_chair)

        self.assertIsNone(ChairReplacementRequest.objects.get().resolved_at)
        self.assertFalse(self.notifications('Conflicto resuelto').exists())

    def test_disabling_the_user_closes_pending_requests_without_notifying(self):
        self.other.is_active = False
        self.other.save()
        self.session.chairs.add(self.other)
        self.request_chair()
        self.other.is_active = True
        self.other.save()

        response = self.client.patch(
            reverse('update-user-status', args=[self.user.id]), {'is_active': False}, format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(ChairReplacementRequest.objects.get().resolved_at)
        self.assertFalse(self.notifications('Conflicto resuelto').exists())

    def test_disable_check_reports_request_status(self):
        conflict = self.session_conflict()
        self.assertTrue(conflict['can_request_conference_chair'])
        self.assertIsNone(conflict['pending_request'])

        chair_request = self.request_chair().json()

        conflict = self.session_conflict()
        self.assertEqual(conflict['pending_request']['id'], chair_request['id'])

        self.conference.chairs.set([self.inactive_conference_chair])
        self.assertFalse(self.session_conflict()['can_request_conference_chair'])
