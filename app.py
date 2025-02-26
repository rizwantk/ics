from flask import Flask, render_template, request, send_file, redirect, url_for, flash
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import csv
import logging
import io
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import re
import numpy as np
from openpyxl import Workbook
from io import BytesIO
import psycopg2
from psycopg2 import pool
from werkzeug.security import generate_password_hash, check_password_hash
import random
import string
from flask_mail import Mail, Message
from twilio.rest import Client

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Required for session management

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # Route to redirect unauthorized users

# Configure PostgreSQL connection pool
def create_connection_pool():
    try:
        return psycopg2.pool.SimpleConnectionPool(
            minconn=5,  # Minimum connections
            maxconn=20,  # Maximum connections
            host="databasetender.cryym0kc27zo.eu-north-1.rds.amazonaws.com",
            database="tender",
            user="postgres",
            password="db_password123###",
            port=5432
        )
    except OperationalError as e:
        logger.error(f"Failed to create connection pool: {e}")
        raise

postgresql_pool = create_connection_pool()

# Initialize Twilio client
TWILIO_ACCOUNT_SID = "AC66e729a523d8f4e7a66ed737c70792a5"
TWILIO_AUTH_TOKEN = 'dd50549a00b1c803d902ca1f69fb5e39'
TWILIO_PHONE_NUMBER = '+16196373882'
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

def init_db():
    """Initialize database tables"""
    conn = postgresql_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    mobile VARCHAR(15) UNIQUE NOT NULL,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    password VARCHAR(200) NOT NULL,
                    registered_on TIMESTAMP NOT NULL,
                    last_login TIMESTAMP
                );
            """)
            conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Error initializing database: {e}")
    finally:
        postgresql_pool.putconn(conn)

# Initialize database on startup
init_db()
# Mock user database
class User(UserMixin):
    def __init__(self, id, name, mobile, email, password, registered_on, last_login):
        self.id = id
        self.name = name
        self.mobile = mobile
        self.email = email
        self.password = password
        self.registered_on = registered_on
        self.last_login = last_login
    @staticmethod
    def get(user_id):
        """Get user from database by ID"""
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                user_data = cur.fetchone()
                if user_data:
                    return User(*user_data)
                return None
        except OperationalError as e:
            logger.error(f"Error fetching user by ID: {e}")
            return None
        finally:
            release_db_connection(conn)

    @staticmethod
    def find_by_email(email):
        """Find user by email"""
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE email = %s", (email,))
                user_data = cur.fetchone()
                if user_data:
                    return User(*user_data)
                return None
        except OperationalError as e:
            logger.error(f"Error fetching user by email: {e}")
            return None
        finally:
            release_db_connection(conn)

    @staticmethod
    def find_by_mobile(mobile):
        """Find user by mobile number"""
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE mobile = %s", (mobile,))
                user_data = cur.fetchone()
                if user_data:
                    return User(*user_data)
                return None
        except OperationalError as e:
            logger.error(f"Error fetching user by mobile: {e}")
            return None
        finally:
            release_db_connection(conn)

# User loader callback
@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your_email@gmail.com'
app.config['MAIL_PASSWORD'] = 'your_email_password'
mail = Mail(app)

# Temporary storage for OTPs and reset tokens
reset_tokens = {}

# Function to generate a random OTP
def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

# Function to send OTP via email
def send_otp_email(email, otp):
    try:
        msg = Message(
            'Password Reset OTP',
            sender='your_email@gmail.com',
            recipients=[email]
        )
        msg.body = f'Your OTP for password reset is: {otp}'
        mail.send(msg)
        logger.info(f"OTP sent to {email}: {otp}")
    except Exception as e:
        logger.error(f"Error sending OTP email: {e}")

# Function to send OTP via SMS
def send_otp_sms(mobile, otp):
    try:
        message = twilio_client.messages.create(
            body=f'Your OTP for password reset is: {otp}',
            from_=TWILIO_PHONE_NUMBER,
            to=mobile
        )
        logger.info(f"OTP sent to {mobile}: {otp}")
    except Exception as e:
        logger.error(f"Error sending OTP SMS: {e}")

# Database connection utility
def get_db_connection():
    try:
        conn = postgresql_pool.getconn()
        return conn
    except OperationalError as e:
        logger.error(f"Failed to get database connection: {e}")
        raise

def release_db_connection(conn):
    try:
        postgresql_pool.putconn(conn)
    except OperationalError as e:
        logger.error(f"Failed to release database connection: {e}")
        raise

# Route to initiate password reset
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email_or_mobile = request.form.get('email_or_mobile')
        user = User.find_by_email(email_or_mobile) if '@' in email_or_mobile else User.find_by_mobile(email_or_mobile)
        
        if user:
            otp = generate_otp()
            reset_tokens[user.id] = {'otp': otp, 'attempts': 0}
            
            if '@' in email_or_mobile:
                send_otp_email(user.email, otp)
                flash('OTP sent to your email.', 'info')
            else:
                send_otp_sms(user.mobile, otp)
                flash('OTP sent to your mobile.', 'info')
            
            return redirect(url_for('verify_otp', user_id=user.id))
        else:
            flash('User not found.', 'danger')
    
    return render_template('forgot_password.html')

# Route to verify OTP
@app.route('/verify-otp/<int:user_id>', methods=['GET', 'POST'])
def verify_otp(user_id):
    if request.method == 'POST':
        otp = request.form.get('otp')
        user_data = reset_tokens.get(user_id)
        
        if user_data and user_data['otp'] == otp:
            reset_tokens[user_id]['verified'] = True
            flash('OTP verified. Please reset your password.', 'success')
            return redirect(url_for('reset_password', user_id=user_id))
        else:
            user_data['attempts'] += 1
            if user_data['attempts'] >= 3:
                del reset_tokens[user_id]
                flash('Too many failed attempts. Please try again.', 'danger')
                return redirect(url_for('forgot_password'))
            flash('Invalid OTP. Please try again.', 'danger')
    
    return render_template('verify_otp.html', user_id=user_id)

# Route to reset password
@app.route('/reset-password/<int:user_id>', methods=['GET', 'POST'])
def reset_password(user_id):
    user_data = reset_tokens.get(user_id)
    if not user_data or not user_data.get('verified'):
        flash('Invalid request.', 'danger')
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('reset_password', user_id=user_id))
        
        # Check if the new password is the same as the old password
        user = User.get(user_id)
        if check_password_hash(user.password, new_password):
            flash('New password cannot be the same as the old password.', 'danger')
            return redirect(url_for('reset_password', user_id=user_id))
        
        # Update the password in the database
        conn = postgresql_pool.getconn()
        try:
            with conn.cursor() as cur:
                hashed_password = generate_password_hash(new_password)
                cur.execute("""
                    UPDATE users SET password = %s WHERE id = %s
                """, (hashed_password, user_id))
                conn.commit()
                flash('Password reset successfully. Please login with your new password.', 'success')
                del reset_tokens[user_id]
                return redirect(url_for('login'))
        except Exception as e:
            conn.rollback()
            logging.error(f"Error resetting password: {e}")
            flash('Error resetting password. Please try again.', 'danger')
        finally:
            postgresql_pool.putconn(conn)
    
    return render_template('reset_password.html', user_id=user_id)
# Function to simplify competition activity by taking the first word
def simplify_competition_activity(activity):
    if activity:
        return activity.split()[0]  # Extract the first word
    return activity  # Return the activity as is if empty or None

# Function to load tenders from CSV
def load_tenders():
    tenders = []
    try:
        with open('newBook6.csv', 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                try:
                    deadline_str = row.get('Deadline for submission of bids', '').strip()

                    # Process deadline as before
                    if deadline_str:
                        deadline_parts = deadline_str.split()
                        if len(deadline_parts) == 2:
                            date_str, time_str = deadline_parts
                            deadline = datetime.strptime(f"{date_str} {time_str}", '%d-%m-%Y %H:%M')
                        elif len(deadline_parts) == 1:
                            date_str = deadline_parts[0]
                            deadline = datetime.strptime(f"{date_str} 00:00", '%d-%m-%Y %H:%M')
                        else:
                            raise ValueError("Invalid deadline format")
                    else:
                        deadline = None

                    # Simplify the competition activity field to the first word
                    scope = row.get('Competition activity', 'N/A')
                    simplified_scope = simplify_competition_activity(scope)

                    tenders.append({
                        'rfp': row.get('Reference Number', 'N/A'),
                        'scope': scope,  # Keep full scope here
                        'simplified_scope': simplified_scope,
                        'tender_cost': row.get('Value of tender documents', 'N/A'),
                        'Deadlines': row.get('Deadline for submission of bids', 'N/A'),
                        'client': row.get('Government agency', 'N/A'),
                        'issue_tender_date': row.get('Start sending questions and inquiries', 'N/A'),
                        'contract_period': row.get('Contract duration', 'N/A'),
                        'participating_firms': row.get('Bidders and Value', 'N/A'),
                        'awarded_date': row.get('Expected award date', 'N/A'),
                        'awarded_to': row.get('Awarded Supplier Name', 'N/A'),
                        'country': row.get('country', 'N/A'),
                        'visitor_details_url': row.get('Visitor Details URL', 'N/A'),
                    })
                except (ValueError, IndexError) as e:
                    logging.warning(f"Invalid deadline format for RFP {row.get('Reference Number')}: {e}")
                    deadline = None

    except Exception as e:
        logging.error(f"Error loading tenders: {e}")
    return tenders

def get_distinct_activities(tenders):
    unique_activities = set()

    for tender in tenders:
        scope = tender.get('scope', '').strip() if tender.get('scope') else ''
        if not scope:
            continue

        # Clean and split into words
        clean_scope = scope.replace(',', '').replace('.', '')
        words = [word.strip() for word in clean_scope.split() if word.strip()]

        if not words:
            continue

        # Process first word
        first_word = words[0].lower()
        activity_words = []

        if len(first_word) <= 3 and len(words) >= 2:
            # Get first two words if first is short
            activity_words = words[:2]
        else:
            # Get only first word
            activity_words = words[:1]

        # Format with proper capitalization
        formatted_activity = ' '.join(
            [word.capitalize() for word in activity_words]
        )

        unique_activities.add(formatted_activity)

    # Return sorted list of unique activities
    return [{'competition_activity': activity}
            for activity in sorted(unique_activities)]

def convert_contract_period_to_days(contract_period):
    try:
        contract_period = str(contract_period).lower().strip()
        if not contract_period:
            return 0

        # Extract numbers and units
        num = int(''.join(filter(str.isdigit, contract_period)))
        if "month" in contract_period:
            return num * 30
        elif "year" in contract_period:
            return num * 365
        elif "week" in contract_period:
            return num * 7
        elif "day" in contract_period:
            return num
        return 0
    except:
        return 0

# Function to convert contract period into days or months
def filter_by_contract_duration(tenders, contract_filter):
    filtered_tenders = []
    for tender in tenders:
        contract_period = tender.get('contract_period', '').lower()
        days = convert_contract_period_to_days(contract_period)

        # Map filter values to day thresholds
        filter_map = {
            "less_than_3_months": (None, 90),
            "less_than_6_months": (None, 180),
            "less_than_1_year": (None, 365),
            "less_than_2_years": (None, 730),
            "above_2_years": (730, None)
        }

        min_days, max_days = filter_map.get(contract_filter, (None, None))
        match = False

        # Handle different filter types
        if min_days is not None and max_days is not None:
            match = min_days <= days <= max_days
        elif max_days is not None:
            match = days <= max_days
        elif min_days is not None:
            match = days >= min_days

        if match:
            filtered_tenders.append(tender)

    return filtered_tenders
# Function to calculate potential next RFP date
def calculate_potential_next_rfp(deadline, contract_period):
    try:
        if deadline is None or not contract_period.strip():
            return None
        contract_period = contract_period.lower()
        if 'month' in contract_period:
            months = int(contract_period.split()[0])
            return deadline + relativedelta(months=months)
        elif 'year' in contract_period:
            years = int(contract_period.split()[0])
            return deadline + relativedelta(years=years)
        else:
            days = convert_contract_period_to_days(contract_period)
            return deadline + timedelta(days=days)
    except Exception as e:
        logging.error(f"Error calculating potential next RFP: {e}")
        return None

def safe_float(value):
    """Convert tender_cost to float, handling non-numeric values."""
    try:
        # Extract numeric value from strings like "500 SAR"
        numeric_str = ''.join(filter(lambda c: c.isdigit() or c == '.', str(value)))
        return float(numeric_str)
    except ValueError:
        return 0.0 # Default to 0 if conversion fails

def classify_tenders():
    tenders = load_tenders()
    current_time = datetime.now()

    open_tenders = []
    closed_tenders = []

    for tender in tenders:
        deadline_str = tender.get('Deadlines', '')
        try:
            deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
        except ValueError:
            logging.warning(
                f"Invalid deadline '{deadline_str}' for RFP {tender.get('rfp', 'Unknown')}"
            )

        if deadline:
            if deadline > current_time:
                time_left = deadline - current_time
                tender['time_left'] = f"{time_left.days} days"
                tender['time_left_days'] = time_left.days  # Store time left in days for filtering
                open_tenders.append(tender)
            else:
                closed_tenders.append(tender)
        else:
            tender['time_left'] = "N/A"
            tender['time_left_days'] = 0  # Default to 0 for tenders without a deadline
            open_tenders.append(tender)

    return open_tenders, closed_tenders

@app.route('/open-rfps')
@login_required
def open_rfps():
    # Load all tenders without any filters
    all_tenders = load_tenders()

    # Calculate total_open_count and total_closed_count BEFORE applying filters
    current_time = datetime.now()
    total_open_count = 0
    total_closed_count = 0

    for tender in all_tenders:
        deadline_str = tender.get('Deadlines', '')
        if deadline_str and deadline_str.strip().lower() != 'n/a':
            try:
                deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
                if deadline > current_time:
                    total_open_count += 1
                else:
                    total_closed_count += 1
            except ValueError:
                pass

    # Now load tenders for filtering
    tenders = load_tenders()
    distinct_activities = get_distinct_activities(tenders)

    # Extract filters
    selected_countries = request.args.getlist('country')
    selected_activities = [a.lower() for a in request.args.getlist('competition_activity') if a]
    selected_contract_duration = request.args.getlist('contract_duration')
    time_left_filter = request.args.get('time_left', type=int, default=None)
    open_search_query = request.args.get('open_search', '').strip().lower()

    # Extract sorting parameters
    sort_field = request.args.get('sort', default='Deadlines')  # Default to Deadlines
    sort_order = request.args.get('order', default='asc')  # Default ascending

    # Validate sort field
    valid_sort_fields = ['Deadlines', 'tender_cost', 'issue_date', 'free_first']
    if sort_field not in valid_sort_fields:
        sort_field = 'Deadlines'  # Fallback to default

    # Apply country filter
    if selected_countries:
        tenders = [tender for tender in tenders if tender.get('country') in selected_countries]

    # Apply contract duration filter
    if selected_contract_duration:
        filtered_tenders = []
        for duration_filter in selected_contract_duration:
            filtered = filter_by_contract_duration(tenders, duration_filter)
            filtered_tenders.extend(filtered)
        # Remove duplicates
        tenders = list({t['rfp']: t for t in filtered_tenders}.values())

    current_time = datetime.now()
    open_tenders = []
    closed_tenders = []

    # Filter tenders based on selected activities
    if selected_activities:
        tenders = [
            tender for tender in tenders
            if any(activity in (tender.get('scope') or '').lower()
                   for activity in selected_activities)
        ]

    for tender in tenders:
        deadline_str = tender.get('Deadlines', '')
        deadline = None
        if deadline_str and deadline_str.strip().lower() != 'n/a':
            try:
                deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
            except ValueError:
                # Log a warning only if the RFP ID is available
                rfp_id = tender.get('rfp', 'Unknown')
                logging.warning(f"Invalid deadline format for RFP {rfp_id}")
        else:
            # Skip tenders with invalid or missing deadlines
            continue

        if deadline:
            time_left = (deadline - current_time).days
            tender['time_left'] = f"{time_left} days"
            tender['time_left_days'] = time_left

            if time_left_filter is None or time_left <= time_left_filter:
                if deadline > current_time:
                    open_tenders.append(tender)
                else:
                    potential_next_rfp_date = calculate_potential_next_rfp(deadline, tender.get('contract_period', ''))
                    tender['potential_next_rfp'] = potential_next_rfp_date.strftime('%d/%m/%Y') if potential_next_rfp_date else "N/A"
                    closed_tenders.append(tender)
        else:
            tender['time_left'] = "N/A"
            tender['time_left_days'] = 0
            open_tenders.append(tender)

    # Apply Open RFP search filter
    if open_search_query:
        open_tenders = [
            t for t in open_tenders if
            any(open_search_query in str(value).lower() for value in t.values())
        ]

    # Sorting logic
    if sort_field and sort_order:
        reverse_order = sort_order == 'desc'
        try:
            if sort_field == 'Deadlines':
                # Custom deadline sorting
                def get_deadline(tender):
                    deadline_str = tender.get('Deadlines', '')
                    try:
                        return datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
                    except ValueError:
                        return datetime.min  # Handle invalid dates
                open_tenders.sort(key=get_deadline, reverse=reverse_order)
            elif sort_field == 'tender_cost':
                # Numeric sorting for tender cost
                open_tenders.sort(key=lambda x: safe_float(x.get('tender_cost', 0)), reverse=reverse_order)
            elif sort_field == 'issue_date':
                # Sorting by tender issue date
                def get_issue_date(tender):
                    issue_date_str = tender.get('issue_tender_date', '')
                    try:
                        return datetime.strptime(issue_date_str, '%d-%m-%Y')
                    except ValueError:
                        return datetime.min  # Handle invalid dates
                open_tenders.sort(key=get_issue_date, reverse=reverse_order)
            elif sort_field == 'free_first':
                # Custom sorting for free tenders first
                open_tenders.sort(key=lambda x: (x.get('tender_cost', '0') == '0'), reverse=True)
        except Exception as e:
            logging.error(f"Error sorting tenders: {e}")

    # Pagination logic
    page = request.args.get('page', 1, type=int)
    per_page = 30
    open_total_pages = (len(open_tenders) + per_page - 1) // per_page
    open_tenders_page = open_tenders[(page - 1) * per_page: page * per_page]

    # RFP counts & values
    filtered_open_count = len(open_tenders)  # Count after filters
    open_rfp_value = sum(safe_float(tender['tender_cost']) for tender in open_tenders)
    flash(f"Found {filtered_open_count} results matching your criteria", 'info')

    return render_template(
        'open_rfps.html',
        open_tenders=open_tenders_page,
        current_page=page,
        open_total_pages=open_total_pages,
        distinct_activities=distinct_activities,
        selected_countries=selected_countries,
        selected_activities=selected_activities,
        selected_contract_duration=selected_contract_duration,
        time_left_filter=time_left_filter,
        tenders=tenders,
        open_search_query=open_search_query,
        open_rfp_value=open_rfp_value,
        total_open_count=total_open_count,  # Use pre-calculated total_open_count
        filtered_open_count=filtered_open_count,  # Pass filtered count
        sort_field=sort_field,  # Pass sort field to template
        sort_order=sort_order   # Pass sort order to template
    )

@app.route('/closed-rfps')
@login_required
def closed_rfps():
    all_tenders = load_tenders()

    # Calculate total_open_count and total_closed_count BEFORE applying filters
    current_time = datetime.now()
    total_open_count = 0
    total_closed_count = 0

    for tender in all_tenders:
        deadline_str = tender.get('Deadlines', '')
        if deadline_str and deadline_str.strip().lower() != 'n/a':
            try:
                deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
                if deadline > current_time:
                    total_open_count += 1
                else:
                    total_closed_count += 1
            except ValueError:
                pass
    tenders = load_tenders()
    distinct_activities = get_distinct_activities(tenders)

    # Extract filters
    selected_countries = request.args.getlist('country')
    selected_activities = [a.lower() for a in request.args.getlist('competition_activity') if a]
    selected_contract_duration = request.args.getlist('contract_duration')
    closed_search_query = request.args.get('closed_search', '').strip().lower()
    selected_awarded_range = request.args.get('awarded_date_range', 'all')

    sort_field = request.args.get('sort', default='Deadlines')  # Default to Deadlines
    sort_order = request.args.get('order', default='asc')  # Default ascending

    # Validate sort field
    valid_sort_fields = ['Deadlines', 'tender_cost', 'issue_date', 'free_first']
    if sort_field not in valid_sort_fields:
        sort_field = 'Deadlines'  # Fallback to default

    # Calculate date ranges
    today = datetime.today()
    date_filters = {
        '3_months': today - relativedelta(months=3),
        '6_months': today - relativedelta(months=6),
        '1_year': today - relativedelta(years=1),
        '2_years': today - relativedelta(years=2)
    }

    # Apply country filter
    if selected_countries:
        tenders = [tender for tender in tenders if tender.get('country') in selected_countries]

    # Apply contract duration filter
    if selected_contract_duration:
        filtered_tenders = []
        for duration_filter in selected_contract_duration:
            filtered = filter_by_contract_duration(tenders, duration_filter)
            filtered_tenders.extend(filtered)
        # Remove duplicates
        tenders = list({t['rfp']: t for t in filtered_tenders}.values())

    # Apply activity filter
    if selected_activities:
        tenders = [
            tender for tender in tenders
            if any(activity in (tender.get('scope') or '').lower()
                   for activity in selected_activities)
        ]

    # Separate open/closed tenders
    current_time = datetime.now()
    open_tenders = []
    closed_tenders = []

    for tender in tenders:
        deadline_str = tender.get('Deadlines', '')
        deadline = None
        if deadline_str and deadline_str.strip().lower() != 'n/a':
            try:
                deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
            except ValueError:
                # Log a warning only if the RFP ID is available
                rfp_id = tender.get('rfp', 'Unknown')
                logging.warning(f"Invalid deadline format for RFP {rfp_id}")
        else:
            # Skip tenders with invalid or missing deadlines
            continue

        # Process awarded date
        awarded_date_str = tender.get('awarded_date', '')
        if awarded_date_str:
            try:
                awarded_date = datetime.strptime(awarded_date_str, '%d-%m-%Y')
            except ValueError:
                awarded_date = None
        else:
            awarded_date = None
        tender['awarded_date_dt'] = awarded_date  # Store parsed date for filtering

        # Categorize tenders
        if deadline and deadline > current_time:
            open_tenders.append(tender)
        else:
            potential_next_rfp_date = calculate_potential_next_rfp(deadline, tender.get('contract_period', ''))
            tender['potential_next_rfp'] = potential_next_rfp_date.strftime('%d/%m/%Y') if potential_next_rfp_date else "N/A"
            closed_tenders.append(tender)

    # Apply awarded date filter to CLOSED tenders
    if selected_awarded_range in date_filters:
        start_date = date_filters[selected_awarded_range]
        closed_tenders = [
            t for t in closed_tenders
            if t['awarded_date_dt'] and t['awarded_date_dt'] >= start_date
        ]

    # Apply search filter
    if closed_search_query:
        closed_tenders = [
            t for t in closed_tenders
            if any(closed_search_query in str(value).lower() for value in t.values())
        ]

    if sort_field and sort_order:
        reverse_order = sort_order == 'desc'
        try:
            if sort_field == 'Deadlines':
                # Custom deadline sorting
                def get_deadline(tender):
                    deadline_str = tender.get('Deadlines', '')
                    try:
                        return datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
                    except ValueError:
                        return datetime.min  # Handle invalid dates
                closed_tenders.sort(key=get_deadline, reverse=reverse_order)
            elif sort_field == 'tender_cost':
                # Numeric sorting for tender cost
                closed_tenders.sort(key=lambda x: safe_float(x.get('tender_cost', 0)), reverse=reverse_order)
            elif sort_field == 'issue_date':
                # Sorting by tender issue date
                def get_issue_date(tender):
                    issue_date_str = tender.get('issue_tender_date', '')
                    try:
                        return datetime.strptime(issue_date_str, '%d-%m-%Y')
                    except ValueError:
                        return datetime.min  # Handle invalid dates
                closed_tenders.sort(key=get_issue_date, reverse=reverse_order)
            elif sort_field == 'free_first':
                # Custom sorting for free tenders first
                closed_tenders.sort(key=lambda x: (x.get('tender_cost', '0') == '0'), reverse=True)
        except Exception as e:
            logging.error(f"Error sorting tenders: {e}")

    # Pagination logic
    page = request.args.get('page', 1, type=int)
    per_page = 30
    closed_total_pages = (len(closed_tenders) + per_page - 1) // per_page
    closed_tenders_page = closed_tenders[(page - 1) * per_page: page * per_page]

    # Counts & values
    filtered_closed_count = len(closed_tenders)  # Count after filters
    closed_rfp_value = sum(safe_float(tender['tender_cost']) for tender in closed_tenders if 'tender_cost' in tender)
    flash(f"Found {filtered_closed_count} results matching your criteria", 'info')
    return render_template(
        'closed_rfps.html',
        closed_tenders=closed_tenders_page,
        current_page=page,
        closed_total_pages=closed_total_pages,
        distinct_activities=distinct_activities,
        selected_countries=selected_countries,
        selected_activities=selected_activities,
        selected_contract_duration=selected_contract_duration,
        closed_search_query=closed_search_query,
        closed_rfp_value=closed_rfp_value,
        selected_awarded_range=selected_awarded_range,
        total_closed_count=total_closed_count,  # Use pre-calculated total_open_count
        filtered_closed_count=filtered_closed_count,  # Pass filtered count
        sort_field=sort_field,  # Pass sort field to template
        sort_order=sort_order   # Pass sort order to template
    )

@app.template_filter('millify')
def millify(n):
    millnames = ['',' Thousand',' Million',' Billion']
    n = float(n)
    millidx = max(0,min(len(millnames)-1,
                        int(np.floor(0 if n == 0 else np.log10(abs(n))/3))))
    return '{:.1f}{}'.format(n / 10**(3 * millidx), millnames[millidx])

# Registration route
@app.route('/register', methods=['GET', 'POST'])  # Allow both GET and POST
def register():
    if request.method == 'POST':
        # Handle registration form submission
        name = request.form.get('name')
        mobile = request.form.get('mobile')
        email = request.form.get('email').lower()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # Validate input
        if not all([name, mobile, email, password, confirm_password]):
            flash('All fields are required', 'danger')
            return redirect(url_for('register'))

        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('register'))

        conn = postgresql_pool.getconn()
        try:
            with conn.cursor() as cur:
                # Check if email or mobile already exists
                cur.execute("SELECT id FROM users WHERE email = %s OR mobile = %s", (email, mobile))
                if cur.fetchone():
                    flash('Email or mobile number already registered', 'danger')
                    return redirect(url_for('register'))

                # Hash password
                hashed_password = generate_password_hash(password)

                # Insert new user
                cur.execute("""
                    INSERT INTO users (name, mobile, email, password, registered_on)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (name, mobile, email, hashed_password, datetime.now()))

                user_id = cur.fetchone()[0]
                conn.commit()

                # Log in the new user
                user = User.get(user_id)
                login_user(user)
                flash('Registration successful!', 'success')
                return redirect(url_for('landing'))

        except Exception as e:
            conn.rollback()
            logging.error(f"Registration error: {e}")
            flash('Registration failed. Please try again.', 'danger')
            return redirect(url_for('register'))
        finally:
            postgresql_pool.putconn(conn)

    # If it's a GET request, render the registration form
    return render_template('register.html')  # Make sure you have a register.html template


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        name = request.form.get('name')
        mobile = request.form.get('mobile')
        email = request.form.get('email').lower()
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        conn = postgresql_pool.getconn()
        try:
            with conn.cursor() as cur:
                # Check email uniqueness
                cur.execute("""
                    SELECT id FROM users 
                    WHERE email = %s AND id != %s
                """, (email, current_user.id))
                if cur.fetchone():
                    flash('Email already in use', 'danger')
                    return redirect(url_for('profile'))

                # Check mobile uniqueness
                cur.execute("""
                    SELECT id FROM users 
                    WHERE mobile = %s AND id != %s
                """, (mobile, current_user.id))
                if cur.fetchone():
                    flash('Mobile number already in use', 'danger')
                    return redirect(url_for('profile'))

                # Update user details
                update_fields = {
                    'name': name,
                    'mobile': mobile,
                    'email': email
                }

                # Handle password change
                if new_password:
                    if new_password != confirm_password:
                        flash('Passwords do not match', 'danger')
                        return redirect(url_for('profile'))
                    update_fields['password'] = generate_password_hash(new_password)

                set_clause = ", ".join([f"{k} = %s" for k in update_fields.keys()])
                values = list(update_fields.values()) + [current_user.id]

                cur.execute(f"""
                    UPDATE users 
                    SET {set_clause}
                    WHERE id = %s
                """, values)
                
                conn.commit()
                flash('Profile updated successfully', 'success')
                return redirect(url_for('profile'))

        except Exception as e:
            conn.rollback()
            logging.error(f"Profile update error: {e}")
            flash('Error updating profile', 'danger')
            return redirect(url_for('profile'))
        finally:
            postgresql_pool.putconn(conn)

    return render_template('profile.html', user=current_user)

# Login Route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email').lower()
        password = request.form.get('password')

        user = User.find_by_email(email)
        if user and check_password_hash(user.password, password):
            # Update last login
            conn = postgresql_pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE users SET last_login = %s 
                        WHERE id = %s
                    """, (datetime.now(), user.id))
                    conn.commit()
            finally:
                postgresql_pool.putconn(conn)
            
            login_user(user)
            flash('Login successful', 'success')
            return redirect(url_for('landing'))
        
        flash('Invalid email or password', 'danger')
    return render_template('login.html')

# Logout Route
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'success')
    return redirect(url_for('login'))

# Index route (protected)
@app.route("/", methods=["GET", "POST"])
@login_required
def landing():
    tenders = load_tenders()
    distinct_activities = get_distinct_activities(tenders)

    # Extract filters
    selected_activity = request.args.get('competition_activity', 'All')
    selected_contract_duration = request.args.getlist('contract_duration')
    time_left_filter = request.args.get('time_left')

    # Separate search queries for Open and Closed RFPs
    open_search_query = request.args.get('open_search', '').strip().lower()
    closed_search_query = request.args.get('closed_search', '').strip().lower()

    # Calculate success rate
    successful_tenders = [t for t in tenders if t.get('awarded_to')]
    total_tenders = len(tenders)
    success_rate = round((len(successful_tenders) / total_tenders * 100) if total_tenders > 0 else 0)
    failure_rate = 100 - success_rate

    # Calculate tender process efficiency
    process_efficiency = []
    for tender in successful_tenders:
        issue_date_str = tender.get('issue_tender_date', '')
        awarded_date_str = tender.get('awarded_date', '')
        if issue_date_str and awarded_date_str:
            try:
                issue_date = datetime.strptime(issue_date_str, '%d-%m-%Y')
                awarded_date = datetime.strptime(awarded_date_str, '%d-%m-%Y')
                days_taken = (awarded_date - issue_date).days
                process_efficiency.append(days_taken)
            except ValueError:
                continue

    # Calculate average process efficiency (in days)
    avg_process_efficiency = round(sum(process_efficiency) / len(process_efficiency)) if process_efficiency else 0

    # Calculate average tender value
    tender_values = [safe_float(t['tender_cost']) for t in tenders if 'tender_cost' in t]
    avg_tender_value = sum(tender_values) / len(tender_values) if tender_values else 0

    # Calculate repeat clients
    clients = [t['client'] for t in tenders]
    repeat_clients = len([c for c in set(clients) if clients.count(c) > 1])

    current_time = datetime.now()
    open_tenders = []
    closed_tenders = []

    # Convert selected activity to lowercase for case-insensitive matching
    selected_activity = request.args.get('competition_activity', 'All').strip().lower()

    # Apply filtering only if the user selects an activity other than "All"
    if selected_activity and selected_activity != "all":
        tenders = [
            tender for tender in tenders
            if selected_activity in tender.get('scope', '').strip().lower()  # Partial match
        ]

    for tender in tenders:
        deadline_str = tender.get('Deadlines', '')
        deadline = None
        if deadline_str and deadline_str.strip().lower() != 'n/a':
            try:
                deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
            except ValueError:
                # Log a warning only if the RFP ID is available
                rfp_id = tender.get('rfp', 'Unknown')
                logging.warning(f"Invalid deadline format for RFP {rfp_id}")
        else:
            # Skip tenders with invalid or missing deadlines
            continue

        if deadline:
            if deadline > current_time:
                time_left = deadline - current_time
                tender['time_left'] = f"{time_left.days} days"
                tender['time_left_days'] = time_left.days
                open_tenders.append(tender)
            else:
                potential_next_rfp_date = calculate_potential_next_rfp(deadline, tender.get('contract_period', ''))
                tender['potential_next_rfp'] = potential_next_rfp_date.strftime('%d/%m/%Y') if potential_next_rfp_date else "N/A"
                closed_tenders.append(tender)
        else:
            tender['time_left'] = "N/A"
            tender['time_left_days'] = 0
            open_tenders.append(tender)

    # Apply Open RFP search filter
    if open_search_query:
        open_tenders = [
            t for t in open_tenders if
            any(open_search_query in str(value).lower() for value in t.values())
        ]

    # Apply Closed RFP search filter
    if closed_search_query:
        closed_tenders = [
            t for t in closed_tenders if
            any(closed_search_query in str(value).lower() for value in t.values())
        ]

    # Pagination logic
    page = int(request.args.get('page', 1))
    per_page = 30
    open_total_pages = (len(open_tenders) + per_page - 1) // per_page
    closed_total_pages = (len(closed_tenders) + per_page - 1) // per_page
    open_tenders_page = open_tenders[(page - 1) * per_page: page * per_page]
    closed_tenders_page = closed_tenders[(page - 1) * per_page: page * per_page]

    # RFP counts
    open_rfp_count = len(open_tenders)
    closed_rfp_count = len(closed_tenders)
    total_rfp_count = open_rfp_count + closed_rfp_count

    # RFP values
    open_rfp_value = sum(safe_float(tender['tender_cost']) for tender in open_tenders)
    closed_rfp_value = sum(safe_float(tender['tender_cost']) for tender in closed_tenders)
    total_rfp_value = open_rfp_value + closed_rfp_value

        # Open RFPs metrics

    return render_template(
        'landing.html',
        open_tenders=open_tenders_page,
        closed_tenders=closed_tenders_page,
        current_page=page,
        open_total_pages=open_total_pages,
        closed_total_pages=closed_total_pages,
        distinct_activities=distinct_activities,
        selected_activity=selected_activity,
        selected_contract_duration=selected_contract_duration,
        time_left_filter=time_left_filter,
        tenders=tenders,
        total_rfp_count=total_rfp_count,
        open_search_query=open_search_query,
        open_rfp_value=open_rfp_value,
        closed_rfp_value=closed_rfp_value,
        closed_search_query=closed_search_query,
        total_rfp_value=total_rfp_value,
        closed_rfp_count=closed_rfp_count,
        open_rfp_count=open_rfp_count,
        success_rate=success_rate,
        failure_rate=failure_rate,
        process_efficiency=process_efficiency,
        avg_tender_value=avg_tender_value,
        repeat_clients=repeat_clients
    )

@app.route("/index.html", methods=["GET", "POST"])
@login_required
def index():
    # Load all tenders
    tenders = load_tenders()
    distinct_activities = get_distinct_activities(tenders)

    # Extract filters
    selected_activity = request.args.get('competition_activity', 'All')
    selected_contract_duration = request.args.getlist('contract_duration')
    time_left_filter = request.args.get('time_left')

    # Separate search queries for Open and Closed RFPs
    open_search_query = request.args.get('open_search', '').strip().lower()
    closed_search_query = request.args.get('closed_search', '').strip().lower()

    current_time = datetime.now()
    open_tenders = []
    closed_tenders = []

    # Convert selected activity to lowercase for case-insensitive matching
    selected_activity = request.args.get('competition_activity', 'All').strip().lower()

    # Apply filtering only if the user selects an activity other than "All"
    if selected_activity and selected_activity != "all":
        tenders = [
            tender for tender in tenders
            if selected_activity in tender.get('scope', '').strip().lower()  # Partial match
        ]

    for tender in tenders:
        deadline_str = tender.get('Deadlines', '')
        deadline = None
        if deadline_str and deadline_str.strip().lower() != 'n/a':
            try:
                deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
            except ValueError:
                # Log a warning only if the RFP ID is available
                rfp_id = tender.get('rfp', 'Unknown')
                logging.warning(f"Invalid deadline format for RFP {rfp_id}")
        else:
            # Skip tenders with invalid or missing deadlines
            continue

        if deadline:
            if deadline > current_time:
                time_left = deadline - current_time
                tender['time_left'] = f"{time_left.days} days"
                tender['time_left_days'] = time_left.days
                open_tenders.append(tender)
            else:
                potential_next_rfp_date = calculate_potential_next_rfp(deadline, tender.get('contract_period', ''))
                tender['potential_next_rfp'] = potential_next_rfp_date.strftime('%d/%m/%Y') if potential_next_rfp_date else "N/A"
                closed_tenders.append(tender)
        else:
            tender['time_left'] = "N/A"
            tender['time_left_days'] = 0
            open_tenders.append(tender)

    # Apply Open RFP search filter
    if open_search_query:
        open_tenders = [
            t for t in open_tenders if
            any(open_search_query in str(value).lower() for value in t.values())
        ]

    # Apply Closed RFP search filter
    if closed_search_query:
        closed_tenders = [
            t for t in closed_tenders if
            any(closed_search_query in str(value).lower() for value in t.values())
        ]

    # Pagination logic
    page = int(request.args.get('page', 1))
    per_page = 30
    open_total_pages = (len(open_tenders) + per_page - 1) // per_page
    closed_total_pages = (len(closed_tenders) + per_page - 1) // per_page
    open_tenders_page = open_tenders[(page - 1) * per_page: page * per_page]
    closed_tenders_page = closed_tenders[(page - 1) * per_page: page * per_page]

    # RFP counts
    open_rfp_count = len(open_tenders)
    closed_rfp_count = len(closed_tenders)
    total_rfp_count = open_rfp_count + closed_rfp_count

    # RFP values
    open_rfp_value = sum(safe_float(tender['tender_cost']) for tender in open_tenders)
    closed_rfp_value = sum(safe_float(tender['tender_cost']) for tender in closed_tenders)
    total_rfp_value = open_rfp_value + closed_rfp_value

        # Open RFPs metrics

    return render_template(
        'index.html',
        open_tenders=open_tenders_page,
        closed_tenders=closed_tenders_page,
        current_page=page,
        open_total_pages=open_total_pages,
        closed_total_pages=closed_total_pages,
        distinct_activities=distinct_activities,
        selected_activity=selected_activity,
        selected_contract_duration=selected_contract_duration,
        time_left_filter=time_left_filter,
        tenders=tenders,
        total_rfp_count=total_rfp_count,
        open_search_query=open_search_query,
        open_rfp_value=open_rfp_value,
        closed_rfp_value=closed_rfp_value,
        closed_search_query=closed_search_query,
        total_rfp_value=total_rfp_value,
        closed_rfp_count=closed_rfp_count,
        open_rfp_count=open_rfp_count
    )

# Download Open RFPs route
@app.route("/download_open_rfps", methods=["GET"])
@login_required
def download_open_rfps():
    open_tenders, _ = classify_tenders()  # Get all Open RFPs

    # Get filters from request
    selected_countries = request.args.getlist('country')
    selected_activity = request.args.getlist('competition_activity') or []  # Ensure it's a list
    selected_contract_duration = request.args.getlist('contract_duration')
    time_left_filter = request.args.get('time_left', type=int, default=None)
    open_search_query = request.args.get('open_search', '').strip().lower()

    # Apply country filter
    if selected_countries:
        open_tenders = [t for t in open_tenders if t.get('country') in selected_countries]

    # Apply competition activity filter (Partial match, case-insensitive)
    if selected_activity:
        open_tenders = [
            t for t in open_tenders
            if any(activity in (t.get('scope') or '').lower() for activity in selected_activity)
        ]

    # Apply contract duration filter (Avoiding duplicates)
    if selected_contract_duration:
        filtered_tenders = []
        for contract_filter in selected_contract_duration:
            filtered_tenders.extend(filter_by_contract_duration(open_tenders, contract_filter))
        # Remove duplicates based on 'rfp' key
        open_tenders = list({t['rfp']: t for t in filtered_tenders}.values())

    # Apply time left filter
    current_time = datetime.now()
    filtered_tenders = []
    for tender in open_tenders:
        deadline_str = tender.get('Deadlines', '')
        rfp_id = tender.get('rfp', 'Unknown')  # Safely access rfp field

        # Skip tenders with invalid or missing deadlines
        if not deadline_str or deadline_str.strip().lower() == 'n/a':
            continue

        try:
            deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
            time_left = (deadline - current_time).days
            tender['time_left'] = f"{time_left} days"
            tender['time_left_days'] = time_left
        except ValueError:
            logging.warning(f"Invalid deadline format for RFP {rfp_id}")
            continue

        # Apply time left filter
        if time_left_filter is None or tender['time_left_days'] <= time_left_filter:
            filtered_tenders.append(tender)

    open_tenders = filtered_tenders

    # Apply Open RFP search filter
    if open_search_query:
        open_tenders = [
            t for t in open_tenders if
            any(open_search_query in str(value).lower() for value in t.values())
        ]

    # Create a Workbook and a sheet
    wb = Workbook()
    ws = wb.active
    ws.title = "Open RFPs"

    # Write header
    ws.append(["RFP#", "SCOPE", "CLIENT", "TIME LEFT", "TENDER COST", "CONTRACT PERIOD", "DEADLINE FOR SUBMISSION", "COUNTRY"])

    # Write rows
    for tender in open_tenders:
        ws.append([
            tender.get("rfp", "N/A"),
            tender.get("scope", "N/A"),
            tender.get("client", "N/A"),
            tender.get("time_left", "N/A"),
            tender.get("tender_cost", "N/A"),
            tender.get("contract_period", "N/A"),
            tender.get("Deadlines", "N/A").split()[0] if tender.get("Deadlines") else "N/A",
            tender.get("country", "N/A")
        ])

    # Save the workbook to a byte stream
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    # Send the file as response
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="open_rfps.xlsx"
    )

@app.route("/download_closed_rfps", methods=["GET"])
@login_required
def download_closed_rfps():
    _, closed_tenders = classify_tenders()  # Get all Closed RFPs

    # Get filters from request
    selected_countries = [c.lower() for c in request.args.getlist('country')]
    selected_activities = [a.lower() for a in request.args.getlist('competition_activity')]
    selected_contract_duration = request.args.getlist('contract_duration')
    closed_search_query = request.args.get('closed_search', '').strip().lower()
    selected_awarded_range = request.args.get('awarded_date_range', 'all')

    # Apply country filter
    if selected_countries:
        closed_tenders = [t for t in closed_tenders if t.get('country', '').strip().lower() in selected_countries]

    # Apply competition activity filter
    if selected_activities and "all" not in selected_activities:
        closed_tenders = [t for t in closed_tenders if any(activity in (t.get('scope') or '').strip().lower() for activity in selected_activities)]

    # Apply contract duration filter
    if selected_contract_duration:
        filtered_tenders = []
        for contract_filter in selected_contract_duration:
            filtered_tenders.extend(filter_by_contract_duration(closed_tenders, contract_filter))
        # Remove duplicates based on 'rfp' key
        seen = set()
        closed_tenders = []
        for tender in filtered_tenders:
            if tender["rfp"] not in seen:
                seen.add(tender["rfp"])
                closed_tenders.append(tender)

    # Process awarded dates for filtering
    filtered_tenders = []
    for tender in closed_tenders:
        deadline_str = tender.get('Deadlines', '')
        rfp_id = tender.get('rfp', 'Unknown')  # Safely access rfp field

        # Skip tenders with invalid or missing deadlines
        if not deadline_str or deadline_str.strip().lower() == 'n/a':
            continue

        try:
            deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
        except ValueError:
            logging.warning(f"Invalid deadline format for RFP {rfp_id}")
            continue

        # Calculate potential next RFP date
        contract_period = tender.get('contract_period', '')
        potential_next_rfp_date = calculate_potential_next_rfp(deadline, contract_period)
        tender['potential_next_rfp'] = potential_next_rfp_date.strftime('%d/%m/%Y') if potential_next_rfp_date else "N/A"

        filtered_tenders.append(tender)

    closed_tenders = filtered_tenders

    # Define date filters with start dates at midnight
    date_filters = {
        '3_months': (datetime.today() - relativedelta(months=3)).replace(hour=0, minute=0, second=0, microsecond=0),
        '6_months': (datetime.today() - relativedelta(months=6)).replace(hour=0, minute=0, second=0, microsecond=0),
        '1_year': (datetime.today() - relativedelta(years=1)).replace(hour=0, minute=0, second=0, microsecond=0),
        '2_years': (datetime.today() - relativedelta(years=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    }

    # Apply awarded date filter
    if selected_awarded_range in date_filters:
        start_date = date_filters[selected_awarded_range]
        closed_tenders = [t for t in closed_tenders if t['awarded_date_dt'] and t['awarded_date_dt'] >= start_date]

    # Apply search filter
    if closed_search_query:
        closed_tenders = [
            t for t in closed_tenders
            if any(closed_search_query in str(value).lower() for value in t.values())
        ]

    # Create and populate the Excel workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Closed RFPs"
    headers = ["RFP#", "SCOPE", "CLIENT", "TENDER COST", "PARTICIPATING FIRMS", "AWARDED TO", "CONTRACT PERIOD",
               "AWARDED DATE", "POTENTIAL NEXT RFP", "DEADLINE FOR SUBMISSION", "COUNTRY"]
    ws.append(headers)

    for tender in closed_tenders:
        ws.append([
            tender.get("rfp", "N/A"),
            tender.get("scope", "N/A"),
            tender.get("client", "N/A"),
            tender.get("tender_cost", "N/A"),
            tender.get("participating_firms", "N/A"),
            tender.get("awarded_to", "N/A"),
            tender.get("contract_period", "N/A"),
            tender.get("awarded_date", "N/A"),
            tender.get("potential_next_rfp", "N/A"),
            tender.get("Deadlines", "N/A").split()[0] if tender.get("Deadlines") else "N/A",  # Date only
            tender.get("country", "N/A")
        ])

    # Send the Excel file as a response
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="closed_rfps.xlsx"
    )

def is_within(deadline_str, hours=0, days=0):
    """Check if deadline is within given time window"""
    deadline = datetime.strptime(deadline_str, '%d-%m-%Y %H:%M')
    now = datetime.now()
    time_window = now + timedelta(hours=hours, days=days)
    return now <= deadline <= time_window

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)