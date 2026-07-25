from uuid import uuid4

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.core.auth import PasswordService, TokenService
from axiz.pe.sql_agent.models.contracts import UserPrincipal


def test_password_hash_is_not_plaintext() -> None:
    service = PasswordService()
    password_hash = service.hash("StrongPassword123!")
    assert password_hash != "StrongPassword123!"
    assert service.verify("StrongPassword123!", password_hash)
    assert not service.verify("wrong", password_hash)


def test_token_round_trip() -> None:
    settings = Settings(APP_SECRET_KEY="a" * 40)
    service = TokenService(settings)
    principal = UserPrincipal(user_id=uuid4(), username="analyst", roles=["analyst"])
    decoded = service.decode(service.create_access_token(principal))
    assert decoded.user_id == principal.user_id
    assert decoded.roles == ["analyst"]
