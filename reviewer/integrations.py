"""Puntos de contacto con datos que dependen de otros grupos (Chairs)."""


def review_window(session):
    """Ventana de envío de revisiones de una sesión.

    Devuelve {"start": ..., "end": ..., "is_open": ...} con fechas en ISO 8601 UTC
    (o None si no hay límite).

    PROVISORIO: Session todavía no tiene campos de período de revisión (hoy solo
    tiene `deadline`, que es una fecha). Mientras no existan, la ventana está
    siempre abierta. La issue 5.1-BE completa esta función cuando Chairs agregue
    los campos; el formato de la respuesta no cambia, así que los llamadores
    (como el endpoint de asignaciones) no se tocan.
    """
    return {"start": None, "end": None, "is_open": True}