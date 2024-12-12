from flask_caching import Cache

cache = Cache(config={'CACHE_TYPE': 'flask_caching.backends.SimpleCache'})

def init_cache(app):
    cache.init_app(app)

def set_memoized_value(func, value, *args):
    """Directly set a value instead of calling the memoized function."""
    key = func.make_cache_key(func.uncached, *args)
    cache.set(key, value, timeout=func.cache_timeout)
