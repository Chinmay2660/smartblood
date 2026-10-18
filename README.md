# Smart BloodLink

A beginner-friendly Flask prototype for coordinating urgent blood requests with a hospital network.

## Features
- Dashboard with hospitals, requests, low-stock entries, and listed blood units
- Create an urgent blood request
- Add hospital records and update blood inventory
- Demo alert history (does not place calls by default)
- Optional Twilio voice-call integration for verified hospitals
- SQLite database created automatically

## Run locally
1. Install Python 3.10 or newer.
2. Open this folder in VS Code.
3. In the VS Code terminal, create a virtual environment (recommended):

   **Windows**
   ```bash
   py -m venv .venv
   .venv\Scripts\activate
   ```

   **macOS/Linux**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Copy `.env.example` to `.env`.
6. Start the app:
   ```bash
   python app.py
   ```
7. Open http://127.0.0.1:5000 in your browser.

The first run creates `smart_bloodlink.db` and adds sample hospitals and sample inventory. The sample data is fictional; replace it with verified information.

## Optional Twilio calling
Real calls require a Twilio account, approved/authorized recipient numbers, account credentials, a public HTTPS URL, and compliance with applicable calling/privacy rules. Put credentials only in `.env`; never publish them. Set all four Twilio settings in `.env`. Only hospital records marked verified are eligible for real calls. In this starter, a call plays a scripted announcement; it is not a full conversational AI agent and it does not automatically confirm blood availability.

**Before any public/production deployment, add authentication and role-based access, CSRF protection, Twilio webhook signature validation, audit logging, rate limits, consent/legal review, encryption and a secure patient-data policy.** The demo verification status is only a manual database flag, not proof of a real hospital's identity. Do not use this prototype for clinical decisions. Always confirm inventory and compatibility directly with qualified hospital/blood-bank staff. In a medical emergency, contact local emergency services and the treating hospital.


## Configure real Twilio voice calls (carefully)

The project already includes the Twilio call route and the `twilio` package. Real calls are disabled unless all four values below are configured:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `PUBLIC_BASE_URL` (a publicly reachable HTTPS URL)

### Setup

1. In this folder, copy `.env.example` to a file named `.env`.
2. Put your real Twilio Account SID, Auth Token, and Twilio voice-capable phone number into `.env`. Do not share the `.env` file or commit it to GitHub.
3. For `PUBLIC_BASE_URL`, use your deployed HTTPS address, or a temporary HTTPS tunnel to your local Flask server. A URL such as `http://127.0.0.1:5000` is not publicly reachable by Twilio.
4. Restart Flask after changing `.env`.
5. First test with a phone number you own and are authorized to call. Trial accounts may require that recipient number to be verified.
6. Only mark a real hospital as verified after confirming its identity and phone number. The included sample hospitals are fictional and are deliberately unverified.

### Important safety notes

- Clicking **Alert hospitals** can place real phone calls once the required settings exist and matching hospital records are verified.
- Do not use the fictional demo hospital records for real calls. Replace them with contacts confirmed directly with the hospitals.
- Make sure recipients have agreed to receive these alerts, and follow Twilio's account, local calling, privacy, and consent requirements.
- The current `/call-status` route notes that Twilio webhook signatures still need validation. Add signature validation, authentication/authorization, CSRF protection, rate limiting, and audit controls before public deployment.
- The voice message is a scripted announcement; it does not confirm blood availability. Always verify inventory with qualified hospital/blood-bank staff.
