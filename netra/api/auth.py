"""
Authentication and authorisation for an air-gapped host.

WHAT IS DIFFERENT HERE, AND WHY
-------------------------------
Every default of web authentication assumes something this deployment does not
have: an email address to send a reset to, an identity provider to delegate to, a
network to reach it over. So the decisions below are deliberately not the defaults,
and each one is here because the usual answer breaks on the target machine.

  * **First administrator, created locally.** A CLI command, not a default
    credential in the repository. A shipped default password is the single most
    common way an appliance gets owned.
  * **Password reset is administrator-mediated.** An admin issues a single-use
    token out of band; the holder redeems it at the sign-in screen and both steps
    are audited. There is nothing to email.
  * **scrypt from the standard library.** argon2 and bcrypt are better libraries by
    a small margin and worse dependencies here: a wheel that has to be vendored for
    the target, and a wheel is a thing that can be missing.
  * **The whole application is gated.** Pages, assets, print routes and exports.
    A case dossier URL that renders without a session is a leak, and a report is
    the artefact most likely to be on somebody's screen when they step away.
  * **Roles are enforced server-side on every route.** The interface hides what a
    role may not do, but hiding a button was never access control.
  * **CSRF tokens on state-changing requests.** Cookie sessions require it, and
    this application mutates: analyses, dispositions, thresholds, model promotion.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from netra import config
from netra.state.product import (CSRF_COOKIE, PERMISSIONS, ROLES, SESSION_COOKIE,
                                 ProductStore, Session, User, new_token)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# The store is opened per request rather than held open: sqlite3 connections are
# not safe to share across threads, and FastAPI runs sync endpoints in a pool. One
# connection per request is cheap; the alternative is a corruption bug that appears
# only under concurrent use, which is exactly when an analyst is presenting.
def open_product_store() -> ProductStore:
    return ProductStore(config.STATE_DB)


@dataclass
class Principal:
    """Who is asking, resolved from the session cookie."""

    user: User
    session: Session

    @property
    def name(self) -> str:
        return self.user.name

    @property
    def role(self) -> str:
        return self.user.role

    def may(self, capability: str) -> bool:
        return capability in PERMISSIONS.get(self.role, set())

    def as_dict(self) -> dict[str, Any]:
        return {**self.user.as_dict(), "session": self.session.id[:8]}


def current_principal(
    netra_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Principal:
    """The authentication dependency. Every protected route depends on this."""
    with open_product_store() as store:
        session, reason = store.session(netra_session)
        if session is None:
            # The reason is in the message on purpose: "signed out after idle
            # timeout" tells an analyst what happened, and reveals nothing an
            # attacker did not already know.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail=reason, headers={"X-NETRA-Reason": reason})
        user = store.user(session.user)
        if user is None or not user.active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="account no longer active")
        return Principal(user=user, session=session)


def require(capability: str) -> Callable[[Principal], Principal]:
    """A route guard for one capability.

    Used as a dependency so the check runs BEFORE the handler body: a role check
    written inside a handler is a check somebody later moves below the work.
    """

    def guard(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.may(capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this action needs the {_role_for(capability)} role; "
                       f"you are signed in as {principal.role}",
            )
        return principal

    return guard


def _role_for(capability: str) -> str:
    """Which role would satisfy a capability -- for a message that says what to ask
    for rather than just refusing."""
    for role in ("supervisor", "admin"):
        if capability in PERMISSIONS[role]:
            return role
    return "admin"


def require_csrf(
    principal: Principal = Depends(current_principal),
    netra_csrf: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Principal:
    """Double-submit check for state-changing requests.

    The token is issued per session, sent as a cookie AND required in a header. A
    cross-site form post can send the cookie but cannot read it to set the header,
    which is what makes the pair meaningful.
    """
    if not netra_csrf or not x_csrf_token or not hmac.compare_digest(netra_csrf, x_csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="missing or stale CSRF token")
    if not hmac.compare_digest(principal.session.csrf, netra_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="CSRF token does not belong to this session")
    return principal


# --------------------------------------------------------------------------
# Requests and responses
# --------------------------------------------------------------------------
class SignIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current: str
    new: str = Field(min_length=12, max_length=256,
                     description="Length only, deliberately: composition rules push "
                                 "people towards predictable substitutions.")


class NewUser(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    role: str
    password: str = Field(min_length=12, max_length=256)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@router.post("/sign-in")
def sign_in(payload: SignIn, response: Response) -> dict[str, Any]:
    """Authenticate and open a session.

    The failure message is deliberately the same for a wrong password, a missing
    account and a disabled one: telling a caller which of those it was turns the
    endpoint into a way to enumerate accounts.
    """
    with open_product_store() as store:
        user, reason = store.verify(payload.username, payload.password)
        if user is None:
            store.audit(actor=payload.username, action="Sign in",
                        detail=reason, result="REFUSED")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="those credentials were not accepted")
        session = store.open_session(user)
        store.audit(actor=user.name, role=user.role, action="Sign in", result="OK")
        response.set_cookie(SESSION_COOKIE, session.id, httponly=True, samesite="lax",
                            secure=False, path="/")
        response.set_cookie(CSRF_COOKIE, session.csrf, httponly=False, samesite="lax",
                            secure=False, path="/")
        return {"user": user.as_dict(), "csrf": session.csrf,
                "engine_version": config.ENGINE_VERSION,
                "session_idle_minutes": config.SESSION_IDLE_MINUTES,
                "session_absolute_hours": config.SESSION_ABSOLUTE_HOURS}


@router.post("/sign-out")
def sign_out(response: Response,
             netra_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
             netra_csrf: str | None = Cookie(default=None, alias=CSRF_COOKIE),
             x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token")) -> dict[str, Any]:
    """Close the session on the server, then clear the cookies.

    A sign-out that only clears the cookie leaves a live session id behind, and a
    captured cookie would still work. This deletes the row.
    """
    with open_product_store() as store:
        session, _ = store.session(netra_session)
        if session and netra_csrf and x_csrf_token \
                and hmac.compare_digest(netra_csrf, x_csrf_token) \
                and hmac.compare_digest(session.csrf, netra_csrf):
            store.audit(actor=session.user, role=session.role, action="Sign out")
            store.close_session(session.id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"signed_out": True}


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Who am I, and what may I do.

    The interface reads this to decide what to show. The server still checks every
    request, because this endpoint tells a client what it may ATTEMPT, not what it
    is allowed to succeed at.
    """
    return {
        "user": principal.as_dict(),
        "capabilities": sorted(PERMISSIONS.get(principal.role, set())),
        "roles": list(ROLES),
        "engine_version": config.ENGINE_VERSION,
    }


@router.post("/password")
def change_password(payload: PasswordChange,
                    principal: Principal = Depends(require_csrf)) -> dict[str, Any]:
    """Change your own password. Requires the current one, so a borrowed session
    cannot lock the owner out of their own account."""
    with open_product_store() as store:
        user, reason = store.verify(principal.name, payload.current)
        if user is None:
            store.audit(actor=principal.name, role=principal.role,
                        action="Password change", detail=reason, result="REFUSED")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="current password is not correct")
        store.set_password(principal.name, payload.new)
        store.audit(actor=principal.name, role=principal.role,
                    action="Password change", result="OK")
    return {"changed": True}


# --------------------------------------------------------------------------
# Administration of accounts. Capability-gated, and every action audited.
# --------------------------------------------------------------------------
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.get("/users")
def list_users(principal: Principal = Depends(require("manage_users"))) -> dict[str, Any]:
    with open_product_store() as store:
        return {"users": [user.as_dict() for user in store.users()],
                "roles": list(ROLES),
                "sessions": store.live_sessions()}


@admin_router.post("/users", status_code=201)
def create_user(payload: NewUser,
                principal: Principal = Depends(require("manage_users")),
                _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    with open_product_store() as store:
        if store.user(payload.username):
            raise HTTPException(status_code=409,
                                detail=f"user '{payload.username}' already exists")
        user = store.create_user(payload.username, payload.display_name,
                                 payload.role, payload.password)
        store.audit(actor=principal.name, role=principal.role, action="Create user",
                    object_type="user", object_id=payload.username,
                    detail=f"role {payload.role}")
        return {"user": user.as_dict()}


@admin_router.post("/users/{username}/reset-token")
def issue_reset_token(username: str,
                      principal: Principal = Depends(require("manage_users")),
                      _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    """Issue a single-use reset token, to be handed over out of band.

    This is the offline replacement for a reset email. The token is shown once, to
    the administrator, and the redemption is audited as well -- so a password reset
    always has two people in its trail.
    """
    with open_product_store() as store:
        if store.user(username) is None:
            raise HTTPException(status_code=404, detail=f"no such user: {username}")
        token = new_token(24)
        # Kept in the audit record rather than a separate table: it is single-use,
        # short-lived and needs exactly one home, and the audit log is the one place
        # that cannot be edited afterwards.
        store.audit(actor=principal.name, role=principal.role,
                    action="Issue reset token", object_type="user", object_id=username,
                    detail=token, result="OK")
        return {"username": username, "token": token,
                "note": "single use, expires in 30 minutes, hand it over out of band"}


@admin_router.post("/users/{username}/unlock")
def unlock_user(username: str,
                principal: Principal = Depends(require("manage_users")),
                _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    with open_product_store() as store:
        store.unlock(username)
        store.audit(actor=principal.name, role=principal.role, action="Unlock user",
                    object_type="user", object_id=username)
    return {"unlocked": username}


@admin_router.post("/users/{username}/active")
def set_active(username: str, active: bool,
               principal: Principal = Depends(require("manage_users")),
               _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    """Enable or disable an account.

    Disabling is how access is withdrawn on an air-gapped host -- there is no
    directory to remove somebody from, and deleting the account would orphan the
    audit trail that names them.
    """
    with open_product_store() as store:
        store.set_active(username, active)
        store.audit(actor=principal.name, role=principal.role,
                    action="Enable account" if active else "Disable account",
                    object_type="user", object_id=username)
    return {"username": username, "active": active}


@admin_router.post("/sessions/{session_id}/terminate")
def terminate_session(session_id: str,
                      principal: Principal = Depends(require("manage_users")),
                      _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    with open_product_store() as store:
        store.close_session(session_id)
        store.audit(actor=principal.name, role=principal.role, action="Terminate session",
                    object_type="session", object_id=session_id[:8])
    return {"terminated": session_id[:8]}

class ResetRedemption(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    token: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/redeem-reset")
def redeem_reset(payload: ResetRedemption) -> dict[str, Any]:
    """Redeem an administrator-issued reset token.

    An air-gapped host has no email, so a reset cannot be a link. An administrator
    issues a single-use token out of band; the holder redeems it here. Both halves
    are audited, which means every password reset has two people in its trail.

    The token is stored in the audit log rather than in a table of its own: it is
    single-use, short-lived, and needs exactly one home -- and the audit log is the
    one place that cannot be edited afterwards. It is consumed by writing a
    redemption entry, so a second attempt finds no outstanding issue.
    """
    from datetime import datetime, timedelta, timezone

    with open_product_store() as store:
        if store.user(payload.username) is None:
            # Same message as a bad token: which of the two failed is not the
            # caller's business.
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="that reset token is not valid for this account")

        issued = [entry for entry in store.audit_entries(actor="*", limit=2000)
                  if entry["action"] == "Issue reset token"
                  and entry["object_id"] == payload.username
                  and entry["detail"] == payload.token]
        # Matched on the abbreviated form the SUCCESS path writes, not on the full
        # token: the token is written in full only when it is issued, and consumed
        # entries record just enough to correlate with the issue. Comparing against
        # the full token here meant a consumed token was never recognised as
        # consumed, so a reset token could be redeemed over and over until it
        # expired -- "single use" was only ever true for 30 minutes of use.
        consumed = payload.token[:6] + "…"
        redeemed = [entry for entry in store.audit_entries(limit=2000)
                    if entry["action"] == "Redeem reset token"
                    and entry["object_id"] == payload.username
                    and entry["detail"] == consumed]

        if not issued or redeemed:
            store.audit(actor=payload.username, action="Redeem reset token",
                        object_type="user", object_id=payload.username,
                        detail=consumed, result="REFUSED")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="that reset token is not valid for this account")

        issued_at = datetime.strptime(issued[-1]["at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - issued_at > timedelta(minutes=config.RESET_TOKEN_MINUTES):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="that reset token has expired")

        store.set_password(payload.username, payload.new_password)
        store.audit(actor=payload.username, action="Redeem reset token",
                    object_type="user", object_id=payload.username,
                    detail=payload.token[:6] + "…", result="OK")
    return {"username": payload.username, "password_set": True}
