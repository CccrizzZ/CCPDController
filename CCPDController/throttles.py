from rest_framework.throttling import SimpleRateThrottle

# default throttle class for login and register
class AppIDThrottle(SimpleRateThrottle):
    scope = 'global_app_id'
    rate = '30/min'

    def get_cache_key(self, request, view):
        app_id = request.headers.get('X-APP-ID')
        # if not app_id:
        #     return None
        return f'{self.scope}_{app_id}'
