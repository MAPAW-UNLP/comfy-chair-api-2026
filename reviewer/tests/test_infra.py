"""Tests de humo de la infraestructura (issue 0.1-BE).

No prueban funcionalidad del rol: comprueban que los helpers de ReviewerTestCase
hacen lo que dicen, para poder confiar en ellos en las demás issues.
"""
import jwt
from django.conf import settings

from .base import PASSWORD_DE_PRUEBA, ReviewerTestCase

# Ruta que no existe: el middleware JWT corre antes de resolver la URL, así que
# alcanza para probar el token sin depender de ningún endpoint real.
RUTA_INEXISTENTE = "/reviewer/ruta-que-no-existe/"


class HelpersDeDatosTests(ReviewerTestCase):
    def test_usuario_guarda_la_password_hasheada(self):
        usuario = self.crear_usuario()
        self.assertTrue(usuario.check_password(PASSWORD_DE_PRUEBA))
        self.assertNotEqual(usuario.password, PASSWORD_DE_PRUEBA)

    def test_usuarios_distintos_tienen_email_distinto(self):
        self.assertNotEqual(self.crear_usuario().email, self.crear_usuario().email)

    def test_sesion_cae_dentro_del_rango_de_la_conferencia(self):
        sesion = self.crear_sesion()
        conferencia = sesion.conference
        self.assertTrue(conferencia.start_date <= sesion.deadline <= conferencia.end_date)

    def test_dos_sesiones_en_la_misma_conferencia_no_chocan(self):
        conferencia = self.crear_conferencia()
        self.crear_sesion(conferencia)
        self.crear_sesion(conferencia)
        self.assertEqual(conferencia.session.count(), 2)

    def test_articulo_queda_con_autor_y_sesion(self):
        autor = self.crear_usuario()
        articulo = self.crear_articulo(autor=autor)
        self.assertIn(autor, articulo.authors.all())
        self.assertEqual(articulo.corresponding_author, autor)
        self.assertIsNotNone(articulo.session)

    def test_articulo_sin_sesion(self):
        articulo = self.crear_articulo(con_sesion=False)
        self.assertIsNone(articulo.session)

    def test_asignacion_y_review(self):
        revisor = self.crear_usuario()
        articulo = self.crear_articulo()
        asignacion = self.crear_asignacion(revisor, articulo)
        borrador = self.crear_review(revisor, articulo)
        self.assertFalse(asignacion.deleted)
        self.assertFalse(asignacion.reviewed)
        self.assertFalse(borrador.is_published)


class MediaTemporalTests(ReviewerTestCase):
    def test_el_archivo_se_guarda_en_la_carpeta_temporal(self):
        articulo = self.crear_articulo()
        self.assertTrue(articulo.main_file.path.startswith(self._media_dir))


class JWTTests(ReviewerTestCase):
    def test_el_token_trae_el_user_id(self):
        usuario = self.crear_usuario()
        payload = jwt.decode(
            self.token_para(usuario),
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        self.assertEqual(payload["user_id"], usuario.id)

    def test_sin_token_responde_401(self):
        respuesta = self.client.get(RUTA_INEXISTENTE)
        self.assertEqual(respuesta.status_code, 401)

    def test_con_token_valido_pasa_el_middleware(self):
        # 404 (y no 401) significa que el token fue aceptado.
        respuesta = self.cliente_de(self.crear_usuario()).get(RUTA_INEXISTENTE)
        self.assertEqual(respuesta.status_code, 404)

    def test_token_vencido_responde_401(self):
        cliente = self.cliente_de(self.crear_usuario(), expira_en=-10)
        self.assertEqual(cliente.get(RUTA_INEXISTENTE).status_code, 401)