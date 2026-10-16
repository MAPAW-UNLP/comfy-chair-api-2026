from article.models import ArticleHistory

def createHistoryEvent(accepted_articles, rejected_articles):
    new_statuses = {}
    for a in accepted_articles:
        new_statuses[a.id] = (a, "accepted")
    for a in rejected_articles:
        new_statuses[a.id] = (a, "rejected")

    for article_obj, new_status in new_statuses.values():
        # Obtener el último evento de veredicto final para este artículo
        last_history = (
            ArticleHistory.objects.filter(
                article=article_obj,
                event_type='final_verdict'
            )
            .order_by('-created_at', '-id')
            .first()
        )
        last_status = None
        if last_history and isinstance(last_history.metadata, dict):
            last_status = last_history.metadata.get('status')

        # Solo se crea si no existía veredicto previo o si el estado cambió
        if last_history is None or last_status != new_status:
            ArticleHistory.objects.create(
                event_type='final_verdict',
                article=article_obj,
                metadata={'status': new_status},
            )
