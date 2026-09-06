import openpyxl
from openpyxl.styles import Font, PatternFill
from database.queries import get_all_transactions
import os

def generate_excel_report(output_path: str):
    """Generates an Excel report of all transactions."""
    transactions = get_all_transactions()
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"
    
    # Headers
    headers = [
        "ID", "Date", "Time", "Type", "Person", "Sender", "Recipient", 
        "Amount", "UPI ID", "Phone", "Reference Number", "Transaction ID", 
        "Payment App", "Bank", "Status", "Balance Before", "Balance After", "Created At"
    ]
    
    ws.append(headers)
    
    # Styling headers
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
    
    # Freeze top row
    ws.freeze_panes = 'A2'
    
    # Data
    for t in transactions:
        row = [
            t['id'], 
            t['transaction_date'], 
            t['transaction_time'], 
            t['transaction_type'],
            t['person_name'], 
            t['sender_name'], 
            t['recipient_name'], 
            t['amount'], 
            t['upi_id'], 
            t['phone_number'],
            t['reference_number'], 
            t['transaction_id'], 
            t['payment_app'], 
            t['bank_name'],
            t['payment_status'], 
            t['balance_before'], 
            t['balance_after'], 
            t['created_at']
        ]
        ws.append(row)
        
    # Formatting
    for row in range(2, len(transactions) + 2):
        # Format amount, balances as currency
        ws.cell(row=row, column=8).number_format = '₹#,##0.00'
        ws.cell(row=row, column=16).number_format = '₹#,##0.00'
        ws.cell(row=row, column=17).number_format = '₹#,##0.00'
        
    # Auto-adjust column widths
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter # Get the column name
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = (max_length + 2)
        ws.column_dimensions[column].width = adjusted_width

    wb.save(output_path)
