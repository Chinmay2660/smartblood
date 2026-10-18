import os
import hmac
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-before-deployment")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///smart_bloodlink.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]

class Hospital(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    city = db.Column(db.String(80), nullable=False, default="Mumbai")
    phone = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(120), default="")
    verified = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    stocks = db.relationship("BloodStock", backref="hospital", cascade="all, delete-orphan")

class BloodStock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospital.id"), nullable=False)
    blood_group = db.Column(db.String(5), nullable=False)
    units = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint("hospital_id", "blood_group", name="unique_hospital_blood_group"),)

class BloodRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(120), nullable=False)
    blood_group = db.Column(db.String(5), nullable=False)
    units = db.Column(db.Integer, nullable=False, default=1)
    city = db.Column(db.String(80), nullable=False, default="Mumbai")
    contact_phone = db.Column(db.String(30), nullable=False)
    urgency = db.Column(db.String(20), nullable=False, default="Urgent")
    status = db.Column(db.String(30), nullable=False, default="Open")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AlertLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("blood_request.id"), nullable=False)
    hospital_name = db.Column(db.String(120), nullable=False)
    result = db.Column(db.String(40), nullable=False)
    details = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    blood_request = db.relationship("BloodRequest", backref="alerts")

def seed_demo_data():
    if Hospital.query.count():
        return
    demo_hospitals = [
        ("Dishant Bhoir", "Mumbai", "+919325427119", "demo-care@example.com", False),
        ("Asmi Chande", "Mumbai", "+919167102509", "demo-lifeline@example.com", False),
        ("Akshay More", "Navi Mumbai", "+917304355117", "demo-greencross@example.com", False),
    ]
    for name, city, phone, email, verified in demo_hospitals:
        h = Hospital(name=name, city=city, phone=phone, email=email, verified=verified)
        db.session.add(h)
        db.session.flush()
        values = {"A+": 6, "A-": 2, "B+": 8, "B-": 1, "AB+": 3, "AB-": 0, "O+": 10, "O-": 2}
        for group, units in values.items():
            db.session.add(BloodStock(hospital_id=h.id, blood_group=group, units=units))
    db.session.commit()

@app.route("/")
def index():
    hospitals = Hospital.query.order_by(Hospital.name).all()
    requests_list = BloodRequest.query.order_by(BloodRequest.created_at.desc()).limit(8).all()
    low_stock = BloodStock.query.filter(BloodStock.units <= 2).all()
    total_units = db.session.query(db.func.sum(BloodStock.units)).scalar() or 0
    stats = {
        "hospitals": Hospital.query.count(),
        "open_requests": BloodRequest.query.filter_by(status="Open").count(),
        "low_stock": len(low_stock),
        "units": total_units,
    }
    return render_template("index.html", hospitals=hospitals, requests=requests_list,
                           low_stock=low_stock, stats=stats, blood_groups=BLOOD_GROUPS)

@app.route("/hospital/add", methods=["POST"])
def add_hospital():
    name = request.form.get("name", "").strip()
    city = request.form.get("city", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    if not name or not city or not phone:
        flash("Please enter hospital name, city and phone number.", "error")
        return redirect(url_for("index"))
    hospital = Hospital(name=name, city=city, phone=phone, email=email, verified=False)
    db.session.add(hospital)
    db.session.flush()
    for group in BLOOD_GROUPS:
        db.session.add(BloodStock(hospital_id=hospital.id, blood_group=group, units=0))
    db.session.commit()
    flash("Hospital added. Its contact details must be verified before real alerts are enabled.", "success")
    return redirect(url_for("index"))

@app.route("/stock/update", methods=["POST"])
def update_stock():
    try:
        hospital_id = int(request.form.get("hospital_id", ""))
        units = int(request.form.get("units", ""))
    except ValueError:
        flash("Please choose a hospital and enter a valid unit count.", "error")
        return redirect(url_for("index"))
    group = request.form.get("blood_group", "")
    if group not in BLOOD_GROUPS or units < 0 or units > 10000:
        flash("Enter a valid blood group and a non-negative unit count.", "error")
        return redirect(url_for("index"))
    stock = BloodStock.query.filter_by(hospital_id=hospital_id, blood_group=group).first()
    if not stock:
        flash("Stock record not found.", "error")
        return redirect(url_for("index"))
    stock.units = units
    db.session.commit()
    flash(f"{group} stock updated successfully.", "success")
    return redirect(url_for("index"))

@app.route("/request/add", methods=["POST"])
def add_request():
    name = request.form.get("patient_name", "").strip()
    group = request.form.get("blood_group", "")
    city = request.form.get("city", "").strip()
    phone = request.form.get("contact_phone", "").strip()
    urgency = request.form.get("urgency", "Urgent")
    try:
        units = int(request.form.get("units", "1"))
    except ValueError:
        units = 0
    if not name or not city or not phone or group not in BLOOD_GROUPS or not 1 <= units <= 20:
        flash("Please complete all request fields with valid values.", "error")
        return redirect(url_for("index"))
    blood_request = BloodRequest(patient_name=name, blood_group=group, units=units,
                                 city=city, contact_phone=phone, urgency=urgency)
    db.session.add(blood_request)
    db.session.commit()
    flash(f"Blood request #{blood_request.id} created. Use “Alert hospitals” to notify matching hospitals.", "success")
    return redirect(url_for("index"))

@app.route("/request/<int:request_id>/alert", methods=["POST"])
def alert_hospitals(request_id):
    blood_request = db.session.get(BloodRequest, request_id)
    if not blood_request:
        flash("Blood request not found.", "error")
        return redirect(url_for("index"))

    # Safe demo mode: no phone calls are placed unless Twilio credentials and a public callback URL exist.
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    twilio_number = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
    public_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    real_calls_enabled = all([twilio_sid, twilio_token, twilio_number, public_url])



    matching = Hospital.query.filter(db.func.lower(Hospital.city) == blood_request.city.lower()).all()
    if not matching:
        matching = Hospital.query.all()
    if not matching:
        flash("No hospitals have been added yet.", "error")
        return redirect(url_for("index"))

    called = 0
    for hospital in matching:
        stock = BloodStock.query.filter_by(hospital_id=hospital.id, blood_group=blood_request.blood_group).first()
        stock_units = stock.units if stock else 0
        if real_calls_enabled and hospital.verified:
            try:
                from twilio.rest import Client
                client = Client(twilio_sid, twilio_token)
                twiml_url = f"{public_url}/voice/{blood_request.id}/{hospital.id}"
                call = client.calls.create(
                    to=hospital.phone,
                    from_=twilio_number,
                    url=twiml_url,
                    method="POST",
                    status_callback=f"{public_url}/call-status",
                    status_callback_method="POST"
                )
                db.session.add(AlertLog(request_id=blood_request.id, hospital_name=hospital.name,
                                        result="Call placed", details=f"Call SID: {call.sid}"))
                called += 1
            except Exception as exc:
                db.session.add(AlertLog(request_id=blood_request.id, hospital_name=hospital.name,
                                        result="Call failed", details=str(exc)[:220]))
        else:
            result = "Demo alert"
            details = f"Inventory listed: {stock_units} unit(s) of {blood_request.blood_group}. No call placed."
            db.session.add(AlertLog(request_id=blood_request.id, hospital_name=hospital.name,
                                    result=result, details=details))
    db.session.commit()
    if real_calls_enabled:
        flash(f"Alert process completed. Real calls attempted to {called} verified hospital(s); check alert history and confirm responses manually.", "success")
    else:
        flash("Demo alerts created. No real phone calls were placed. Add Twilio settings and a public HTTPS URL to enable calling.", "success")
    return redirect(url_for("index"))

@app.route("/voice/<int:request_id>/<int:hospital_id>", methods=["GET", "POST"])
def voice_prompt(request_id, hospital_id):
    from flask import Response
    from xml.sax.saxutils import escape
    blood_request = db.session.get(BloodRequest, request_id)
    hospital = db.session.get(Hospital, hospital_id)
    if not blood_request or not hospital:
        return Response("<Response><Say>Request not found. Goodbye.</Say></Response>", mimetype="text/xml")
    message = (f"Hello {hospital.name}. Smart BloodLink has an urgent blood request. "
               f"{blood_request.units} unit or units of {blood_request.blood_group} blood are requested in "
               f"{blood_request.city}. Please check your verified inventory and contact the requester or the "
               f"hospital coordination team. This automated message is not a confirmation of availability. Goodbye.")
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="alice">{escape(message)}</Say></Response>'
    return Response(xml, mimetype="text/xml")

@app.route("/call-status", methods=["POST"])
def call_status():
    # Basic callback endpoint. Validate Twilio webhook signatures before production use.
    status = request.form.get("CallStatus", "unknown")
    sid = request.form.get("CallSid", "")
    log = AlertLog.query.filter(AlertLog.details.contains(sid)).first() if sid else None
    if log:
        log.details = f"Call SID: {sid}; status: {status}"
        db.session.commit()
    return ("", 204)

@app.route("/api/summary")
def api_summary():
    return jsonify({
        "hospitals": Hospital.query.count(),
        "open_requests": BloodRequest.query.filter_by(status="Open").count(),
        "low_stock": BloodStock.query.filter(BloodStock.units <= 2).count(),
        "total_units": db.session.query(db.func.sum(BloodStock.units)).scalar() or 0
    })

with app.app_context():
    db.create_all()
    seed_demo_data()

if __name__ == "__main__":
    app.run(debug=True)
