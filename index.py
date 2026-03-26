from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import re
import time
import json
import os
from datetime import datetime
import threading

app = Flask(__name__, template_folder='../templates')

# Database for OTPs and numbers (in-memory for Vercel)
otp_database = []
numbers_list = []
otp_cache = set()

# iVASMS credentials from environment variables
IVASMS_EMAIL = os.environ.get('IVASMS_EMAIL', '')
IVASMS_PASSWORD = os.environ.get('IVASMS_PASSWORD', '')
SESSION_COOKIE = None

def login_ivasms():
    """Login to iVASMS and get session cookie"""
    global SESSION_COOKIE
    
    try:
        session = requests.Session()
        
        # First get login page to get CSRF token
        login_page = session.get('https://ivasms.com/login')
        soup = BeautifulSoup(login_page.text, 'html.parser')
        
        # Find CSRF token if exists
        csrf_token = None
        token_input = soup.find('input', {'name': 'csrf_token'})
        if token_input:
            csrf_token = token_input.get('value')
        
        # Login data
        login_data = {
            'email': IVASMS_EMAIL,
            'password': IVASMS_PASSWORD
        }
        if csrf_token:
            login_data['csrf_token'] = csrf_token
        
        # Post login
        response = session.post('https://ivasms.com/login', data=login_data)
        
        # Check if login successful
        if 'dashboard' in response.url or 'sms' in response.url:
            SESSION_COOKIE = session.cookies.get_dict()
            return True
        else:
            return False
            
    except Exception as e:
        print(f"Login error: {e}")
        return False

def get_otps_from_ivasms():
    """Fetch OTPs from iVASMS"""
    global SESSION_COOKIE
    
    if not SESSION_COOKIE:
        if not login_ivasms():
            return []
    
    try:
        session = requests.Session()
        session.cookies.update(SESSION_COOKIE)
        
        # Go to SMS page
        response = session.get('https://ivasms.com/sms')
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all SMS messages
        messages = soup.find_all('div', class_='sms-message')
        if not messages:
            messages = soup.find_all('div', class_='message')
        if not messages:
            messages = soup.find_all('li', class_='sms-item')
        
        otps = []
        
        for msg in messages:
            text = msg.get_text()
            
            # Extract OTP (4-6 digit numbers)
            otp_match = re.search(r'\b\d{4,6}\b', text)
            if otp_match:
                otp_value = otp_match.group()
                
                # Extract phone number if present
                phone_match = re.search(r'\+\d{10,15}', text)
                phone = phone_match.group() if phone_match else 'Unknown'
                
                # Extract service name
                service_match = re.search(r'([A-Za-z]+)\s*:', text)
                service = service_match.group(1) if service_match else 'Unknown'
                
                otps.append({
                    'otp': otp_value,
                    'text': text[:200],
                    'phone': phone,
                    'service': service,
                    'time': datetime.now().strftime('%H:%M:%S %d/%m/%Y')
                })
        
        return otps
        
    except Exception as e:
        print(f"Fetch error: {e}")
        # Session might be expired
        SESSION_COOKIE = None
        return []

def process_new_otps():
    """Check for new OTPs and add to database"""
    global otp_database, otp_cache
    
    new_otps = get_otps_from_ivasms()
    
    added_count = 0
    for otp in new_otps:
        otp_id = f"{otp['otp']}_{otp['phone']}"
        
        if otp_id not in otp_cache:
            otp_cache.add(otp_id)
            otp['id'] = len(otp_database)
            otp_database.insert(0, otp)  # Add to beginning
            added_count += 1
            
            # Keep only last 100 OTPs
            if len(otp_database) > 100:
                old_otp = otp_database.pop()
                old_id = f"{old_otp['otp']}_{old_otp['phone']}"
                if old_id in otp_cache:
                    otp_cache.remove(old_id)
    
    return added_count

# Background thread for continuous monitoring
monitoring_active = False
monitoring_thread = None

def background_monitor():
    """Background thread to monitor OTPs"""
    global monitoring_active
    while monitoring_active:
        try:
            count = process_new_otps()
            if count > 0:
                print(f"Added {count} new OTPs")
        except Exception as e:
            print(f"Monitor error: {e}")
        time.sleep(60)  # Check every 60 seconds

@app.route('/')
def dashboard():
    """Main dashboard page"""
    return render_template('dashboard.html', 
                         otps=otp_database[:50],
                         numbers=numbers_list)

@app.route('/api/otps')
def get_otps():
    """API endpoint to get OTPs"""
    return jsonify(otp_database[:50])

@app.route('/api/numbers', methods=['GET', 'POST'])
def manage_numbers():
    """API to manage numbers list"""
    global numbers_list
    
    if request.method == 'POST':
        data = request.get_json()
        number = data.get('number')
        
        if number and number not in numbers_list:
            numbers_list.append(number)
            return jsonify({'status': 'added', 'number': number})
        
        return jsonify({'status': 'error', 'message': 'Number already exists or invalid'})
    
    return jsonify(numbers_list)

@app.route('/api/check', methods=['POST'])
def manual_check():
    """Manually check for new OTPs"""
    count = process_new_otps()
    return jsonify({'status': 'success', 'new_otps': count})

@app.route('/api/status')
def status():
    """Bot status"""
    return jsonify({
        'monitoring': monitoring_active,
        'total_otps': len(otp_database),
        'total_numbers': len(numbers_list),
        'ivasms_configured': bool(IVASMS_EMAIL and IVASMS_PASSWORD)
    })

@app.route('/api/start')
def start_monitor():
    """Start background monitoring"""
    global monitoring_active, monitoring_thread
    
    if not monitoring_active:
        monitoring_active = True
        monitoring_thread = threading.Thread(target=background_monitor, daemon=True)
        monitoring_thread.start()
        return jsonify({'status': 'started'})
    
    return jsonify({'status': 'already_running'})

@app.route('/api/stop')
def stop_monitor():
    """Stop background monitoring"""
    global monitoring_active
    monitoring_active = False
    return jsonify({'status': 'stopped'})

@app.route('/api/clear')
def clear_cache():
    """Clear OTP cache"""
    global otp_database, otp_cache
    otp_database = []
    otp_cache = set()
    return jsonify({'status': 'cleared'})

# Auto-start monitoring when app loads
if IVASMS_EMAIL and IVASMS_PASSWORD:
    monitoring_active = True
    monitoring_thread = threading.Thread(target=background_monitor, daemon=True)
    monitoring_thread.start()

# For local testing
if __name__ == '__main__':
    app.run(debug=True, port=5000)