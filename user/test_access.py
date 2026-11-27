"""Regression tests for direct requests, conference scope and accumulated roles."""
from datetime import date
from chair.models import ReviewAssignment
from reviewer.models import ReviewerInvitation
from reviewer.tests.base import ReviewerTestCase


class ConferenceAccessTests(ReviewerTestCase):
    def setUp(self):
        super().setUp()
        self.author = self.crear_usuario()
        self.reviewer = self.crear_usuario()
        self.chair = self.crear_usuario()
        self.session_chair = self.crear_usuario()
        self.outsider = self.crear_usuario()
        self.conference = self.crear_conferencia(start_date=date.today())
        self.session = self.crear_sesion(self.conference)
        self.conference.chairs.add(self.chair)
        self.session.chairs.add(self.session_chair)
        self.article = self.crear_articulo(autor=self.author, sesion=self.session)
        self.assignment = self.crear_asignacion(self.reviewer, self.article)
        self.other = self.crear_conferencia()
        self.other_session = self.crear_sesion(self.other)
        self.other_article = self.crear_articulo(sesion=self.other_session)

    def test_access_snapshot_accumulates_roles_in_same_conference(self):
        self.conference.chairs.add(self.author)
        self.crear_asignacion(self.author, self.other_article)
        ReviewerInvitation.objects.create(reviewer=self.author, conference=self.conference, status='accepted')
        data = self.cliente_de(self.author).get('/user/access/').json()
        self.assertEqual(data['chair_conference_ids'], [self.conference.pk])
        self.assertIn(self.conference.pk, data['reviewer_conference_ids'])
        self.assertIn(self.other.pk, data['reviewer_conference_ids'])
        self.assertEqual(data['author_article_ids'], [self.article.pk])

    def test_no_roles_snapshot_is_empty(self):
        data = self.cliente_de(self.outsider).get('/user/access/').json()
        self.assertTrue(all(value == [] for value in data.values()))

    def test_session_chair_has_chair_access_only_in_its_conference(self):
        data = self.cliente_de(self.session_chair).get('/user/access/').json()
        self.assertEqual(data['chair_conference_ids'], [self.conference.pk])
        self.assertEqual(data['chair_session_ids'], [self.session.pk])

    def test_removed_roles_disappear_on_next_request(self):
        client = self.cliente_de(self.reviewer)
        self.assertEqual(client.get('/user/access/').json()['reviewer_conference_ids'], [self.conference.pk])
        self.assignment.deleted = True
        self.assignment.save()
        self.assertEqual(client.get('/user/access/').json()['reviewer_conference_ids'], [])
        self.assertEqual(client.get('/api/reviewer/assignments/').status_code, 403)

    def test_pending_invitation_is_not_a_reviewer_role_but_remains_readable(self):
        invitation = ReviewerInvitation.objects.create(reviewer=self.outsider, conference=self.conference)
        client = self.cliente_de(self.outsider)
        self.assertEqual(client.get('/api/reviewer/assignments/').status_code, 403)
        self.assertEqual(client.get(f'/api/reviewer/invitations/{invitation.pk}/').status_code, 200)
        self.assertEqual(client.post(f'/api/reviewer/invitations/{invitation.pk}/accept/').status_code, 200)
        self.assertEqual(client.get('/api/reviewer/assignments/').status_code, 200)

    def test_chair_operations_deny_other_roles_and_do_not_mutate(self):
        for user in (self.outsider, self.author, self.reviewer):
            client = self.cliente_de(user)
            requests = [
                ('post', '/api/chair/new/', {'reviewer': self.outsider.pk, 'article': self.article.pk}),
                ('delete', f'/api/chair/{self.reviewer.pk}/{self.article.pk}/delete/', {}),
                ('post', f'/api/selection/cut-off/{self.session.pk}/', {'percentage': 50}),
                ('post', f'/api/selection/score-threshold/{self.session.pk}/', {'cutoff_score': 0}),
                ('post', f'/api/session/{self.session.pk}/lock-selection/', {'type_selection': 'CutoffSelection'}),
                ('patch', f'/api/session/{self.session.pk}/', {'capacity': 500}),
            ]
            for method, url, data in requests:
                with self.subTest(user=user.pk, url=url):
                    self.assertEqual(getattr(client, method)(url, data, format='json').status_code, 403)
        self.session.refresh_from_db()
        self.assignment.refresh_from_db()
        self.assertFalse(self.session.locked_selection)
        self.assertEqual(self.session.capacity, 10)
        self.assertFalse(self.assignment.deleted)
        self.assertEqual(ReviewAssignment.objects.count(), 1)

    def test_chair_reads_deny_other_roles(self):
        paths = [
            f'/api/chair/articles/{self.article.pk}/available-reviewers/',
            f'/api/chair/articles/{self.article.pk}/reviews/',
            f'/api/chair/articles-reviewed/?session_id={self.session.pk}',
            f'/api/selection/session/{self.session.pk}/articles/?status=accepted',
            f'/api/session/{self.session.pk}/articles/',
            f'/user/sessions/{self.session.pk}/reviewers/',
            f'/api/conference/{self.conference.pk}/users/',
        ]
        for user in (self.outsider, self.author, self.reviewer):
            client = self.cliente_de(user)
            for path in paths:
                with self.subTest(user=user.pk, path=path):
                    self.assertEqual(client.get(path).status_code, 403)

    def test_both_chair_roles_can_assign_reviewer(self):
        for user in (self.chair, self.session_chair):
            with self.subTest(user=user.pk):
                response = self.cliente_de(user).post('/api/chair/new/', {
                    'reviewer': self.outsider.pk, 'article': self.article.pk,
                    'assigned_by': self.author.pk,
                }, format='json')
                self.assertIn(response.status_code, (200, 201))
                self.assertEqual(self.article.history_events.latest('id').created_by_id, user.pk)

    def test_chair_in_another_conference_cannot_operate_here(self):
        self.other.chairs.add(self.outsider)
        client = self.cliente_de(self.outsider)
        self.assertEqual(client.post('/api/chair/new/', {
            'reviewer': self.outsider.pk, 'article': self.article.pk,
        }, format='json').status_code, 403)
        self.assertEqual(client.get(f'/api/chair/articles/{self.article.pk}/reviews/').status_code, 403)

    def test_body_cannot_override_session_target_for_authorization(self):
        client = self.cliente_de(self.chair)
        response = client.post(f'/api/session/{self.other_session.pk}/lock-selection/', {
            'article': self.article.pk, 'type_selection': 'CutoffSelection',
        }, format='json')
        self.assertEqual(response.status_code, 403)
        self.other_session.refresh_from_db()
        self.assertFalse(self.other_session.locked_selection)
        response = client.post(f'/api/chair/new/?session_id={self.session.pk}', {
            'article': self.other_article.pk, 'reviewer': self.outsider.pk,
        }, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReviewAssignment.objects.filter(article=self.other_article).exists())

    def test_token_identity_cannot_be_spoofed_for_periods_or_conference(self):
        admin = self.crear_usuario(role='admin')
        client = self.cliente_de(self.author)
        url = f'/api/conference/{self.conference.pk}/'
        for identity in (self.chair.pk, admin.pk):
            self.assertEqual(client.patch(url + 'periods/', {
                'user_id': identity, 'review_start': str(date.today()),
            }, format='json').status_code, 403)
        self.assertEqual(client.patch(url, {'user_id': admin.pk, 'description': 'Unauthorized'}, format='json').status_code, 403)

    def test_chair_can_change_periods_using_token_without_client_identity(self):
        response = self.cliente_de(self.chair).patch(f'/api/conference/{self.conference.pk}/periods/', {
            'review_start': str(date.today()),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_author_operations_require_authorship_even_for_submitted_article(self):
        for user in (self.outsider, self.reviewer, self.chair):
            client = self.cliente_de(user)
            url = f'/api/article/{self.article.pk}/'
            with self.subTest(user=user.pk):
                self.assertEqual(client.patch(url, {'title': 'Unauthorized'}, format='json').status_code, 403)
                self.assertEqual(client.delete(url).status_code, 403)
                self.assertEqual(client.get(url + 'history/').status_code, 403)
        self.article.refresh_from_db()
        self.assertNotEqual(self.article.title, 'Unauthorized')

    def test_shared_article_reads_allow_related_roles_only(self):
        url = f'/api/article/{self.article.pk}/'
        for user in (self.author, self.reviewer, self.chair, self.session_chair):
            self.assertEqual(self.cliente_de(user).get(url).status_code, 200)
        self.assertEqual(self.cliente_de(self.outsider).get(url).status_code, 403)
        self.assertEqual(self.cliente_de(self.chair).get(f'/api/article/{self.other_article.pk}/').status_code, 403)

    def test_article_list_and_counts_do_not_leak_unrelated_articles(self):
        self.article.authors.add(self.crear_usuario())
        self.crear_review(self.reviewer, self.article, publicada=True)
        self.crear_review(self.outsider, self.other_article, publicada=True)
        client = self.cliente_de(self.author)
        self.assertEqual([item['id'] for item in client.get('/api/article/').json()], [self.article.pk])
        self.assertEqual(client.get('/api/article/review-counts/').json(), [{'id': self.article.pk, 'review_count': 1}])
        self.assertEqual(self.cliente_de(self.outsider).get('/api/article/').json(), [])

    def test_author_cannot_read_another_authors_article(self):
        another = self.crear_articulo(sesion=self.session)
        self.assertEqual(self.cliente_de(self.author).get(f'/api/article/{another.pk}/').status_code, 403)

    def test_reviewer_sections_require_reviewer_role_in_requested_conference(self):
        paths = ['/api/reviewer/assignments/', '/api/bids/', f'/api/reviewer/sessions/{self.session.pk}/periods/']
        for user in (self.outsider, self.author, self.chair):
            for path in paths:
                self.assertEqual(self.cliente_de(user).get(path).status_code, 403)
        client = self.cliente_de(self.reviewer)
        self.assertEqual(client.get('/api/reviewer/assignments/').status_code, 200)
        self.assertEqual(client.get(f'/api/reviewer/assignments/?conference_id={self.other.pk}').status_code, 403)
        self.assertEqual(client.post('/api/bidding/', {'article': self.other_article.pk, 'choice': 'Interesado'}, format='json').status_code, 403)

    def test_reviewer_can_only_read_own_drafts(self):
        other_reviewer = self.crear_usuario()
        self.crear_asignacion(other_reviewer, self.article)
        self.crear_review(other_reviewer, self.article, opinion='Private other review')
        own = self.crear_review(self.reviewer, self.article, opinion='Own review')
        client = self.cliente_de(self.reviewer)
        self.assertEqual(client.get(f'/api/reviews/{self.article.pk}/').json()['id'], own.pk)
        self.assertEqual(client.get(f'/api/reviews/reviewer/{other_reviewer.pk}/').status_code, 403)

    def test_admin_retains_existing_access(self):
        client = self.cliente_de(self.crear_usuario(role='admin'))
        self.assertEqual(client.get(f'/api/chair/articles/{self.article.pk}/reviews/').status_code, 200)
        self.assertEqual(client.get(f'/api/conference/{self.conference.pk}/users/').status_code, 200)

    def test_access_requires_token(self):
        self.assertEqual(self.client.get('/user/access/').status_code, 401)
