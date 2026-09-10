from datetime import date, datetime
from icalendar import Calendar, Event
from flask import Blueprint, Response, request, abort, render_template
from collections import defaultdict

from .auth import *
from .logic import get_user, create_user, user_exists, parse_job_period, build_description, build_pay_day
from .extensions import limiter, db

bp = Blueprint("routes", __name__)

STANDARD_PAY = 13

@bp.route("/", methods=['GET'])
def index():
    return render_template("index.html")

@bp.route("/token", methods=['POST'])
@limiter.limit("10 per 10 minutes")
def get_token():
    creds = request.get_json(silent=True) or {}
    username = (creds.get("username") or "").strip()
    password = creds.get("password") or ""

    if not (username and password):
        abort(400, description="A username and password are required")

    if user := user_exists(username):
        return user.create_token(password)

    new_user = create_user(username, password)

    db.session.commit()
    return new_user.create_token(password)

@bp.route("/calendar", methods=['GET'])
@limiter.limit("100 per hour")
def calendar_feed():
    token = request.args.get("token", "")
    user = get_user(token)
    if user is None:
        abort(401, "Invalid Credentials")

    jobs = user.get_jobs()

    cal = Calendar()
    cal.add("prodid", "-//HS Staff Calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "High Society")

    monthly_pay = defaultdict(lambda: [0.0, 0.0, 0])

    for job in jobs:
        event = Event()

        if parsed_job := parse_job_period(job):
            start_date, end_date, hour_length = parsed_job
        else:
            continue

        rate = float(job.get("StaffRate") or STANDARD_PAY)
        pay = rate * hour_length

        details = build_description(job, hour_length, rate, pay)

        # Add payment to monthly total
        totals = monthly_pay[(start_date.year, start_date.month)]
        totals[0] += pay
        totals[1] += hour_length
        totals[2] += 1

        event.add("summary", job.get("Client", ""))
        event.add("location", job.get("Venue", ""))
        event.add("dtstart", start_date)
        event.add("dtend", end_date)
        event.add("dtstamp", datetime.now())
        event.add("description", details)
        event.add("url", job.get("MapLink", ""))
        event.add("uid", job.get("EventID", ""))

        cal.add_component(event)

    for (year, month), (pay, hours, shifts) in monthly_pay.items():
        if hours <= 0: continue
        pay_month = date(year + (month == 12), month % 12 + 1, 1)
        cal.add_component(build_pay_day(pay_month, pay, hours, shifts))

    user.mark_synced()
    db.session.commit()
    return Response(cal.to_ical(), mimetype="text/calendar")

@bp.route("/raw", methods=['GET'])
@limiter.limit("100 per hour")
def raw_job_data():
    token = request.args.get("token", "")
    user = get_user(token)
    if user is None:
        abort(401, "Invalid Credentials")

    return user.get_jobs()

