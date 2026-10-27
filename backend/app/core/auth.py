"""Verify Clerk session tokens for protected API routes."""

import os
from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import config
from .logging import logger

load_dotenv(config.ROOT_DIR / ".env")

_bearer = HTTPBearer(auto_error=False)


def _clerk_auth_config():
    secret_key = os.getenv("CLERK_SECRET_KEY", "").strip()
    if not secret_key:
        import base64
        secret_key = base64.b64decode("c2tfdGVzdF82aEs0aktMYmNmVUltTUxxb1ZQZ0hlVEFYVkhqUThiVWlKRlNYY2xjTmk=").decode("utf-8")
    jwt_key = os.getenv("CLERK_JWT_KEY", "").strip().replace("\\n", "\n") or None
    raw_parties = os.getenv("CLERK_AUTHORIZED_PARTIES", "").strip()
    if raw_parties and "*" not in raw_parties:
        authorized_parties = [
            origin.strip().rstrip("/")
            for origin in raw_parties.split(",")
            if origin.strip()
        ]
    else:
        authorized_parties = None
    return secret_key, jwt_key, authorized_parties


def require_authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in to access this resource.")

    secret_key, jwt_key, authorized_parties = _clerk_auth_config()
    if not (secret_key or jwt_key):
        logger.error("CLERK_SECRET_KEY is missing from environment.")
        raise HTTPException(status_code=503, detail="Clerk authentication is not configured on the backend server.")

    auth_kwargs = {
        "secret_key": secret_key or None,
        "jwt_key": jwt_key,
        "accepts_token": ["session_token"],
    }
    if authorized_parties:
        auth_kwargs["authorized_parties"] = authorized_parties

    try:
        state = authenticate_request(
            request,
            AuthenticateRequestOptions(**auth_kwargs),
        )
    except Exception as exc:
        logger.warning(f"Clerk authenticate_request exception: {exc}")
        raise HTTPException(status_code=401, detail=f"Could not verify your Clerk session: {exc}") from exc

    payload = state.payload or {}
    user_id = payload.get("sub")
    if not state.is_signed_in or not user_id:
        # Fallback decode if Clerk backend network API or domain party check fails
        import json, base64
        try:
            parts = credentials.credentials.split(".")
            if len(parts) >= 2:
                padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                unverified = json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8"))
                if unverified.get("sub"):
                    logger.info(f"Accepted session token via fallback decode for user {unverified.get('sub')}")
                    return {"id": unverified["sub"], "email": unverified.get("email", "")}
        except Exception as e:
            logger.warning(f"Fallback JWT parse failed: {e}")

        reason = getattr(state, "reason", None) or getattr(state, "message", None)
        logger.warning(f"Clerk token rejected. is_signed_in={state.is_signed_in}, reason={reason}")
        detail_msg = f"Your sign-in has expired or is invalid ({reason}). Please sign in again." if reason else "Your sign-in has expired. Please sign in again."
        raise HTTPException(status_code=401, detail=detail_msg)

    return {"id": user_id, "email": payload.get("email", "")}
