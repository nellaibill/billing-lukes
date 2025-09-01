from flask import Flask, render_template, request, redirect, url_for, make_response, session, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta
from sqlalchemy import and_
from functools import wraps
import os
import shutil

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lukes_billing_db.db'
#app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@localhost/lukes_billing_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# Master tables
class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class PaymentType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

# Header table
class HeaderEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    op_bill_no = db.Column(db.String(50), nullable=False)
    patient_name = db.Column(db.String(100), nullable=False)
    date = db.Column(db.Date, nullable=False)
    is_cancelled = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(IST), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(IST), onupdate=lambda: datetime.now(IST), nullable=False)
    details = db.relationship('DetailsEntry', backref='header', lazy=True)

# Details table
class DetailsEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    header_id = db.Column(db.Integer, db.ForeignKey('header_entry.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_type_id = db.Column(db.Integer, db.ForeignKey('payment_type.id'), nullable=False)

    category = db.relationship('Category', backref='details_entries')
    payment_type = db.relationship('PaymentType', backref='details_entries')

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    usertype = db.Column(db.String(20), nullable=False)  # 'admin' or 'user'
    createddate = db.Column(db.DateTime, default=lambda: datetime.now(IST), nullable=False)
    updateddate = db.Column(db.DateTime, default=lambda: datetime.now(IST), onupdate=lambda: datetime.now(IST), nullable=False)

    def __repr__(self):
        return f'<User {self.username}>'

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            session['user_id'] = user.id
            session['username'] = user.username
            session['usertype'] = user.usertype
            return redirect(url_for('index'))
        else:
            error = 'Invalid username or password'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    today_str = datetime.now().strftime('%Y-%m-%d')
    # printed_datetime already set at top
    from_date = request.args.get('from_date', today_str)
    to_date = request.args.get('to_date', today_str)
    is_cancelled = request.args.get('is_cancelled')
    # Cancel bill logic
    if request.method == 'POST' and 'cancel_id' in request.form:
        header = HeaderEntry.query.get(int(request.form['cancel_id']))
        if header and not header.is_cancelled:
            header.is_cancelled = True
            header.updated_at = datetime.now(IST)
            db.session.commit()
    # Convert to date objects for correct comparison
    from_dt = datetime.strptime(from_date, '%Y-%m-%d').date()
    to_dt = datetime.strptime(to_date, '%Y-%m-%d').date()
    query = HeaderEntry.query.filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt)
    if is_cancelled:
        query = query.filter(HeaderEntry.is_cancelled == True)
    headers = query.order_by(HeaderEntry.id.desc()).all()
    bills = []
    grand_total = 0.0
    cancelled_total = 0.0
    for header in headers:
        details = DetailsEntry.query.filter_by(header_id=header.id).all()
        total_amount = sum(d.amount for d in details)
        total_items = len(details)
        bills.append({
            'header': header,
            'details': details,
            'total_amount': total_amount,
            'total_items': total_items
        })
        if header.is_cancelled:
            cancelled_total += total_amount
        else:
            grand_total += total_amount
    printed_datetime = datetime.now().strftime('%d/%B/%Y %I:%M %p')
    return render_template('index.html', bills=bills, from_date=from_date, to_date=to_date, grand_total=grand_total, cancelled_total=cancelled_total, printed_datetime=printed_datetime)

@app.route('/bill/<int:header_id>')
def print_bill(header_id):
    header = HeaderEntry.query.get_or_404(header_id)
    details = DetailsEntry.query.filter_by(header_id=header.id).all()
    printed_datetime = datetime.now().strftime('%d/%B/%Y %I:%M %p')
    return render_template('print_bill.html', header=header, details=details, printed_datetime=printed_datetime)

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_entry():
    categories = Category.query.all()
    payment_types = PaymentType.query.all()
    slno = db.session.query(db.func.count(HeaderEntry.id)).scalar() + 1
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        if action == 'save':
            op_bill_no = request.form['op_bill_no']
            patient_name = request.form['patient_name']
            date = datetime.strptime(request.form['date'], '%Y-%m-%d')
            category_ids = request.form.getlist('category')
            amounts = request.form.getlist('amount')
            payment_type_ids = request.form.getlist('payment_type')
            # Check if at least one valid detail is present
            valid_details = [
                (cat_id, amt, pay_id)
                for cat_id, amt, pay_id in zip(category_ids, amounts, payment_type_ids)
                if cat_id and amt and pay_id and float(amt) > 0
            ]
            if not valid_details:
                flash('No valid entries to save. Please enter at least one item.', 'danger')
                return redirect(url_for('add_entry'))
            header = HeaderEntry(op_bill_no=op_bill_no, patient_name=patient_name, date=date, created_at=datetime.now(IST), updated_at=datetime.now(IST))
            db.session.add(header)
            db.session.commit()
            for cat_id, amt, pay_id in valid_details:
                details = DetailsEntry(header_id=header.id, category_id=cat_id, amount=float(amt), payment_type_id=pay_id)
                db.session.add(details)
            db.session.commit()
            flash('Entry saved successfully!', 'success')
            return redirect(url_for('add_entry'))
        elif action == 'print':
            # Print the most recent (last) bill, do not save a new one
            last_header = HeaderEntry.query.order_by(HeaderEntry.id.desc()).first()
            if last_header:
                return redirect(url_for('print_bill', header_id=last_header.id, autoprint=1))
            else:
                flash('No bills found to print.', 'danger')
                return redirect(url_for('add_entry'))
    response = make_response(render_template('add_entry.html', categories=categories, payment_types=payment_types, datetime=datetime, slno=slno))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/masters', methods=['GET', 'POST'])
@login_required
def masters():
    categories = Category.query.all()
    payment_types = PaymentType.query.all()
    edit_category = None
    # Handle edit GET
    edit_category_id = request.args.get('edit_category')
    if edit_category_id:
        edit_category = Category.query.get(int(edit_category_id))

    if request.method == 'POST':
        # Edit category
        if 'edit_category_id' in request.form:
            cat_id = request.form['edit_category_id']
            new_name = request.form['edit_category_name']
            cat = Category.query.get(int(cat_id))
            if cat and new_name:
                cat.name = new_name
                db.session.commit()
                flash('Category updated successfully!', 'success')
            else:
                flash('Failed to update category.', 'danger')
            return redirect(url_for('masters'))
        # Add category
        if 'category' in request.form:
            name = request.form['category']
            if name:
                db.session.add(Category(name=name))
                db.session.commit()
                flash('Category added successfully!', 'success')
            else:
                flash('Failed to add category.', 'danger')
        # Add payment type
        if 'payment_type' in request.form:
            name = request.form['payment_type']
            if name:
                db.session.add(PaymentType(name=name))
                db.session.commit()
                flash('Payment type added successfully!', 'success')
            else:
                flash('Failed to add payment type.', 'danger')
        return redirect(url_for('masters'))
    return render_template('masters.html', categories=categories, payment_types=payment_types, edit_category=edit_category)

@app.route('/category_report')
@login_required
def category_report():
    today_str = datetime.now().strftime('%Y-%m-%d')
    from_date = request.args.get('from_date', today_str)
    to_date = request.args.get('to_date', today_str)
    detailed_view = request.args.get('detailed') == '1'
    datewise_view = request.args.get('datewise') == '1'
    combine_bank = request.args.get('combine_bank') == '1'
    from_dt = datetime.strptime(from_date, '%Y-%m-%d').date()
    to_dt = datetime.strptime(to_date, '%Y-%m-%d').date()
    printed_datetime = datetime.now().strftime('%d/%B/%Y %I:%M %p')

    # Get all categories and payment types
    all_categories = [c.name for c in Category.query.order_by(Category.id).all()]
    all_categories_dropdown = all_categories.copy()
    filter_category = request.args.get('filter_category', '')
    if filter_category:
        all_categories = [filter_category]
    payment_types_raw = [pt.name for pt in PaymentType.query.order_by(PaymentType.name).all()]
    if combine_bank:
        all_payment_types = [pt for pt in payment_types_raw if pt not in ('Card', 'QR')]
        all_payment_types.append('Bank')
    else:
        # Force order: Cash, QR, Card if present
        ordered_types = []
        for pt in ['Cash', 'QR', 'Card']:
            if pt in payment_types_raw:
                ordered_types.append(pt)
        # Add any other payment types not in the preferred order
        for pt in payment_types_raw:
            if pt not in ordered_types:
                ordered_types.append(pt)
        all_payment_types = ordered_types

    if datewise_view:
        # Query: group by date, category, payment type
        results = db.session.query(
            HeaderEntry.date,
            Category.name.label('category'),
            PaymentType.name.label('payment_type'),
            db.func.sum(DetailsEntry.amount).label('total')
        )\
        .join(DetailsEntry, DetailsEntry.header_id == HeaderEntry.id)\
        .join(Category, DetailsEntry.category_id == Category.id)\
        .join(PaymentType, DetailsEntry.payment_type_id == PaymentType.id)\
        .filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt, HeaderEntry.is_cancelled == False)\
        .group_by(HeaderEntry.date, Category.name, PaymentType.name)\
        .order_by(HeaderEntry.date, Category.name, PaymentType.name).all()

        # Organize data: {date: {category: {payment_type: total}}}
        data = {}
        payment_types_present = set()
        categories_present = set()
        for date, category, payment_type, total in results:
            payment_types_present.add(payment_type)
            categories_present.add(category)
            if combine_bank and payment_type in ('Card', 'QR'):
                payment_type_key = 'Bank'
            else:
                payment_type_key = payment_type
            data.setdefault(date, {}).setdefault(category, {})[payment_type_key] = data.setdefault(date, {}).setdefault(category, {}).get(payment_type_key, 0) + (total or 0)

        all_dates = sorted(data.keys())
        # Only show categories/payment types present in results
        all_categories = [c for c in all_categories if c in categories_present]
        if combine_bank:
            payment_types_present.discard('Card')
            payment_types_present.discard('QR')
            payment_types_present.add('Bank')
        all_payment_types = [pt for pt in all_payment_types if pt in payment_types_present]

        # Calculate totals by category and payment type for each date
        total_by_category_date = {}
        total_by_payment_date = {}
        grand_total_date = {}
        for date in all_dates:
            total_by_category_date[date] = {}
            total_by_payment_date[date] = {pt: 0 for pt in all_payment_types}
            grand_total_date[date] = 0
            for cat in all_categories:
                cat_total = 0
                for pt in all_payment_types:
                    amt = data.get(date, {}).get(cat, {}).get(pt, 0)
                    cat_total += amt
                    total_by_payment_date[date][pt] += amt
                total_by_category_date[date][cat] = cat_total
                grand_total_date[date] += cat_total

        # Calculate overall totals
        total_by_category = {cat: 0 for cat in all_categories}
        total_by_payment = {pt: 0 for pt in all_payment_types}
        grand_total = 0
        for date in all_dates:
            for cat in all_categories:
                total_by_category[cat] += total_by_category_date[date][cat]
            for pt in all_payment_types:
                total_by_payment[pt] += total_by_payment_date[date][pt]
            grand_total += grand_total_date[date]

        return render_template(
            'category_report.html',
            data=data,
            all_dates=all_dates,
            all_categories=all_categories,
            all_payment_types=all_payment_types,
            total_by_category_date=total_by_category_date,
            total_by_payment_date=total_by_payment_date,
            grand_total_date=grand_total_date,
            total_by_category=total_by_category,
            total_by_payment=total_by_payment,
            grand_total=grand_total,
            from_date=from_date,
            to_date=to_date,
            detailed_view=detailed_view,
            datewise_view=datewise_view,
            combine_bank=combine_bank,
            all_categories_dropdown=all_categories_dropdown,
            filter_category=filter_category,
            printed_datetime=printed_datetime
        )
    else:
        all_categories_dropdown = [c.name for c in Category.query.order_by(Category.id).all()]
        filter_category = request.args.get('filter_category', '')
        if filter_category:
            all_categories = [filter_category]
        # Query details with patient name, bill number, and date
        details = db.session.query(
            Category.name.label('category'),
            PaymentType.name.label('payment_type'),
            DetailsEntry.amount,
            HeaderEntry.patient_name,
            HeaderEntry.op_bill_no,
            HeaderEntry.date
        ).join(DetailsEntry, DetailsEntry.category_id == Category.id)
        details = details.join(PaymentType, DetailsEntry.payment_type_id == PaymentType.id)
        details = details.join(HeaderEntry, DetailsEntry.header_id == HeaderEntry.id)
        details = details.filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt, HeaderEntry.is_cancelled == False)
        details = details.order_by(Category.id, PaymentType.name, HeaderEntry.op_bill_no).all()

        # Organize data: {category: {payment_type: [details]}} for detailed, {category: {payment_type: float}} for summary
        data = {}
        payment_types_present = set()
        categories_present = set()
        for category, payment_type, amount, patient_name, bill_no, entry_date in details:
            payment_types_present.add(payment_type)
            categories_present.add(category)
            if combine_bank and payment_type in ('Card', 'QR'):
                payment_type_key = 'Bank'
            else:
                payment_type_key = payment_type
            data.setdefault(category, {}).setdefault(payment_type_key, []).append({
                'amount': amount,
                'patient_name': patient_name,
                'bill_no': bill_no,
                'date': entry_date
            })

        # Sort each list of entries by date (ascending) for detailed view
        if detailed_view:
            for cat in data:
                for pt in data[cat]:
                    data[cat][pt].sort(key=lambda x: x['date'] or datetime.min)

        # Only show categories/payment types present in results
        all_categories = [c for c in all_categories if c in categories_present]
        if combine_bank:
            payment_types_present.discard('Card')
            payment_types_present.discard('QR')
            payment_types_present.add('Bank')
        all_payment_types = [pt for pt in all_payment_types if pt in payment_types_present]

        # Calculate totals
        total_by_category = {}
        total_by_payment = {pt: 0 for pt in all_payment_types}
        grand_total = 0
        # Prepare summary data if not detailed_view
        if not detailed_view:
            summary_data = {}
            for cat in all_categories:
                summary_data[cat] = {}
                for pt in all_payment_types:
                    pt_total = sum(entry['amount'] for entry in data.get(cat, {}).get(pt, []))
                    summary_data[cat][pt] = pt_total
            data = summary_data

        for cat in all_categories:
            cat_total = 0
            for pt in all_payment_types:
                if detailed_view:
                    pt_total = sum(entry['amount'] for entry in data.get(cat, {}).get(pt, []))
                else:
                    pt_total = data.get(cat, {}).get(pt, 0)
                cat_total += pt_total
                total_by_payment[pt] += pt_total
            total_by_category[cat] = cat_total
            grand_total += cat_total

    return render_template(
        'category_report.html',
        data=data,
        all_categories=all_categories,
        all_payment_types=all_payment_types,
        total_by_category=total_by_category,
        total_by_payment=total_by_payment,
        grand_total=grand_total,
        from_date=from_date,
        to_date=to_date,
        detailed_view=detailed_view,
        datewise_view=datewise_view,
        combine_bank=combine_bank,
        all_categories_dropdown=all_categories_dropdown,
        filter_category=filter_category,
        printed_datetime=printed_datetime
    )

@app.route('/payment_type_report')
@login_required
def payment_type_report():
    today_str = datetime.now().strftime('%Y-%m-%d')
    from_date = request.args.get('from_date', today_str)
    to_date = request.args.get('to_date', today_str)
    from_dt = datetime.strptime(from_date, '%Y-%m-%d').date()
    to_dt = datetime.strptime(to_date, '%Y-%m-%d').date()

    # Get all categories (order by ID for consistency with category report)
    all_categories = [c.name for c in Category.query.order_by(Category.id).all()]

    # Get all payment types, but combine 'Card' and 'QR' as 'Bank'
    payment_types_raw = [pt.name for pt in PaymentType.query.order_by(PaymentType.name).all()]
    all_payment_types = []
    for pt in payment_types_raw:
        if pt in ('Card', 'QR'):
            if 'Bank' not in all_payment_types:
                all_payment_types.append('Bank')
        else:
            all_payment_types.append(pt)

    # Prepare data structure: data[category][payment_type] = amount
    data = {cat: {pt: 0 for pt in all_payment_types} for cat in all_categories}

    # Query all details in range
    details = db.session.query(Category.name, PaymentType.name, DetailsEntry.amount)\
        .join(DetailsEntry, DetailsEntry.category_id == Category.id)\
        .join(PaymentType, DetailsEntry.payment_type_id == PaymentType.id)\
        .join(HeaderEntry, DetailsEntry.header_id == HeaderEntry.id)\
        .filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt, HeaderEntry.is_cancelled == False).all()

    for cat, pt, amt in details:
        pt_key = 'Bank' if pt in ('Card', 'QR') else pt
        if cat in data and pt_key in data[cat]:
            data[cat][pt_key] += amt

    # Calculate totals
    total_by_category = {cat: sum(data[cat].values()) for cat in all_categories}
    total_by_payment = {pt: sum(data[cat][pt] for cat in all_categories) for pt in all_payment_types}
    grand_total = sum(total_by_category.values())

    return render_template(
        'payment_type_report.html',
        all_categories=all_categories,
        all_payment_types=all_payment_types,
        data=data,
        total_by_category=total_by_category,
        total_by_payment=total_by_payment,
        grand_total=grand_total,
        from_date=from_date,
        to_date=to_date
    )

@app.route('/users', methods=['GET', 'POST'])
@login_required
def users():
    edit_id = request.args.get('edit')
    user = None
    if edit_id:
        user = User.query.get(int(edit_id))
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        username = request.form['username']
        password = request.form['password']
        usertype = request.form['usertype']
        if user_id:  # Edit existing
            user = User.query.get(int(user_id))
            if user:
                user.username = username
                if password:
                    user.password = password  # In production, hash the password!
                user.usertype = usertype
                user.updateddate = datetime.now(IST)
        else:  # Add new
            user = User(username=username, password=password, usertype=usertype, createddate=datetime.now(IST), updateddate=datetime.now(IST))
            db.session.add(user)
        db.session.commit()
        return redirect(url_for('users'))
    users = User.query.order_by(User.id).all()
    return render_template('users.html', users=users, user=user)

@app.route('/dashboard')
@login_required
def dashboard():
    from datetime import datetime
    today_str = datetime.now().strftime('%Y-%m-%d')
    from_date = request.args.get('from_date', today_str)
    to_date = request.args.get('to_date', today_str)
    from_dt = datetime.strptime(from_date, '%Y-%m-%d').date()
    to_dt = datetime.strptime(to_date, '%Y-%m-%d').date()

    # Filtered headers
    headers_query = HeaderEntry.query.filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt)

    # Total bills
    total_bills = headers_query.count()
    # Total amount (excluding cancelled)
    total_amount = db.session.query(db.func.sum(DetailsEntry.amount)).join(HeaderEntry).filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt, HeaderEntry.is_cancelled == False).scalar() or 0.0
    # Cancelled bills
    cancelled_bills = headers_query.filter(HeaderEntry.is_cancelled == True).count()
    cancelled_amount = db.session.query(db.func.sum(DetailsEntry.amount)).join(HeaderEntry).filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt, HeaderEntry.is_cancelled == True).scalar() or 0.0

    # Top categories by amount
    top_cat_query = db.session.query(Category.name, db.func.sum(DetailsEntry.amount))\
        .join(DetailsEntry, DetailsEntry.category_id == Category.id)\
        .join(HeaderEntry, DetailsEntry.header_id == HeaderEntry.id)\
        .filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt, HeaderEntry.is_cancelled == False)\
        .group_by(Category.name)\
        .order_by(db.func.sum(DetailsEntry.amount).desc())\
        .limit(5).all()
    top_categories = [(cat, amt or 0.0) for cat, amt in top_cat_query]

    # Top payment types by amount
    top_pay_query = db.session.query(PaymentType.name, db.func.sum(DetailsEntry.amount))\
        .join(DetailsEntry, DetailsEntry.payment_type_id == PaymentType.id)\
        .join(HeaderEntry, DetailsEntry.header_id == HeaderEntry.id)\
        .filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt, HeaderEntry.is_cancelled == False)\
        .group_by(PaymentType.name)\
        .order_by(db.func.sum(DetailsEntry.amount).desc())\
        .limit(5).all()
    top_payment_types = [(pt, amt or 0.0) for pt, amt in top_pay_query]

    # Recent bills (last 10)
    recent_headers = headers_query.order_by(HeaderEntry.id.desc()).limit(10).all()
    recent_bills = []
    for header in recent_headers:
        total_amt = sum(d.amount for d in header.details)
        recent_bills.append({
            'op_bill_no': header.op_bill_no,
            'patient_name': header.patient_name,
            'date': header.date,
            'total_amount': total_amt,
            'is_cancelled': header.is_cancelled
        })

    return render_template('dashboard.html',
        total_bills=total_bills,
        total_amount=total_amount,
        cancelled_bills=cancelled_bills,
        cancelled_amount=cancelled_amount,
        top_categories=top_categories,
        top_payment_types=top_payment_types,
        recent_bills=recent_bills,
        from_date=from_date,
        to_date=to_date
    )

# Date-wise detailed category report
@app.route('/daily_category_report')
@login_required
def daily_category_report():
    today_str = datetime.now().strftime('%Y-%m-%d')
    from_date = request.args.get('from_date', today_str)
    to_date = request.args.get('to_date', today_str)
    from_dt = datetime.strptime(from_date, '%Y-%m-%d').date()
    to_dt = datetime.strptime(to_date, '%Y-%m-%d').date()

    # Query: group by date, category, payment type
    results = db.session.query(
        HeaderEntry.date,
        Category.name.label('category'),
        PaymentType.name.label('payment_type'),
        db.func.sum(DetailsEntry.amount).label('total')
    )\
    .join(DetailsEntry, DetailsEntry.header_id == HeaderEntry.id)\
    .join(Category, DetailsEntry.category_id == Category.id)\
    .join(PaymentType, DetailsEntry.payment_type_id == PaymentType.id)\
    .filter(HeaderEntry.date >= from_dt, HeaderEntry.date <= to_dt, HeaderEntry.is_cancelled == False)\
    .group_by(HeaderEntry.date, Category.name, PaymentType.name)\
    .order_by(HeaderEntry.date, Category.name, PaymentType.name).all()

    # Organize data: {date: {category: {payment_type: total}}}
    data = {}
    payment_types = set()
    categories = set()
    for date, category, payment_type, total in results:
        payment_types.add(payment_type)
        categories.add(category)
        data.setdefault(date, {}).setdefault(category, {})[payment_type] = total or 0

    all_dates = sorted(data.keys())
    # Get all categories ordered by Category.id, but only those present in results
    all_categories = [c.name for c in Category.query.order_by(Category.id).all() if c.name in categories]
    all_payment_types = sorted(payment_types)

    printed_datetime = datetime.now().strftime('%d/%B/%Y %I:%M %p')
    return render_template(
        'daily_category_report.html',
        data=data,
        all_dates=all_dates,
        all_categories=all_categories,
        all_payment_types=all_payment_types,
        from_date=from_date,
        to_date=to_date,
        printed_datetime=printed_datetime
    )

@app.context_processor
def inject_usertype():
    return dict(session=session)

# Jinja2 filter for date formatting
@app.template_filter('datetime')
def format_datetime(value, format='%d/%B/%Y'):
    if not value:
        return ''
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d')
        except Exception:
            return value
    return value.strftime(format)

app.secret_key = 'your_secret_key_here'

BACKUP_DIR = os.path.join(os.path.dirname(__file__), 'instance', 'backups')
DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'lukes_billing_db.db')
LAST_BACKUP_FILE = os.path.join(os.path.dirname(__file__), 'instance', 'last_backup.txt')

@app.before_request
def daily_backup():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    today = datetime.now().strftime('%Y-%m-%d')
    last_backup = None
    if os.path.exists(LAST_BACKUP_FILE):
        with open(LAST_BACKUP_FILE, 'r') as f:
            last_backup = f.read().strip()
    if last_backup != today:
        # Backup DB
        backup_file = os.path.join(BACKUP_DIR, f'billing_{today}.db')
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, backup_file)
        # Update last backup date
        with open(LAST_BACKUP_FILE, 'w') as f:
            f.write(today)
        # Remove backups older than 7 days
        for fname in os.listdir(BACKUP_DIR):
            if fname.startswith('billing_') and fname.endswith('.db'):
                date_str = fname[len('billing_'):-3]
                try:
                    file_date = datetime.strptime(date_str, '%Y-%m-%d')
                    if (datetime.now() - file_date).days > 7:
                        os.remove(os.path.join(BACKUP_DIR, fname))
                except Exception:
                    pass

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
