from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from app.database.postgres import AsyncSessionLocal
from app.database.models import User
from app.database.schemas import UserCreate, Token
from sqlalchemy import select
import os

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = 30
REFRESH_TOKEN_EXPIRE = 7 * 24 * 60

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    async def register(self, user: UserCreate) -> dict:
        async with AsyncSessionLocal() as db:
            hashed = pwd_ctx.hash(user.password)
            db_user = User(email=user.email, hashed_password=hashed, role=user.role)
            db.add(db_user)
            await db.commit()
            return {"message": "User created", "id": str(db_user.id)}

    async def login(self, email: str, password: str) -> Token:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            if not user or not pwd_ctx.verify(password, user.hashed_password):
                raise ValueError("Invalid credentials")
            return Token(
                access_token=self._create_token(str(user.id), ACCESS_TOKEN_EXPIRE),
                refresh_token=self._create_token(str(user.id), REFRESH_TOKEN_EXPIRE),
            )

    def _create_token(self, user_id: str, expire_minutes: int) -> str:
        exp = datetime.utcnow() + timedelta(minutes=expire_minutes)
        return jwt.encode({"sub": user_id, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

    async def refresh(self, refresh_token: str) -> Token:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        return Token(
            access_token=self._create_token(user_id, ACCESS_TOKEN_EXPIRE),
            refresh_token=self._create_token(user_id, REFRESH_TOKEN_EXPIRE),
        )

    async def logout(self, token: str) -> dict:
        # Add token to blacklist (Redis in production)
        return {"message": "Logged out"}
