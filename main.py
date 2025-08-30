from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import requests, os
from typing import List

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DATABASE_URL = "sqlite:///./chat.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class ChatLog(Base):
    __tablename__ = "chat_logs"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    role = Column(String)
    content = Column(Text)

class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True)
    title = Column(String)

class ChatSessionBase(BaseModel):
    session_id: str
    title: str

class ChatSessionCreate(ChatSessionBase):
    pass

class ChatSessionResponse(ChatSessionBase):
    id: int
    class Config:
        orm_mode = True

class ChatRequest(BaseModel):
    message: str
    session_id: str

Base.metadata.create_all(bind=engine)

@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/chat")
def chat(request: ChatRequest):
    db = SessionLocal()

    # 履歴取得
    history = db.query(ChatLog).filter(ChatLog.session_id==request.session_id).order_by(ChatLog.id.asc()).all()
    messages = [{"role": log.role, "content": log.content} for log in history]

    db.add(ChatLog(session_id=request.session_id, role="user", content=request.message))
    db.commit()

    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}
    data = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [{"role":"system","content":"あなたは勉強のアドバイザーです。ユーザーに寄り添い、質問しながらアドバイスしてください、返答文はかならず3文に収めてください、会話っぽくです。"}] + messages + [{"role":"user","content":request.message}]
    }

    response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
    if response.status_code != 200:
        return {"response": f"APIエラー: {response.status_code} - {response.text}"}

    try:
        assistant_reply = response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        assistant_reply
