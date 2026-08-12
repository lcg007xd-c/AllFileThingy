from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "allfilethingy_session"


class Auth:
    def __init__(self, password: str, secret: str, max_age: int):
        self.password = password
        self.max_age = max_age
        self.signer = URLSafeTimedSerializer(secret, salt="allfilethingy-session-v1")

    def password_matches(self, candidate: str) -> bool:
        return hmac.compare_digest(
            candidate.encode("utf-8"), self.password.encode("utf-8")
        )

    def issue(self) -> str:
        return self.signer.dumps({"authenticated": True})

    def authenticated(self, request: Request) -> bool:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return False
        try:
            value = self.signer.loads(token, max_age=self.max_age)
        except (BadSignature, SignatureExpired):
            return False
        return value == {"authenticated": True}

    def require(self, request: Request) -> None:
        if not self.authenticated(request):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")

