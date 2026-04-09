import sqlite3
from contextlib import asynccontextmanager
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, Query
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "finance.db"

# --- PYDANTIC MODELS (Data Validation) ---
# This is how FastAPI knows exactly what JSON to expect and validate.
class TransactionBase(BaseModel):
    date: str
    amount: float
    category: str
    description: str

class TransactionCreate(TransactionBase):
    pass

class TransactionResponse(TransactionBase):
    id: int

class SummaryResponse(BaseModel):
    current_income: float
    total_income: float
    total_expenses: float

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

# Lifespan event to ensure DB exists when server starts
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Finance Tracker API", lifespan=lifespan)

# Dependency to get a database connection per request
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# --- API ENDPOINTS ---

@app.get("/transactions", response_model=list[TransactionResponse])
def get_transactions(
    month: Optional[str] = Query(None, description="Format YYYY-MM"),
    category: Optional[str] = Query(None),
    db: sqlite3.Connection = Depends(get_db)
):
    where_clauses = []
    params =[]

    if month:
        where_clauses.append("substr(date, 1, 7) = ?")
        params.append(month)
    if category:
        where_clauses.append("LOWER(category) LIKE ?")
        params.append(f"%{category.lower()}%")

    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    query = f"SELECT id, date, amount, category, description FROM transactions {where_sql} ORDER BY date DESC, id DESC"
    rows = db.execute(query, params).fetchall()
    
    return[dict(row) for row in rows]

@app.get("/transactions/summary", response_model=SummaryResponse)
def get_summary(
    month: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: sqlite3.Connection = Depends(get_db)
):
    where_clauses = []
    params =[]

    if month:
        where_clauses.append("substr(date, 1, 7) = ?")
        params.append(month)
    if category:
        where_clauses.append("LOWER(category) LIKE ?")
        params.append(f"%{category.lower()}%")

    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    query = f"""
        SELECT
            COALESCE(SUM(amount), 0) AS current_income,
            COALESCE(SUM(CASE WHEN LOWER(category) = 'income' THEN amount ELSE 0 END), 0) AS total_income,
            COALESCE(SUM(CASE WHEN LOWER(category) != 'income' THEN ABS(amount) ELSE 0 END), 0) AS total_expenses
        FROM transactions
        {where_sql}
    """
    row = db.execute(query, params).fetchone()
    
    return SummaryResponse(
        current_income=float(row["current_income"]),
        total_income=float(row["total_income"]),
        total_expenses=float(row["total_expenses"])
    )

@app.post("/transactions", response_model=TransactionResponse)
def create_transaction(t: TransactionCreate, db: sqlite3.Connection = Depends(get_db)):
    # Note: Textual handled normalize_amount. You can move that logic here later, 
    # but for now we expect the client to send the exact float.
    cur = db.cursor()
    cur.execute(
        "INSERT INTO transactions (date, amount, category, description) VALUES (?, ?, ?, ?)",
        (t.date, t.amount, t.category, t.description)
    )
    db.commit()
    t_id = cur.lastrowid
    
    # Return the created record
    row = db.execute("SELECT * FROM transactions WHERE id = ?", (t_id,)).fetchone()
    return dict(row)

@app.put("/transactions/{transaction_id}", response_model=TransactionResponse)
def update_transaction(transaction_id: int, t: TransactionCreate, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    cur.execute(
        "UPDATE transactions SET date = ?, amount = ?, category = ?, description = ? WHERE id = ?",
        (t.date, t.amount, t.category, t.description, transaction_id)
    )
    db.commit()
    
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")
        
    row = db.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    return dict(row)

@app.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    cur.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    db.commit()
    
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")
        
    return {"message": "Transaction deleted"}
