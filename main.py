import base64
import json
import logging
import os
import queue
import re
import yaml
import requests
import threading

from datetime import datetime
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, request, Response, render_template, abort, send_from_directory, url_for, redirect, session, \
    flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_MAX_INSTANCES

import libraries.logger as logger
import libraries.ntfy as ntfy
import libraries.stagerApi as stagerApi
import libraries.neushoorn_scraper as scrp

log = logger.file_logger()
log.initialize('Main')
log.info("Logging Initialized!")

app = Flask(__name__)

# Create a scheduler instance
scheduler = BackgroundScheduler(daemon=True)


apscheduler_logger = logging.getLogger('apscheduler')
apscheduler_logger.setLevel(logging.ERROR)  # Hide warnings & info from APScheduler internals

def load(reload=False):
    global cfg, stager, default_language, languages, shiftCache, siteCache, scraper, loading_state, user_cache

    with open(r'config.json', encoding='utf-8') as config:
        cfg = json.load(config)

    stager = stagerApi.stagerApi(f"https://{cfg['webinterface_backend']['stager_subdomain']}.stager.co/mobile/",
                                 cfg['dev_options']['debug'])

    if cfg['dev_options']['devmode']:
        log.warn(
            "Developer mode is enabled! Do not use this mode in a deployment! If this mode is in use on a deployment no support will be provided!")
    if not cfg['dev_options']['wipe_logs']:
        log.warn(
            "Log Wiping enabled! This wipes ALL logs! Do not use this mode in a deployment! If this mode is in use on a deployment no support will be provided!")
    log.delete_logs(delete_only_empty=not cfg['dev_options']['wipe_logs'])

    if cfg['webinterface_backend']['behind_proxy']:
        log.info("Proxy is being taken into account!")
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
        )

    # Check if lang file is valid
    def _check_lang(cfg, path=""):
        for key, value in cfg.items():
            current_path = f"{path}.{key}" if path else key

            if isinstance(value, dict):
                _check_lang(value, current_path)
            elif not isinstance(value, str):
                log.error(
                    f"Invalid config entry found! Entry '{current_path}' should be a string, but found type '{type(value).__name__}'")
                exit(1)

    languages = {}
    for file in os.listdir('lang'):
        with open(f"lang/{file}", 'r', encoding='utf-8') as f:
            lang = yaml.full_load(f)
            languages[re.findall(r'(\w+)\.', file)[0]] = lang
            _check_lang(lang)
            log.info(f"Loaded language {file}")

    if os.path.isfile("data/shiftCache.json"):
        with open("data/shiftCache.json", "r") as f:
            shiftCache = json.load(f)
    else:
        shiftCache = {}

    if os.path.isfile("data/siteCache.json"):
        with open("data/siteCache.json", "r") as f:
            siteCache = json.load(f)
    else:
        siteCache = {}

    if os.path.isfile("data/user_cache.json"):
        with open("data/user_cache.json", "r") as f:
            user_cache = json.load(f)
    else:
        user_cache = {}

    loading_state = {}

    scraper = scrp.Scraper(cfg['dev_options']['debug'])

    random_bytes = os.urandom(48)
    app.secret_key = base64.b64encode(random_bytes).decode('utf-8')  # Generates and encodes a random 24-byte secret key
    log.info(f"Generated Secret Key: {app.secret_key}")


# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = '/'

job_lock = threading.Lock()

load()

push_note = ntfy.send()


# User class for Flask-Login
class User(UserMixin):
    def __init__(self, id):
        self.id = id

    def get_id(self):
        return self.id


def slugify(text):
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)  # remove punctuation except spaces and hyphens
    text = re.sub(r"\s+", "-", text)  # spaces → hyphens
    return text


job_queue = queue.Queue()


def worker():
    while True:
        user_id = job_queue.get()  # waits for next job
        try:
            update_caches(user_id)
        finally:
            job_queue.task_done()


threading.Thread(target=worker, daemon=True).start()


def trigger_cache_update(user):
    if datetime.now().timestamp() - user_cache[user]['last_cache'] >= cfg['gui']['update_interval_stager'] * 60:
        job_queue.put(user)
        log.info(f"Queued Cache update for: {user_cache[user]['username']}")

def update_caches(id: str, get_open_shifts=True, skip_scrape=False, date=False):
    loading_state['running'] = True
    loading_state[id] = {'partial_load': False, 'full_load': False}

    # Get all assigned shifts via the Token

    rawShiftsDict = stager.assignedShifts(user_cache[id]['token'])['myShiftsByDate']

    # Get shift details from stager for each date found in rawShiftsDict
    for shifts in rawShiftsDict:
        if date and date != shifts['date']:
            # Skips the item if the date doesnt match the requested date
            continue
        elif shifts['date'] not in shiftCache[id].keys() or "last_updated" not in shiftCache[id][shifts['date']].keys():
            # Skips the other if loop because itll fail otherwise
            pass
        elif datetime.now().timestamp() - shiftCache[id][shifts['date']]['last_updated'] < cfg['gui'][
            'update_interval_stager'] * 60:
            # runs if the cache for the date has not been updated for at least the update interval anmount of time
            continue
        log.info(f"Updating shift details for: {shifts['date']}")
        # Get colleagues if not already in cache and/or not recently updated
        colleagues = stager.colleagues(user_cache[id]['token'], shifts['date'])
        shift_number = 0
        for colleague in colleagues['groupsByEvent'][0]['shiftsByTeam'][0]['shifts']:
            shift_length = datetime.fromisoformat(colleague['end']) - datetime.fromisoformat(colleague['start'])
            shift_length = int(shift_length.total_seconds())
            hours = round(shift_length / 3600, 2)
            shift_length = f"{hours}h"
            colleagues['groupsByEvent'][0]['shiftsByTeam'][0]['shifts'][shift_number]['length'] = shift_length
            shift_number += 1

        # Sort colleague list based on function
        order_lookup = {
            role: index
            for index, role in enumerate(cfg['gui']['function_order'])
        }
        sorted_shifts = sorted(
            colleagues['groupsByEvent'][0]['shiftsByTeam'][0]['shifts'],
            key=lambda shift: order_lookup.get(
                shift["role"],
                float("inf")  # unknown roles go to the end
            )
        )

        shift_number = 0
        for entry in shifts['groups'][0]['shifts']:
            shift_length = datetime.fromisoformat(entry['end']) - datetime.fromisoformat(entry['start'])
            shift_length = int(shift_length.total_seconds())
            hours = round(shift_length / 3600, 2)
            shift_length = f"{hours}h"
            shifts['groups'][0]['shifts'][shift_number]['length'] = shift_length
            shift_number += 1

        # Update shift Cache
        shiftCache[id][shifts['date']] = {"shifts": shifts['groups'][0]['shifts'], "colleagues": sorted_shifts,
                                          "last_updated": datetime.now().timestamp()}
        loading_state[id]['partial_load'] = True

    shift_ids_1 = {
        shift["shiftId"]
        for day in rawShiftsDict
        for group in day["groups"]
        for shift in group["shifts"]
    }

    today = datetime.now().date()

    for date_str, data in shiftCache[id].items():
        if 'shifts' in data.keys():
            for shift in data["shifts"]:
                shift_id = shift["shiftId"]

                # parse shift end date
                shift_end_date = datetime.fromisoformat(shift["end"]).date()

                if shift_id in shift_ids_1:
                    shift["state"] = "upcoming"
                else:
                    if shift_end_date < today:
                        shift["state"] = "finished"
                    else:
                        shift["state"] = "cancelled"

    if get_open_shifts:
        rawOpenShifts = stager.openShifts(user_cache[id]['token'])['openShiftsByDate']
        for show_date in rawOpenShifts:
            temp_dict = {'isAvalible': show_date['isAvailable']}
            for show in show_date['groups']:
                temp_dict['shows'] = {}
                temp_dict['shows'][show['eventName']] = []
                for shift in show['shifts']:
                    shift_length = datetime.fromisoformat(shift['end']) - datetime.fromisoformat(shift['start'])
                    shift_length = int(shift_length.total_seconds())
                    hours = round(shift_length / 3600, 2)
                    shift_length = f"{hours}h"
                    shift['length'] = shift_length
                    temp_dict['shows'][show['eventName']].append(shift)

            if show_date['date'] not in shiftCache[id].keys():
                shiftCache[id][show_date['date']] = {}

            shiftCache[id][show_date['date']]['open_shifts'] = temp_dict

            # shiftCache[id][shift['date']]['openShifts'] = shift

    # Get data from neushoorn website
    if not skip_scrape:
        if date:
            if date not in siteCache.keys():
                siteCache[date] = {}
            elif datetime.now().timestamp() - siteCache[date]['last_updated'] < cfg['gui'][
                'update_interval_neushoorn'] * 3600:
                return
            siteCache[date]['shows'] = scraper.get_program_data(date)
            siteCache[date]['last_updated'] = datetime.now().timestamp()
        else:
            for key in shiftCache[id].keys():
                if key not in siteCache.keys():
                    siteCache[key] = {}
                elif 'last_updated' not in siteCache[key].keys():
                    siteCache[key]['last_updated'] = 0
                elif datetime.now().timestamp() - siteCache[key]['last_updated'] < cfg['gui'][
                    'update_interval_neushoorn'] * 3600:
                    continue
                log.info(f"Updating sitecache for: {key}")
                siteCache[key]['shows'] = scraper.get_program_data(key, cfg['dev_options']['ui_test'])
                siteCache[key]['last_updated'] = datetime.now().timestamp()
                loading_state[id]['partial_load'] = True
    loading_state[id] = {'partial_load': True, 'full_load': True}
    loading_state['running'] = False

def save_data():
    log.info("Saving data")
    with open('data/shiftCache.json', 'w') as f:
        json.dump(shiftCache, f)
    log.info("Saved Shift Cache")

    with open('data/siteCache.json', 'w') as f:
        json.dump(siteCache, f)
    log.info("Saved Site Cache")

    with open('data/user_cache.json', 'w') as f:
        json.dump(user_cache, f)
    log.info("Saved User Cache")



# User loader for Flask-Login
@login_manager.user_loader
def load_user(token):
    return User(token)


@app.template_filter('format_time')
def format_time(value):
    dt = datetime.fromisoformat(value)
    return dt.strftime("%H:%M")  # 18:00


@app.route('/')
def index():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']}")
    if current_user.is_authenticated:
        next_url = request.args.get("next")

        if not next_url or not next_url.startswith("/"):
            next_url = url_for('home')
        return redirect(next_url)
    else:
        lang = languages[cfg['gui']['language']]
    next_url = request.args.get("next")

    if not next_url or not next_url.startswith("/"):
        next_url = "/home"
    return render_template('index.html', config=cfg, lang=lang, hide_nav=True, next_url=next_url)


@app.route('/login', methods=['POST'])
def login():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']}")
    username = request.form['username']
    password = request.form['password']
    token = None
    if cfg['dev_options']['ui_test']:
        token = "test"
    else:
        login = stager.login(username, password)
        if 'sessionToken' in login:
            token = login['sessionToken']
    if token:
        user_key = None
        for key,data in user_cache.items():
            if data['username'] == username:
                user_key = key
                user_cache[key]['token'] = token
                break
        if not user_key:
            user_key = base64.b64encode(os.urandom(48)).decode('utf-8')
            user_cache[user_key] = {"token": token, "username": username, "password": password,
                                    "lang": cfg['gui']['language'], "last_cache": 0}
        user = User(id=user_key)
        login_user(user)
        lang = languages[user_cache[user_key]['lang']]
        if user_key not in shiftCache.keys():
            shiftCache[user_key] = {}
        flash(lang['login']['login_successful'], 'success')
        log.info(f"User {username} logged in successfully")
        return redirect(request.args.get("next_url"))
    else:
        flash(languages[cfg['gui']['language']]['login']['invalid_creds'], 'danger')
        log.info(f"Invalid login attempt for user {username}")
        return redirect(url_for('index'))


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}")
    logout_user()
    flash(languages[cfg['gui']['language']]['login']['logged_out'], 'success')
    return redirect(url_for('index'))


@app.route('/home')
@login_required
def home():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}")
    trigger_cache_update(current_user.id)
    return render_template('home.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']], active_page='home',
                           shifts=shiftCache[current_user.id])


@app.route('/open_shifts')
@login_required
def open_shifts():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}")
    trigger_cache_update(current_user.id)
    return render_template('open_shifts.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']],
                           active_page='open_shifts')

@app.route('/past_shifts')
@login_required
def past_shifts():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}")
    trigger_cache_update(current_user.id)
    return render_template('past_shifts.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']], active_page='past_shifts',
                           shifts=shiftCache[current_user.id])


@app.route("/api/loading_state")
@login_required
def api_loading_state():
    if loading_state['running']:
        loading = loading_state[current_user.id].copy()
        loading_state[current_user.id] = {'partial_load': False, 'full_load': False}
        return jsonify(loading)
    else:
        return jsonify({'partial_load': True, 'full_load': True})

@app.route("/api/shifts")
@login_required
def api_shifts():
    return jsonify(shiftCache[current_user.id])


@app.route('/shifts/<date>')
@login_required
def shift_details(date):
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}")
    update_caches(current_user.id, date=date)
    if date not in shiftCache[current_user.id]:
        flash(languages[current_user.language]['messages']['unknown_shift'], 'danger')
        return redirect(url_for('home'))
    else:
        return render_template('shift_details.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']],
                               active_page='shift_details', details=shiftCache[current_user.id][date],
                               siteCache=siteCache[date], date=date)

@app.route("/debug")
@login_required
def debug():
    if cfg['dev_options']['debug'] or user_cache[current_user.id]['username'] == "vamting@gmail.com":
        with open('data/debug_data.json', 'w') as f:
            json.dump({"ShiftCache": shiftCache, "SiteCache": siteCache, "loading_state": loading_state, "user_cache": user_cache}, f)
        return jsonify({"ShiftCache": shiftCache, "SiteCache": siteCache, "loading_state": loading_state, "user_cache": user_cache})
    else:
        return abort(403)

@app.route("/saveall")
@login_required
def save_all():
    if cfg['dev_options']['debug'] or user_cache[current_user.id]['username'] == "vamting@gmail.com":
        save_data()
    return jsonify({"message": "Saved data"})

scheduler.add_job(
    func=save_data,
    trigger=IntervalTrigger(hours=4),
    id='save_data',
    name='Save cached data',
    replace_existing=True
)

if cfg['dev_options']['devmode']:
    app.run(debug=True)

elif __name__ == "__main__":

    scheduler.start()
    log.info("starting Scheduler")

    from waitress import serve

    log.info(F"Starting server on {cfg['webinterface_backend']['bind_ip']}:{cfg['webinterface_backend']['bind_port']}")
    serve(app, host=cfg['webinterface_backend']['bind_ip'], port=cfg['webinterface_backend']['bind_port'], threads=8)
