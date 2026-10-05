from datetime import date, datetime, timedelta
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
from reviewer.models import ReviewerInvitation
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
            {'id', 'status', 'conference', 'invited_by', 'sent_at', 'expires_at', 'responded_at'},
        )
        self.assertEqual(item['id'], invitation.id)
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(item['conference'], {'id': invitation.conference.id, 'title': 'CACIC'})
        self.assertEqual(item['invited_by'], {'id': self.chair.id, 'full_name': 'Chair Uno'})
        self.assertIsNone(item['responded_at'])

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
            {'id', 'status', 'conference', 'invited_by', 'sent_at', 'expires_at', 'responded_at'},
        )
        self.assertEqual(data['id'], invitation.id)
        self.assertEqual(data['status'], 'pending')
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
