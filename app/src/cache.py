from flask_caching import Cache

cache = Cache(config={'CACHE_TYPE': 'flask_caching.backends.SimpleCache'})

def init_cache(app):
    cache.init_app(app)
