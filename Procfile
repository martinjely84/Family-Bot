web: python -c "import database; database.init_db()" && gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 2
