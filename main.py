from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import requests
import os
from typing import List

# --- FastAPI アプリ ---
app = FastAPI()

# static
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- DB ---
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
    question_index = Column(Integer, default=0)  # 質問インデックス保持

Base.metadata.create_all(bind=engine)

# --- Pydantic ---
class ChatRequest(BaseModel):
    message: str
    session_id: str

# --- ルート ---
@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- 履歴取得 ---
@app.get("/history/{session_id}")
def get_history(session_id: str):
    db = SessionLocal()
    history = db.query(ChatLog).filter(ChatLog.session_id==session_id).order_by(ChatLog.id.asc()).all()
    last_index = 0
    if history:
        last_index = max([h.question_index for h in history])
    return {
        "history": [{"role": h.role, "content": h.content} for h in history],
        "last_question_index": last_index
    }

# --- チャット ---
@app.post("/chat")
def chat(request: ChatRequest):
    db = SessionLocal()

    # 履歴取得（最新10件）
    history = db.query(ChatLog).filter(ChatLog.session_id==request.session_id).order_by(ChatLog.id.desc()).limit(10).all()
    history = list(reversed(history))
    messages = [{"role": log.role, "content": log.content} for log in history]

    # ユーザー発言保存
    # 質問インデックスは最新の質問を取得
    last_index = db.query(ChatLog).filter(ChatLog.session_id==request.session_id).order_by(ChatLog.id.desc()).first()
    q_index = last_index.question_index if last_index else 0

    db.add(ChatLog(session_id=request.session_id, role="user", content=request.message, question_index=q_index))
    db.commit()

    # AIにリクエスト
    headers = {
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [{"role": "system",
                      "content": "あなたは勉強のアドバイザーです。寄り添う文体で質問とアドバイスを交えてください。"}] + messages + [{"role": "user", "content": request.message}]
    }

    response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
    if response.status_code != 200:
        return {"response": f"APIエラー: {response.status_code} - {response.text}"}

    try:
        assistant_reply = response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return {"response": f"AIの返答取得エラー: {e}"}

    # AI返信保存
    db.add(ChatLog(session_id=request.session_id, role="assistant", content=assistant_reply, question_index=q_index))
    db.commit()

    return {"response": assistant_reply}
