# Billing Application

This is a simple web-based billing application built with Python (Flask) and SQLite.

## Features
- Enter billing header (OP bill no, Patient Name, Date)
- Enter billing details (Category, Amount, Payment Type)
- Manage Category and Payment Type master records
- Minimal, user-friendly web UI

## Setup
1. Ensure you have Python 3.7+ installed.
2. (Optional) Create and activate a virtual environment.
3. Install dependencies:
   ```
   pip install flask flask_sqlalchemy
   ```
4. Run the application:
   ```
   python app.py
   ```
5. Open your browser and go to http://127.0.0.1:5000/

## Project Structure
- `app.py` - Main Flask application
- `templates/` - HTML templates
- `billing.db` - SQLite database (auto-created)

## Notes
- The database and tables are created automatically on first run.
- Use the "Manage Masters" page to add categories and payment types before adding entries.
