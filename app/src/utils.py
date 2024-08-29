class Storage:
    CARRIED = 'carried'
    LOCAL = 'local'
    UNIVERSAL = 'universal'
    TYPES = [CARRIED, LOCAL, UNIVERSAL]

def _get_from_request(request, key, source):
    """Retrieve a value from the request."""
    if source not in ['form', 'args']:
        raise ValueError("Source must be 'form' or 'args'")
    source_obj = getattr(request, source)
    return source_obj.get(key, '')

def request_int(request, key, default=0, source='form'):
    """Retrieve an integer value from the request."""
    value_str = _get_from_request(request, key, source)
    try:
        return int(value_str) if value_str else default
    except ValueError:
        return default

def request_float(request, key, default=0.0, source='form'):
    """Retrieve a floating point value from the request."""
    value_str = _get_from_request(request, key, source)
    try:
        return float(value_str) if value_str else default
    except ValueError:
        return default

def request_bool(request, key, default=False, source='form'):
    """Retrieve a boolean value from the request."""
    value_str = _get_from_request(request, key, source)
    try:
        return bool(value_str) if value_str else default
    except ValueError:
        return default
