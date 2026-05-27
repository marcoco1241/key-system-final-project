from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import pymysql
import qrcode
import io
import base64
import os

app = Flask(__name__)
app.secret_key = 'ccs_key_nexus_secret_key_2026'

DB_HOST = os.environ.get("DB_HOST", "mysql-2ed41329-marcojayreyes98-b389.h.aivencloud.com")
DB_USER = os.environ.get("DB_USER", "avnadmin")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "AVNS_f8AtOSvb71Nns91q0eb")
DB_NAME = os.environ.get("DB_NAME", "defaultdb")
DB_PORT = int(os.environ.get("DB_PORT", 11287))

def get_db_connection():
    """Helper function to open a secure SSL connection to your Aiven MySQL cloud database."""
    try:
        connection = pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            cursorclass=pymysql.cursors.DictCursor,
            ssl={'ssl': {}}
        )
        return connection
    except pymysql.MySQLError as e:
        print(f"Database Connection Failed: {e}")
        return None

@app.route('/', methods=['GET'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    """Unified Login Portal Route with Next-Page Routing Redirection Target Hooks."""
    if session.get('logged_in'):
        if session.get('role') == 'admin':
            return redirect(url_for('dashboard'))
        elif session.get('role') == 'student':
            return redirect(url_for('student_dashboard'))

    if request.method == 'POST':
        user_input = request.form.get('username', '').strip()
        password   = request.form.get('password', '').strip()

        if not user_input or not password:
            flash("Please fill in all layout authentication fields.", "error")
            return render_template('index.html')

        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    admin_sql = """
                        SELECT username, email, password FROM admin 
                        WHERE (username = %s OR email = %s) AND password = %s
                    """
                    cursor.execute(admin_sql, (user_input, user_input, password))
                    admin_row = cursor.fetchone()

                    if admin_row:
                        session['logged_in'] = True
                        session['username']  = admin_row['username']
                        session['role']      = 'admin'
                        return redirect(url_for('dashboard'))

                    student_sql = """
                        SELECT student_id, fullname, password FROM students 
                        WHERE student_id = %s AND password = %s
                    """
                    cursor.execute(student_sql, (user_input, password))
                    student_row = cursor.fetchone()

                    if student_row:
                        session['logged_in']  = True
                        session['username']   = student_row['fullname']
                        session['student_id'] = student_row['student_id']
                        session['role']       = 'student'
                        
                        if 'target_qr_room' in session:
                            destination_room = session.pop('target_qr_room')
                            return redirect(url_for('student_issue', room_number=destination_room))
                            
                        return redirect(url_for('student_dashboard'))
                    
                    flash("Access Denied! Invalid Student ID / Username or Password.", "error")

            except pymysql.MySQLError as e:
                print(f"Unified Authentication Crash: {e}")
                flash("Internal server framework database error.", "error")
            finally:
                conn.close()
        else:
            flash("Could not reach database server.", "error")

    return render_template('index.html')

@app.route('/student/dashboard')
def student_dashboard():
    """Protected Terminal Screen - Automatically checks for persistent active items."""
    if not session.get('logged_in') or session.get('role') != 'student':
        session.clear()
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT room_number FROM transactions WHERE id_number = %s AND status = 'Active'",
                    (session.get('student_id'),)
                )
                active_hold = cursor.fetchone()
                if active_hold:
                    return render_template('student_lockout.html', room_number=active_hold['room_number'])
        except pymysql.MySQLError as e:
            print(f"Dashboard Safety Check Failed: {e}")
        finally:
            conn.close()
            
    return render_template('student_dashboard.html')

@app.route('/dashboard')
def dashboard():
    """Protected Control Panel - Parses alert texts for Borrowed, Returned, Damaged, or Lost states."""
    if not session.get('logged_in') or session.get('role') != 'admin':
        session.clear()
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if not conn:
        return "Database Connection Error", 500
        
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM transactions WHERE status = 'Active' AND return_date >= NOW()")
            keys_out_now = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM transactions WHERE status = 'Active' AND return_date < NOW()")
            overdue_keys = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(DISTINCT room_number) as count FROM transactions")
            total_keys = cursor.fetchone()['count']

            cursor.execute("SELECT message, created_at FROM alerts ORDER BY id DESC LIMIT 5")
            raw_alerts = cursor.fetchall()
            
            alerts = []
            for alert in raw_alerts:
                msg = alert['message']
                status = 'borrowed'
                due_time = ""
                
                if "lost" in msg.lower() or "missing" in msg.lower() or "damaged" in msg.lower() or "repair" in msg.lower():
                    status = 'missing'
                elif "returned key:" in msg.lower():
                    status = 'returned'
                elif "issued key:" in msg.lower() or "qr request:" in msg.lower():
                    status = 'borrowed'
                    try:
                        parts = msg.split(':')
                        if len(parts) > 1:
                            room_part = parts[1].strip().split(' ')[0].strip()
                            room_part = room_part.replace('|', '').strip()
                            
                            cursor.execute(
                                "SELECT return_date FROM transactions WHERE room_number = %s AND status = 'Active' ORDER BY id DESC LIMIT 1",
                                (room_part,)
                            )
                            trans_row = cursor.fetchone()
                            if trans_row and trans_row['return_date']:
                                due_time = trans_row['return_date'].strftime('%Y-%m-%dT%H:%M:%S')
                    except Exception as parse_err:
                        print(f"Timestamp link skipped: {parse_err}")

                alerts.append({
                    'message': msg,
                    'created_at': alert['created_at'],
                    'status': status,
                    'due_time': due_time
                })
            
    except pymysql.MySQLError as e:
        print(f"Query Error: {e}")
        keys_out_now = overdue_keys = total_keys = 0
        alerts = []
    finally:
        conn.close()

    return render_template('dashboard.html', 
                           keys_out_now=keys_out_now, 
                           overdue_keys=overdue_keys, 
                           total_keys=total_keys, 
                           alerts=alerts)

@app.route('/logout')
def logout():
    """Unified Secure Logout Command Strategy."""
    session.clear()
    return redirect(url_for('login'))

@app.route('/issue', methods=['GET', 'POST'])
def issue():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        room_number   = request.form.get('room_number', '').strip()
        schedule      = request.form.get('schedule', '')
        borrower_name = request.form.get('borrower_name', '').strip()
        id_number     = request.form.get('id_number', '').strip()
        professor     = request.form.get('professor', '').strip()
        department    = request.form.get('department', '').strip()
        class_hours   = request.form.get('class_hours', '').strip()
        return_date   = request.form.get('return_date', '')

        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    sql_transaction = """
                        INSERT INTO transactions 
                        (room_number, schedule, borrower_name, id_number, professor, department, class_hours, return_date, status) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Active')
                    """
                    cursor.execute(sql_transaction, (
                        room_number, schedule, borrower_name, id_number, 
                        professor, department, class_hours, return_date
                    ))

                    alert_msg = f"Issued key: {room_number} | Borrower: {borrower_name}"
                    cursor.execute("INSERT INTO alerts (message, created_at) VALUES (%s, 'New')", (alert_msg,))
                    
                    conn.commit()
                    flash(f"Key for {room_number} issued successfully!", "success")
                    return redirect(url_for('dashboard'))
                    
            except pymysql.MySQLError as e:
                print(f"Transaction Write Failure: {e}")
                flash("Database storage error occurred. Failed to issue key.", "error")
            finally:
                conn.close()
        else:
            flash("Database network pipeline unreachable.", "error")

    return render_template('issue.html')

@app.route('/return', methods=['GET', 'POST'])
def key_return():
    """Logs the return of a key and formats conditional values ('Good', 'Damaged', 'Lost') automatically."""
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        transaction_id = request.form.get('transaction_id')
        room_number    = request.form.get('room_number')
        condition_val  = request.form.get('condition', 'Good')
        remarks_val    = request.form.get('remarks', '').strip()

        if transaction_id:
            conn = get_db_connection()
            if conn:
                try:
                    with conn.cursor() as cursor:
                        if condition_val == 'Lost':
                            cursor.execute("UPDATE transactions SET status = 'Lost' WHERE id = %s", (transaction_id,))
                            alert_msg = f"LOST KEY ALERT: Room {room_number} marked as lost! Master key required. {f'- Remarks: {remarks_val}' if remarks_val else ''}"
                        elif condition_val == 'Damaged':
                            cursor.execute("UPDATE transactions SET status = 'Damaged' WHERE id = %s", (transaction_id,))
                            alert_msg = f"DAMAGED KEY ALERT: Room {room_number} key broken/damaged sent for repair! {f'- Remarks: {remarks_val}' if remarks_val else ''}"
                        else:
                            cursor.execute("UPDATE transactions SET status = 'Returned' WHERE id = %s", (transaction_id,))
                            alert_msg = f"Returned key: {room_number} ({condition_val}) {f'- {remarks_val}' if remarks_val else ''}"

                        cursor.execute("INSERT INTO alerts (message, created_at) VALUES (%s, 'New')", (alert_msg,))
                        conn.commit()
                        flash(f"Log registered successfully for Room {room_number}.", "success")
                        return redirect(url_for('dashboard'))
                except pymysql.MySQLError as e:
                    print(f"Database update failure: {e}")
                    flash("System failed to update transaction state.", "error")
                finally:
                    conn.close()
        else:
            flash("No active room transaction selected to return.", "error")

    return render_template('return.html')

@app.route('/api/repair_done/<room_number>', methods=['POST'])
def api_repair_done(room_number):
    """AUTOMATIC REPAIR TERMINAL: Marks a damaged key fixed and resets it to Available."""
    if not session.get('logged_in') or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database offline'}), 500

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM transactions WHERE room_number = %s AND status = 'Damaged' ORDER BY id DESC LIMIT 1",
                (room_number,)
            )
            target_row = cursor.fetchone()

            if target_row:
                cursor.execute("UPDATE transactions SET status = 'Returned' WHERE id = %s", (target_row['id'],))
                
                alert_msg = f"Fixed key: {room_number} has been repaired and is back in the locker locker."
                cursor.execute("INSERT INTO alerts (message, created_at) VALUES (%s, 'New')", (alert_msg,))
                
                conn.commit()
                return jsonify({'success': True})
            else:
                return jsonify({'error': 'No active damaged log found for this room location.'})
    except pymysql.MySQLError as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/replace_lost/<room_number>', methods=['POST'])
def api_replace_lost(room_number):
    """AUTOMATIC REPLACEMENT TERMINAL: Resolves key lockdown state by updating it to a new provisioned duplicate."""
    if not session.get('logged_in') or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database offline'}), 500

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM transactions WHERE room_number = %s AND status = 'Lost' ORDER BY id DESC LIMIT 1",
                (room_number,)
            )
            target_row = cursor.fetchone()

            if target_row:
                cursor.execute("UPDATE transactions SET status = 'Returned' WHERE id = %s", (target_row['id'],))
                
                alert_msg = f"Replaced key: New duplicate master deployed for {room_number}. Storage restored."
                cursor.execute("INSERT INTO alerts (message, created_at) VALUES (%s, 'New')", (alert_msg,))
                
                conn.commit()
                return jsonify({'success': True})
            else:
                return jsonify({'error': 'No tracking entry flagged as Lost found for this room facility.'})
    except pymysql.MySQLError as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/search_log', methods=['GET'])
def api_search_log():
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    room_query = request.args.get('room', '').strip()
    if not room_query:
        return jsonify(None)

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                sql = """
                    SELECT id, room_number, borrower_name, id_number, return_date 
                    FROM transactions 
                    WHERE LOWER(room_number) = LOWER(%s) AND status = 'Active' 
                    ORDER BY id DESC LIMIT 1
                """
                cursor.execute(sql, (room_query,))
                log = cursor.fetchone()

                if log:
                    log['is_overdue'] = str(datetime.now()) > str(log['return_date']) if log['return_date'] else False
                    return jsonify(log)
                else:
                    return jsonify(None)
        except pymysql.MySQLError as e:
            return jsonify({'error': str(e)}), 500
        finally:
            conn.close()
    return jsonify({'error': 'No database connection'}), 500

@app.route('/room')
def find_room():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect(url_for('login'))
    return render_template('room.html')

@app.route('/api/fetch_rooms', methods=['GET'])
def api_fetch_rooms():
    """Resolves unique rooms using a subquery to select only the latest transaction row per room."""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    search_query  = request.args.get('search', '').strip().lower()
    status_filter = request.args.get('status', 'All Statuses')

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database offline'}), 500

    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT t.id, t.room_number, t.borrower_name, t.id_number, t.return_date, t.status 
                FROM transactions t
                INNER JOIN (
                    SELECT room_number, MAX(id) as max_id 
                    FROM transactions 
                    GROUP BY room_number
                ) sub ON t.id = sub.max_id
            """
            cursor.execute(sql)
            all_logs = cursor.fetchall()

            results = []
            for log in all_logs:
                if search_query:
                    match_found = (
                        search_query in log['room_number'].lower() or 
                        search_query in log['borrower_name'].lower() or 
                        search_query in log['id_number'].lower()
                    )
                    if not match_found:
                        continue

                if log['status'] == 'Active':
                    if log['return_date'] and datetime.now() > log['return_date']:
                        computed_status = 'Overdue'
                    else:
                        computed_status = 'Keys Out Now'
                elif log['status'] == 'Lost':
                    computed_status = 'Lost'
                elif log['status'] == 'Damaged':
                    computed_status = 'Damaged'
                else:
                    computed_status = 'Available'

                if status_filter != 'All Statuses' and status_filter != computed_status:
                    continue

                if log['status'] == 'Returned':
                    time_display = "Returned to Locker"
                elif log['status'] == 'Lost':
                    time_display = "⚠️ LOCKDOWN: KEY LOST"
                elif log['status'] == 'Damaged':
                    time_display = "🔧 OUT OF SERVICE: REPAIRING"
                else:
                    time_display = f"Expected: {log['return_date'].strftime('%b %d, %I:%M %p') if log['return_date'] else '—'}"

                results.append({
                    'room': log['room_number'],
                    'key_id': f"KEY-CCS-{ ''.join(filter(str.isdigit, log['room_number'])) or '000' }",
                    'status': computed_status,
                    'holder': '— Secure in Locker' if computed_status in ['Available', 'Lost', 'Damaged'] else log['borrower_name'],
                    'time_logs': time_display
                })

            return jsonify(results)

    except pymysql.MySQLError as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/generate_qr', methods=['GET', 'POST'])
def generate_qr():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect(url_for('login'))
        
    qr_data_uri = None
    room_number = None
    available_rooms = []
    
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                sql = """
                    SELECT t.room_number, t.status 
                    FROM transactions t
                    INNER JOIN (
                        SELECT room_number, MAX(id) as max_id 
                        FROM transactions 
                        GROUP BY room_number
                    ) sub ON t.id = sub.max_id
                """
                cursor.execute(sql)
                latest_logs = cursor.fetchall()
                
                for log in latest_logs:
                    if log['status'] not in ['Active', 'Damaged', 'Lost']:
                        available_rooms.append(log['room_number'])
                        
        except pymysql.MySQLError as e:
            print(f"Error compiling active listings: {e}")
        finally:
            conn.close()

    if request.method == 'POST':
        room_number = request.form.get('room_number', '').strip()
        
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT status FROM transactions WHERE LOWER(room_number) = LOWER(%s) AND status = 'Active'",
                        (room_number,)
                    )
                    active_match = cursor.fetchone()
                    
                    if active_match:
                        flash(f"Generation Blocked! Key for '{room_number}' is currently Out Now or Overdue.", "error")
                        return render_template('generate_qr.html', qr_data_uri=None, room_number=None, available_rooms=available_rooms)
                        
                    cursor.execute(
                        "SELECT status FROM transactions WHERE LOWER(room_number) = LOWER(%s) ORDER BY id DESC LIMIT 1",
                        (room_number,)
                    )
                    state_match = cursor.fetchone()
                    if state_match and state_match['status'] in ['Lost', 'Damaged']:
                        flash(f"Generation Blocked! '{room_number}' key is currently flagged as {state_match['status']}.", "error")
                        return render_template('generate_qr.html', qr_data_uri=None, room_number=None, available_rooms=available_rooms)

                    if room_number:
                        student_form_url = f"http://{request.host}/student_issue/{room_number}"
                        
                        qr = qrcode.QRCode(version=1, box_size=10, border=4)
                        qr.add_data(student_form_url)
                        qr.make(fit=True)
                        
                        img = qr.make_image(fill_color="black", back_color="white")
                        buffered = io.BytesIO()
                        img.save(buffered, format="PNG")
                        
                        img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
                        qr_data_uri = f"data:image/png;base64,{img_str}"
                        
            except pymysql.MySQLError as e:
                print(f"QR Validation Engine Error: {e}")
            finally:
                conn.close()
            
    return render_template('generate_qr.html', qr_data_uri=qr_data_uri, room_number=room_number, available_rooms=available_rooms)

@app.route('/api/check_room_status/<room_number>', methods=['GET'])
def api_check_room_status(room_number):
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM transactions WHERE room_number = %s AND status = 'Active'",
                    (room_number,)
                )
                active_transaction = cursor.fetchone()
                if active_transaction:
                    return jsonify({'status': 'Occupied'})
                return jsonify({'status': 'Available'})
        except pymysql.MySQLError as e:
            return jsonify({'error': str(e)}), 500
        finally:
            conn.close()
    return jsonify({'status': 'Unknown'}), 500

@app.route('/admin/students', methods=['GET', 'POST'])
def manage_students():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    students_list = []
    
    if request.method == 'POST':
        student_id = request.form.get('student_id', '').strip()
        fullname   = request.form.get('fullname', '').strip()
        email      = request.form.get('email', '').strip() 
        password   = request.form.get('password', '').strip()
        
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO students (student_id, fullname, gmail, password) VALUES (%s, %s, %s, %s)",
                        (student_id, fullname, email, password)
                    )
                    conn.commit()
                    flash(f"Student Account for {fullname} created successfully!", "success")
            except pymysql.MySQLError as e:
                print(f"Error creating student row: {e}")
                flash("Registration failed. Student ID might already exist.", "error")

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM students ORDER BY id DESC")
                students_list = cursor.fetchall()
        except pymysql.MySQLError as e:
            print(f"Error fetching students: {e}")
        finally:
            conn.close()
            
    return render_template('manage_students.html', students=students_list)

@app.route('/admin/students/get/<int:id>', methods=['GET'])
def get_student_data(id):
    """API Endpoint to fetch a specific student's details for modal asynchronous population."""
    if not session.get('logged_in') or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, student_id, fullname, gmail as email, password FROM students WHERE id = %s", (id,))
                student = cursor.fetchone()
                if student:
                    return jsonify(student)
                return jsonify({'error': 'Student not found'}), 404
        except pymysql.MySQLError as e:
            return jsonify({'error': str(e)}), 500
        finally:
            conn.close()
    return jsonify({'error': 'Database connection error'}), 500

@app.route('/admin/students/update/<int:id>', methods=['POST'])
def update_student(id):
    """Database processing route to apply modifications to a student row registry entity."""
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect(url_for('login'))

    student_id = request.form.get('student_id', '').strip()
    fullname   = request.form.get('fullname', '').strip()
    email      = request.form.get('email', '').strip()
    password   = request.form.get('password', '').strip()

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE students SET student_id=%s, fullname=%s, gmail=%s, password=%s WHERE id=%s",
                    (student_id, fullname, email, password, id)
                )
                conn.commit()
                flash(f"Student account details for '{fullname}' successfully updated.", "success")
        except pymysql.MySQLError as e:
            print(f"Database row modification crash: {e}")
            flash("Update failed. Make sure the Student ID isn't a duplicate assignment.", "error")
        finally:
            conn.close()

    return redirect(url_for('manage_students'))

@app.route('/admin/students/delete/<int:id>', methods=['POST'])
def delete_student(id):
    """Secure structural tracking block to remove a specific student record by unique primary key."""
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT fullname FROM students WHERE id = %s", (id,))
                target_student = cursor.fetchone()
                
                if target_student:
                    cursor.execute("DELETE FROM students WHERE id = %s", (id,))
                    conn.commit()
                    flash(f"Student account for '{target_student['fullname']}' successfully deleted.", "success")
                else:
                    flash("Delete targeted match verification record context could not be located.", "error")
        except pymysql.MySQLError as e:
            print(f"Database row elimination failure: {e}")
            flash("Failed to delete student. They might have active key logs bound to their account.", "error")
        finally:
            conn.close()
            
    return redirect(url_for('manage_students'))

@app.route('/student_issue/<room_number>', methods=['GET', 'POST'])
def student_issue(room_number):
    if not session.get('logged_in') or session.get('role') != 'student':
        session['target_qr_room'] = room_number
        flash("Authentication Required! Please log into your student account to request this room key.", "error")
        return redirect(url_for('login'))

    error_message = None
    prefilled_id = session.get('student_id', '')

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT room_number FROM transactions WHERE id_number = %s AND status = 'Active'", 
                    (prefilled_id,)
                )
                active_hold = cursor.fetchone()
                if active_hold:
                    return render_template('student_lockout.html', room_number=active_hold['room_number'])
        except pymysql.MySQLError as e:
            print(f"Pre-check Validation Failure: {e}")
        finally:
            conn.close()

    if request.method == 'POST':
        id_number     = request.form.get('id_number', '').strip()
        schedule      = request.form.get('schedule', '')
        professor     = request.form.get('professor', '').strip()
        class_hours   = request.form.get('class_hours', '').strip()
        return_date   = request.form.get('return_date', '')

        if id_number != prefilled_id:
            error_message = "Security Error! Input ID data mismatch verified session parameters."
            return render_template('student_form.html', room_number=room_number, error_message=error_message, prefilled_id=prefilled_id)

        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT fullname FROM students WHERE student_id = %s", (id_number,))
                    registered_student = cursor.fetchone()

                    if not registered_student:
                        error_message = "Access Denied! Your Student ID is not registered."
                        return render_template('student_form.html', room_number=room_number, error_message=error_message, prefilled_id=prefilled_id)

                    borrower_name = registered_student['fullname']
                    department    = "CCS"

                    sql_transaction = """
                        INSERT INTO transactions 
                        (room_number, schedule, borrower_name, id_number, professor, department, class_hours, return_date, status) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Active')
                    """
                    cursor.execute(sql_transaction, (
                        room_number, schedule, borrower_name, id_number, 
                        professor, department, class_hours, return_date
                    ))

                    alert_msg = f"QR Request: {room_number} self-issued by Student: {borrower_name}"
                    cursor.execute("INSERT INTO alerts (message, created_at) VALUES (%s, 'New')", (alert_msg,))
                    
                    conn.commit()
                    return render_template('student_success.html', room_number=room_number)
                    
            except pymysql.MySQLError as e:
                print(f"Student Self-Issue Crash: {e}")
                return "Database recording failure.", 500
            finally:
                conn.close()
        else:
            return "Database pipeline unreachable.", 500

    return render_template('student_form.html', room_number=room_number, error_message=error_message, prefilled_id=prefilled_id)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)