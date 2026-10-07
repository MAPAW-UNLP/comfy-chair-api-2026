# Conflicto de interés: un autor no puede revisar (ni ser asignado a revisar) su propio artículo.
# Lo usan reviewer/api.py (revisiones) y chair/api.py (asignación).
from django.db.models import Q

from article.models import Article

AUTHOR_CANNOT_REVIEW_ERROR = "No podés revisar un artículo del que sos autor"
AUTHOR_CANNOT_BE_ASSIGNED_ERROR = "No se puede asignar a un autor como revisor de su propio artículo"


def is_article_author(user, article):
    # Acepta instancias o ids. Autor = está en `authors` o es el autor de notificación
    return Article.objects.filter(pk=getattr(article, 'pk', article)).filter(
        Q(authors=user) | Q(corresponding_author=user)
    ).exists()


def article_author_ids(article):
    ids = set(article.authors.values_list('id', flat=True))
    if article.corresponding_author_id:
        ids.add(article.corresponding_author_id)
    return ids
