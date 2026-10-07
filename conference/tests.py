from datetime import date, datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from article.models import Article
from chair.models import ReviewAssignment
from conference.models import Conference
from conference_session.models import Session
from user.models import User


class ConferenceUsersActionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        requester = User.objects.create(
            email='requester@example.com',
            full_name='Requester',
            affiliation='MAPAW',
            role='user',
        )
        self.client.force_authenticate(user=requester)
        token = jwt.encode(
            {
                'user_id': requester.id,
                'exp': datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.conference = Conference.objects.create(
            title='Conference A',
            description='First conference',
            start_date=date(2030, 1, 1),
            end_date=date(2030, 1, 10),
        )
        self.other_conference = Conference.objects.create(
            title='Conference B',
            description='Second conference',
            start_date=date(2030, 2, 1),
            end_date=date(2030, 2, 10),
        )

    def create_user(self, email, **kwargs):
        return User.objects.create(
            email=email,
            full_name=kwargs.pop('full_name', email),
            affiliation='MAPAW',
            role='user',
            **kwargs,
        )

    def create_session(self, conference, title, *chairs):
        session = Session.objects.create(
            title=title,
            deadline=conference.start_date,
            capacity=20,
            conference=conference,
        )
        session.chairs.set(chairs)
        return session

    def create_article(self, session, title, *authors):
        article = Article.objects.create(
            title=title,
            main_file='articles/example.pdf',
            type='regular',
            abstract='Article abstract',
            corresponding_author=authors[0],
            session=session,
        )
        article.authors.set(authors)
        return article

    def test_lists_users_with_all_conference_roles(self):
        conference_chair = self.create_user('conference-chair@example.com')
        session_chair = self.create_user('session-chair@example.com')
        author = self.create_user('author@example.com')
        reviewer = self.create_user('reviewer@example.com')
        multi_role_user = self.create_user('multi-role@example.com')
        session = self.create_session(
            self.conference,
            'Session A',
            session_chair,
            multi_role_user,
        )
        article = self.create_article(
            session,
            'Article A',
            author,
            multi_role_user,
        )
        self.conference.chairs.add(conference_chair, multi_role_user)
        ReviewAssignment.objects.create(reviewer=reviewer, article=article)

        response = self.client.get(
            reverse('conference-users', kwargs={'pk': self.conference.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['conference_id'], self.conference.pk)
        users = {user['id']: user for user in response.data['users']}
        self.assertEqual(
            users[conference_chair.pk]['email'],
            conference_chair.email,
        )
        self.assertEqual(
            users[conference_chair.pk]['roles'],
            ['conference_chair'],
        )
        self.assertEqual(users[session_chair.pk]['roles'], ['session_chair'])
        self.assertEqual(users[author.pk]['roles'], ['author'])
        self.assertEqual(users[reviewer.pk]['roles'], ['reviewer'])
        self.assertEqual(
            users[multi_role_user.pk]['roles'],
            ['conference_chair', 'session_chair', 'author'],
        )

    def test_excludes_users_outside_conference_and_inactive_roles(self):
        other_session_chair = self.create_user('other-chair@example.com')
        deleted_user = self.create_user(
            'deleted@example.com',
            deleted=True,
        )
        inactive_user = self.create_user(
            'inactive@example.com',
            is_active=False,
        )
        active_reviewer = self.create_user('active-reviewer@example.com')
        session = self.create_session(
            self.other_conference,
            'Other session',
            other_session_chair,
        )
        article = self.create_article(session, 'Other article', deleted_user)
        ReviewAssignment.objects.create(
            reviewer=active_reviewer,
            article=article,
            deleted=True,
        )
        self.conference.chairs.add(deleted_user, inactive_user)

        response = self.client.get(
            reverse('conference-users', kwargs={'pk': self.conference.pk})
        )

        self.assertEqual(response.status_code, 200)
        returned_ids = {user['id'] for user in response.data['users']}
        self.assertEqual(returned_ids, set())

    def test_returns_not_found_for_unknown_conference(self):
        response = self.client.get(
            reverse('conference-users', kwargs={'pk': 999999})
        )

        self.assertEqual(response.status_code, 404)

    def test_search_filters_by_full_name_case_insensitively(self):
        matching_author = self.create_user(
            'matching-author@example.com',
            full_name='Ana Pérez',
        )
        non_matching_author = self.create_user(
            'other-author@example.com',
            full_name='Luis Gómez',
        )
        session = self.create_session(self.conference, 'Session A')
        self.create_article(session, 'Article A', matching_author)
        self.create_article(session, 'Article B', non_matching_author)

        response = self.client.get(
            reverse('conference-users', kwargs={'pk': self.conference.pk}),
            {'search': '  aNa  '},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [user['id'] for user in response.data['users']],
            [matching_author.pk],
        )

    def test_search_filters_by_email_case_insensitively(self):
        matching_author = self.create_user(
            'ana.perez@example.com',
            full_name='Ana Pérez',
        )
        non_matching_author = self.create_user(
            'luis.gomez@example.com',
            full_name='Luis Gómez',
        )
        session = self.create_session(self.conference, 'Session A')
        self.create_article(session, 'Article A', matching_author)
        self.create_article(session, 'Article B', non_matching_author)

        response = self.client.get(
            reverse('conference-users', kwargs={'pk': self.conference.pk}),
            {'search': '  ANA.PEREZ@EXAMPLE.COM  '},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [user['id'] for user in response.data['users']],
            [matching_author.pk],
        )

    def test_role_filter_returns_matching_users_with_all_their_roles(self):
        multi_role_user = self.create_user(
            'multi-role@example.com',
            full_name='Ana Pérez',
        )
        conference_chair = self.create_user(
            'chair@example.com',
            full_name='Luis Gómez',
        )
        session = self.create_session(
            self.conference,
            'Session A',
            multi_role_user,
        )
        article = self.create_article(session, 'Article A', multi_role_user)
        self.conference.chairs.add(multi_role_user, conference_chair)
        ReviewAssignment.objects.create(
            reviewer=multi_role_user,
            article=article,
        )

        response = self.client.get(
            reverse('conference-users', kwargs={'pk': self.conference.pk}),
            {'role': 'author'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [user['id'] for user in response.data['users']],
            [multi_role_user.pk],
        )
        self.assertEqual(
            response.data['users'][0]['roles'],
            ['conference_chair', 'session_chair', 'author', 'reviewer'],
        )

    def test_invalid_role_returns_bad_request(self):
        response = self.client.get(
            reverse('conference-users', kwargs={'pk': self.conference.pk}),
            {'role': 'admin'},
        )

        self.assertEqual(response.status_code, 400)
