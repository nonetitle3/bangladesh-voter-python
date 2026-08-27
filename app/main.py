import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .api import router
from .database import init_database

app = FastAPI(title="Bangladesh Voter Search API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)

@app.on_event("startup")
def startup():
    init_database()

@app.get("/")
def root():
    return {"status":"ok","message":"Bangladesh Voter Python API","features":["font-aware Bengali PDF extraction","EasyOCR/Tesseract fallback","SQLite FTS5 search"]}

@app.get("/api/health")
def health():
    return {"status":"ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT","10000")))
