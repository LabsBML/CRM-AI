# leads/templatetags/json_extras.py

import json
from django import template

register = template.Library()

@register.filter
def json_load(value):
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return [value]

    return [value]
