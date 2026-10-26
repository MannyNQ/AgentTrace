"""/v1/auth (claiming the install, signing in and out, invite and reset links), /v1/members,
and /v1/keys (ADR-0032, ADR-0033, ADR-0034). Each route parses, calls one rule in `auth`, and
returns its record. Routes that hash a password run it through `Auth.hashing`."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from agenttrace_ingest import auth as au
from agenttrace_ingest.dataset_api import Body

PREFIX = "/v1/auth"
MEMBERS_PREFIX = "/v1/members"
KEYS_PREFIX = "/v1/keys"

Password = Annotated[str, Field(max_length=au.MAX_PASSWORD_CHARS)]
Email = Annotated[str, Field(max_length=254)]


class SetupState(BaseModel):
    claimed: bool


class Claim(Body):
    token: str
    email: Email
    password: Password


class Login(Body):
    email: Email
    password: Password


class PasswordChange(Body):
    current: Password
    new: Password


class AcceptInvite(Body):
    email: Email
    password: Password


class ResetPassword(Body):
    password: Password


class Me(BaseModel):
    user: au.User


class MemberList(BaseModel):
    members: list[au.User]


class MemberPatch(Body):
    role: au.Role | None = None
    deactivated: bool | None = None


class NewInvite(Body):
    role: au.Role = "member"


class KeyList(BaseModel):
    keys: list[au.KeyInfo]


class NewKeyBody(Body):
    name: Annotated[str, Field(max_length=100)]
    scope: au.Scope


def router(a: au.Auth) -> APIRouter:
    r = APIRouter(prefix=PREFIX)
    signed_in = Depends(au.SignedIn(a))

    @r.get("/setup")
    def setup_state() -> SetupState:
        """Whether the web app should show the "create the owner account" screen."""
        return SetupState(claimed=a.claimed())

    @r.post("/setup", status_code=201)
    async def claim(body: Claim) -> au.NewSession:
        return await a.hashing(a.claim, body.token, body.email, body.password)

    @r.post("/login")
    async def login(body: Login) -> au.NewSession:
        return await a.hashing(a.login, body.email, body.password)

    @r.post("/logout", status_code=204)
    def logout(session: Annotated[tuple[au.User, str], signed_in]) -> Response:
        a.logout(session[1])
        return Response(status_code=204)

    @r.get("/me")
    def me(session: Annotated[tuple[au.User, str], signed_in]) -> Me:
        return Me(user=session[0])

    @r.post("/password", status_code=204)
    async def change_password(
        session: Annotated[tuple[au.User, str], signed_in], body: PasswordChange
    ) -> Response:
        """Signs the user out everywhere else."""
        await a.hashing(a.change_password, session[0], session[1], body.current, body.new)
        return Response(status_code=204)

    @r.get("/links/{token}")
    def link(token: str) -> au.Link:
        """What an invite or reset link is for, so the page can say before the form."""
        return a.link(token)

    @r.post("/links/{token}/accept", status_code=201)
    async def accept_invite(token: str, body: AcceptInvite) -> au.NewSession:
        """Creates the invited user and signs them in."""
        return await a.hashing(a.accept_invite, token, body.email, body.password)

    @r.post("/links/{token}/reset", status_code=204)
    async def reset_password(token: str, body: ResetPassword) -> Response:
        """Sets the password and signs the user out everywhere; they then sign in."""
        await a.hashing(a.reset_password, token, body.password)
        return Response(status_code=204)

    return r


def members_router(a: au.Auth) -> APIRouter:
    r = APIRouter(prefix=MEMBERS_PREFIX)
    owner = Depends(au.SignedIn(a, owner=True))

    @r.get("")
    def list_members(session: Annotated[tuple[au.User, str], owner]) -> MemberList:
        return MemberList(members=a.members(session[0]))

    @r.patch("/{user_id}")
    def update_member(
        session: Annotated[tuple[au.User, str], owner], user_id: UUID, body: MemberPatch
    ) -> au.User:
        """Deactivating signs the member out; keys they created keep working."""
        return a.update_member(session[0], user_id, body.role, body.deactivated)

    @r.post("/invites", status_code=201)
    def invite(session: Annotated[tuple[au.User, str], owner], body: NewInvite) -> au.NewLink:
        return a.invite(session[0], body.role)

    @r.post("/{user_id}/reset", status_code=201)
    def reset_link(session: Annotated[tuple[au.User, str], owner], user_id: UUID) -> au.NewLink:
        return a.reset_link(session[0], user_id)

    return r


def keys_router(a: au.Auth) -> APIRouter:
    r = APIRouter(prefix=KEYS_PREFIX)
    owner = Depends(au.OwnerProject(a))

    @r.get("")
    def list_keys(caller: Annotated[tuple[au.User, UUID], owner]) -> KeyList:
        return KeyList(keys=a.keys(caller[1]))

    @r.post("", status_code=201)
    def create_key(caller: Annotated[tuple[au.User, UUID], owner], body: NewKeyBody) -> au.NewKey:
        """The key is in this response and nowhere else."""
        return a.create_key(caller[0], caller[1], body.name, body.scope)

    @r.post("/{key_id}/revoke")
    def revoke_key(caller: Annotated[tuple[au.User, UUID], owner], key_id: UUID) -> au.KeyInfo:
        return a.revoke_key(caller[1], key_id)

    return r
