import uuid

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, db: Session) -> None:
        super().__init__(User, db)

    def get_by_email(self, email: str) -> User | None:
        return self.db.query(User).filter(User.email == email.lower()).first()

    def create_user(self, email: str, hashed_password: str, full_name: str) -> User:
        user = User(
            email=email.lower(),
            hashed_password=hashed_password,
            full_name=full_name,
            is_active=True,
        )
        return self.save(user)

    def email_exists(self, email: str) -> bool:
        return self.db.query(User.id).filter(User.email == email.lower()).first() is not None
