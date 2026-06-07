from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
import time

_request_counts: dict = {}
RATE_LIMIT = 100
WINDOW = 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
        now = time.time()
        window_start = now - WINDOW
        calls = [t for t in _request_counts.get(client_ip, []) if t > window_start]
        if len(calls) >= RATE_LIMIT:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        calls.append(now)
        _request_counts[client_ip] = calls
        return await call_next(request)
