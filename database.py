import pandas as pd
import datetime
import time
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from functools import wraps

# 마운자로 용량별 박스(4펜) 가격 (원)
DOSE_PRICES = {
    2.5: 305_000,
    5.0: 405_000,
    7.5: 560_000,
    10.0: 560_000,
}

def retry_on_operational_error(max_retries=3, delay=1.5):
    """
    Catch OperationalError (like DB connection dropped or cold start),
    wait for `delay` seconds, and retry.
    Only delays when an error occurs.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    retries += 1
                    if retries > max_retries:
                        raise e
                    time.sleep(delay)
        return wrapper
    return decorator

@st.cache_resource
def get_engine():
    db_url = st.secrets["secrets"]["DB_URL"] if "secrets" in st.secrets else st.secrets["DB_URL"]
    return create_engine(db_url, pool_pre_ping=True, pool_recycle=300)

@retry_on_operational_error()
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
        
        # Create injection_boxes table (마운자로 투여 박스 기록)
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS injection_boxes (
                id SERIAL PRIMARY KEY,
                start_date TEXT NOT NULL,
                dose_mg REAL NOT NULL,
                box_price INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))

@retry_on_operational_error()
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

@retry_on_operational_error()
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

@retry_on_operational_error()
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

@retry_on_operational_error()
def save_side_effect(date_str, notes):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text('INSERT INTO side_effects (date, notes) VALUES (:date, :notes)'), 
                     {'date': date_str, 'notes': notes})

@retry_on_operational_error()
def get_all_side_effects():
    engine = get_engine()
    query = "SELECT id, date, notes FROM side_effects ORDER BY id DESC"
    df = pd.read_sql_query(query, engine)
    return df

@retry_on_operational_error()
def delete_side_effect(record_id):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM side_effects WHERE id = :id'), {'id': int(record_id)})

@retry_on_operational_error()
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

@retry_on_operational_error()
def skip_date(date_str):
    """Mark a date as skipped (no measurement taken)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text('''
            INSERT INTO skipped_dates (date) VALUES (:date)
            ON CONFLICT (date) DO NOTHING
        '''), {'date': date_str})

@retry_on_operational_error()
def get_skipped_dates():
    """Returns a set of skipped date strings."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text('SELECT date FROM skipped_dates ORDER BY date'))
        dates = set(r[0] for r in result.fetchall())
    return dates

@retry_on_operational_error()
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

# ─── 투여 박스 기록 관련 함수 ───

# 기존 투여 이력 (앱 최초 실행 시 자동 삽입)
_INITIAL_INJECTION_HISTORY = [
    ('2025-12-20', 2.5),
    ('2026-01-17', 5.0),
    ('2026-02-14', 5.0),
    ('2026-03-14', 5.0),
    ('2026-04-11', 5.0),
    ('2026-05-09', 7.5),
    ('2026-06-06', 7.5),
    ('2026-07-04', 7.5),
    ('2026-08-01', 7.5),
]

@retry_on_operational_error()
def init_injection_data():
    """테이블이 비어있으면 기존 투여 이력을 자동 삽입합니다."""
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text('SELECT COUNT(*) FROM injection_boxes'))
        count = result.scalar()
        if count == 0:
            for start_date, dose in _INITIAL_INJECTION_HISTORY:
                price = DOSE_PRICES[dose]
                conn.execute(text('''
                    INSERT INTO injection_boxes (start_date, dose_mg, box_price)
                    VALUES (:start_date, :dose_mg, :box_price)
                '''), {'start_date': start_date, 'dose_mg': dose, 'box_price': price})

@retry_on_operational_error()
def add_injection_box(start_date, dose_mg):
    """새 박스를 등록합니다. 가격은 DOSE_PRICES에서 자동 매핑."""
    price = DOSE_PRICES[dose_mg]
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text('''
            INSERT INTO injection_boxes (start_date, dose_mg, box_price)
            VALUES (:start_date, :dose_mg, :box_price)
        '''), {'start_date': start_date, 'dose_mg': dose_mg, 'box_price': price})

@retry_on_operational_error()
def delete_last_injection_box():
    """가장 마지막에 등록된 박스를 삭제합니다."""
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text('SELECT id FROM injection_boxes ORDER BY start_date DESC, id DESC LIMIT 1'))
        row = result.fetchone()
        if row:
            conn.execute(text('DELETE FROM injection_boxes WHERE id = :id'), {'id': row[0]})
            return True
    return False

@retry_on_operational_error()
def get_all_injection_boxes():
    """전체 박스 목록을 DataFrame으로 반환합니다."""
    engine = get_engine()
    query = "SELECT id, start_date, dose_mg, box_price FROM injection_boxes ORDER BY start_date ASC"
    df = pd.read_sql_query(query, engine)
    return df

def get_all_injection_dates(boxes_df=None):
    """
    모든 투여 날짜 + 용량 리스트를 반환합니다.
    각 박스의 start_date로부터 4주(4회 토요일)를 파생합니다.
    Returns: list of dicts [{'date': datetime, 'dose_mg': float, 'box_id': int}, ...]
    """
    if boxes_df is None:
        boxes_df = get_all_injection_boxes()
    
    injections = []
    for _, box in boxes_df.iterrows():
        start = pd.to_datetime(box['start_date'])
        for week in range(4):
            inj_date = start + pd.Timedelta(weeks=week)
            injections.append({
                'date': inj_date,
                'dose_mg': box['dose_mg'],
                'box_id': box['id'],
                'box_price': box['box_price'],
            })
    return injections
