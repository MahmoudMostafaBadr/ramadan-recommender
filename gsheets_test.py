import gspread
from google.oauth2.service_account import Credentials

# 1) عدّل اسم ملف الـ JSON هنا لو مختلف
SERVICE_ACCOUNT_FILE = "ramadan-recommender-336c84ce6b13.json"

# 2) Sheet ID بتاعك (من اللينك)
SHEET_ID = "19BsqWLeMByqhNoybXPowEgqiYdFumJY1-dqagv5atlU"

# 3) اسم التاب جوه الشيت (غالباً Sheet1 إلا لو غيرته)
WORKSHEET_NAME = "logs"  # لو اسمها logs أو login غيّره هنا

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
gc = gspread.authorize(creds)

sh = gc.open_by_key(SHEET_ID)
ws = sh.worksheet(WORKSHEET_NAME)

ws.append_row(["TEST", "dodo", "suhoor", 700, 20, 1200, 10, "OK"])
print("✅ DONE: row appended successfully!")
