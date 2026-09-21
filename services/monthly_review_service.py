"""
Monthly Review & Financial Closing Service.
Calculates net savings, savings rate %, top spend category, top payee,
peak single expense, and budget usage. Records frozen monthly reviews.
"""

from typing import Dict, Optional
from database.db import get_db_connection, LEDGER_LOCK
from database.queries import increment_revision_and_mark_dirty
from services.budget_service import get_budget_info
from utils.dates import utc_now_iso, get_current_time_in_tz

def calculate_monthly_closing_metrics(year: int, month: int) -> Dict:
    """Calculates all key performance and retrospective metrics for a month."""
    month_str = f"{year:04d}-{month:02d}"
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Total Income & Total Expenses (excluding TRANSFER)
        cursor.execute("""
            SELECT 
                transaction_type,
                COUNT(*) as count,
                SUM(amount) as total_amount
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND deleted_at IS NULL
            GROUP BY transaction_type
        """, (month_str,))
        rows = cursor.fetchall()
        
        total_income = 0.0
        total_expense = 0.0
        tx_count = 0
        for r in rows:
            tx_count += r['count']
            if r['transaction_type'] == 'RECEIVED':
                total_income = float(r['total_amount'] or 0.0)
            elif r['transaction_type'] == 'SENT':
                total_expense = float(r['total_amount'] or 0.0)
                
        net_savings = round(total_income - total_expense, 2)
        savings_rate = round((net_savings / total_income * 100.0), 1) if total_income > 0 else (0.0 if total_expense == 0 else -100.0)
        
        # 2. Top Category
        cursor.execute("""
            SELECT category, SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND transaction_type = 'SENT' AND deleted_at IS NULL
            GROUP BY category
            ORDER BY total DESC LIMIT 1
        """, (month_str,))
        top_cat_row = cursor.fetchone()
        top_cat = top_cat_row['category'] if top_cat_row else "None"
        top_cat_amt = float(top_cat_row['total'] or 0.0) if top_cat_row else 0.0
        
        # 3. Top Payee
        cursor.execute("""
            SELECT person_name, SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND transaction_type = 'SENT' AND person_name != '' AND deleted_at IS NULL
            GROUP BY person_name
            ORDER BY total DESC LIMIT 1
        """, (month_str,))
        top_payee_row = cursor.fetchone()
        top_payee = top_payee_row['person_name'] if top_payee_row else "None"
        top_payee_amt = float(top_payee_row['total'] or 0.0) if top_payee_row else 0.0
        
        # 4. Largest single transaction
        cursor.execute("""
            SELECT id, person_name, amount
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND transaction_type = 'SENT' AND deleted_at IS NULL
            ORDER BY amount DESC LIMIT 1
        """, (month_str,))
        max_tx_row = cursor.fetchone()
        max_tx_id = max_tx_row['id'] if max_tx_row else None
        max_tx_amt = float(max_tx_row['amount'] or 0.0) if max_tx_row else 0.0
        max_tx_payee = max_tx_row['person_name'] if max_tx_row else "None"
        
        # 5. Budget adherence
        b_info = get_budget_info(year, month)
        budget_allocated = float(b_info.get('budget', 0.0))
        budget_spent_pct = float(b_info.get('percent_used', 0.0))
        
        # 6. Check existing recorded review
        cursor.execute("SELECT * FROM monthly_reviews WHERE year = ? AND month = ?", (year, month))
        review_row = cursor.fetchone()
        is_closed = bool(review_row)
        reviewed_at = review_row['reviewed_at'] if review_row else None
        
        return {
            'year': year,
            'month': month,
            'month_str': month_str,
            'total_income': total_income,
            'total_expense': total_expense,
            'net_savings': net_savings,
            'savings_rate_pct': savings_rate,
            'top_category': top_cat,
            'top_category_amount': top_cat_amt,
            'top_payee': top_payee,
            'top_payee_amount': top_payee_amt,
            'max_transaction_id': max_tx_id,
            'max_transaction_amount': max_tx_amt,
            'max_transaction_payee': max_tx_payee,
            'budget_allocated': budget_allocated,
            'budget_spent_pct': budget_spent_pct,
            'transaction_count': tx_count,
            'is_closed': is_closed,
            'reviewed_at': reviewed_at
        }

def close_and_record_monthly_review(year: int, month: int, notes: str = '') -> Dict:
    """
    Freezes and records the monthly retrospective review into monthly_reviews table.
    Idempotent: updates existing review timestamp if already reviewed.
    """
    metrics = calculate_monthly_closing_metrics(year, month)
    now_iso = utc_now_iso()
    
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO monthly_reviews (
                    year, month, total_income, total_expense, net_savings, savings_rate_pct,
                    top_category, top_category_amount, top_payee, top_payee_amount,
                    max_transaction_id, max_transaction_amount, budget_allocated, budget_spent_pct,
                    is_closed, reviewed_at, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """, (
                year, month, metrics['total_income'], metrics['total_expense'],
                metrics['net_savings'], metrics['savings_rate_pct'],
                metrics['top_category'], metrics['top_category_amount'],
                metrics['top_payee'], metrics['top_payee_amount'],
                metrics['max_transaction_id'], metrics['max_transaction_amount'],
                metrics['budget_allocated'], metrics['budget_spent_pct'],
                now_iso, notes or "", now_iso
            ))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            
    metrics['is_closed'] = True
    metrics['reviewed_at'] = now_iso
    return metrics

def get_monthly_review(year: int, month: int) -> Optional[Dict]:
    """Retrieves a previously frozen monthly review."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monthly_reviews WHERE year = ? AND month = ?", (year, month))
        row = cursor.fetchone()
        return dict(row) if row else None
