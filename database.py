import pandas as pd
import datetime
import streamlit as st
from sqlalchemy import create_engine, text

@st.cache_resource
def get_engine():
    db_url = st.secrets["secrets"]["DB_URL"] if "secrets" in st.secrets else st.secrets["DB_URL"]
    return create_engine(db_url, pool_pre_ping=True, pool_recycle=300)

def init_db():
    engine = get_engine()
    with engine.begin() as conn:
        # Create daily_metrics table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS daily_metrics (
                date TEXT PRIMARY KEY,
                weight REAL,
                body_fat_percent REAL,
                skeletal_muscle_percent REAL,
                bmi REAL,
                basal_metabolic_rate REAL,
                source TEXT
            )
        '''))
        
        # Create side_effects table with SERIAL (auto-increment) in PostgreSQL
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS side_effects (
                id SERIAL PRIMARY KEY,
                date TEXT,
                notes TEXT
            )
        '''))
        
        # Create skipped_dates table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS skipped_dates (
                date TEXT PRIMARY KEY
            )
        '''))

def process_and_upload_csv(file_path_or_buffer):
    """
    Reads the CSV, filters for the earliest record per day (morning),
    and upserts into the database.
    """
    df = pd.read_csv(file_path_or_buffer)
    
    col_mapping = {
        '측정 날짜': 'datetime',
        '체중(kg)': 'weight',
        '체지방(%)': 'body_fat_percent',
        '골격근(%)': 'skeletal_muscle_percent',
        'BMI': 'bmi',
        '기초 대사(kcal)': 'basal_metabolic_rate'
    }
    
    existing_cols = {k: v for k, v in col_mapping.items() if k in df.columns}
    df = df[list(existing_cols.keys())]
    df = df.rename(columns=existing_cols)
    
    df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')
    df = df.dropna(subset=['datetime', 'weight'])
    
    df['date'] = df['datetime'].dt.strftime('%Y-%m-%d')
    df['hour'] = df['datetime'].dt.hour
    
    df_morning = df[(df['hour'] >= 5) & (df['hour'] < 11)]
    df_morning_daily = df_morning.sort_values('datetime').drop_duplicates(subset=['date'], keep='first')
    
    morning_dates = set(df_morning_daily['date'])
    df_fallback = df[~df['date'].isin(morning_dates)].sort_values('datetime').drop_duplicates(subset=['date'], keep='first')
    
    df_daily = pd.concat([df_morning_daily, df_fallback]).copy()
    df_daily['source'] = 'CSV'
    
    engine = get_engine()
    with engine.begin() as conn:
        for _, row in df_daily.iterrows():
            conn.execute(text('''
                INSERT INTO daily_metrics 
                (date, weight, body_fat_percent, skeletal_muscle_percent, bmi, basal_metabolic_rate, source)
                VALUES (:date, :weight, :bf, :sm, :bmi, :bmr, :source)
                ON CONFLICT (date) DO UPDATE SET
                    weight = EXCLUDED.weight,
                    body_fat_percent = EXCLUDED.body_fat_percent,
                    skeletal_muscle_percent = EXCLUDED.skeletal_muscle_percent,
                    bmi = EXCLUDED.bmi,
                    basal_metabolic_rate = EXCLUDED.basal_metabolic_rate,
                    source = EXCLUDED.source
            '''), {
                'date': row['date'], 
                'weight': None if pd.isna(row.get('weight')) else float(row.get('weight')), 
                'bf': None if pd.isna(row.get('body_fat_percent')) else float(row.get('body_fat_percent')), 
                'sm': None if pd.isna(row.get('skeletal_muscle_percent')) else float(row.get('skeletal_muscle_percent')), 
                'bmi': None if pd.isna(row.get('bmi')) else float(row.get('bmi')), 
                'bmr': None if pd.isna(row.get('basal_metabolic_rate')) else float(row.get('basal_metabolic_rate')), 
                'source': row['source']
            })

def upsert_manual_entry(date_str, weight):
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text('SELECT source FROM daily_metrics WHERE date = :date'), {'date': date_str})
        row = result.fetchone()
        
        if row is not None and row[0] == 'CSV':
            return False, "CSV 데이터가 이미 존재하여 수동 입력이 무시되었습니다."
        
        conn.execute(text('''
            INSERT INTO daily_metrics (date, weight, source)
            VALUES (:date, :weight, 'MANUAL')
            ON CONFLICT (date) DO UPDATE SET
                weight = EXCLUDED.weight,
                source = EXCLUDED.source
        '''), {'date': date_str, 'weight': float(weight)})
        
    return True, "저장되었습니다."

def get_interpolated_data():
    """
    Fetches all daily metrics, creates a complete date range, 
    and interpolates missing values.
    """
    engine = get_engine()
    query = "SELECT * FROM daily_metrics ORDER BY date ASC"
    df = pd.read_sql_query(query, engine)
    
    if df.empty:
        return df
        
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    
    min_date = df.index.min()
    max_date = pd.to_datetime(datetime.date.today())
    if max_date < df.index.max():
        max_date = df.index.max()
        
    full_date_range = pd.date_range(start=min_date, end=max_date, freq='D')
    
    df = df.reindex(full_date_range)
    df.index.name = 'date'
    df = df.reset_index()
    
    numeric_cols = ['weight', 'body_fat_percent', 'skeletal_muscle_percent', 'bmi', 'basal_metabolic_rate']
    df[numeric_cols] = df[numeric_cols].interpolate(method='linear')
    df = df.dropna(subset=['weight'])
    
    return df

def save_side_effect(date_str, notes):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text('INSERT INTO side_effects (date, notes) VALUES (:date, :notes)'), 
                     {'date': date_str, 'notes': notes})

def get_all_side_effects():
    engine = get_engine()
    query = "SELECT id, date, notes FROM side_effects ORDER BY id DESC"
    df = pd.read_sql_query(query, engine)
    return df

def delete_side_effect(record_id):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM side_effects WHERE id = :id'), {'id': int(record_id)})

def get_missing_dates():
    """
    Returns a list of date strings (YYYY-MM-DD) that have no record
    in daily_metrics AND are not in skipped_dates, from the earliest existing date to today.
    """
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text('SELECT MIN(date), MAX(date) FROM daily_metrics'))
        row = result.fetchone()
        
        if row is None or row[0] is None:
            return []
        
        min_date = datetime.datetime.strptime(row[0], '%Y-%m-%d').date()
        max_date = datetime.date.today()
        
        result = conn.execute(text('SELECT date FROM daily_metrics ORDER BY date'))
        existing = set(r[0] for r in result.fetchall())
        
        result = conn.execute(text('SELECT date FROM skipped_dates'))
        skipped = set(r[0] for r in result.fetchall())
        
    missing = []
    current = min_date
    while current <= max_date:
        ds = current.strftime('%Y-%m-%d')
        if ds not in existing and ds not in skipped:
            missing.append(ds)
        current += datetime.timedelta(days=1)
    
    return missing

def skip_date(date_str):
    """Mark a date as skipped (no measurement taken)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text('''
            INSERT INTO skipped_dates (date) VALUES (:date)
            ON CONFLICT (date) DO NOTHING
        '''), {'date': date_str})

def get_skipped_dates():
    """Returns a set of skipped date strings."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text('SELECT date FROM skipped_dates ORDER BY date'))
        dates = set(r[0] for r in result.fetchall())
    return dates

def upsert_manual_entries(entries):
    """
    Bulk upsert manual entries. entries is a list of (date_str, weight) tuples.
    Skips dates that already have CSV data.
    Returns (saved_count, skipped_count).
    """
    engine = get_engine()
    saved = 0
    skipped = 0
    with engine.begin() as conn:
        for date_str, weight in entries:
            result = conn.execute(text('SELECT source FROM daily_metrics WHERE date = :date'), {'date': date_str})
            row = result.fetchone()
            if row is not None and row[0] == 'CSV':
                skipped += 1
                continue
            
            conn.execute(text('''
                INSERT INTO daily_metrics (date, weight, source)
                VALUES (:date, :weight, 'MANUAL')
                ON CONFLICT (date) DO UPDATE SET
                    weight = EXCLUDED.weight,
                    source = EXCLUDED.source
            '''), {'date': date_str, 'weight': float(weight)})
            saved += 1
    return saved, skipped
