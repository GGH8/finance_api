from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "finance.db"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Finance Tracker API", lifespan=lifespan)


def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


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


class TopDescriptionItem(BaseModel):
    description: str
    total_amount: float
    count: int


def build_transaction_filters(
    month: Optional[str],
    category: Optional[str],
) -> tuple[str, list[str]]:
    where_clauses: list[str] = []
    params: list[str] = []

    if month:
        where_clauses.append("substr(date, 1, 7) = ?")
        params.append(month)

    if category:
        where_clauses.append("LOWER(category) LIKE ?")
        params.append(f"%{category.lower()}%")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    return where_sql, params


@app.get("/transactions", response_model=list[TransactionResponse])
def get_transactions(
    month: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    where_sql, params = build_transaction_filters(month, category)

    rows = db.execute(
        f"""
        SELECT id, date, amount, category, description
        FROM transactions
        {where_sql}
        ORDER BY date DESC, id DESC
        """,
        params,
    ).fetchall()

    return [dict(row) for row in rows]


@app.get("/transactions/summary", response_model=SummaryResponse)
def get_summary(
    month: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    where_sql, params = build_transaction_filters(month, category)

    row = db.execute(
        f"""
        SELECT
            COALESCE(SUM(amount), 0) AS current_income,
            COALESCE(SUM(CASE WHEN LOWER(category) = 'income' THEN amount ELSE 0 END), 0) AS total_income,
            COALESCE(SUM(CASE WHEN LOWER(category) != 'income' THEN ABS(amount) ELSE 0 END), 0) AS total_expenses
        FROM transactions
        {where_sql}
        """,
        params,
    ).fetchone()

    return SummaryResponse(
        current_income=float(row["current_income"]),
        total_income=float(row["total_income"]),
        total_expenses=float(row["total_expenses"]),
    )


@app.post("/transactions", response_model=TransactionResponse)
def create_transaction(
    transaction: TransactionCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO transactions (date, amount, category, description)
        VALUES (?, ?, ?, ?)
        """,
        (
            transaction.date,
            transaction.amount,
            transaction.category,
            transaction.description,
        ),
    )
    db.commit()

    new_id = cur.lastrowid
    row = db.execute(
        """
        SELECT id, date, amount, category, description
        FROM transactions
        WHERE id = ?
        """,
        (new_id,),
    ).fetchone()

    return dict(row)


@app.put("/transactions/{transaction_id}", response_model=TransactionResponse)
def update_transaction(
    transaction_id: int,
    transaction: TransactionCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.cursor()
    cur.execute(
        """
        UPDATE transactions
        SET date = ?, amount = ?, category = ?, description = ?
        WHERE id = ?
        """,
        (
            transaction.date,
            transaction.amount,
            transaction.category,
            transaction.description,
            transaction_id,
        ),
    )
    db.commit()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")

    row = db.execute(
        """
        SELECT id, date, amount, category, description
        FROM transactions
        WHERE id = ?
        """,
        (transaction_id,),
    ).fetchone()

    return dict(row)


@app.delete("/transactions/{transaction_id}")
def delete_transaction(
    transaction_id: int,
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.cursor()
    cur.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    db.commit()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return {"message": "Transaction deleted"}


@app.get("/analytics/top-descriptions", response_model=list[TopDescriptionItem])
def get_top_descriptions(
    month: Optional[str] = Query(None),
    limit: int = Query(7, ge=1, le=20),
    db: sqlite3.Connection = Depends(get_db),
):
    where_clauses = [
        "amount < 0",
        "LOWER(category) = 'alte cheltuieli'",
    ]
    params: list[str] = []

    if month:
        where_clauses.append("substr(date, 1, 7) = ?")
        params.append(month)

    where_sql = "WHERE " + " AND ".join(where_clauses)

    rows = db.execute(
        f"""
        SELECT
            TRIM(description) AS description,
            COALESCE(SUM(ABS(amount)), 0) AS total_amount,
            COUNT(*) AS count
        FROM transactions
        {where_sql}
        GROUP BY TRIM(description)
        ORDER BY total_amount DESC, count DESC, description ASC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()

    return [
        TopDescriptionItem(
            description=str(row["description"]),
            total_amount=float(row["total_amount"]),
            count=int(row["count"]),
        )
        for row in rows
    ]
