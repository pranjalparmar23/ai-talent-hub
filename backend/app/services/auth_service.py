import os
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from passlib.context import CryptContext
from jose import JWTError, jwt
from sqlalchemy import select
from app.database.postgres import AsyncSessionLocal
from app.database.models import User
from app.database.schemas import UserCreate, Token

SECRET_KEY = os.getenv("SECRET_KEY", "0a6389904da316b26f94c2487af5e6a082e5f7aaa50c00710711c7ebc0e6f52d")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")) * 24 * 60

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    async def register(self, user: UserCreate) -> dict:
        async with AsyncSessionLocal() as db:
            # Reject duplicate emails before hitting the DB unique constraint
            existing = await db.execute(select(User).where(User.email == user.email))
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already registered",
                )

            hashed = pwd_ctx.hash(user.password)
            db_user = User(email=user.email, hashed_password=hashed, role=user.role)
            db.add(db_user)
            await db.commit()
            await db.refresh(db_user)
            return {"message": "User created", "id": str(db_user.id)}

    async def login(self, email: str, password: str) -> Token:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            if not user or not pwd_ctx.verify(password, user.hashed_password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid credentials",
                )
            return Token(
                access_token=self._create_token(str(user.id), ACCESS_TOKEN_EXPIRE),
                refresh_token=self._create_token(str(user.id), REFRESH_TOKEN_EXPIRE),
            )

    def _create_token(self, user_id: str, expire_minutes: int) -> str:
        exp = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
        return jwt.encode({"sub": user_id, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

    async def refresh(self, refresh_token: str) -> Token:
        try:
            payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token",
                )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        return Token(
            access_token=self._create_token(user_id, ACCESS_TOKEN_EXPIRE),
            refresh_token=self._create_token(user_id, REFRESH_TOKEN_EXPIRE),
        )

    async def logout(self, token: str) -> dict:
        return {"message": "Logged out"}