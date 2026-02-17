from flask import Flask, render_template, request
from datetime import datetime, timedelta, time, date

app = Flask(__name__)

# --- CONFIGURABLES ---
ACADEMIC_WEEK1_START = date(2025, 10, 29)  # S-a pornit in 29.10.2025

SCHEDULE_ODD = {
    'Luni': [
        ("Calculus II (S) | AN204 ", "08:00", "10:00"),
        ("EE2 (S) | CB020 ", "10:00", "12:00"),
        ("API (C) | AN015 ", "14:00", "16:00"),
    ],
    'Marti': [
        ("Physics 1 (S) | AN202 ", "08:00", "10:00"),
        ("Prof Com (S) | CJ06 ", "10:00", "12:00"),
        ("Sport (L) | Sports Hall ", "14:00", "16:00"),
    ],
    'Miercuri': [
        ("ED (C) | NaN ", "08:00", "10:00"),
        ("EE2 (C) | CB105 ", "10:00", "12:00"),
        ("Physics 1 (L) | BN122A ", "12:00", "14:00"),
    ],
    'Joi': [
        ("Physics 1 (C) | AN024 ", "10:00", "12:00"),
    ],
    'Vineri': [
        ("API (L) | JA001A ", "08:00", "10:00"),
        ("Calculus II (C) | AN015 ", "10:00", "12:00"),
        ("DSA (C) | AN015 ", "12:00", "14:00"),
        ("DSA (L) | JA001A ", "14:00", "16:00"),
    ],
    'Sambata': [],
    'Duminica': [],
}

SCHEDULE_EVEN = {
    'Luni': [
        ("Calculus II (S) | AN204 ", "08:00", "10:00"),
        ("EE2 (S) | CB020 ", "10:00", "12:00"),
        ("API (C) | AN015 ", "14:00", "16:00"),
    ],
    'Marti': [
        ("Prof Com (S) | CJ06 ", "10:00", "12:00"),
    ],
    'Miercuri': [
        ("ED (C) | NaN ", "08:00", "10:00"),
        ("EE2 (C) | CB105 ", "10:00", "12:00"),
        ("ED (S) | AN204 ", "12:00", "14:00"),
        ("ED (L) | CB020 ", "16:00", "18:00"),
    ],
    'Joi': [
        ("Physics 1 (C) | AN024 ", "10:00", "12:00"),
    ],
    'Vineri': [
        ("API (L) | JA001A ", "08:00", "10:00"),
        ("Calculus II (C) | AN015 ", "10:00", "12:00"),
        ("DSA (C) | AN015 ", "12:00", "14:00"),
        ("DSA (L) | JA001A ", "14:00", "16:00"),
    ],
    'Sambata': [],
    'Duminica': [],
}

DAYS = ["Luni", "Marti", "Miercuri", "Joi", "Vineri", "Sambata", "Duminica"]


def is_odd_week(check_date=None):
    if check_date is None:
        check_date = datetime.now().date()
    week_num = (check_date - ACADEMIC_WEEK1_START).days // 7
    return week_num % 2 == 0  # 0-based: first academic week = impar (odd)


def build_day_timeline(schedule, target_date=None):
    if target_date is None:
        target_date = datetime.now().date()
    dayname = DAYS[target_date.weekday()]
    blocks = schedule.get(dayname, [])
    # convert to datetime intervals
    intervals = []
    for title, start, end in blocks:
        s = datetime.combine(target_date, datetime.strptime(start, "%H:%M").time())
        e = datetime.combine(target_date, datetime.strptime(end, "%H:%M").time())
        intervals.append({
            'type': 'course',
            'title': title,
            'start_dt': s,
            'end_dt': e,
        })
    intervals.sort(key=lambda x: x['start_dt'])
    # insert breaks between, plus optional pre/post breaks
    full = []
    current = datetime.combine(target_date, time(hour=0, minute=0))
    end_of_day = datetime.combine(target_date, time(hour=23, minute=59))
    for iv in intervals:
        if iv['start_dt'] > current:
            full.append({
                'type': 'break',
                'start_dt': current,
                'end_dt': iv['start_dt'],
            })
        full.append(iv)
        current = iv['end_dt']
    if current < end_of_day:
        full.append({
            'type': 'break',
            'start_dt': current,
            'end_dt': end_of_day,
        })
    return full


def minutes_until(target_dt, now_dt):
    delta = target_dt - now_dt
    seconds = max(0, int(delta.total_seconds()))
    minutes = (seconds + 59) // 60  # ceil to next minute
    return minutes


def compute_status(now_dt, timeline):
    current_block = None
    for blk in timeline:
        if blk['start_dt'] <= now_dt < blk['end_dt']:
            current_block = blk
            break
    if current_block is None:
        # before first block (should not happen due to pre-break), fallback to next
        current_block = timeline[0]
    until_dt = current_block['end_dt']
    minutes = minutes_until(until_dt, now_dt)
    end_str = until_dt.strftime('%H:%M')
    if current_block['type'] == 'course':
        status_main = f"Andrei are curs până la ora {end_str}."
    else:
        status_main = f"Andrei e liber până la ora {end_str}."
    status_sub = f"Mai sunt {minutes} minute până atunci." if minutes != 1 else "Mai este 1 minut până atunci."
    return status_main, status_sub, current_block


@app.route('/')
def index():
    # Determine view date
    date_str = request.args.get('date')
    if date_str:
        try:
            view_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            view_date = datetime.now().date()
    else:
        view_date = datetime.now().date()

    now = datetime.now()
    # "now" for logic is only relevant if we are viewing today
    # But we still need "now" to compute is_odd_week based on academic calendar relative to *view_date*?
    # Actually, is_odd_week(check_date) uses check_date. So we should pass view_date.
    
    week_is_odd = is_odd_week(view_date)
    schedule = SCHEDULE_ODD if week_is_odd else SCHEDULE_EVEN
    timeline = build_day_timeline(schedule, view_date)
    
    # Status is only relevant if view_date is today
    if view_date == now.date():
        status_main, status_sub, current_block = compute_status(now, timeline)
    else:
        status_main = f"Program pentru {view_date.strftime('%d.%m.%Y')}"
        status_sub = ""
        current_block = None

    # prepare blocks for template
    blocks_for_ui = []
    for blk in timeline:
        start = blk['start_dt'].strftime('%H:%M')
        end = blk['end_dt'].strftime('%H:%M')
        # split title into subject and room if possible
        full_title = blk.get('title', '')
        if ' | ' in full_title:
            parts = full_title.split(' | ', 1)
            subject = parts[0]
            room = parts[1]
        else:
            subject = full_title
            room = ''

        blocks_for_ui.append({
            'type': blk['type'],
            'title': full_title,
            'subject': subject,
            'room': room,
            'start': start,
            'end': end,
            'is_current': blk is current_block,
        })

    prev_date = view_date - timedelta(days=1)
    next_date = view_date + timedelta(days=1)

    context = {
        'week_label': "Săptămână impară" if week_is_odd else "Săptămână pară",
        'today_label': DAYS[view_date.weekday()],
        'view_date_str': view_date.strftime("%d %b %Y"), # e.g. 17 Feb 2026
        'view_date_iso': view_date.strftime("%Y-%m-%d"),
        'now': now.strftime("%H:%M"),
        'status_main': status_main,
        'status_sub': status_sub,
        'blocks': blocks_for_ui,
        'prev_link': f"?date={prev_date.strftime('%Y-%m-%d')}",
        'next_link': f"?date={next_date.strftime('%Y-%m-%d')}",
    }
    return render_template('index.html', context=context)

if __name__ == '__main__':
    app.run(debug=True, port=2026)

