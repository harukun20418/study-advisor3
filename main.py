from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import requests
from typing import List
import sqlite3
import os

# --- FastAPIアプリの作成 ---
app = FastAPI()

# staticフォルダを公開
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- テンプレートの設定 ---
templates = Jinja2Templates(directory="templates")

# --- SQLiteデータベースの設定 ---
DATABASE_URL = "sqlite:///./chat.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# --- チャットログのデータベースモデル ---
class ChatLog(Base):
    __tablename__ = "chat_logs"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    role = Column(String)
    content = Column(Text)

# --- セッションのモデル ---
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True)
    title = Column(String)

Base.metadata.create_all(bind=engine)

# --- Pydantic モデル ---
class ChatSessionBase(BaseModel):
    session_id: str
    title: str

class ChatSessionCreate(ChatSessionBase):
    pass

class ChatSessionResponse(ChatSessionBase):
    id: int
    class Config:
        orm_mode = True

# --- ルート（トップページ） ---
@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- リクエストの構造 ---
class ChatRequest(BaseModel):
    message: str
    session_id: str

# --- チャットAPI（会話＋保存） ---
@app.post("/chat")
def chat(request: ChatRequest):
    db = SessionLocal()

    # 履歴取得（そのセッションの最新10件）
    history = db.query(ChatLog).filter(
        ChatLog.session_id == request.session_id
    ).order_by(ChatLog.id.desc()).limit(10).all()
    history = list(reversed(history))  # 昇順に並べ替え
    messages = [{"role": log.role, "content": log.content} for log in history]

    # ユーザーの発言を保存
    db.add(ChatLog(session_id=request.session_id, role="user", content=request.message))
    db.commit()

    # OpenRouter API
    headers = {
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": (
                    "あなたは勉強のアドバイザーです。ユーザーの気持ちに寄り添い、"
                    "共感しながら質問やアドバイスをしてください。堅苦しすぎず、"
                    "フレンドリーで頼れる先生のような雰囲気を意識してください。"
                )
            },
            *messages,
            {"role": "user", "content": request.message}
        ]
    }

    response = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers=headers, json=data)

    if response.status_code != 200:
        return {"response": f"APIエラー: {response.status_code} - {response.text}"}

    try:
        assistant_reply = response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return {"response": f"AIの返答取得エラー: {e}"}

    # AIの返答を保存
    db.add(ChatLog(session_id=request.session_id, role="assistant", content=assistant_reply))
    db.commit()

    return {"response": assistant_reply}

# --- 会話履歴取得API ---
@app.get("/history/{session_id}")
def get_history(session_id: str):
    db = SessionLocal()
    history = db.query(ChatLog).filter(
        ChatLog.session_id == session_id
    ).order_by(ChatLog.id.asc()).all()
    return {"history": [{"role": h.role, "content": h.content} for h in history]}

# --- セッション作成API ---
@app.post("/sessions", response_model=ChatSessionResponse)
def create_session(session: ChatSessionCreate):
    db = SessionLocal()
    db_session = ChatSession(session_id=session.session_id, title=session.title)
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    return db_session

# --- セッション一覧の取得 ---
@app.get("/sessions", response_model=List[ChatSessionResponse])
def get_sessions_api():
    db = SessionLocal()
    sessions = db.query(ChatSession).all()
    return sessions

# --- セッション一覧画面（HTML） ---
@app.get("/sessions", response_class=HTMLResponse)
async def get_sessions_html(request: Request):
    db = SessionLocal()
    sessions = db.query(ChatLog.session_id).distinct().all()
    session_data = [{"session_id": session[0], "title": f"セッション {session[0]}"} for session in sessions]
    return templates.TemplateResponse("sessions.html", {"request": request, "sessions": session_data})

# --- DBチェック用 ---
def check_chat_logs():
    conn = sqlite3.connect('chat.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM chat_logs")
    logs = cursor.fetchall()
    if logs:
        for log in logs:
            print(log)
    else:
        print("No logs found.")
    conn.close()

check_chat_logs()
