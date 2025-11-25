from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from pymongo import MongoClient
from bson import ObjectId
import bcrypt
import os
import re
import jwt
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from email_validator import validate_email, EmailNotValidError
import math
import sys

load_dotenv()

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

app = Flask(__name__,
            template_folder=resource_path('templates'),
            static_folder=resource_path('static'))
# Set a fixed secret key for development - in production, use a secure environment variable
app.secret_key = 'Password'  # Using the string 'Password' as requested

# Email configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('EMAIL_USER')
app.config['MAIL_PASSWORD'] = os.getenv('EMAIL_PASSWORD')
mail = Mail(app)

# MongoDB setup - with error handling and retry logic
def get_db_connection():
    try:
        # Try to connect with default local MongoDB URI if environment variable is not set
        mongodb_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
        client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)  # 5 second timeout
        # Test the connection
        client.server_info()
        print("Successfully connected to MongoDB")
        return client
    except Exception as e:
        print(f"MongoDB Connection Error: {e}")
        return None

# Initialize MongoDB client and collections
client = get_db_connection()
if client:
    db = client['calculator_db']
    # Create collections if they don't exist
    if 'users' not in db.list_collection_names():
        users_collection = db.create_collection('users')
        users_collection.create_index('username', unique=True)
        users_collection.create_index('email', unique=True)
    else:
        users_collection = db['users']
        
    if 'calculations' not in db.list_collection_names():
        calculations_collection = db.create_collection('calculations')
        calculations_collection.create_index([('user_id', 1), ('timestamp', -1)])
        calculations_collection.create_index([('user_id', 1), ('result', 1)])
    else:
        calculations_collection = db['calculations']
        
    if 'otp' not in db.list_collection_names():
        otp_collection = db.create_collection('otp')
        otp_collection.create_index('created_at', expireAfterSeconds=300)  # OTP expires after 5 minutes
    else:
        otp_collection = db['otp']
else:
    print("Failed to establish MongoDB connection")
    users_collection = None
    calculations_collection = None
    otp_collection = None

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def validate_username(username):
    if not re.match("^[a-zA-Z0-9][a-zA-Z0-9_]{3,}$", username):
        return False
    return True

def validate_password(password):
    if len(password) < 6 or len(password) > 14:
        return False
    return True

def generate_otp():
    return str(random.randint(100000, 999999))

def send_verification_email(email, otp):
    try:
        msg = Message('Email Verification',
                     sender=app.config['MAIL_USERNAME'],
                     recipients=[email])
        msg.body = f'Your verification code is: {otp}'
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Email sending error: {e}")
        return False

class User(UserMixin):
    def __init__(self, user_data):
        self.user_data = user_data
        
    def get_id(self):
        return str(self.user_data['_id'])

@login_manager.user_loader
def load_user(user_id):
    try:
        if not client:
            return None
        user_data = users_collection.find_one({'_id': ObjectId(user_id)})
        return User(user_data) if user_data else None
    except Exception as e:
        print(f"User loading error: {e}")
        return None

@app.route('/')
def index():
    if current_user.is_authenticated:
        return render_template('calculator.html')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if not client:
            flash('Service temporarily unavailable. Please try again later.')
            return redirect(url_for('register'))
            
        try:
            username = request.form['username']
            password = request.form['password']
            email = request.form['email']
            
            # Validate username
            if not validate_username(username):
                flash('Username must start with a letter or number, be at least 4 characters long, and contain only letters, numbers, and underscores')
                return redirect(url_for('register'))
            
            # Validate password
            if not validate_password(password):
                flash('Password must be between 6 and 14 characters')
                return redirect(url_for('register'))
            
            # Validate email
            try:
                valid = validate_email(email)
                email = valid.email
            except EmailNotValidError:
                flash('Invalid email address')
                return redirect(url_for('register'))
            
            if users_collection.find_one({'username': username}):
                flash('Username already exists')
                return redirect(url_for('register'))
                
            if users_collection.find_one({'email': email}):
                flash('Email already registered')
                return redirect(url_for('register'))
            
            # Generate and store OTP
            otp = generate_otp()
            otp_collection.insert_one({
                'email': email,
                'otp': otp,
                'created_at': datetime.utcnow()
            })
            
            # Store user data temporarily
            session['temp_user'] = {
                'username': username,
                'password': password,
                'email': email
            }
            
            # Send verification email
            if not send_verification_email(email, otp):
                flash('Failed to send verification email. Please try again.')
                return redirect(url_for('register'))
            
            return redirect(url_for('verify_email'))
            
        except Exception as e:
            print(f"Registration error: {e}")
            flash('An error occurred during registration. Please try again.')
            return redirect(url_for('register'))
            
    return render_template('register.html')

@app.route('/verify-email', methods=['GET', 'POST'])
def verify_email():
    if not client:
        flash('Service temporarily unavailable. Please try again later.')
        return redirect(url_for('register'))
        
    if request.method == 'POST':
        try:
            otp = request.form['otp']
            temp_user = session.get('temp_user')
            
            if not temp_user:
                flash('Registration session expired')
                return redirect(url_for('register'))
            
            stored_otp = otp_collection.find_one({
                'email': temp_user['email'],
                'created_at': {'$gt': datetime.utcnow() - timedelta(minutes=10)}
            })
            
            if stored_otp and stored_otp['otp'] == otp:
                # Create verified user
                hashed_password = bcrypt.hashpw(temp_user['password'].encode('utf-8'), bcrypt.gensalt())
                users_collection.insert_one({
                    'username': temp_user['username'],
                    'password': hashed_password,
                    'email': temp_user['email'],
                    'verified': True
                })
                
                # Clean up
                otp_collection.delete_one({'_id': stored_otp['_id']})
                session.pop('temp_user', None)
                
                flash('Registration successful')
                return redirect(url_for('login'))
            
            flash('Invalid or expired OTP')
            return redirect(url_for('verify_email'))
            
        except Exception as e:
            print(f"Email verification error: {e}")
            flash('An error occurred during verification. Please try again.')
            return redirect(url_for('verify_email'))
            
    return render_template('verify_email.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if not client:
        flash('Service temporarily unavailable. Please try again later.')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        try:
            email = request.form['email']
            user = users_collection.find_one({'email': email})
            
            if user:
                otp = generate_otp()
                otp_collection.insert_one({
                    'email': email,
                    'otp': otp,
                    'created_at': datetime.utcnow(),
                    'for_password_reset': True
                })
                
                if not send_verification_email(email, otp):
                    flash('Failed to send reset code. Please try again.')
                    return redirect(url_for('forgot_password'))
                    
                session['reset_email'] = email
                return redirect(url_for('verify_reset_otp'))
                
            flash('Email not found')
            return redirect(url_for('forgot_password'))
            
        except Exception as e:
            print(f"Forgot password error: {e}")
            flash('An error occurred. Please try again.')
            return redirect(url_for('forgot_password'))
            
    return render_template('forgot_password.html')

@app.route('/verify-reset-otp', methods=['GET', 'POST'])
def verify_reset_otp():
    if not client:
        flash('Service temporarily unavailable. Please try again later.')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        try:
            otp = request.form['otp']
            email = session.get('reset_email')
            
            if not email:
                flash('Password reset session expired')
                return redirect(url_for('forgot_password'))
            
            stored_otp = otp_collection.find_one({
                'email': email,
                'otp': otp,
                'for_password_reset': True,
                'created_at': {'$gt': datetime.utcnow() - timedelta(minutes=10)}
            })
            
            if stored_otp:
                # Generate reset token
                reset_token = jwt.encode(
                    {'email': email, 'exp': datetime.utcnow() + timedelta(minutes=10)},
                    app.secret_key,
                    algorithm='HS256'
                )
                session['reset_token'] = reset_token
                
                # Clean up used OTP
                otp_collection.delete_one({'_id': stored_otp['_id']})
                
                return redirect(url_for('reset_password'))
                
            flash('Invalid or expired OTP')
            return redirect(url_for('verify_reset_otp'))
            
        except Exception as e:
            print(f"OTP verification error: {e}")
            flash('An error occurred. Please try again.')
            return redirect(url_for('verify_reset_otp'))
            
    return render_template('verify_reset_otp.html')

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if not client:
        flash('Service temporarily unavailable. Please try again later.')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        try:
            reset_token = session.get('reset_token')
            if not reset_token:
                flash('Password reset session expired')
                return redirect(url_for('forgot_password'))
                
            try:
                payload = jwt.decode(reset_token, app.secret_key, algorithms=['HS256'])
                email = payload['email']
                new_password = request.form['password']
                
                if not validate_password(new_password):
                    flash('Password must be between 6 and 14 characters')
                    return redirect(url_for('reset_password'))
                    
                hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
                users_collection.update_one(
                    {'email': email},
                    {'$set': {'password': hashed_password}}
                )
                
                # Clean up
                session.pop('reset_token', None)
                session.pop('reset_email', None)
                
                flash('Password reset successful')
                return redirect(url_for('login'))
                
            except jwt.ExpiredSignatureError:
                flash('Password reset link expired')
                return redirect(url_for('forgot_password'))
                
            except jwt.InvalidTokenError:
                flash('Invalid reset token')
                return redirect(url_for('forgot_password'))
                
        except Exception as e:
            print(f"Password reset error: {e}")
            flash('An error occurred. Please try again.')
            return redirect(url_for('reset_password'))
            
    return render_template('reset_password.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not client:
        flash('Service temporarily unavailable. Please try again later.')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        try:
            username = request.form['username']
            password = request.form['password']
            
            user_data = users_collection.find_one({'username': username})
            if user_data and bcrypt.checkpw(password.encode('utf-8'), user_data['password']):
                if not user_data.get('verified', False):
                    flash('Please verify your email before logging in')
                    return redirect(url_for('login'))
                    
                user = User(user_data)
                login_user(user)
                return redirect(url_for('index'))
                
            flash('Invalid username or password')
            
        except Exception as e:
            print(f"Login error: {e}")
            flash('An error occurred during login. Please try again.')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    try:
        logout_user()
    except Exception as e:
        print(f"Logout error: {e}")
    return redirect(url_for('login'))

@app.route('/calculate', methods=['POST'])
@login_required
def calculate():
    if not client:
        return jsonify({'success': False, 'error': 'Service temporarily unavailable'})
        
    try:
        data = request.get_json()
        expression = data.get('expression', '').strip()
        name = data.get('name', '').strip()
        result = data.get('result', '').strip()
        
        if not expression or not result:
            return jsonify({'success': False, 'error': 'Invalid calculation data'})
            
        try:
            # Convert result to float
            result = float(result)
            
            # Save to database
            calc = {
                'user_id': ObjectId(current_user.get_id()),
                'name': name or f"Calculation {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                'expression': expression,
                'result': result,
                'timestamp': datetime.now()
            }
            
            # Insert the calculation
            result = calculations_collection.insert_one(calc)
            
            if not result.inserted_id:
                return jsonify({'success': False, 'error': 'Failed to save calculation'})
            
            return jsonify({
                'success': True,
                'calculation': {
                    '_id': str(calc['_id']),
                    'name': calc['name'],
                    'expression': calc['expression'],
                    'result': calc['result'],
                    'timestamp': calc['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
                }
            })
            
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid result format'})
            
    except Exception as e:
        print(f"Error in calculate: {e}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/history')
@login_required
def history():
    if not client:
        print("MongoDB client is not available")
        flash('Service temporarily unavailable. Please try again later.')
        return redirect(url_for('index'))
        
    try:
        sort = request.args.get('sort', 'date-desc')
        
        # Get all calculations from the database
        calculations = get_sorted_calculations(sort)
        
        # If it's an AJAX request, return only the history items
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('history_items.html', calculations=calculations)
        
        # Otherwise return the full page
        return render_template('history.html', calculations=calculations)
    except Exception as e:
        print(f"History error: {e}")
        flash('Error loading calculation history')
        return redirect(url_for('index'))

@app.route('/delete-calculation/<calc_id>', methods=['DELETE'])
@login_required
def delete_calculation(calc_id):
    if not client:
        return jsonify({'success': False, 'error': 'Service temporarily unavailable'})
        
    try:
        # Delete the calculation from MongoDB
        result = calculations_collection.delete_one({
            '_id': ObjectId(calc_id),
            'user_id': ObjectId(current_user.get_id())
        })
        
        if result.deleted_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Calculation not found'})
    except Exception as e:
        print(f"Delete calculation error: {e}")
        return jsonify({'success': False, 'error': str(e)})

def get_sorted_calculations(sort_by):
    if not client:
        return []
        
    try:
        # Define the sort order based on the parameter
        if sort_by == 'date-asc':
            sort_order = [('timestamp', 1)]
        elif sort_by == 'result-desc':
            sort_order = [('result', -1)]
        elif sort_by == 'result-asc':
            sort_order = [('result', 1)]
        else:  # date-desc (default)
            sort_order = [('timestamp', -1)]
        
        # Get calculations for the current user
        calculations = list(calculations_collection.find(
            {'user_id': ObjectId(current_user.get_id())}
        ).sort(sort_order))
        
        # Clean up expressions for display
        for calc in calculations:
            if 'expression' in calc:
                # Remove any float() wrappers and clean up the expression
                calc['expression'] = (calc['expression']
                    .replace('float(', '')
                    .replace('int(', '')
                    .replace('str(', '')
                    .replace(')', '')
                    .replace('×', '*')
                    .replace('÷', '/')
                    .strip())
            
            # Ensure result is properly formatted
            if 'result' in calc and isinstance(calc['result'], (int, float)):
                if abs(calc['result']) >= 1e15 or (abs(calc['result']) < 1e-7 and calc['result'] != 0):
                    calc['result'] = format(calc['result'], '.10e')
                else:
                    calc['result'] = format(calc['result'], '.10f').rstrip('0').rstrip('.')
        
        return calculations
    except Exception as e:
        print(f"Error getting sorted calculations: {e}")
        return []

if __name__ == '__main__':
    if not client:
        print("Warning: MongoDB is not connected. Application may not function correctly.")
    app.run(host='0.0.0.0', port=5000, debug=True) 