from fastapi import APIRouter, Depends, status, Body
from fastapi.security import OAuth2PasswordRequestForm
from app.services.auth_service import AuthService
from app.database.schemas import UserCreate, Token, RefreshTokenRequest, MessageResponse

router = APIRouter()


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(user: UserCreate, auth_service: AuthService = Depends()):
    return await auth_service.register(user)


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_service: AuthService = Depends(),
):
    return await auth_service.login(form_data.username, form_data.password)


@router.post("/refresh", response_model=Token)
async def refresh_token(
    body: RefreshTokenRequest,
    auth_service: AuthService = Depends(),
):
    return await auth_service.refresh(body.refresh_token)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    token: str = Body(..., embed=True),
    auth_service: AuthService = Depends(),
):
    return await auth_service.logout(token)