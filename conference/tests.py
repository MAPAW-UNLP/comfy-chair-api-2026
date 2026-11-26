from datetime import date, datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient, APIRequestFactory
from article.models import Article
from chair.models import ReviewAssignment
from conference.models import Conference
from conference.serializers import ConferenceSerializer
from conference.views import ConferenceViewSet
from conference_session.models import Session
from notification.models import Notification
from user.models import User


def create_user(email, **kwargs):
    return User.objects.create(
        email=email,
        full_name=kwargs.pop("full_name", email),
        affiliation="MAPAW",
        role="user",
        **kwargs,
    )


class ConferenceUsersActionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        requester = User.objects.create(
            email="requester@example.com",
            full_name="Requester",
            affiliation="MAPAW",
            role="user",
        )
        self.client.force_authenticate(user=requester)
        token = jwt.encode(
            {
                "user_id": requester.id,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.conference = Conference.objects.create(
            title="Conference A",
            description="First conference",
            start_date=date(2030, 1, 1),
            end_date=date(2030, 1, 10),
        )
        self.other_conference = Conference.objects.create(
            title="Conference B",
            description="Second conference",
            start_date=date(2030, 2, 1),
            end_date=date(2030, 2, 10),
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
            main_file="articles/example.pdf",
            type="regular",
            abstract="Article abstract",
            corresponding_author=authors[0],
            session=session,
        )
        article.authors.set(authors)
        return article

    def test_lists_users_with_all_conference_roles(self):
        conference_chair = create_user("conference-chair@example.com")
        session_chair = create_user("session-chair@example.com")
        author = create_user("author@example.com")
        reviewer = create_user("reviewer@example.com")
        multi_role_user = create_user("multi-role@example.com")
        session = self.create_session(
            self.conference,
            "Session A",
            session_chair,
            multi_role_user,
        )
        article = self.create_article(
            session,
            "Article A",
            author,
            multi_role_user,
        )
        self.conference.chairs.add(conference_chair, multi_role_user)
        ReviewAssignment.objects.create(reviewer=reviewer, article=article)

        response = self.client.get(
            reverse("conference-users", kwargs={"pk": self.conference.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["conference_id"], self.conference.pk)
        users = {user["id"]: user for user in response.data["users"]}
        self.assertEqual(
            users[conference_chair.pk]["email"],
            conference_chair.email,
        )
        self.assertEqual(
            users[conference_chair.pk]["roles"],
            ["conference_chair"],
        )
        self.assertEqual(users[session_chair.pk]["roles"], ["session_chair"])
        self.assertEqual(users[author.pk]["roles"], ["author"])
        self.assertEqual(users[reviewer.pk]["roles"], ["reviewer"])
        self.assertEqual(
            users[multi_role_user.pk]["roles"],
            ["conference_chair", "session_chair", "author"],
        )

    def test_excludes_users_outside_conference_and_inactive_roles(self):
        other_session_chair = create_user("other-chair@example.com")
        deleted_user = create_user(
            "deleted@example.com",
            deleted=True,
        )
        inactive_user = create_user(
            "inactive@example.com",
            is_active=False,
        )
        active_reviewer = create_user("active-reviewer@example.com")
        session = self.create_session(
            self.other_conference,
            "Other session",
            other_session_chair,
        )
        article = self.create_article(session, "Other article", deleted_user)
        ReviewAssignment.objects.create(
            reviewer=active_reviewer,
            article=article,
            deleted=True,
        )
        self.conference.chairs.add(deleted_user, inactive_user)

        response = self.client.get(
            reverse("conference-users", kwargs={"pk": self.conference.pk})
        )

        self.assertEqual(response.status_code, 200)
        returned_ids = {user["id"] for user in response.data["users"]}
        self.assertEqual(returned_ids, set())

    def test_returns_not_found_for_unknown_conference(self):
        response = self.client.get(reverse("conference-users", kwargs={"pk": 999999}))

        self.assertEqual(response.status_code, 404)

    def test_search_filters_by_full_name_case_insensitively(self):
        matching_author = create_user(
            "matching-author@example.com",
            full_name="Ana Pérez",
        )
        non_matching_author = create_user(
            "other-author@example.com",
            full_name="Luis Gómez",
        )
        session = self.create_session(self.conference, "Session A")
        self.create_article(session, "Article A", matching_author)
        self.create_article(session, "Article B", non_matching_author)

        response = self.client.get(
            reverse("conference-users", kwargs={"pk": self.conference.pk}),
            {"search": "  aNa  "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [user["id"] for user in response.data["users"]],
            [matching_author.pk],
        )

    def test_search_filters_by_email_case_insensitively(self):
        matching_author = create_user(
            "ana.perez@example.com",
            full_name="Ana Pérez",
        )
        non_matching_author = create_user(
            "luis.gomez@example.com",
            full_name="Luis Gómez",
        )
        session = self.create_session(self.conference, "Session A")
        self.create_article(session, "Article A", matching_author)
        self.create_article(session, "Article B", non_matching_author)

        response = self.client.get(
            reverse("conference-users", kwargs={"pk": self.conference.pk}),
            {"search": "  ANA.PEREZ@EXAMPLE.COM  "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [user["id"] for user in response.data["users"]],
            [matching_author.pk],
        )

    def test_role_filter_returns_matching_users_with_all_their_roles(self):
        multi_role_user = create_user(
            "multi-role@example.com",
            full_name="Ana Pérez",
        )
        conference_chair = create_user(
            "chair@example.com",
            full_name="Luis Gómez",
        )
        session = self.create_session(
            self.conference,
            "Session A",
            multi_role_user,
        )
        article = self.create_article(session, "Article A", multi_role_user)
        self.conference.chairs.add(multi_role_user, conference_chair)
        ReviewAssignment.objects.create(
            reviewer=multi_role_user,
            article=article,
        )

        response = self.client.get(
            reverse("conference-users", kwargs={"pk": self.conference.pk}),
            {"role": "author"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [user["id"] for user in response.data["users"]],
            [multi_role_user.pk],
        )
        self.assertEqual(
            response.data["users"][0]["roles"],
            ["conference_chair", "session_chair", "author", "reviewer"],
        )

    def test_invalid_role_returns_bad_request(self):
        response = self.client.get(
            reverse("conference-users", kwargs={"pk": self.conference.pk}),
            {"role": "admin"},
        )

        self.assertEqual(response.status_code, 400)

    def test_removing_reviewer_role_attaches_session_to_notification(self):
        actor = User.objects.create(
            email="admin@example.com",
            full_name="Conference Admin",
            affiliation="MAPAW",
            role="admin",
        )
        reviewer = create_user("reviewer@example.com")
        author = create_user("author@example.com")
        session = self.create_session(self.conference, "Session A", actor)
        article = self.create_article(session, "Article A", author)
        ReviewAssignment.objects.create(reviewer=reviewer, article=article)
        request = APIRequestFactory().patch(
            "/",
            {"role": "reviewer", "action": "remove"},
            format="json",
        )
        request.user_id = actor.pk

        response = ConferenceViewSet.as_view({"patch": "update_user_role"})(
            request, pk=self.conference.pk, user_id=reviewer.pk
        )

        self.assertEqual(response.status_code, 200)
        notification = Notification.objects.get(
            user=actor,
            title="Reasignar revisiones pendientes",
        )
        self.assertEqual(notification.session, session)


class ConferenceAlphabeticalOrderingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        requester = User.objects.create(
            email="ordering-requester@example.com",
            full_name="Ordering Requester",
            affiliation="MAPAW",
            role="user",
        )
        self.client.force_authenticate(user=requester)
        token = jwt.encode(
            {
                "user_id": requester.id,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        today = django_timezone.now().date()
        conferences = (
            ("Zeta", today + timedelta(days=30)),
            ("delta", today - timedelta(days=1)),
            ("alpha", today + timedelta(days=30)),
            ("Bravo", today - timedelta(days=1)),
        )
        for title, end_date in conferences:
            Conference.objects.create(
                title=title,
                description="Shared topic",
                start_date=end_date - timedelta(days=7),
                end_date=end_date,
            )

    def test_conference_lists_are_alphabetical_with_and_without_search(self):
        expected_titles = {
            "conference-list": ["alpha", "Bravo", "delta", "Zeta"],
            "conference-active": ["alpha", "Zeta"],
            "conference-finished": ["Bravo", "delta"],
        }
        for endpoint, titles in expected_titles.items():
            for params in ({}, {"search": "  SHARED  "}):
                with self.subTest(endpoint=endpoint, params=params):
                    response = self.client.get(reverse(endpoint), params)

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        [conference["title"] for conference in response.data],
                        titles,
                    )


class ConferencePeriodsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        requester = User.objects.create(
            email="requester@example.com",
            full_name="Requester",
            affiliation="MAPAW",
            role="user",
        )
        self.client.force_authenticate(user=requester)
        token = jwt.encode(
            {
                "user_id": requester.id,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.conference = Conference.objects.create(
            title="Conference A",
            description="First conference",
            start_date=date(2030, 1, 1),
            end_date=date(2030, 1, 10),
        )
        self.chair_user = create_user("chair@example.com")
        self.conference.chairs.add(self.chair_user)

    def test_start_and_end_date(self):
        start_date = str(date.today() - timedelta(days=1))
        serializer = ConferenceSerializer(
            self.conference, data={"start_date": start_date}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors["start_date"][0],  # type: ignore
            "La fecha de inicio debe ser hoy o en el futuro.",
        )

        start_date = date.today() + timedelta(days=1)
        end_date = date.today() - timedelta(days=1)
        serializer = ConferenceSerializer(
            self.conference,
            data={"start_date": start_date, "end_date": end_date},
            partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors["end_date"][0],  # type: ignore
            "La fecha de fin debe ser hoy o en el futuro.",
        )

        start_date = date.today() + timedelta(weeks=2)
        end_date = date.today() + timedelta(weeks=1)
        serializer = ConferenceSerializer(
            self.conference,
            data={"start_date": start_date, "end_date": end_date},
            partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors["end_date"][0],  # type: ignore
            "La fecha de fin no puede ser anterior a la fecha de inicio.",
        )

        start_date = date.today() + timedelta(weeks=2)
        end_date = date.today() + timedelta(weeks=3)
        serializer = ConferenceSerializer(
            self.conference,
            data={"start_date": start_date, "end_date": end_date},
            partial=True,
        )
        self.assertTrue(serializer.is_valid())

    def test_submission_dates(self):
        response = self.client.patch(
            reverse("conference-update-periods", kwargs={"pk": self.conference.pk}),
            {
                "user_id": self.chair_user.id,
                "submission_start": date.today() + timedelta(weeks=2),
                "submission_end": date.today() + timedelta(weeks=1),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["submission_end"][0],
            "submission_end no puede ser anterior a submission_start.",
        )

    def test_review_dates(self):
        response = self.client.patch(
            reverse("conference-update-periods", kwargs={"pk": self.conference.pk}),
            {
                "user_id": self.chair_user.id,
                "review_start": date.today() + timedelta(weeks=2),
                "review_end": date.today() + timedelta(weeks=1),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["review_end"][0],
            "review_end no puede ser anterior a review_start.",
        )

    def test_bidding_dates(self):
        response = self.client.patch(
            reverse("conference-update-periods", kwargs={"pk": self.conference.pk}),
            {
                "user_id": self.chair_user.id,
                "bidding_start": date.today() + timedelta(weeks=2),
                "bidding_end": date.today() + timedelta(weeks=1),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["bidding_end"][0],
            "bidding_end no puede ser anterior a bidding_start.",
        )

    def test_deadline_date(self):
        response = self.client.patch(
            reverse("conference-update-periods", kwargs={"pk": self.conference.pk}),
            {
                "user_id": self.chair_user.id,
                "review_end": date.today() + timedelta(weeks=2),
                "camera_ready_deadline": date.today() + timedelta(weeks=1),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["camera_ready_deadline"][0],
            "El deadline de camera ready tiene que ser posterior o igual a la fecha de fin de revisión",
        )

        response = self.client.patch(
            reverse("conference-update-periods", kwargs={"pk": self.conference.pk}),
            {
                "user_id": self.chair_user.id,
                "camera_ready_deadline": self.conference.end_date + timedelta(days=1),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["camera_ready_deadline"][0],
            "El deadline de camera ready tiene que ser anterior a la fecha de fin",
        )

        response = self.client.patch(
            reverse("conference-update-periods", kwargs={"pk": self.conference.pk}),
            {
                "user_id": self.chair_user.id,
                "review_end": date.today() + timedelta(weeks=2),
                "camera_ready_deadline": date.today() + timedelta(weeks=3),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        response = self.client.patch(
            reverse("conference-update-periods", kwargs={"pk": self.conference.pk}),
            {
                "user_id": self.chair_user.id,
                "review_end": date.today() + timedelta(weeks=2),
                "camera_ready_deadline": date.today() + timedelta(weeks=2),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        response = self.client.patch(
            reverse("conference-update-periods", kwargs={"pk": self.conference.pk}),
            {
                "user_id": self.chair_user.id,
                "review_end": date.today() + timedelta(weeks=2),
                "camera_ready_deadline": self.conference.end_date,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

