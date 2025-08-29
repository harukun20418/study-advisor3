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


# --- ChatSession の Pydantic モデル ---
class ChatSessionBase(BaseModel):
    session_id: str
    title: str


class ChatSessionCreate(ChatSessionBase):
    pass  # セッション作成用のモデル（他のフィールドが必要なら追加）


class ChatSessionResponse(ChatSessionBase):
    id: int

    class Config:
        orm_mode = True  # SQLAlchemyモデルをPydanticで扱えるようにする


Base.metadata.create_all(bind=engine)


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
        ChatLog.session_id == request.session_id).order_by(
            ChatLog.id.desc()).limit(10).all()
    history = list(reversed(history))  # 昇順に並べ替え
    messages = [{"role": log.role, "content": log.content} for log in history]

    # ユーザーの発言を保存
    db.add(
        ChatLog(session_id=request.session_id,
                role="user",
                content=request.message))
    db.commit()

    # AIにリクエスト
    headers = {
        "Authorization":
        "Bearer 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",
        "Content-Type": "application/json"
    }
    data = {
        "model":
        "openai/gpt-3.5-turbo",
        "messages": [{
            "role":
            "system",
            "content":
            "あなたは勉強のアドバイザーです。スクリーンタイムの内容に応じて寄り添うように質問をしながらアドバイスしてください。アドバイスをするだけでなく、ユーザーに共感しながら会話を行ってください。会話の文体が硬くならないように、もう少し親しみやすい雰囲気をつくってください。"
        }, *messages, {
            "role": "user",
            "content": request.message
        }]
    }

    response = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers=headers,
                             json=data)

    # --- エラーハンドリング追加 ---
    if response.status_code != 200:
        return {
            "response": f"APIエラー: {response.status_code} - {response.text}"
        }

    try:
        assistant_reply = response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return {"response": f"AIの返答取得エラー: {e}"}

    # AIの返答を保存
    db.add(
        ChatLog(session_id=request.session_id,
                role="assistant",
                content=assistant_reply))
    db.commit()

    return {"response": assistant_reply}


# --- 会話履歴取得API ---
@app.get("/history/{session_id}")
def get_history(session_id: str):
    db = SessionLocal()
    history = db.query(ChatLog).filter(
        ChatLog.session_id == session_id).order_by(ChatLog.id.asc()).all()
    return {
        "history": [{
            "role": h.role,
            "content": h.content
        } for h in history]
    }


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True)
    title = Column(String)  # ユーザーが決めたタイトルや自動生成タイトル


# --- セッション作成API ---
@app.post("/sessions", response_model=ChatSessionResponse)
def create_session(session: ChatSessionCreate):
    db = SessionLocal()

    # 新しいセッションを作成
    db_session = ChatSession(session_id=session.session_id,
                             title=session.title)
    db.add(db_session)
    db.commit()
    db.refresh(db_session)

    return db_session


# --- セッション一覧の取得 ---
@app.get("/sessions", response_model=List[ChatSessionResponse])
def get_sessions():
    db = SessionLocal()
    sessions = db.query(ChatSession).all()
    return sessions


# --- セッション一覧画面の取得 ---
@app.get("/sessions", response_class=HTMLResponse)
async def get_sessions(request: Request):
    db = SessionLocal()

    # セッション情報を全て取得（必要に応じて変更）
    sessions = db.query(ChatLog.session_id).distinct().all()
    session_data = [{
        "session_id": session[0],
        "title": f"セッション {session[0]}"
    } for session in sessions]

    return templates.TemplateResponse("sessions.html", {
        "request": request,
        "sessions": session_data
    })


Base.metadata.create_all(bind=engine)


def check_chat_logs():
    conn = sqlite3.connect('chat.db')  # データベースに接続
    cursor = conn.cursor()

    # チャットログを取得
    cursor.execute("SELECT * FROM chat_logs")
    logs = cursor.fetchall()

    # ログの内容を表示
    if logs:
        for log in logs:
            print(log)
    else:
        print("No logs found.")

    # 接続を閉じる
    conn.close()


check_chat_logs()