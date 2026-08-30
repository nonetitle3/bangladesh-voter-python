import os
from datetime import datetime, timedelta, timezone
import jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from .database import get_db
from .models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

# Keep the signing secret stable in Render. Never rotate this value between
# requests/restarts unless all existing sessions are intentionally invalidated.
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-secret-in-render").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123456")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = max(1, int(os.getenv("JWT_EXPIRE_HOURS", "12")))


def ensure_admin(db: Session):
    user = db.query(User).filter(User.username == ADMIN_USERNAME).first()
    if not user:
        user = User(
            username=ADMIN_USERNAME,
            password=pwd_context.hash(ADMIN_PASSWORD),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str):
    user = db.query(User).filter(
        User.username == username,
        User.is_active.is_(True),
    ).first()
    if not user:
        return None
    try:
        return user if pwd_context.verify(password, user.password) else None
    except Exception:
        return None


def make_token(user: User):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials.strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        subject = payload.get("sub")
        if subject is None:
            raise ValueError("Token subject is missing")
        user_id = int(subject)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(
        User.id == user_id,
        User.is_active.is_(True),
    ).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def admin_user(user: User = Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
