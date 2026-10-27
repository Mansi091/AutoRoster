"""Verify Clerk session tokens for protected API routes."""

import os

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import config

load_dotenv(config.ROOT_DIR / ".env")

_bearer = HTTPBearer(auto_error=False)


def _clerk_auth_config():
    secret_key = os.getenv("CLERK_SECRET_KEY", "").strip()
    jwt_key = os.getenv("CLERK_JWT_KEY", "").strip().replace("\\n", "\n") or None
    authorized_parties = [
        origin.strip().rstrip("/")
        for origin in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",")
        if origin.strip()
    ]
    return secret_key, jwt_key, authorized_parties


def require_authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in to access this resource.")

    secret_key, jwt_key, authorized_parties = _clerk_auth_config()
    if not (secret_key or jwt_key) or not authorized_parties:
        raise HTTPException(status_code=503, detail="Clerk authentication is not fully configured.")

    try:
        state = authenticate_request(
            request,
            AuthenticateRequestOptions(
                secret_key=secret_key or None,
                jwt_key=jwt_key,
                authorized_parties=authorized_parties,
                accepts_token=["session_token"],
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Could not verify your Clerk session.") from exc

    payload = state.payload or {}
    user_id = payload.get("sub")
    if not state.is_signed_in or not user_id:
        raise HTTPException(status_code=401, detail="Your sign-in has expired. Please sign in again.")
    return {"id": user_id, "email": payload.get("email", "")}
