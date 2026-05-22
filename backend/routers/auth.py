import os
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from backend.auth import (
    clear_auth_cookies, decode_token, set_auth_cookies,
    create_access_token, create_refresh_token, ACCESS_TOKEN_EXPIRE, _secure,
)
from backend.dependencies import get_current_user
from backend.email import send_verification_email, send_password_reset_email
from backend.limiter import limiter
from core.crypto import hash_password, verify_password
from core.db import (
    create_user, get_user_by_username, get_user_by_email, get_user_by_id,
    update_user_password, seed_category_map,
    consume_refresh_token, store_refresh_token, revoke_user_refresh_tokens,
    create_email_verification_token, consume_email_verification_token,
    create_password_reset_token, consume_password_reset_token,
    get_last_synced_at,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=254)  # accepts username or email
    password: str = Field(min_length=1, max_length=128)


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str = Field(min_length=7, max_length=30)


class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


def _background_sync(user_id: int) -> None:
    try:
        import services.pull as pull_service
        pull_service.main(user_id, full_sync=False)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Background sync failed for user {user_id}: {e}")


def _background_warmup(user_id: int) -> None:
    # Pre-build the per-user DataFrame cache so the Overview page lands on a
    # warm cache instead of triggering an 8-way cache stampede on first load.
    try:
        from core import insights as ins
        ins.load_data(user_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Background warmup failed for user {user_id}: {e}")


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, body: LoginBody, response: Response, background_tasks: BackgroundTasks):
    # Accept either username or email
    user = get_user_by_username(body.username) or get_user_by_email(body.username)
    if not user or not verify_password(body.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Please verify your email before signing in")
    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="Account is not active")
    set_auth_cookies(response, user["id"], user["username"])

    # Auto-sync once per day on first login
    import datetime
    last = get_last_synced_at(user["id"])
    if not last or str(last)[:10] < datetime.date.today().isoformat():
        background_tasks.add_task(_background_sync, user["id"])

    # Warm the DataFrame cache so Overview lands on a hot cache. Runs after
    # sync (if scheduled), so the warmup reflects any newly-pulled transactions.
    background_tasks.add_task(_background_warmup, user["id"])

    return {"id": user["id"], "username": user["username"]}


@router.post("/register")
@limiter.limit("5/minute")
def register(request: Request, body: RegisterBody, response: Response):
    if get_user_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    if get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = create_user(
        username=body.username,
        password_hash=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        phone=body.phone,
    )
    seed_category_map(user_id)

    token = create_email_verification_token(user_id)
    send_verification_email(to=body.email, name=body.first_name, token=token)

    return {"message": "Account created. Check your email to verify your account."}


@router.post("/verify-email")
@limiter.limit("10/minute")
def verify_email(request: Request, body: dict, response: Response):
    token = body.get("token", "")
    user_id = consume_email_verification_token(token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=400, detail="User not found")
    set_auth_cookies(response, user_id, user["username"])
    return {"id": user_id, "username": user["username"]}


@router.post("/resend-verification")
@limiter.limit("3/minute")
def resend_verification(request: Request, body: ForgotPasswordBody):
    user = get_user_by_email(body.email)
    # Always return success to avoid email enumeration
    if user and not user.get("email_verified"):
        token = create_email_verification_token(user["id"])
        send_verification_email(to=user["email"], name=user.get("first_name", ""), token=token)
    return {"message": "If that email exists and is unverified, a new confirmation link has been sent."}


@router.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password(request: Request, body: ForgotPasswordBody):
    user = get_user_by_email(body.email)
    # Always return success to avoid email enumeration
    if user and user.get("is_active"):
        token = create_password_reset_token(user["id"])
        send_password_reset_email(to=user["email"], name=user.get("first_name", ""), token=token)
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
@limiter.limit("5/minute")
def reset_password(request: Request, body: ResetPasswordBody):
    user_id = consume_password_reset_token(body.token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    update_user_password(user_id, hash_password(body.new_password))
    revoke_user_refresh_tokens(user_id)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, current_user: Optional[dict] = Depends(get_current_user)):
    token = request.cookies.get("refresh_token")
    if token:
        payload = decode_token(token)
        if payload and payload.get("jti"):
            consume_refresh_token(payload["jti"])
    clear_auth_cookies(response)
    return {"ok": True}


@router.post("/refresh")
@limiter.limit("30/minute")
def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")

    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    row = consume_refresh_token(jti)
    if not row:
        user_id = int(payload["sub"])
        revoke_user_refresh_tokens(user_id)
        raise HTTPException(status_code=401, detail="Refresh token already used")

    user_id = row["user_id"]
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_refresh, new_jti, expires_at = create_refresh_token(user_id)
    store_refresh_token(new_jti, user_id, expires_at)

    access_token = create_access_token(user_id, user["username"])
    response.set_cookie(key="access_token", value=access_token, httponly=True,
                        secure=_secure, samesite="lax", max_age=ACCESS_TOKEN_EXPIRE * 60, path="/")
    response.set_cookie(key="refresh_token", value=new_refresh, httponly=True,
                        secure=_secure, samesite="lax", max_age=7 * 24 * 60 * 60, path="/")
    return {"ok": True}


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    return {"id": current_user["id"], "username": current_user["username"]}
