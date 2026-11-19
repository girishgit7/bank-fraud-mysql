from datetime import date, datetime, timezone
from decimal import Decimal

from flask.json.provider import DefaultJSONProvider


class AppJSONProvider(DefaultJSONProvider):
    """Money as numbers, timestamps as UTC ISO strings (the browser formats them)."""

    sort_keys = False

    @staticmethod
    def default(o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            if o.tzinfo is None:
                o = o.replace(tzinfo=timezone.utc)
            return o.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(o, date):
            return o.isoformat()
        return DefaultJSONProvider.default(o)
