from flask import Flask, render_template, request
from datetime import datetime, timedelta, time, date
from schedule_config import USER_NAME, ACADEMIC_WEEK1_START, SCHEDULE_ODD, SCHEDULE_EVEN

app = Flask(__name__)

DAYS = ["Luni", "Marti", "Miercuri", "Joi", "Vineri", "Sambata", "Duminica"]

def is_odd_week(check_date=None):
    if check_date is None:
        check_date = datetime.now().date()
    # Align to Mondays for week counting
    week1_monday = ACADEMIC_WEEK1_START - timedelta(days=ACADEMIC_WEEK1_START.weekday())
    curr_monday = check_date - timedelta(days=check_date.weekday())
    weeks_delta = (curr_monday - week1_monday).days // 7
    week_number = weeks_delta + 1  # week 1 is the academic start week
    return week_number % 2 == 1  # True => odd week


def schedule_for_date(d):
    return SCHEDULE_ODD if is_odd_week(d) else SCHEDULE_EVEN


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


def find_next_course_start(after_dt):
    # Search up to the next 14 days for the first course
    search_date = after_dt.date() + timedelta(days=1) if after_dt.time() > time(23, 50) else after_dt.date()
    for i in range(0, 14):
        day = search_date + timedelta(days=i)
        sched = schedule_for_date(day)
        dayname = DAYS[day.weekday()]
        blocks = sched.get(dayname, [])
        if not blocks:
            continue
        # first block start of that day
        first_start = datetime.combine(day, datetime.strptime(blocks[0][1], "%H:%M").time())
        if first_start > after_dt:
            return first_start
    # fallback: one day ahead at midnight
    return datetime.combine(after_dt.date() + timedelta(days=1), time(0, 0))


def compute_status(now_dt, timeline):
    current_block = None
    for idx, blk in enumerate(timeline):
        if blk['start_dt'] <= now_dt < blk['end_dt']:
            current_block = blk
            current_index = idx
            break
    if current_block is None:
        current_block = timeline[0]
        current_index = 0

    # default until current block end
    until_dt = current_block['end_dt']

    # If we're in the last break (end of day), show next day's first course instead of 23:59
    is_last_block = current_index == len(timeline) - 1
    if current_block['type'] == 'break' and is_last_block:
        until_dt = find_next_course_start(now_dt)

    minutes = minutes_until(until_dt, now_dt)
    end_str = until_dt.strftime('%H:%M')
    if current_block['type'] == 'course':
        status_main = f"{USER_NAME} are curs până la ora {end_str}."
    else:
        if until_dt.date() == (now_dt.date() + timedelta(days=1)):
            status_main = f"{USER_NAME} e liber până mâine la ora {end_str}."
        else:
            status_main = f"{USER_NAME} e liber până la ora {end_str}."

    if minutes < 60:
        status_sub = "Mai este 1 minut până atunci." if minutes == 1 else f"Mai sunt {minutes} minute până atunci."
    else:
        hh = minutes // 60
        mm = minutes % 60
        ore = "oră" if hh == 1 else "de ore"
        minute_txt = "minut" if mm == 1 else "de minute"
        status_sub = f"Mai sunt {hh} {ore} și {mm} {minute_txt} până atunci."

    return status_main, status_sub, current_block, until_dt


@app.route('/')
def index():
    date_str = request.args.get('date')
    real_now = datetime.now()
    
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            # Keep the current time but on the target date
            now = datetime.combine(target_date, real_now.time())
        except ValueError:
            now = real_now
    else:
        now = real_now

    week_is_odd = is_odd_week(now.date())
    schedule = SCHEDULE_ODD if week_is_odd else SCHEDULE_EVEN
    timeline = build_day_timeline(schedule, now.date())
    status_main, status_sub, current_block, _ = compute_status(now, timeline)

    # prepare blocks for template
    blocks_for_ui = []
    for blk in timeline:
        start = blk['start_dt'].strftime('%H:%M')
        end = blk['end_dt'].strftime('%H:%M')
        blocks_for_ui.append({
            'type': blk['type'],
            'title': blk.get('title', ''),
            'start': start,
            'end': end,
            'is_current': blk is current_block,
        })

    # Navigation dates
    prev_date = (now.date() - timedelta(days=1)).strftime('%Y-%m-%d')
    next_date = (now.date() + timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Romanian date formatting for display
    day_name = DAYS[now.weekday()]
    date_display = f"{day_name}, {now.day} {now.strftime('%b')}"

    context = {
        'user_name': USER_NAME,
        'week_label': "Săptămână impară" if week_is_odd else "Săptămână pară",
        'today': day_name, # Kept for backward compatibility if needed, but date_display is better
        'date_display': date_display,
        'now': now.strftime("%H:%M"),
        'status_main': status_main,
        'status_sub': status_sub,
        'blocks': blocks_for_ui,
        'prev_date': prev_date,
        'next_date': next_date,
        'is_today': now.date() == real_now.date()
    }
    return render_template('index.html', context=context)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=2025, debug=False)