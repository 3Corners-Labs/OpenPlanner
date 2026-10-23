"""HTTP entry point: FastAPI routers and ASGI middleware.

Routers translate HTTP to domain calls and back. Validation of external input happens here
(Pydantic models); business rules live in ``app.domain``.
"""
