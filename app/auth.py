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
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-secret-in-render")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123456")

def ensure_admin(db: Session):
    user = db.query(User).filter(User.username == ADMIN_USERNAME).first()
    if not user:
        user = User(username=ADMIN_USERNAME, password=pwd_context.hash(ADMIN_PASSWORD), role="admin", is_active=True)
        db.add(user); db.commit(); db.refresh(user)
    return user

def authenticate(db: Session, username: str, password: str):
    user = db.query(User).filter(User.username == username, User.is_active == True).first()
    if not user or not pwd_context.verify(password, user.password):
        return None
    return user

def make_token(user: User):
    return jwt.encode({"sub": str(user.id), "username": user.username, "role": user.role, "exp": datetime.now(timezone.utc)+timedelta(hours=12)}, JWT_SECRET, algorithm="HS256")

def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)):
    if not credentials: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try: payload=jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"]); user=db.query(User).filter(User.id==int(payload["sub"]), User.is_active==True).first()
    except Exception: user=None
    if not user: raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user

def admin_user(user: User = Depends(current_user)):
    if user.role != "admin": raise HTTPException(status_code=403, detail="Admin access required")
    return user
