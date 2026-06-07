from fastapi import APIRouter, Depends
from app.middleware.auth import require_role

router = APIRouter()


@router.get("/users")
async def list_users(current_user=Depends(require_role("admin"))):
    return {"users": []}


@router.get("/analytics")
async def get_analytics(current_user=Depends(require_role("admin"))):
    return {"analytics": {}}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, current_user=Depends(require_role("admin"))):
    return {"message": f"User {user_id} deleted"}
