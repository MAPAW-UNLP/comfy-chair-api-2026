from datetime import date, datetime, timedelta
import unittest
from unittest import mock

import jwt
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from article.models import Article
from chair.models import ReviewAssignment
from conference.models import Conference
from conference_session.models import Session
from notification.models import Notification
from reviewer.models import Bid, InvitationNotification, Review, ReviewerInvitation
from user.models import User


INVITATIONS_URL = '/api/reviewer/invitations/'


def make_user(email, full_name='Usuario Test'):
    return User.objects.create(email=email, full_name=full_name, affiliation='UNLP', role='user')


def make_conference(title):
    return Conference.objects.create(
        title=title,
        description='Descripción',
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 5),
    )


def auth_header(user):
    # Mismo payload que genera el login (user/api.py)
    payload = {
        'user_id': user.id,
        'rol': user.role,
        'exp': datetime.utcnow() + timedelta(seconds=settings.JWT_EXP_DELTA_SECONDS),
        'iat': datetime.utcnow(),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


# Base para tests del rol revisor: usuarios de prueba y requests autenticados
class ReviewerTestCase(APITestCase):
    def setUp(self):
        self.reviewer = make_user('revisor@test.com', 'Revisor')
        self.other = make_user('otro@test.com', 'Otro')
        self.chair = make_user('chair@test.com', 'Chair Uno')

    def invite(self, conference, reviewer=None, **kwargs):
        return ReviewerInvitation.objects.create(
            reviewer=reviewer or self.reviewer, conference=conference, invited_by=self.chair, **kwargs,
        )

    def api_get(self, url, user=None):
        return self.client.get(url, **auth_header(user or self.reviewer))

    def api_post(self, url, body=None, user=None):
        return self.client.post(url, body or {}, format='json', **auth_header(user or self.reviewer))

    def invitation_url(self, invitation_id, action=None):
        return f'{INVITATIONS_URL}{invitation_id}/' + (f'{action}/' if action else '')


class ReviewerInvitationsViewTests(APITestCase):
    def setUp(self):
        self.reviewer = make_user('revisor@test.com', 'Revisor')
        self.other = make_user('otro@test.com', 'Otro')
        self.chair = make_user('chair@test.com', 'Chair Uno')
        self.now = timezone.now()

    def invite(self, conference_title, reviewer=None, **kwargs):
        return ReviewerInvitation.objects.create(
            reviewer=reviewer or self.reviewer,
            conference=make_conference(conference_title),
            invited_by=self.chair,
            **kwargs,
        )

    def get(self, params='', user=None):
        return self.client.get(INVITATIONS_URL + params, **auth_header(user or self.reviewer))

    def ids(self, response):
        return [item['id'] for item in response.json()['results']]

    def test_sin_token_devuelve_401(self):
        response = self.client.get(INVITATIONS_URL)
        self.assertEqual(response.status_code, 401)

    def test_sin_invitaciones_devuelve_lista_vacia(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'results': []})

    def test_contrato_de_respuesta(self):
        invitation = self.invite('CACIC', expires_at=self.now + timedelta(days=5))
        item = self.get().json()['results'][0]
        self.assertEqual(
            set(item.keys()),
            {'id', 'status', 'conference', 'invited_by', 'sent_at', 'expires_at', 'responded_at', 'rejection_reason'},
        )
        self.assertEqual(item['id'], invitation.id)
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(item['conference'], {'id': invitation.conference.id, 'title': 'CACIC'})
        self.assertEqual(item['invited_by'], {'id': self.chair.id, 'full_name': 'Chair Uno'})
        self.assertIsNone(item['responded_at'])
        self.assertEqual(item['rejection_reason'], '')

    def test_rechazada_incluye_el_motivo(self):
        self.invite('Rechazada', status='rejected', responded_at=self.now, rejection_reason='Sin disponibilidad')
        item = self.get('?status=rejected').json()['results'][0]
        self.assertEqual(item['rejection_reason'], 'Sin disponibilidad')

    def test_nunca_aparecen_invitaciones_de_otro_usuario(self):
        mine = self.invite('Conf A')
        self.invite('Conf B', reviewer=self.other)
        self.assertEqual(self.ids(self.get()), [mine.id])

    def test_el_revisor_no_se_toma_del_query(self):
        self.invite('Conf B', reviewer=self.other)
        response = self.get(f'?reviewer={self.other.id}&user_id={self.other.id}')
        self.assertEqual(response.json()['results'], [])

    def test_invitaciones_en_los_cuatro_estados(self):
        self.invite('Pendiente', expires_at=self.now + timedelta(days=1))
        self.invite('Aceptada', status='accepted', responded_at=self.now)
        self.invite('Rechazada', status='rejected', responded_at=self.now)
        self.invite('Vencida', expires_at=self.now - timedelta(days=1))
        statuses = {item['conference']['title']: item['status'] for item in self.get().json()['results']}
        self.assertEqual(statuses, {
            'Pendiente': 'pending',
            'Aceptada': 'accepted',
            'Rechazada': 'rejected',
            'Vencida': 'expired',
        })

    def test_filtro_pending_devuelve_solo_pendientes_no_vencidas(self):
        pending = self.invite('Pendiente', expires_at=self.now + timedelta(days=1))
        self.invite('Vencida', expires_at=self.now - timedelta(days=1))
        self.invite('Aceptada', status='accepted', responded_at=self.now)
        self.assertEqual(self.ids(self.get('?status=pending')), [pending.id])

    def test_filtro_expired(self):
        self.invite('Pendiente', expires_at=self.now + timedelta(days=1))
        expired = self.invite('Vencida', expires_at=self.now - timedelta(days=1))
        self.assertEqual(self.ids(self.get('?status=expired')), [expired.id])

    def test_filtro_accepted_y_rejected(self):
        accepted = self.invite('Aceptada', status='accepted', responded_at=self.now)
        rejected = self.invite('Rechazada', status='rejected', responded_at=self.now)
        self.assertEqual(self.ids(self.get('?status=accepted')), [accepted.id])
        self.assertEqual(self.ids(self.get('?status=rejected')), [rejected.id])

    def test_invitacion_sin_fecha_limite_no_vence(self):
        invitation = self.invite('Sin límite', expires_at=None)
        item = self.get().json()['results'][0]
        self.assertEqual(item['status'], 'pending')
        self.assertIsNone(item['expires_at'])
        self.assertEqual(self.ids(self.get('?status=pending')), [invitation.id])

    def test_status_invalido_devuelve_400(self):
        for value in ['cualquiera', '']:
            response = self.get(f'?status={value}')
            self.assertEqual(response.status_code, 400)
            self.assertIn('error', response.json())

    def test_orden(self):
        responded_old = self.invite('Respondida vieja', status='rejected', responded_at=self.now - timedelta(days=3))
        pending_no_limit = self.invite('Pendiente sin límite')
        pending_late = self.invite('Pendiente tarde', expires_at=self.now + timedelta(days=10))
        responded_new = self.invite('Respondida nueva', status='accepted', responded_at=self.now - timedelta(days=1))
        pending_soon = self.invite('Pendiente pronto', expires_at=self.now + timedelta(days=1))
        self.assertEqual(
            self.ids(self.get()),
            [pending_soon.id, pending_late.id, pending_no_limit.id, responded_new.id, responded_old.id],
        )

    def test_cantidad_de_queries_no_depende_de_la_cantidad_de_invitaciones(self):
        self.invite('Conf 0')
        with CaptureQueriesContext(connection) as one:
            self.get()

        for i in range(1, 20):
            ReviewerInvitation.objects.create(
                reviewer=self.reviewer,
                conference=make_conference(f'Conf {i}'),
                invited_by=make_user(f'chair{i}@test.com'),
            )
        with CaptureQueriesContext(connection) as twenty:
            response = self.get()

        self.assertEqual(len(response.json()['results']), 20)
        self.assertEqual(len(one), len(twenty))


class ReviewerInvitationDetailViewTests(APITestCase):
    def setUp(self):
        self.reviewer = make_user('revisor@test.com', 'Revisor')
        self.other = make_user('otro@test.com', 'Otro')
        self.chair = make_user('chair@test.com', 'Chair Uno')
        self.now = timezone.now()

    def invite(self, **kwargs):
        return ReviewerInvitation.objects.create(
            reviewer=self.reviewer,
            conference=make_conference('CACIC'),
            invited_by=self.chair,
            **kwargs,
        )

    def get(self, invitation_id, user=None):
        return self.client.get(f'{INVITATIONS_URL}{invitation_id}/', **auth_header(user or self.reviewer))

    def test_sin_token_devuelve_401(self):
        invitation = self.invite()
        response = self.client.get(f'{INVITATIONS_URL}{invitation.id}/')
        self.assertEqual(response.status_code, 401)

    def test_invitacion_pendiente(self):
        invitation = self.invite(expires_at=self.now + timedelta(days=5))
        response = self.get(invitation.id)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            set(data.keys()),
            {'id', 'status', 'conference', 'invited_by', 'sent_at', 'expires_at', 'responded_at', 'rejection_reason'},
        )
        self.assertEqual(data['id'], invitation.id)
        self.assertEqual(data['status'], 'pending')
        self.assertEqual(data['rejection_reason'], '')
        self.assertEqual(data['conference'], {
            'id': invitation.conference.id,
            'title': 'CACIC',
            'description': 'Descripción',
            'start_date': '2026-11-01',
            'end_date': '2026-11-05',
            'blind_kind': 'completo',
        })
        self.assertEqual(data['invited_by'], {'id': self.chair.id, 'full_name': 'Chair Uno'})
        self.assertIsNone(data['responded_at'])

    def test_invitacion_ya_respondida(self):
        invitation = self.invite(status='accepted', responded_at=self.now)
        data = self.get(invitation.id).json()
        self.assertEqual(data['status'], 'accepted')
        self.assertIsNotNone(data['responded_at'])

    def test_invitacion_rechazada_muestra_el_motivo(self):
        invitation = self.invite(status='rejected', responded_at=self.now, rejection_reason='Sin disponibilidad')
        data = self.get(invitation.id).json()
        self.assertEqual(data['status'], 'rejected')
        self.assertEqual(data['rejection_reason'], 'Sin disponibilidad')

    def test_invitacion_vencida(self):
        invitation = self.invite(expires_at=self.now - timedelta(days=1))
        self.assertEqual(self.get(invitation.id).json()['status'], 'expired')

    def test_otro_usuario_recibe_403(self):
        invitation = self.invite()
        response = self.get(invitation.id, user=self.other)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'error': 'No tenés acceso a esta invitación'})

    def test_invitacion_inexistente_devuelve_404(self):
        response = self.get(9999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'Invitación no encontrada'})


class RespondInvitationViewTests(APITestCase):
    def setUp(self):
        self.reviewer = make_user('revisor@test.com', 'Revisor')
        self.other = make_user('otro@test.com', 'Otro')
        self.chair = make_user('chair@test.com', 'Chair Uno')
        self.now = timezone.now()

    def invite(self, **kwargs):
        return ReviewerInvitation.objects.create(
            reviewer=self.reviewer,
            conference=make_conference('CACIC'),
            invited_by=self.chair,
            **kwargs,
        )

    def post(self, invitation_id, action, body=None, user=None):
        return self.client.post(
            f'{INVITATIONS_URL}{invitation_id}/{action}/',
            body or {},
            format='json',
            **auth_header(user or self.reviewer),
        )

    def snapshot(self, invitation):
        invitation.refresh_from_db()
        return (invitation.status, invitation.responded_at, invitation.rejection_reason)

    def assert_unchanged(self, invitation, before):
        self.assertEqual(self.snapshot(invitation), before)

    # --- Casos exitosos ---

    def test_aceptar_invitacion_pendiente(self):
        invitation = self.invite(expires_at=self.now + timedelta(days=1))
        response = self.post(invitation.id, 'accept')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['id'], invitation.id)
        self.assertEqual(data['status'], 'accepted')
        self.assertIsNotNone(data['responded_at'])
        self.assertIn('description', data['conference'])  # mismo serializer que el detalle

        invitation.refresh_from_db()
        self.assertEqual(invitation.status, 'accepted')
        self.assertIsNotNone(invitation.responded_at)

    def test_rechazar_sin_motivo(self):
        invitation = self.invite()
        response = self.post(invitation.id, 'reject')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'rejected')

        invitation.refresh_from_db()
        self.assertEqual(invitation.status, 'rejected')
        self.assertIsNotNone(invitation.responded_at)
        self.assertEqual(invitation.rejection_reason, '')

    def test_rechazar_con_motivo_lo_guarda(self):
        invitation = self.invite()
        response = self.post(invitation.id, 'reject', {'reason': 'Conflicto de interés'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rejection_reason'], 'Conflicto de interés')

        invitation.refresh_from_db()
        self.assertEqual(invitation.status, 'rejected')
        self.assertEqual(invitation.rejection_reason, 'Conflicto de interés')

    def test_invitacion_sin_fecha_limite_se_puede_aceptar(self):
        invitation = self.invite(expires_at=None)
        self.assertEqual(self.post(invitation.id, 'accept').status_code, 200)

    # --- Errores: no deben quedar cambios en la base ---

    def test_aceptar_dos_veces_devuelve_409_y_no_modifica(self):
        invitation = self.invite()
        self.post(invitation.id, 'accept')
        before = self.snapshot(invitation)

        response = self.post(invitation.id, 'accept')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'error': 'Esta invitación ya fue respondida'})
        self.assert_unchanged(invitation, before)

    def test_rechazar_dos_veces_devuelve_409_y_no_modifica(self):
        invitation = self.invite()
        self.post(invitation.id, 'reject', {'reason': 'Primer motivo'})
        before = self.snapshot(invitation)

        response = self.post(invitation.id, 'reject', {'reason': 'Otro motivo'})
        self.assertEqual(response.status_code, 409)
        self.assert_unchanged(invitation, before)

    def test_rechazar_una_aceptada_devuelve_409(self):
        invitation = self.invite(status='accepted', responded_at=self.now)
        before = self.snapshot(invitation)
        self.assertEqual(self.post(invitation.id, 'reject').status_code, 409)
        self.assert_unchanged(invitation, before)

    def test_vencida_devuelve_400(self):
        invitation = self.invite(expires_at=self.now - timedelta(days=1))
        before = self.snapshot(invitation)
        for action in ['accept', 'reject']:
            response = self.post(invitation.id, action)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {'error': 'La invitación está vencida'})
        self.assert_unchanged(invitation, before)

    def test_otro_usuario_recibe_403(self):
        invitation = self.invite()
        before = self.snapshot(invitation)
        for action in ['accept', 'reject']:
            response = self.post(invitation.id, action, user=self.other)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {'error': 'No tenés permiso sobre esta invitación'})
        self.assert_unchanged(invitation, before)

    def test_invitacion_inexistente_devuelve_404(self):
        for action in ['accept', 'reject']:
            response = self.post(9999, action)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {'error': 'Invitación no encontrada'})

    def test_motivo_demasiado_largo_devuelve_400(self):
        invitation = self.invite()
        before = self.snapshot(invitation)
        response = self.post(invitation.id, 'reject', {'reason': 'x' * 501})
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        self.assert_unchanged(invitation, before)

    def test_sin_token_devuelve_401(self):
        invitation = self.invite()
        for action in ['accept', 'reject']:
            response = self.client.post(f'{INVITATIONS_URL}{invitation.id}/{action}/')
            self.assertEqual(response.status_code, 401)


class ReviewerConferencesViewTests(APITestCase):
    URL = '/api/reviewer/conferences/'

    def setUp(self):
        self.reviewer = make_user('revisor@test.com', 'Revisor')
        self.other = make_user('otro@test.com', 'Otro')
        self.chair = make_user('chair@test.com', 'Chair Uno')

    def invite(self, conference, status='pending', reviewer=None):
        return ReviewerInvitation.objects.create(
            reviewer=reviewer or self.reviewer, conference=conference, invited_by=self.chair, status=status,
        )

    def assign(self, conference, reviewer=None, deleted=False, article_title='Artículo'):
        # El deadline de la sesión tiene que estar dentro de las fechas de la conferencia
        session, _ = Session.objects.get_or_create(
            conference=conference, title='Sesión 1',
            defaults={'deadline': conference.start_date, 'capacity': 10},
        )
        article = Article.objects.create(
            title=article_title, main_file='articles/test.pdf', type='regular',
            abstract='Resumen', session=session, corresponding_author=self.chair,
        )
        return ReviewAssignment.objects.create(reviewer=reviewer or self.reviewer, article=article, deleted=deleted)

    def get(self):
        return self.client.get(self.URL, **auth_header(self.reviewer))

    def test_sin_token_devuelve_401(self):
        self.assertEqual(self.client.get(self.URL).status_code, 401)

    def test_sin_conferencias_devuelve_lista_vacia(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'results': []})

    def test_invitacion_aceptada_sin_asignaciones(self):
        conference = make_conference('CACIC')
        self.invite(conference, status='accepted')
        self.assertEqual(
            self.get().json()['results'],
            [{'id': conference.id, 'title': 'CACIC', 'has_assignments': False}],
        )

    def test_invitacion_pendiente_rechazada_o_vencida_no_agrega_la_conferencia(self):
        self.invite(make_conference('Pendiente'), status='pending')
        self.invite(make_conference('Rechazada'), status='rejected')
        ReviewerInvitation.objects.create(
            reviewer=self.reviewer, conference=make_conference('Vencida'), invited_by=self.chair,
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertEqual(self.get().json()['results'], [])

    def test_con_asignaciones(self):
        conference = make_conference('CACIC')
        self.assign(conference)
        self.assertEqual(
            self.get().json()['results'],
            [{'id': conference.id, 'title': 'CACIC', 'has_assignments': True}],
        )

    def test_asignacion_borrada_no_cuenta(self):
        self.assign(make_conference('CACIC'), deleted=True)
        self.assertEqual(self.get().json()['results'], [])

    def test_asignacion_borrada_con_invitacion_aceptada_no_marca_has_assignments(self):
        conference = make_conference('CACIC')
        self.invite(conference, status='accepted')
        self.assign(conference, deleted=True)
        self.assertEqual(self.get().json()['results'][0]['has_assignments'], False)

    def test_sin_duplicados(self):
        conference = make_conference('CACIC')
        self.invite(conference, status='accepted')
        self.assign(conference, article_title='Artículo 1')
        self.assign(conference, article_title='Artículo 2')
        self.assertEqual(
            self.get().json()['results'],
            [{'id': conference.id, 'title': 'CACIC', 'has_assignments': True}],
        )

    def test_no_aparecen_conferencias_de_otro_usuario(self):
        self.invite(make_conference('Ajena invitación'), status='accepted', reviewer=self.other)
        self.assign(make_conference('Ajena asignación'), reviewer=self.other)
        self.assertEqual(self.get().json()['results'], [])

    def test_ordenadas_por_titulo(self):
        self.invite(make_conference('Zeta'), status='accepted')
        self.assign(make_conference('Alfa'))
        self.invite(make_conference('Medio'), status='accepted')
        titles = [c['title'] for c in self.get().json()['results']]
        self.assertEqual(titles, ['Alfa', 'Medio', 'Zeta'])


# Regresión: recorrido completo de una invitación a través de todos los endpoints
class ReviewerInvitationsRegressionTests(ReviewerTestCase):
    CONFERENCES_URL = '/api/reviewer/conferences/'

    def setUp(self):
        super().setUp()
        self.conference = make_conference('CACIC')

    def ids_with_status(self, status, user=None):
        response = self.api_get(f'{INVITATIONS_URL}?status={status}', user=user)
        return [item['id'] for item in response.json()['results']]

    def conference_ids(self, user=None):
        return [c['id'] for c in self.api_get(self.CONFERENCES_URL, user=user).json()['results']]

    def snapshot(self, invitation):
        invitation.refresh_from_db()
        return (invitation.status, invitation.responded_at, invitation.rejection_reason)

    # --- Escenario 1: flujo completo ---

    def test_flujo_completo_aceptar(self):
        invitation = self.invite(self.conference, expires_at=timezone.now() + timedelta(days=7))
        self.assertEqual(self.ids_with_status('pending'), [invitation.id])
        self.assertEqual(self.conference_ids(), [])

        response = self.api_post(self.invitation_url(invitation.id, 'accept'))
        self.assertEqual(response.status_code, 200)

        self.assertEqual(self.ids_with_status('pending'), [])
        self.assertEqual(self.ids_with_status('accepted'), [invitation.id])
        self.assertEqual(self.api_get(self.invitation_url(invitation.id)).json()['status'], 'accepted')
        self.assertEqual(self.conference_ids(), [self.conference.id])

    # --- Escenario 2: rechazo ---

    def test_rechazo_sin_motivo(self):
        invitation = self.invite(self.conference)
        response = self.api_post(self.invitation_url(invitation.id, 'reject'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ids_with_status('rejected'), [invitation.id])
        self.assertEqual(self.conference_ids(), [])

    def test_rechazo_con_motivo(self):
        invitation = self.invite(self.conference)
        response = self.api_post(self.invitation_url(invitation.id, 'reject'), {'reason': 'Sin disponibilidad'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(invitation)[2], 'Sin disponibilidad')
        self.assertEqual(self.ids_with_status('rejected'), [invitation.id])
        self.assertEqual(self.conference_ids(), [])

    # --- Escenario 3: vencimiento ---

    def test_vencimiento(self):
        now = timezone.now()
        invitation = self.invite(self.conference, expires_at=now + timedelta(days=1))
        self.assertEqual(self.ids_with_status('pending'), [invitation.id])

        with mock.patch('django.utils.timezone.now', return_value=now + timedelta(days=2)):
            self.assertEqual(self.ids_with_status('pending'), [])
            self.assertEqual(self.ids_with_status('expired'), [invitation.id])
            self.assertEqual(self.api_get(self.invitation_url(invitation.id)).json()['status'], 'expired')

            before = self.snapshot(invitation)
            response = self.api_post(self.invitation_url(invitation.id, 'accept'))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {'error': 'La invitación está vencida'})
            self.assertEqual(self.snapshot(invitation), before)

        self.assertEqual(self.conference_ids(), [])

    # --- Escenario 4: doble respuesta ---

    def test_aceptar_y_despues_rechazar_devuelve_409_sin_cambios(self):
        invitation = self.invite(self.conference)
        self.assertEqual(self.api_post(self.invitation_url(invitation.id, 'accept')).status_code, 200)
        before = self.snapshot(invitation)

        response = self.api_post(self.invitation_url(invitation.id, 'reject'), {'reason': 'Me arrepentí'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'error': 'Esta invitación ya fue respondida'})
        self.assertEqual(self.snapshot(invitation), before)
        self.assertEqual(self.ids_with_status('accepted'), [invitation.id])
        self.assertEqual(self.conference_ids(), [self.conference.id])

    # --- Escenario 5: aislamiento ---

    def test_dos_revisores_en_la_misma_conferencia_ven_solo_lo_suyo(self):
        mine = self.invite(self.conference)
        theirs = self.invite(self.conference, reviewer=self.other)

        self.assertEqual(self.ids_with_status('pending'), [mine.id])
        self.assertEqual(self.ids_with_status('pending', user=self.other), [theirs.id])

        # Responder o ver la invitación ajena da 403 y no la modifica
        before = self.snapshot(theirs)
        self.assertEqual(self.api_get(self.invitation_url(theirs.id)).status_code, 403)
        for action in ['accept', 'reject']:
            response = self.api_post(self.invitation_url(theirs.id, action))
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {'error': 'No tenés permiso sobre esta invitación'})
        self.assertEqual(self.snapshot(theirs), before)

        # Que uno acepte no afecta lo que ve el otro
        self.api_post(self.invitation_url(mine.id, 'accept'))
        self.assertEqual(self.conference_ids(), [self.conference.id])
        self.assertEqual(self.conference_ids(user=self.other), [])
        self.assertEqual(self.ids_with_status('pending', user=self.other), [theirs.id])

    # --- Escenario 6: privacidad ---

    def test_el_nombre_de_un_autor_no_aparece_en_ningun_endpoint(self):
        author = make_user('autora@test.com', 'Ada Privacidad Lovelace')
        session = Session.objects.create(
            conference=self.conference, title='Sesión 1', deadline=self.conference.start_date, capacity=10,
        )
        article = Article.objects.create(
            title='Artículo', main_file='articles/test.pdf', type='regular',
            abstract='Resumen', session=session, corresponding_author=author,
        )
        article.authors.add(author)
        ReviewAssignment.objects.create(reviewer=self.reviewer, article=article)

        to_accept = self.invite(self.conference)
        to_reject = self.invite(make_conference('Otra'))

        responses = [
            self.api_get(INVITATIONS_URL),
            self.api_get(self.invitation_url(to_accept.id)),
            self.api_post(self.invitation_url(to_accept.id, 'accept')),
            self.api_post(self.invitation_url(to_reject.id, 'reject')),
            self.api_get(self.CONFERENCES_URL),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b'Ada Privacidad Lovelace', response.content)
            self.assertNotIn(b'autora@test.com', response.content)

    # --- Errores en el flujo ---

    def test_invitacion_inexistente_devuelve_404_en_todos_los_endpoints(self):
        self.assertEqual(self.api_get(self.invitation_url(9999)).status_code, 404)
        for action in ['accept', 'reject']:
            self.assertEqual(self.api_post(self.invitation_url(9999, action)).status_code, 404)

    def test_sin_token_devuelve_401_en_todos_los_endpoints(self):
        invitation = self.invite(self.conference)
        before = self.snapshot(invitation)
        for url in [INVITATIONS_URL, self.invitation_url(invitation.id), self.CONFERENCES_URL]:
            self.assertEqual(self.client.get(url).status_code, 401)
        for action in ['accept', 'reject']:
            self.assertEqual(self.client.post(self.invitation_url(invitation.id, action)).status_code, 401)
        self.assertEqual(self.snapshot(invitation), before)


# Notificación al recibir una invitación (usa el sistema de notificaciones existente)
class ReviewerInvitationNotificationTests(ReviewerTestCase):
    TITLE = 'Nueva invitación para revisar'

    def invitation_notifications(self, user=None):
        return Notification.objects.filter(user=user or self.reviewer, title=self.TITLE)

    def test_crear_una_invitacion_notifica_al_revisor(self):
        self.invite(make_conference('CACIC'))
        notifications = self.invitation_notifications()
        self.assertEqual(notifications.count(), 1)
        notification = notifications.get()
        self.assertEqual(notification.type, 'info')
        self.assertFalse(notification.read)
        self.assertIn("'CACIC'", notification.message)
        self.assertIn('Chair Uno', notification.message)
        self.assertIn('Revisor → Invitaciones', notification.message)

    def test_el_mensaje_incluye_la_fecha_limite_si_tiene(self):
        expires_at = timezone.now() + timedelta(days=3)
        self.invite(make_conference('CACIC'), expires_at=expires_at)
        message = self.invitation_notifications().get().message
        self.assertIn(timezone.localtime(expires_at).strftime('%d/%m/%Y'), message)

    def test_sin_fecha_limite_no_menciona_plazo(self):
        self.invite(make_conference('CACIC'))
        self.assertNotIn('hasta el', self.invitation_notifications().get().message)

    def test_solo_se_notifica_al_invitado(self):
        self.invite(make_conference('CACIC'))
        self.assertEqual(self.invitation_notifications(user=self.other).count(), 0)
        self.assertEqual(self.invitation_notifications(user=self.chair).count(), 0)

    def test_aceptar_o_rechazar_no_genera_otra_notificacion_de_invitacion(self):
        accepted = self.invite(make_conference('Conf A'))
        rejected = self.invite(make_conference('Conf B'))
        self.api_post(self.invitation_url(accepted.id, 'accept'))
        self.api_post(self.invitation_url(rejected.id, 'reject'), {'reason': 'Sin tiempo'})
        self.assertEqual(self.invitation_notifications().count(), 2)  # una por cada invitación creada

    def test_la_notificacion_aparece_en_el_endpoint_de_notificaciones(self):
        self.invite(make_conference('CACIC'))
        response = self.api_get('/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.TITLE, [n['title'] for n in response.json()])

    # --- Notificación ligada a su invitación (aceptar/rechazar desde la notificación) ---

    LINKS_URL = '/api/reviewer/invitation-notifications/'

    def test_la_notificacion_conoce_su_invitacion(self):
        invitation = self.invite(make_conference('CACIC'))
        link = InvitationNotification.objects.get(user=self.reviewer)
        self.assertEqual(link.invitation, invitation)
        # Sigue siendo una Notification: la misma fila aparece en /notifications/
        self.assertTrue(Notification.objects.filter(id=link.id, title=self.TITLE).exists())

    def test_endpoint_lista_notificaciones_de_invitacion_con_su_estado(self):
        pending = self.invite(make_conference('Pendiente'))
        expired = self.invite(make_conference('Vencida'), expires_at=timezone.now() - timedelta(days=1))
        response = self.api_get(self.LINKS_URL)
        self.assertEqual(response.status_code, 200)
        by_invitation = {r['invitation']: r for r in response.json()['results']}
        self.assertEqual(by_invitation[pending.id]['status'], 'pending')
        self.assertEqual(by_invitation[expired.id]['status'], 'expired')
        self.assertEqual(by_invitation[pending.id]['conference_title'], 'Pendiente')
        self.assertEqual(
            by_invitation[pending.id]['notification'],
            InvitationNotification.objects.get(invitation=pending).id,
        )

    def test_endpoint_refleja_la_respuesta(self):
        invitation = self.invite(make_conference('CACIC'))
        self.api_post(self.invitation_url(invitation.id, 'accept'))
        self.assertEqual(self.api_get(self.LINKS_URL).json()['results'][0]['status'], 'accepted')

    def test_endpoint_solo_devuelve_las_propias_y_no_otras_notificaciones(self):
        self.invite(make_conference('Ajena'), reviewer=self.other)
        Notification.objects.create(user=self.reviewer, title='Otra cosa', message='...')
        self.assertEqual(self.api_get(self.LINKS_URL).json()['results'], [])

    def test_endpoint_sin_token_devuelve_401(self):
        self.assertEqual(self.client.get(self.LINKS_URL).status_code, 401)

    def test_borrar_la_invitacion_borra_su_notificacion(self):
        invitation = self.invite(make_conference('CACIC'))
        invitation.delete()
        self.assertFalse(Notification.objects.filter(user=self.reviewer, title=self.TITLE).exists())


# Solo un revisor con una asignación vigente puede crear y publicar una revisión
class ReviewAssignmentRequiredTests(ReviewerTestCase):
    NOT_ASSIGNED = {'error': 'No estás asignado para revisar este artículo'}

    def setUp(self):
        super().setUp()
        conference = make_conference('CACIC')
        session = Session.objects.create(
            conference=conference, title='Sesión 1', deadline=conference.start_date, capacity=10,
        )
        self.article = Article.objects.create(
            title='Artículo', main_file='articles/test.pdf', type='regular',
            abstract='Resumen', session=session, corresponding_author=self.chair,
        )

    def assign(self, deleted=False):
        return ReviewAssignment.objects.create(reviewer=self.reviewer, article=self.article, deleted=deleted)

    def create_review(self):
        return self.api_post('/api/reviews/', {
            'reviewer': self.reviewer.id, 'article': self.article.id, 'score': 2, 'opinion': 'Buen artículo',
        })

    def publish(self, review):
        return self.client.put(f'/api/reviews/{review.id}/publish/', **auth_header(self.reviewer))

    def test_crear_revision_sin_asignacion_devuelve_403_y_no_guarda_nada(self):
        response = self.create_review()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), self.NOT_ASSIGNED)
        self.assertFalse(Review.objects.exists())

    def test_crear_revision_con_asignacion_borrada_devuelve_403(self):
        self.assign(deleted=True)
        self.assertEqual(self.create_review().status_code, 403)
        self.assertFalse(Review.objects.exists())

    def test_crear_revision_con_asignacion_funciona(self):
        self.assign()
        self.assertEqual(self.create_review().status_code, 201)
        self.assertEqual(Review.objects.count(), 1)

    def test_publicar_sin_asignacion_devuelve_403_y_no_publica(self):
        review = Review.objects.create(reviewer=self.reviewer, article=self.article, score=1, opinion='Ok')
        response = self.publish(review)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), self.NOT_ASSIGNED)
        review.refresh_from_db()
        self.assertFalse(review.is_published)

    def test_publicar_con_asignacion_borrada_devuelve_403(self):
        self.assign(deleted=True)
        review = Review.objects.create(reviewer=self.reviewer, article=self.article, score=1, opinion='Ok')
        self.assertEqual(self.publish(review).status_code, 403)
        review.refresh_from_db()
        self.assertFalse(review.is_published)

    def test_publicar_con_asignacion_funciona_y_marca_revisado(self):
        assignment = self.assign()
        review = Review.objects.create(reviewer=self.reviewer, article=self.article, score=1, opinion='Ok')
        self.assertEqual(self.publish(review).status_code, 200)
        review.refresh_from_db()
        assignment.refresh_from_db()
        self.assertTrue(review.is_published)
        self.assertTrue(assignment.reviewed)

    # --- Editar ---

    def update(self, review, endpoint):
        return self.client.put(
            f'/api/reviews/{review.id}/{endpoint}/', {'opinion': 'Cambiada'},
            content_type='application/json', **auth_header(self.reviewer),
        )

    def test_editar_borrador_sin_asignacion_devuelve_403_y_no_cambia(self):
        review = Review.objects.create(reviewer=self.reviewer, article=self.article, score=1, opinion='Ok')
        response = self.update(review, 'updateDraft')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), self.NOT_ASSIGNED)
        review.refresh_from_db()
        self.assertEqual(review.opinion, 'Ok')

    def test_editar_publicada_sin_asignacion_devuelve_403_y_no_cambia(self):
        review = Review.objects.create(
            reviewer=self.reviewer, article=self.article, score=1, opinion='Ok', is_published=True,
        )
        response = self.update(review, 'updatePublished')
        self.assertEqual(response.status_code, 403)
        review.refresh_from_db()
        self.assertEqual(review.opinion, 'Ok')
        self.assertFalse(review.versions.exists())

    def test_editar_borrador_con_asignacion_funciona(self):
        self.assign()
        review = Review.objects.create(reviewer=self.reviewer, article=self.article, score=1, opinion='Ok')
        self.assertEqual(self.update(review, 'updateDraft').status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.opinion, 'Cambiada')

    # --- Acceso al formulario: GET /api/reviewer/articles/<id>/assignment/ ---

    def assignment_url(self, article_id):
        return f'/api/reviewer/articles/{article_id}/assignment/'

    def test_acceso_con_asignacion_devuelve_200(self):
        self.assign()
        response = self.api_get(self.assignment_url(self.article.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'assigned': True})

    def test_acceso_sin_asignacion_devuelve_403(self):
        response = self.api_get(self.assignment_url(self.article.id))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), self.NOT_ASSIGNED)

    def test_acceso_con_asignacion_borrada_devuelve_403(self):
        self.assign(deleted=True)
        self.assertEqual(self.api_get(self.assignment_url(self.article.id)).status_code, 403)

    def test_acceso_usa_el_usuario_del_token(self):
        # La asignación es del revisor: otro usuario logueado no tiene acceso
        self.assign()
        self.assertEqual(self.api_get(self.assignment_url(self.article.id), user=self.other).status_code, 403)

    def test_acceso_a_articulo_inexistente_devuelve_404(self):
        self.assertEqual(self.api_get(self.assignment_url(9999)).status_code, 404)

    def test_acceso_sin_token_devuelve_401(self):
        self.assertEqual(self.client.get(self.assignment_url(self.article.id)).status_code, 401)


# Conflicto de interés: un autor no puede ser asignado ni revisar su propio artículo
class AuthorConflictTests(ReviewerTestCase):
    AUTHOR_CANNOT_REVIEW = {'error': 'No podés revisar un artículo del que sos autor'}
    AUTHOR_CANNOT_BE_ASSIGNED = {'error': 'No se puede asignar a un autor como revisor de su propio artículo'}

    def setUp(self):
        super().setUp()
        conference = make_conference('CACIC')
        session = Session.objects.create(
            conference=conference, title='Sesión 1', deadline=conference.start_date, capacity=10,
        )
        # El revisor es coautor; el autor de notificación es otro usuario
        self.article = Article.objects.create(
            title='Artículo', main_file='articles/test.pdf', type='regular',
            abstract='Resumen', session=session, corresponding_author=self.other,
        )
        self.article.authors.add(self.reviewer, self.other)

    def assign_anyway(self, reviewer=None):
        # Una asignación que ya existía antes de este control (o creada por fuera de la API)
        return ReviewAssignment.objects.create(reviewer=reviewer or self.reviewer, article=self.article)

    # --- Revisión (app reviewer) ---

    def test_autor_asignado_no_puede_crear_revision(self):
        self.assign_anyway()
        response = self.api_post('/api/reviews/', {
            'reviewer': self.reviewer.id, 'article': self.article.id, 'score': 3, 'opinion': 'Excelente',
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), self.AUTHOR_CANNOT_REVIEW)
        self.assertFalse(Review.objects.exists())

    def test_autor_asignado_no_puede_publicar_ni_editar(self):
        self.assign_anyway()
        review = Review.objects.create(reviewer=self.reviewer, article=self.article, score=3, opinion='Ok')
        headers = auth_header(self.reviewer)
        publish = self.client.put(f'/api/reviews/{review.id}/publish/', **headers)
        draft = self.client.put(
            f'/api/reviews/{review.id}/updateDraft/', {'opinion': 'Cambiada'},
            content_type='application/json', **headers,
        )
        self.assertEqual(publish.status_code, 403)
        self.assertEqual(publish.json(), self.AUTHOR_CANNOT_REVIEW)
        self.assertEqual(draft.status_code, 403)
        review.refresh_from_db()
        self.assertFalse(review.is_published)
        self.assertEqual(review.opinion, 'Ok')

    def test_autor_asignado_no_accede_al_formulario(self):
        self.assign_anyway()
        response = self.api_get(f'/api/reviewer/articles/{self.article.id}/assignment/')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), self.AUTHOR_CANNOT_REVIEW)

    def test_autor_de_notificacion_tambien_cuenta_como_autor(self):
        # self.other es corresponding_author (y coautor); se prueba solo como autor de notificación
        self.article.authors.remove(self.other)
        self.assign_anyway(reviewer=self.other)
        response = self.api_get(f'/api/reviewer/articles/{self.article.id}/assignment/', user=self.other)
        self.assertEqual(response.json(), self.AUTHOR_CANNOT_REVIEW)

    # --- Asignación (app chair: dependencia externa pendiente) ---

    @unittest.expectedFailure
    def test_chair_no_puede_asignar_a_un_autor(self):
        response = self.api_post('/api/chair/new/', {'reviewer': self.reviewer.id, 'article': self.article.id}, user=self.chair)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), self.AUTHOR_CANNOT_BE_ASSIGNED)
        self.assertFalse(ReviewAssignment.objects.exists())

    def test_chair_puede_asignar_a_quien_no_es_autor(self):
        outsider = make_user('externo@test.com', 'Revisor externo')
        response = self.api_post('/api/chair/new/', {'reviewer': outsider.id, 'article': self.article.id}, user=self.chair)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(ReviewAssignment.objects.filter(reviewer=outsider, article=self.article).exists())

    @unittest.expectedFailure
    def test_los_autores_no_aparecen_como_revisores_disponibles(self):
        outsider = make_user('externo@test.com', 'Revisor externo')
        Bid.objects.create(reviewer=self.reviewer, article=self.article, choice='Interesado')
        response = self.api_get(f'/api/chair/articles/{self.article.id}/available-reviewers/', user=self.chair)
        self.assertEqual(response.status_code, 200)
        ids = {r['id'] for r in response.json()}
        self.assertNotIn(self.reviewer.id, ids)  # autor con bid
        self.assertNotIn(self.other.id, ids)  # autor sin bid
        self.assertIn(outsider.id, ids)
