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
    username = Column(String, default="")
    study_time = Column(Integer, default=0)
    phone_time = Column(Integer, default=0)


# --- テーブル作成 ---
Base.metadata.create_all(bind=engine)


# --- Pydantic ---
class ChatRequest(BaseModel):
    message: str
    session_id: str
    study_time: int = 0
    phone_time: int = 0


class ChatSessionCreate(BaseModel):
    session_id: str
    title: str


class ChatSessionResponse(BaseModel):
    id: int
    session_id: str
    title: str
    question_index: int
    username: str
    study_time: int
    phone_time: int

    class Config:
        orm_mode = True


# --- 質問リスト ---
QUESTIONS = [
    "こんにちは！私はあなたの勉強をサポートします！まずは4つの質問であなたのことを教えてください、あなたの事は何と呼べばいいですか？",
    "了解です！何のための勉強をサポートしてほしいですか？(例: 試験対策、受験勉強など)", "なるほど、普段の1日の勉強時間はどのくらいですか？",
    "スマホは1日どれくらい使いますか？", "勉強はコツコツ派ですか、それとも一夜漬けタイプですか？"
]


# --- トップページ ---
@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# --- チャットAPI ---
@app.post("/chat")
def chat(request: ChatRequest):
    db = SessionLocal()
    session_obj = db.query(ChatSession).filter(
        ChatSession.session_id == request.session_id).first()
    if not session_obj:
        session_obj = ChatSession(session_id=request.session_id,
                                  title=f"セッション {request.session_id}")
        db.add(session_obj)
        db.commit()
        db.refresh(session_obj)

    # --- 質問モード ---
    if session_obj.question_index < len(QUESTIONS):
        db.add(
            ChatLog(session_id=request.session_id,
                    role="user",
                    content=request.message))
        db.commit()

        if session_obj.question_index == 1:
            session_obj.username = request.message
        if session_obj.question_index == 3:
            session_obj.study_time = request.study_time
        if session_obj.question_index == 4:
            session_obj.phone_time = request.phone_time
        db.commit()

        response_text = QUESTIONS[session_obj.question_index]
        session_obj.question_index += 1
        db.commit()

        if session_obj.username:
            response_text = response_text.replace("あなた", session_obj.username)

        db.add(
            ChatLog(session_id=request.session_id,
                    role="assistant",
                    content=response_text))
        db.commit()
        db.close()
        return {"response": response_text}

    # --- 自由会話モード ---
    history = db.query(ChatLog).filter(
        ChatLog.session_id == request.session_id).order_by(
            ChatLog.id.asc()).all()
    messages = [{"role": log.role, "content": log.content} for log in history]

    if request.study_time:
        session_obj.study_time = request.study_time
    if request.phone_time:
        session_obj.phone_time = request.phone_time
    db.commit()

    db.add(
        ChatLog(session_id=request.session_id,
                role="user",
                content=request.message))
    db.commit()

    headers = {
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        "Content-Type": "application/json"
    }
    system_prompt = (
        f"あなたは勉強アドバイザーです。あなたは、ユーザと会話を重ねて、ユーザーのしたい勉強に応じた計画を立てることを補助するスペシャリストです。ユーザーの名前は {session_obj.username or 'ユーザー'} です。"
        f"今日の勉強時間は {session_obj.study_time} 時間、スマホ時間は {session_obj.phone_time} 時間です。"
        "ユーザーに共感してください。短く1〜2文で質問や提案を出してください。提案の具体性がない場合は補足を加えてください。回答を出す前に、その回答が適切かどうかを考えてください。ユーザーに確認しながらスケジュールを決める会話形式で返答してください。例えばユーザーが勉強したいことに対して、分類し、一日目はこれを、二日目はこれをしてはどうですかと提案します。"
    )
    data = {
        "model":
        "openai/gpt-3.5-turbo",
        "messages": [{
            "role": "system",
            "content": system_prompt
        }, *messages, {
            "role": "user",
            "content": request.message
        }],
        "max_tokens":
        150
    }
    response = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers=headers,
                             json=data)

    if response.status_code != 200:
        db.close()
        return {"response": f"APIエラー: {response.status_code}"}

    try:
        assistant_reply = response.json()["choices"][0]["message"]["content"]
    except:
        assistant_reply = "AIの返答取得エラー"

    db.add(
        ChatLog(session_id=request.session_id,
                role="assistant",
                content=assistant_reply))
    db.commit()
    db.close()
    return {"response": assistant_reply}


# --- 履歴取得 ---
@app.get("/history/{session_id}")
def get_history(session_id: str):
    db = SessionLocal()
    logs = db.query(ChatLog).filter(ChatLog.session_id == session_id).order_by(
        ChatLog.id.asc()).all()
    db.close()
    return {
        "history": [{
            "role": log.role,
            "content": log.content
        } for log in logs]
    }


# --- セッション作成 ---
@app.post("/sessions", response_model=ChatSessionResponse)
def create_session(session: ChatSessionCreate):
    db = SessionLocal()
    db_session = ChatSession(session_id=session.session_id,
                             title=session.title)
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
