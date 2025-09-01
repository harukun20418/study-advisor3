from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import requests
import os
from typing import List
from datetime import date

# --- FastAPIアプリ ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- DB設定 ---
DATABASE_URL = "sqlite:///./chat.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# --- DBモデル ---
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
    question_index = Column(Integer, default=0)

class ScreenTimeLog(Base):
    __tablename__ = "screen_time_logs"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    date = Column(Date)
    study_time = Column(Float, default=0.0)
    sns_time = Column(Float, default=0.0)
    game_time = Column(Float, default=0.0)

class SchedulePlan(Base):
    __tablename__ = "schedule_plans"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    date = Column(Date)
    plan_json = Column(Text)

# --- テーブル作成 ---
Base.metadata.create_all(bind=engine)

# --- Pydantic ---
class ChatRequest(BaseModel):
    message: str
    session_id: str

class ChatSessionCreate(BaseModel):
    session_id: str
    title: str

class ChatSessionResponse(BaseModel):
    id: int
    session_id: str
    title: str
    question_index: int
    class Config:
        orm_mode = True

class ScreenTimeInput(BaseModel):
    study_time: float
    sns_time: float
    game_time: float

# --- 質問リスト ---
QUESTIONS = [
    "こんにちは！私はあなたの勉強をサポートします！まずは5つの質問であなたのことを教えてください",
    "あなたのことは何と呼べばいいですか？",
    "了解です！何のための勉強をサポートしてほしいですか？(例: 試験対策、受験勉強など)",
    "なるほど、普段の1日の勉強時間はどのくらいですか？",
    "スマホは１日どれくらい使いますか？",
    "勉強はコツコツやる派ですかそれとも一夜漬けタイプ？"
]

# --- トップページ ---
@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- チャットAPI ---
@app.post("/chat")
def chat(request: ChatRequest):
    db = SessionLocal()
    session_obj = db.query(ChatSession).filter(ChatSession.session_id == request.session_id).first()
    if not session_obj:
        session_obj = ChatSession(session_id=request.session_id, title=f"セッション {request.session_id}")
        db.add(session_obj)
        db.commit()
        db.refresh(session_obj)

    if session_obj.question_index < len(QUESTIONS):
        db.add(ChatLog(session_id=request.session_id, role="user", content=request.message))
        db.commit()

        response_text = QUESTIONS[session_obj.question_index]
        session_obj.question_index += 1
        db.commit()

        db.add(ChatLog(session_id=request.session_id, role="assistant", content=response_text))
        db.commit()

        db.close()
        return {"response": response_text}

    history = db.query(ChatLog).filter(ChatLog.session_id == request.session_id).order_by(ChatLog.id.asc()).all()
    messages = [{"role": log.role, "content": log.content} for log in history]

    db.add(ChatLog(session_id=request.session_id, role="user", content=request.message))
    db.commit()

    headers = {
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "あなたは勉強のアドバイザーです。共感しつつ柔らかくアドバイスしてください。"},
            *messages,
            {"role": "user", "content": request.message}
        ]
    }
    response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
    if response.status_code != 200:
        db.close()
        return {"response": f"APIエラー: {response.status_code}"}
    try:
        assistant_reply = response.json()["choices"][0]["message"]["content"]
    except:
        assistant_reply = "AIの返答取得エラー"

    db.add(ChatLog(session_id=request.session_id, role="assistant", content=assistant_reply))
    db.commit()
    db.close()
    return {"response": assistant_reply}

# --- 履歴取得 ---
@app.get("/history/{session_id}")
def get_history(session_id: str):
    db = SessionLocal()
    logs = db.query(ChatLog).filter(ChatLog.session_id == session_id).order_by(ChatLog.id.asc()).all()
    db.close()
    return {"history": [{"role": log.role, "content": log.content} for log in logs]}

# --- セッション作成 ---
@app.post("/sessions", response_model=ChatSessionResponse)
def create_session(session: ChatSessionCreate):
    db = SessionLocal()
    db_session = ChatSession(session_id=session.session_id, title=session.title)
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    db.close()
    return db_session

# --- セッション一覧 ---
@app.get("/sessions", response_model=List[ChatSessionResponse])
def get_sessions():
    db = SessionLocal()
    sessions = db.query(ChatSession).all()
    db.close()
    return sessions

# --- スクリーンタイム送信 ---
@app.post("/screen_time")
def send_screen_time(data: ScreenTimeInput, session_id: str = Form(...)):
    db = SessionLocal()
    log = ScreenTimeLog(
        session_id=session_id,
        date=date.today(),
        study_time=data.study_time,
        sns_time=data.sns_time,
        game_time=data.game_time
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    db.close()
    return {"status": "ok", "log_id": log.id}

# --- AIとスケジュール相談 ---
@app.post("/plan_schedule")
def plan_schedule(message: str = Form(...), session_id: str = Form(...)):
    db = SessionLocal()
    logs = db.query(ScreenTimeLog).filter(ScreenTimeLog.session_id==session_id).order_by(ScreenTimeLog.date.desc()).all()

    if logs:
        recent = logs[0]
        prompt = f"""
あなたは勉強アドバイザーです。
ユーザーは昨日 {recent.study_time}時間勉強し、SNS {recent.sns_time}時間、ゲーム {recent.game_time}時間使いました。
ユーザーの希望は「{message}」です。
会話形式で、無理なく勉強スケジュールを決める提案をしてください。
"""
    else:
        prompt = f"ユーザーの希望: {message}\n会話形式でスケジュール提案をしてください。"

    headers = {
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [{"role": "system", "content": prompt}]
    }
    response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
    if response.status_code != 200:
        db.close()
        return {"response": f"APIエラー: {response.status_code}"}
    try:
        assistant_reply = response.json()["choices"][0]["message"]["content"]
    except:
        assistant_reply = "AIの返答取得エラー"

    db.close()
    return {"response": assistant_reply}