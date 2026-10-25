class ArticleCompletenessValidator:
    """Determina si los datos de un artículo alcanzan para dejar el estado 'draft'.

    No conoce Django ORM ni request: recibe los valores ya resueltos por quien
    la invoca (serializer o view), para quedar testeable en aislamiento y ser
    la única fuente de verdad sobre qué campos son obligatorios fuera de draft.
    """

    def __init__(self, *, title, abstract, main_file, article_type,
                 authors_count, corresponding_author, has_sources):
        self._valores = {
            'title': title,
            'abstract': abstract,
            'main_file': main_file,
            'type': article_type,
            'authors_ids': authors_count,
            'corresponding_author_id': corresponding_author,
        }
        self._article_type = article_type
        self._has_sources = has_sources

    def errors(self):
        errores = {
            campo: "Este campo es obligatorio."
            for campo, valor in self._valores.items()
            if not valor
        }
        if self._article_type == 'poster' and not self._has_sources:
            errores['source_file'] = "Los artículos tipo poster requieren un archivo fuente."
        return errores
