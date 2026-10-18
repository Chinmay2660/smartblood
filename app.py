import os
import secrets
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()


def resolve_database_uri():
    """Neon on Vercel injects DATABASE_URL (pooled). Local dev falls back to SQLite."""
    for key in ("DATABASE_URL", "POSTGRES_URL"):
        raw = os.getenv(key, "").strip()
        if not raw:
            continue
        if raw.startswith("postgres://"):
            raw = "postgresql://" + raw[len("postgres://") :]
        if raw.startswith("postgresql://"):
            return "postgresql+psycopg://" + raw[len("postgresql://") :]
        return raw
    if os.getenv("VERCEL"):
        raise RuntimeError(
            "DATABASE_URL is required on Vercel. Install Neon from the Vercel Marketplace "
            "or set DATABASE_URL to your Neon pooled connection string."
        )
    return "sqlite:///smart_bloodlink.db"


_database_uri = resolve_database_uri()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-before-deployment")
app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
if _database_uri.startswith("postgresql"):
    # ponytail: one pool per serverless instance; Neon pooled URL + pre_ping avoids stale conns
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 300}
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
    hospital_id = db.Column(db.Integer, db.ForeignKey("hospital.id"), nullable=True)
    hospital_name = db.Column(db.String(120), nullable=False)
    result = db.Column(db.String(40), nullable=False)
    details = db.Column(db.String(255), default="")
    call_sid = db.Column(db.String(64), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    blood_request = db.relationship("BloodRequest", backref="alerts")

def twilio_credentials():
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    number = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
    return sid, token, number

def twilio_rest_client():
    """Use API key (SK…) if set; otherwise Account SID + primary Auth Token."""
    from twilio.rest import Client
    account_sid, auth_token, _ = twilio_credentials()
    api_key = os.getenv("TWILIO_API_KEY_SID", "").strip()
    api_secret = os.getenv("TWILIO_API_KEY_SECRET", "").strip()
    if api_key and api_secret:
        return Client(api_key, api_secret, account_sid)
    return Client(account_sid, auth_token)

def public_base_url():
    url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not url:
        vercel_host = os.getenv("VERCEL_URL", "").strip()
        if vercel_host:
            url = f"https://{vercel_host}".rstrip("/")
    return url

# Twilio Console “Try out Voice” templates (allowed on trial accounts).
TWILIO_TRIAL_VOICE_TEMPLATE = (
    "https://webhooks.twilio.com/v1/Voice/Template/voice_speech_recognition"
)

def twilio_trial_voice_url():
    """When set, outbound calls use Twilio’s trial template URL instead of /voice/…"""
    explicit = os.getenv("TWILIO_TRIAL_VOICE_URL", "").strip()
    if explicit:
        return explicit
    flag = os.getenv("TWILIO_USE_TRIAL_VOICE", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return TWILIO_TRIAL_VOICE_TEMPLATE
    return ""

def twilio_outbound_voice_url(request_id, hospital_id, public_url):
    trial = twilio_trial_voice_url()
    if trial:
        return trial
    return f"{public_url}/voice/{request_id}/{hospital_id}"

def twilio_calls_enabled():
    sid, token, number = twilio_credentials()
    api_key = os.getenv("TWILIO_API_KEY_SID", "").strip()
    api_secret = os.getenv("TWILIO_API_KEY_SECRET", "").strip()
    has_api_auth = bool(api_key and api_secret)
    has_token_auth = bool(token)
    has_webhooks = bool(public_base_url())
    has_trial_voice = bool(twilio_trial_voice_url())
    return bool(
        sid and (has_api_auth or has_token_auth) and number and (has_webhooks or has_trial_voice)
    )

def place_outbound_call(client, to, from_, voice_url, public_url):
    """Trial accounts reject Method / StatusCallbackMethod on Calls.create."""
    minimal = {"to": to, "from_": from_, "url": voice_url}
    if voice_url.startswith("https://webhooks.twilio.com/"):
        return client.calls.create(**minimal)
    with_status = {**minimal, "status_callback": f"{public_url}/call-status"}
    try:
        return client.calls.create(**with_status)
    except Exception as exc:
        msg = str(exc).lower()
        if "limited parameter access" not in msg and "disallowed parameters" not in msg:
            raise
        # ponytail: trial allows To, Url, optional StatusCallback — retry bare minimum
        return client.calls.create(**minimal)

def twilio_request_is_valid():
    """Validate X-Twilio-Signature when Auth Token is configured."""
    _, token, _ = twilio_credentials()
    if not token:
        return True
    from twilio.request_validator import RequestValidator
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        return False
    validator = RequestValidator(token)
    # Behind reverse proxies, request.url may differ from the URL Twilio called.
    callback_url = public_base_url()
    if callback_url:
        path = request.full_path.rstrip("?") if request.query_string else request.path
        url = f"{callback_url}{path}"
    else:
        url = request.url
    params = request.form.to_dict(flat=False)
    flat = {k: (v[0] if len(v) == 1 else v) for k, v in params.items()}
    return validator.validate(url, flat, signature)

def hospitals_for_request(blood_request, all_hospitals):
    """Hospitals in the request city; if none match, fall back to the full network."""
    matching = [h for h in all_hospitals if h.city.lower() == blood_request.city.lower()]
    return matching if matching else list(all_hospitals)

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
    alert_logs = AlertLog.query.order_by(AlertLog.created_at.desc()).limit(12).all()
    request_alert_targets = {
        r.id: hospitals_for_request(r, hospitals) for r in requests_list
    }
    return render_template(
        "index.html",
        hospitals=hospitals,
        requests=requests_list,
        request_alert_targets=request_alert_targets,
        low_stock=low_stock,
        stats=stats,
        blood_groups=BLOOD_GROUPS,
        alert_logs=alert_logs,
        real_calls_enabled=twilio_calls_enabled(),
        trial_voice_mode=bool(twilio_trial_voice_url()),
    )

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

@app.route("/hospital/<int:hospital_id>/verify", methods=["POST"])
def verify_hospital(hospital_id):
    expected = os.getenv("ADMIN_VERIFY_KEY", "").strip()
    provided = request.form.get("admin_key", "")
    if not expected:
        flash("Hospital verification is disabled. Set ADMIN_VERIFY_KEY in .env first.", "error")
        return redirect(url_for("index"))
    if not secrets.compare_digest(provided, expected):
        flash("Invalid administrator key.", "error")
        return redirect(url_for("index"))
    hospital = db.session.get(Hospital, hospital_id)
    if not hospital:
        flash("Hospital not found.", "error")
        return redirect(url_for("index"))
    hospital.verified = True
    db.session.commit()
    flash(f"{hospital.name} marked as verified for outbound alerts.", "success")
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
    flash(f"Blood request #{blood_request.id} created. Select a hospital and use “Alert hospital” to notify.", "success")
    return redirect(url_for("index"))

@app.route("/request/<int:request_id>/alert", methods=["POST"])
def alert_hospitals(request_id):
    blood_request = db.session.get(BloodRequest, request_id)
    if not blood_request:
        flash("Blood request not found.", "error")
        return redirect(url_for("index"))

    try:
        hospital_id = int(request.form.get("hospital_id", ""))
    except ValueError:
        flash("Please select a hospital to alert.", "error")
        return redirect(url_for("index"))

    all_hospitals = Hospital.query.order_by(Hospital.name).all()
    if not all_hospitals:
        flash("No hospitals have been added yet.", "error")
        return redirect(url_for("index"))

    eligible = hospitals_for_request(blood_request, all_hospitals)
    eligible_ids = {h.id for h in eligible}
    hospital = db.session.get(Hospital, hospital_id)
    if not hospital or hospital.id not in eligible_ids:
        flash("Choose a hospital in the request’s city (or any hospital if none match that city).", "error")
        return redirect(url_for("index"))

    _, _, twilio_number = twilio_credentials()
    public_url = public_base_url()
    real_calls_enabled = twilio_calls_enabled()

    stock = BloodStock.query.filter_by(
        hospital_id=hospital.id, blood_group=blood_request.blood_group
    ).first()
    stock_units = stock.units if stock else 0
    called = False

    if real_calls_enabled and hospital.verified:
        try:
            client = twilio_rest_client()
            voice_url = twilio_outbound_voice_url(
                blood_request.id, hospital.id, public_url
            )
            call = place_outbound_call(
                client, hospital.phone, twilio_number, voice_url, public_url
            )
            if twilio_trial_voice_url():
                call_details = (
                    "Trial template call placed (Twilio demo script, not the blood-request message). "
                    "Unset TWILIO_USE_TRIAL_VOICE after upgrade for full /voice alerts."
                )
            else:
                call_details = "Awaiting hospital response"
            db.session.add(AlertLog(
                request_id=blood_request.id,
                hospital_id=hospital.id,
                hospital_name=hospital.name,
                result="Call placed",
                details=call_details,
                call_sid=call.sid,
            ))
            called = True
        except Exception as exc:
            detail = str(exc)[:220]
            if "limited parameter access" in detail.lower():
                detail = (
                    "Twilio trial blocked this call. Verify recipient numbers in Twilio, "
                    "call within your trial country, or upgrade the account. "
                    f"({detail[:120]})"
                )
            db.session.add(
                AlertLog(
                    request_id=blood_request.id,
                    hospital_name=hospital.name,
                    result="Call failed",
                    details=detail,
                )
            )
    else:
        result = "Demo alert"
        if real_calls_enabled and not hospital.verified:
            details = (
                f"Hospital not verified — no call placed. "
                f"Inventory listed: {stock_units} unit(s) of {blood_request.blood_group}. "
                f"Use “Verify hospital” in the network section (requires ADMIN_VERIFY_KEY)."
            )
        else:
            details = (
                f"Inventory listed: {stock_units} unit(s) of {blood_request.blood_group}. "
                f"No call placed (Twilio or public URL not configured)."
            )
        db.session.add(AlertLog(
            request_id=blood_request.id,
            hospital_name=hospital.name,
            result=result,
            details=details,
        ))
    db.session.commit()
    if real_calls_enabled:
        if called:
            flash(
                f"Call placed to {hospital.name}. Check alert history and confirm the response manually.",
                "success",
            )
        elif hospital.verified:
            flash(f"Call to {hospital.name} failed. See alert history for details.", "error")
        else:
            flash(
                f"{hospital.name} is not verified for outbound calls. "
                "Verify the hospital in the network section, then try again.",
                "error",
            )
    else:
        flash(
            f"Demo alert logged for {hospital.name}. No real phone call was placed.",
            "success",
        )
    return redirect(url_for("index"))

@app.route("/voice/<int:request_id>/<int:hospital_id>", methods=["GET", "POST"])
def voice_prompt(request_id, hospital_id):
    from flask import Response
    from xml.sax.saxutils import escape
    if not twilio_request_is_valid():
        return Response("<Response><Reject/></Response>", mimetype="text/xml", status=403)
    blood_request = db.session.get(BloodRequest, request_id)
    hospital = db.session.get(Hospital, hospital_id)
    if not blood_request or not hospital:
        return Response("<Response><Say>Request not found. Goodbye.</Say></Response>", mimetype="text/xml")
    base = public_base_url()
    respond_url = f"{base}/voice/{request_id}/{hospital_id}/respond"
    intro = (
        f"Hello {hospital.name}. This is Smart BloodLink with an urgent blood request. "
        f"We need {blood_request.units} units of {blood_request.blood_group} blood in {blood_request.city}. "
        f"Requester contact number is {blood_request.contact_phone}. "
        f"If you can supply this blood, press 1. If not available, press 2."
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Gather numDigits="1" action="{escape(respond_url)}" method="POST" timeout="8">'
        f'<Say voice="alice">{escape(intro)}</Say>'
        "</Gather>"
        "<Say>We did not receive a response. Please call the requesting hospital directly. Goodbye.</Say>"
        "</Response>"
    )
    return Response(xml, mimetype="text/xml")

@app.route("/voice/<int:request_id>/<int:hospital_id>/respond", methods=["POST"])
def voice_respond(request_id, hospital_id):
    from flask import Response
    from xml.sax.saxutils import escape
    if not twilio_request_is_valid():
        return Response("<Response><Reject/></Response>", mimetype="text/xml", status=403)
    digit = request.form.get("Digits", "")
    call_sid = request.form.get("CallSid", "")
    blood_request = db.session.get(BloodRequest, request_id)
    hospital = db.session.get(Hospital, hospital_id)
    log = None
    if call_sid:
        log = AlertLog.query.filter_by(call_sid=call_sid).first()
    if not log:
        log = AlertLog.query.filter_by(request_id=request_id, hospital_id=hospital_id).order_by(
            AlertLog.created_at.desc()
        ).first()
    if digit == "1":
        reply = "Thank you. Your response is recorded as available. A coordinator may call you shortly."
        if log:
            log.result = "Available"
            log.details = f"Hospital pressed 1 (can supply {blood_request.blood_group if blood_request else 'blood'})"
    elif digit == "2":
        reply = "Thank you. Your response is recorded as not available. Goodbye."
        if log:
            log.result = "Not available"
            log.details = "Hospital pressed 2 (cannot supply)"
    else:
        reply = "Invalid input. Please contact the requesting hospital directly. Goodbye."
        if log:
            log.result = "No valid response"
            log.details = f"Unexpected keypad input: {digit or 'none'}"
    if log:
        db.session.commit()
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="alice">{escape(reply)}</Say></Response>'
    return Response(xml, mimetype="text/xml")

@app.route("/call-status", methods=["POST"])
def call_status():
    if not twilio_request_is_valid():
        return ("Forbidden", 403)
    status = request.form.get("CallStatus", "unknown")
    sid = request.form.get("CallSid", "")
    log = AlertLog.query.filter_by(call_sid=sid).first() if sid else None
    if log and status in {"busy", "failed", "no-answer", "canceled"}:
        log.result = "Call failed"
        log.details = f"Twilio status: {status}"
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

def ensure_alert_log_columns():
    """create_all does not add new columns on existing databases."""
    from sqlalchemy import inspect, text
    if not inspect(db.engine).has_table("alert_log"):
        return
    cols = {c["name"] for c in inspect(db.engine).get_columns("alert_log")}
    dialect = db.engine.dialect.name
    additions = []
    if "hospital_id" not in cols:
        additions.append(
            "ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS hospital_id INTEGER"
            if dialect == "postgresql"
            else "ALTER TABLE alert_log ADD COLUMN hospital_id INTEGER"
        )
    if "call_sid" not in cols:
        additions.append(
            "ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS call_sid VARCHAR(64) DEFAULT ''"
            if dialect == "postgresql"
            else "ALTER TABLE alert_log ADD COLUMN call_sid VARCHAR(64) DEFAULT ''"
        )
    for stmt in additions:
        db.session.execute(text(stmt))
    if additions:
        db.session.commit()

with app.app_context():
    db.create_all()
    ensure_alert_log_columns()
    seed_demo_data()

if __name__ == "__main__":
    app.run(debug=True)
