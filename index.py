from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import re
import json
import os
from datetime import datetime

app = Flask(__name__, template_folder='../templates')

# Store OTPs and numbers (in-memory for Vercel)
otp_database = []
numbers_list = []
otp_cache = set()

# iVASMS credentials from environment variables
IVASMS_EMAIL = os.environ.get('IVASMS_EMAIL', '')
IVASMS_PASSWORD = os.environ.get('IVASMS_PASSWORD', '')
SESSION_COOKIE = None
LAST_CHECK_TIME = None

def login_ivasms():
    """Login to iVASMS and get session cookie"""
    global SESSION_COOKIE
    
    if not IVASMS_EMAIL or not IVASMS_PASSWORD:
        return False
    
    try:
        session = requests.Session()
        
        # Get login page first
        login_page = session.get('https://ivasms.com/login', timeout=30)
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
        response = session.post('https://ivasms.com/login', data=login_data, timeout=30)
        
        # Check if login successful
        if 'dashboard' in response.url or 'sms' in response.url or response.status_code == 200:
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
        
        # Try different possible URLs
        urls_to_try = [
            'https://ivasms.com/sms',
            'https://ivasms.com/dashboard',
            'https://ivasms.com/messages'
        ]
        
        response = None
        for url in urls_to_try:
            try:
                response = session.get(url, timeout=30)
                if response.status_code == 200:
                    break
            except:
                continue
        
        if not response or response.status_code != 200:
            return []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try different selectors for SMS messages
        messages = []
        selectors = [
            'div.sms-message',
            'div.message',
            'li.sms-item',
            'div.msg-item',
            'div.sms-content',
            'div.message-content',
            'td.message-text'
        ]
        
        for selector in selectors:
            found = soup.select(selector)
            if found:
                messages = found
                break
        
        if not messages:
            # Try to find any div with text containing digits
            all_divs = soup.find_all(['div', 'li', 'p', 'span'])
            for div in all_divs:
                text = div.get_text()
                if re.search(r'\b\d{4,6}\b', text) and len(text) < 500:
                    messages.append(div)
        
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
                
                # Extract service name (words before colon or common patterns)
                service = 'Unknown'
                service_match = re.search(r'([A-Za-z0-9]+)\s*:', text)
                if service_match:
                    service = service_match.group(1)
                else:
                    common_services = ['Amazon', 'Google', 'Facebook', 'PayPal', 'Apple', 'Microsoft', 'WhatsApp', 'Instagram']
                    for s in common_services:
                        if s.lower() in text.lower():
                            service = s
                            break
                
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
    global otp_database, otp_cache, LAST_CHECK_TIME
    
    new_otps = get_otps_from_ivasms()
    
    added_count = 0
    for otp in new_otps:
        otp_id = f"{otp['otp']}_{otp['phone']}"
        
        if otp_id not in otp_cache:
            otp_cache.add(otp_id)
            otp['id'] = len(otp_database)
            otp_database.insert(0, otp)
            added_count += 1
            
            # Keep only last 200 OTPs
            if len(otp_database) > 200:
                old_otp = otp_database.pop()
                old_id = f"{old_otp['otp']}_{old_otp['phone']}"
                if old_id in otp_cache:
                    otp_cache.remove(old_id)
    
    LAST_CHECK_TIME = datetime.now().strftime('%H:%M:%S')
    return added_count

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

@app.route('/api/numbers', methods=['GET', 'POST', 'DELETE'])
def manage_numbers():
    """API to manage numbers list"""
    global numbers_list
    
    if request.method == 'POST':
        data = request.get_json()
        number = data.get('number')
        
        if number and number not in numbers_list:
            numbers_list.append(number)
            return jsonify({'status': 'added', 'number': number, 'numbers': numbers_list})
        
        return jsonify({'status': 'error', 'message': 'Number already exists or invalid'})
    
    elif request.method == 'DELETE':
        data = request.get_json()
        number = data.get('number')
        
        if number and number in numbers_list:
            numbers_list.remove(number)
            return jsonify({'status': 'removed', 'number': number, 'numbers': numbers_list})
    
    return jsonify(numbers_list)

@app.route('/api/check', methods=['POST', 'GET'])
def manual_check():
    """Manually check for new OTPs"""
    count = process_new_otps()
    return jsonify({
        'status': 'success', 
        'new_otps': count,
        'total_otps': len(otp_database),
        'last_check': LAST_CHECK_TIME
    })

@app.route('/api/status')
def status():
    """Bot status"""
    return jsonify({
        'total_otps': len(otp_database),
        'total_numbers': len(numbers_list),
        'ivasms_configured': bool(IVASMS_EMAIL and IVASMS_PASSWORD),
        'last_check': LAST_CHECK_TIME,
        'session_active': SESSION_COOKIE is not None
    })

@app.route('/api/clear', methods=['POST'])
def clear_cache():
    """Clear OTP cache"""
    global otp_database, otp_cache
    otp_database = []
    otp_cache = set()
    return jsonify({'status': 'cleared', 'total_otps': 0})

@app.route('/api/refresh', methods=['POST'])
def refresh_session():
    """Force refresh session"""
    global SESSION_COOKIE
    SESSION_COOKIE = None
    success = login_ivasms()
    return jsonify({'status': 'refreshed', 'success': success})

# For local testing
if __name__ == '__main__':
    app.run(debug=True, port=5000)