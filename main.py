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
    role = Column(String)  # user または assistant
    content = Column(Text)

# --- ChatSession の Pydantic モデル ---
class ChatSessionBase(BaseModel):
    session_id: str
    title: str

class ChatSessionCreate(ChatSessionBase):
    pass

class ChatSessionResponse(ChatSessionBase):
    id: int
    class Config:
        orm_mode = True

# --- セッション管理テーブル ---
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True)
    title = Column(String)

# --- データベース作成 ---
Base.metadata.create_all(bind=engine)

# --- ルート（トップページ） ---
@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- リクエストモデル ---
class ChatRequest(BaseModel):
    message: str
    session_id: str

# --- 質問リスト ---
QUESTIONS = [
    "こんにちは！私はあなたの勉強をサポートします！まずは5つの質問であなたのことを教えてください",
    "あなたのことは何と呼べばいいですか？",
    "何のための勉強をサポートしてほしいですか？(例: 試験対策、受験勉強など)",
    "普段の1日の勉強時間はどのくらいですか？",
    "スマホは１日どれくらい使いますか？",
    "勉強はコツコツ派ですか、それとも一夜漬けタイプですか？"
]

# --- チャットAPI ---
@app.post("/chat")
def chat(request: ChatRequest):
    db = SessionLocal()

    # 履歴取得（セッション内すべて）
    history = db.query(ChatLog).filter(ChatLog.session_id == request.session_id).order_by(ChatLog.id.asc()).all()
    messages = [{"role": log.role, "content": log.content} for log in history]

    # ユーザーの発言を保存
    db.add(ChatLog(session_id=request.session_id, role="user", content=request.message))
    db.commit()

    # どの質問まで答えたか判定
    answered_questions = [msg for msg in messages if msg["role"] == "user"]
    question_index = len(answered_questions)
    next_question = QUESTIONS[question_index] if question_index < len(QUESTIONS) else None

    # AIに送るメッセージ作成
    system_prompt = (
        "あなたは勉強アドバイザーです。スクリーンタイムや勉強習慣に寄り添い、"
        "ユーザーに質問を投げかけながらアドバイスしてください。"
        "文体は親しみやすく、丁寧に。"
    )

    ai_messages = [{"role": "system", "content": system_prompt}, *messages]
    if next_question:
        ai_messages.append({"role": "assistant", "content": next_question})

    data = {"model": "openai/gpt-3.5-turbo", "messages": ai_messages}
    response = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                                      "Content-Type": "application/json"},
                             json=data)

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

# --- 直前メッセージ削除API ---
@app.delete("/delete_last/{session_id}")
def delete_last(session_id: str):
    db = SessionLocal()
    last_user = db.query(ChatLog).filter(ChatLog.session_id == session_id, ChatLog.role=="user").order_by(ChatLog.id.desc()).first()
    last_ai = db.query(ChatLog).filter(ChatLog.session_id == session_id, ChatLog.role=="assistant").order_by(ChatLog.id.desc()).first()
    if last_user:
        db.delete(last_user)
    if last_ai:
        db.delete(last_ai)
    db.commit()
    return {"status": "deleted"}

# --- 会話履歴取得API ---
@app.get("/history/{session_id}")
def get_history(session_id: str):
    db = SessionLocal()
    history = db.query(ChatLog).filter(ChatLog.session_id == session_id).order_by(ChatLog.id.asc()).all()
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

# --- セッション一覧取得API ---
@app.get("/sessions", response_model=List[ChatSessionResponse])
def get_sessions():
    db = SessionLocal()
    sessions = db.query(ChatSession).all()
    return sessions

# --- セッション一覧画面 ---
@app.get("/sessions_page", response_class=HTMLResponse)
async def get_sessions_page(request: Request):
    db = SessionLocal()
    sessions = db.query(ChatLog.session_id).distinct().all()
    session_data = [{"session_id": s[0], "title": f"セッション {s[0]}"} for s in sessions]
    return templates.TemplateResponse("sessions.html", {"request": request, "sessions": session_data})

# --- デバッグ用チャットログ確認 ---
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
