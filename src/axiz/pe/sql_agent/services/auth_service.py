from uuid import UUID

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.core.auth import PasswordService, TokenService
from axiz.pe.sql_agent.models.contracts import TokenResponse, UserPrincipal
from axiz.pe.sql_agent.repositories.user_repository import UserRepository


class AuthService:
    def __init__(
        self,
        settings: Settings,
        users: UserRepository,
        passwords: PasswordService,
        tokens: TokenService,
    ) -> None:
        self.settings = settings
        self.users = users
        self.passwords = passwords
        self.tokens = tokens

    async def bootstrap(self) -> None:
        existing = await self.users.find_by_username(self.settings.bootstrap_username)
        if existing:
            return
        await self.users.create_local_user(
            username=self.settings.bootstrap_username,
            password_hash=self.passwords.hash(
                self.settings.bootstrap_password.get_secret_value()
            ),
            roles=["admin", "analyst"],
        )

    async def login(self, username: str, password: str) -> TokenResponse:
        user = await self.users.find_by_username(username)
        if not user or not user.get("is_active"):
            raise ValueError("Invalid credentials")
        password_hash = user.get("password_hash")
        if not password_hash or not self.passwords.verify(password, password_hash):
            raise ValueError("Invalid credentials")
        principal = UserPrincipal(
            user_id=UUID(str(user["id"])),
            username=str(user["username"]),
            roles=list(user.get("roles") or []),
            auth_source=str(user.get("auth_source") or "local"),
        )
        return TokenResponse(
            access_token=self.tokens.create_access_token(principal),
            expires_in=self.settings.jwt_expire_minutes * 60,
        )
