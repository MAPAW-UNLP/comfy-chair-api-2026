from django.utils import timezone


def is_period_open(session, period, current_date=None):
    # grupo 5 - función para verificar si una sesión está dentro del período configurado
    """Devuelve si una sesión está dentro del período configurado.
        Las fechas de la sesión tienen prioridad sobre las fechas de la conferencia. Los límites faltantes permanecen abiertos para que las conferencias y sesiones existentes mantengan su comportamiento actual.
    """
    conference = session.conference
    start = getattr(session, f'{period}_start') or getattr(conference, f'{period}_start')
    end = getattr(session, f'{period}_end') or getattr(conference, f'{period}_end')
    current_date = current_date or timezone.localdate()

    return (start is None or current_date >= start) and (
        end is None or current_date <= end
    )