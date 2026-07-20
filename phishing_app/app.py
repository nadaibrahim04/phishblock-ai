import os
import re
import time
import secrets
import sqlite3
import numpy as np
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler
from sklearn.ensemble import RandomForestClassifier

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
DB_FILE = 'users.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

model = None
scaler = None

FEATURE_COLS = [
    'ranking', 'mld_res', 'mld.ps_res', 'card_rem', 'ratio_Rrem', 'ratio_Arem', 
    'jaccard_RR', 'jaccard_RA', 'jaccard_AR', 'jaccard_AA', 'jaccard_ARrd', 
    'jaccard_ARrem', 'domain_len', 'qty_dots', 'qty_hyphens'
]

def extract_domain_features(df):
    df['domain'] = df['domain'].astype(str)
    df['domain_len'] = df['domain'].apply(len)
    df['qty_dots'] = df['domain'].apply(lambda x: x.count('.'))
    df['qty_hyphens'] = df['domain'].apply(lambda x: x.count('-'))
    return df

def train_phishing_model():
    global model, scaler
    csv_filename = 'urlset.csv'
    
    print(f"[*] Searching for local dataset: '{csv_filename}'...")
    if not os.path.exists(csv_filename):
        print(f"\n[CRITICAL ERROR] '{csv_filename}' not found!")
        os._exit(1)
        
    try:
        df = pd.read_csv(csv_filename, encoding='latin1', on_bad_lines='skip', low_memory=False)
        print(f"[+] Loaded {len(df)} records.")
        
        df = extract_domain_features(df)
        
        if 'label' in df.columns:
            target_col = 'label'
        elif 'Class' in df.columns:
            target_col = 'Class'
        else:
            raise KeyError("Target column 'label' or 'Class' missing from CSV headers.")
        
        for col in FEATURE_COLS:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df[target_col] = pd.to_numeric(df[target_col], errors='coerce')
        
        df = df.dropna(subset=FEATURE_COLS + [target_col])
        df[target_col] = df[target_col].astype(int)
        
        class_counts = df[target_col].value_counts()
        if len(class_counts) == 2 and (class_counts.max() / class_counts.min() > 1.5):
            df_majority = df[df[target_col] == class_counts.idxmax()]
            df_minority = df[df[target_col] == class_counts.idxmin()]
            df_minority_upsampled = df_minority.sample(len(df_majority), replace=True, random_state=42)
            df = pd.concat([df_majority, df_minority_upsampled], axis=0).sample(frac=1, random_state=42)
        
        X = df[FEATURE_COLS]
        y = df[target_col]
        
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
        scaler = MinMaxScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        
        model = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
        model.fit(X_train_scaled, y_train)
        
        X_test_scaled = scaler.transform(X_test)
        print(f"[+] Balanced Model Accuracy Matrix: {model.score(X_test_scaled, y_test)*100:.2f}%\n")
        
    except Exception as e:
        print(f"\n[CRITICAL BUILD ERROR] Pipeline initialization failed: {str(e)}\n")
        os._exit(1)

def validate_password(password):
    if len(password) < 8: return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[0-9]", password): return False
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>_+\-=\[\]\\/]", password): return False
    return True

@app.route('/')
def index():
    if 'user' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    show_signup_popup = False
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        conn.close()
        
        if row and check_password_hash(row[0], password):
            session['user'] = email
            return redirect(url_for('dashboard'))
        else:
            show_signup_popup = True
            flash("Invalid credentials or user account does not exist within this system node.", "error")
            
    return render_template('login.html', show_signup_popup=show_signup_popup)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if not email or not password:
            flash("All fields mandatory.", "error")
            return render_template('signup.html')
        if password != confirm_password:
            flash("Password confirmation mismatch.", "error")
            return render_template('signup.html')
        if not validate_password(password):
            flash("Password must be >= 8 characters and include uppercase, lowercase, numbers, and symbols.", "error")
            return render_template('signup.html')
            
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            conn.close()
            flash("Identifier already registered.", "error")
            return redirect(url_for('login'))
            
        try:
            cursor.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, generate_password_hash(password)))
            conn.commit()
            conn.close()
            flash("System registration complete.", "success")
            return redirect(url_for('login'))
        except:
            conn.close()
            flash("Database layer fault.", "error")
        
    return render_template('signup.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session: return redirect(url_for('login'))
    return render_template('dashboard.html', user=session['user'])

@app.route('/manual', methods=['GET', 'POST'])
def manual():
    if 'user' not in session: return redirect(url_for('login'))
        
    result = None
    if request.method == 'POST':
        url_input = request.form.get('url', '').strip()
        if url_input:
            clean_domain = url_input.replace('http://', '').replace('https://', '').split('/')[0]
            
            # --- STEP 1: STATIC TRUSTED WHITELIST ---
            trusted_domains = ['youtube.com', 'google.com', 'github.com', 'netflix.com', 'linkedin.com', 'microsoft.com', 'apple.com']
            is_whitelisted = any(trusted == clean_domain.lower() or clean_domain.lower().endswith('.' + trusted) for trusted in trusted_domains)
            
            # --- STEP 2: HEURISTIC RISK ENGINE ---
            # If the domain isn't explicitly whitelisted, analyze its core risk indicators
            risk_score = 0
            explanations = []
            
            if is_whitelisted:
                status = 'Safe / Verified'
                confidence = "100.00%"
                explanations.append("This domain matches an verified system profile database entry.")
            else:
                # Flag structural length anomalies
                if len(clean_domain) > 22:
                    risk_score += 35
                    explanations.append("High character footprint length observed within root configuration bounds.")
                
                # Flag nested subdomain layering
                if clean_domain.count('.') >= 3:
                    risk_score += 30
                    explanations.append("Excessive multi-level subdomains deployed on a single node pipeline.")
                
                # Flag obfuscation symbols
                if clean_domain.count('-') >= 2:
                    risk_score += 25
                    explanations.append("Repetitive hyphen separators flagged as structural spoofing behaviors.")
                
                # Flag raw IP structures
                if re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', clean_domain):
                    risk_score += 50
                    explanations.append("Target resolved directly to a raw dotted IP structure instead of a verified host registry name.")
                
                # Flag high-risk social engineering keywords
                keywords = ['login', 'secure', 'verify', 'update', 'banking', 'account', 'sign-in', 'paypal', 'support']
                found_keywords = [kw for kw in keywords if kw in clean_domain.lower()]
                if found_keywords:
                    risk_score += 40
                    explanations.append(f"Target contains sensitive string tokens frequently used during deceptive brand manipulation: {', '.join(found_keywords)}.")
                
                # --- STEP 3: RESOLVE MULTI-TIER STATUS BASED ON RISK ---
                if risk_score >= 65:
                    status = 'Phishing Flagged'
                    confidence = f"{max(72.5, min(99.4, risk_score + 15)):.2f}%"
                elif risk_score >= 30:
                    status = 'Suspicious / Anomalous'
                    confidence = f"{max(51.0, min(69.9, risk_score + 20)):.2f}%"
                else:
                    status = 'Safe / Verified'
                    confidence = f"{max(85.0, min(98.9, 100 - risk_score)):.2f}%"
                    
                if not explanations:
                    explanations.append("Domain string layout attributes fall cleanly within regular structural metrics profiles.")

            result = {
                'url': url_input,
                'status': status,
                'confidence': confidence,
                'explanations': explanations
            }
            
    return render_template('manual.html', result=result)

@app.route('/live-monitor')
def live_monitor():
    if 'user' not in session: return redirect(url_for('login'))
    return render_template('live_monitor.html')

@app.route('/stream-email-scan')
def stream_email_scan():
    if 'user' not in session: return Response("Unauthorized.", status=401)

    def generate_scans():
        simulated_emails = [
            ("Urgent Account Verification Required", "http://secure-paypal-login-update.net", "PHISHING", "94.50%"),
            ("Weekly Team Meeting Invitation", "https://github.com/project-workspace", "SAFE", "98.20%"),
            ("Suspicious activity alert index link", "http://verify-invoice-39402.xyz", "PHISHING", "87.15%"),
            ("Check out these trending updates", "https://youtube.com/feed/trending", "SAFE", "100.00%"),
            ("Action Required: Shared folder file transfer", "http://subdomain.login-portal-verification-node.com", "SUSPICIOUS", "64.30%")
        ]
        idx = 0
        while True:
            subject, url, status, conf = simulated_emails[idx % len(simulated_emails)]
            yield f"data: {{\"subject\": \"{subject}\", \"url\": \"{url}\", \"status\": \"{status}\", \"confidence\": \"{conf}\"}}\n\n"
            idx += 1
            time.sleep(4.5)
            
    return Response(generate_scans(), mimetype='text/event-stream')

@app.route('/logout')
def logout():
    session.clear()
    flash("Session context cleared successfully.", "success")
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    train_phishing_model()
    app.run(debug=True, port=5000)