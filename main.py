import base64
import json
import logging
import os
import queue
import re
import yaml
import threading

from datetime import datetime
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, request, render_template, abort, url_for, redirect, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import libraries.logger as logger
import libraries.ntfy as ntfy
import libraries.stagerApi as stagerApi
import libraries.neushoorn_scraper as scrp

log = logger.fileLogger()
log.initialize('Main')
log.info("Logging Initialized!")

app = Flask(__name__)

# Create a scheduler instance
scheduler = BackgroundScheduler(daemon=True)

apscheduler_logger = logging.getLogger('apscheduler')
apscheduler_logger.setLevel(logging.ERROR)  # Hide warnings & info from APScheduler internals


# noinspection PyGlobalUndefined
def load(reload=False):
    global cfg, stager, default_language, languages, shiftCache, siteCache, scraper, loading_state, user_cache

    with open(r'config.json', encoding='utf-8') as config:
        cfg = json.load(config)

    stager = stagerApi.stagerApi(f"https://{cfg['webinterface_backend']['stager_subdomain']}.stager.co/mobile/",
                                 cfg['dev_options']['debug'])

    if cfg['dev_options']['devmode']:
        log.warn(
            "Developer mode is enabled! Do not use this mode in a deployment! If this mode is in use on a deployment no support will be provided!")
    if cfg['dev_options']['wipe_logs']:
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
        log.info(f"Queued Cache update for: {user_cache[user]['username']}",
                 cmdout=not cfg['presetup']['clean_console'])


def update_caches(id: str, get_open_shifts=True, skip_scrape=False, date=False):
    loading_state['running'] = True
    loading_state[id] = {'partial_load': False, 'full_load': False}

    # Get all assigned shifts via the Token

    #"""
    rawShiftsDict = stager.assignedShifts(user_cache[id]['token'])['myShiftsByDate']

    log.info('Getting shifts')
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

    log.info('Getting open shifts')
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

    log.info("Clearing past open shifts from cache")
    #Clear past shifts
    for show_date, date_data in sorted(shiftCache[id].items()):
        if datetime.fromisoformat(show_date) >= datetime.now():
            break
        else:
            if 'open_shifts' in date_data.keys():
                if 'shifts' in date_data.keys():
                    print("Deleting Open Shift")
                    del shiftCache[id][show_date]['open_shifts']
                else:
                    print("Deleting empty date")
                    del shiftCache[id][show_date]

    #"""

    log.info("Scraping neushoorn.nl")
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
                program_data = scraper.get_program_data(key, cfg['dev_options']['ui_test'])
                if program_data:
                    siteCache[key]['shows'] = program_data
                siteCache[key]['last_updated'] = datetime.now().timestamp()
                loading_state[id]['partial_load'] = True
    log.info("Finished Update")
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


@app.route('/privacy-disclaimer')
def disclaimer():
    if current_user.is_authenticated:
        lang = languages[user_cache[current_user.id]['lang']]
    else:
        lang = languages[cfg['gui']['language']]
    return render_template('privacy-disclaimer.html', config=cfg, lang=lang, commit_date="06-06-2026")


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
        for key, data in user_cache.items():
            if data['username'] == username:
                user_key = key
                user_cache[key]['token'] = token
                break
        if not user_key:
            user_key = base64.b64encode(os.urandom(48)).decode('utf-8')
            lang = cfg['gui']['language']
        else:
            lang = user_cache[user_key]['lang']
        if token != "test":
            profile_data = stager.profile(token)
            current_account = stager.currentAccount(token)

            user_cache[user_key] = {"token": token, "username": username, "lang": cfg['gui']['language'], "last_cache": 0}

            user_keys_pdata = ['fullName', 'roles', 'preferences', 'profilePicture', 'phoneNumber', 'address', 'postalCode', 'city', 'country', 'birthDate']
            for key in user_keys_pdata:
                if key in profile_data.keys():
                    data = profile_data[key]
                else:
                    data = ''
                user_cache[user_key][key] = data

            user_keys_curracc = ['permissions', 'featureFlags', 'intercomAndroidUserHash', 'intercomIosUserHash', 'crewMemberId', 'crewMemberCanSeeColleagues', 'crewMemberAvailabilityType', 'lastLogin']
            for key in user_keys_curracc:
                if key in current_account.keys():
                    data = current_account[key]
                else:
                    data = ''
                user_cache[user_key][key] = data
        if user_key not in shiftCache.keys():
            shiftCache[user_key] = {}

        user = User(id=user_key)
        login_user(user)
        lang = languages[lang]
        flash(lang['login']['login_successful'], 'success')
        log.info(f"User {username} logged in successfully")
        next_url = request.args.get("next_url")
        if next_url:
            return redirect(next_url)
        else:
            return abort(403)
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
    log.info(
        f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}")
    logout_user()
    flash(languages[cfg['gui']['language']]['login']['logged_out'], 'success')
    return redirect(url_for('index'))


@app.route('/settings', methods=['GET'])
@login_required
def settings():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(
        f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}")
    return render_template('settings.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']],
                           active_page='settings',
                           user_data=user_cache[current_user.id], languages=languages)


@app.route('/home')
@login_required
def home():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(
        f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}",
        cmdout=not cfg['presetup']['clean_console'])
    trigger_cache_update(current_user.id)
    return render_template('home.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']],
                           active_page='home',
                           shifts=shiftCache[current_user.id], languages=languages,
                           user_data=user_cache[current_user.id])


@app.route('/open_shifts')
@login_required
def open_shifts():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(
        f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}",
        cmdout=not cfg['presetup']['clean_console'])
    trigger_cache_update(current_user.id)
    return render_template('open_shifts.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']],
                           user_data=user_cache[current_user.id], active_page='open_shifts', languages=languages)


@app.route('/past_shifts')
@login_required
def past_shifts():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(
        f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}",
        cmdout=not cfg['presetup']['clean_console'])
    trigger_cache_update(current_user.id)
    return render_template('past_shifts.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']],
                           active_page='past_shifts', user_data=user_cache[current_user.id],
                           shifts=shiftCache[current_user.id], languages=languages)


@app.route('/updatelanguage')
@login_required
def update_language():
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(
        f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}",
        cmdout=not cfg['presetup']['clean_console'])

    language = request.args.get("lang")
    user_cache[current_user.id]['lang'] = language

    next_url = request.args.get("next")

    if not next_url or not next_url.startswith("/"):
        next_url = url_for("home")
    return redirect(next_url)


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


@app.route("/api/updateavailability", methods=["POST"])
def update_availability():
    data = request.get_json()

    stager_availability = stager.setAvalability(user_cache[current_user.id]['token'], data['date'], data['available'])
    if stager_availability:
        shiftCache[current_user.id][data['date']]['open_shifts']['isAvailable'] = data['available']
        return jsonify({"success": stager_availability})
    else:
        return jsonify(
            {"success": False, "error": f"Sorry! Something went wrong when updating availability for {data['date']}"})


@app.route('/shifts/<date>')
@login_required
def shift_details(date):
    if cfg['webinterface_backend']['behind_proxy']:
        request_ip = request.headers.get("X-Real-IP")
    else:
        request_ip = request.remote_addr
    log.info(
        f"Recieving {request.method} to {request.full_path} from {request_ip}:{request.environ['REMOTE_PORT']} as user {user_cache[current_user.id]['username']}",
        cmdout=not cfg['presetup']['clean_console'])
    update_caches(current_user.id, date=date)
    if date not in shiftCache[current_user.id]:
        flash(languages[current_user.language]['message']['unknown_shift'], 'danger')
        return redirect(url_for('home'))
    else:
        return render_template('shift_details.html', config=cfg, lang=languages[user_cache[current_user.id]['lang']],
                               active_page='shift_details', details=shiftCache[current_user.id][date],
                               siteCache=siteCache[date], user_data=user_cache[current_user.id], date=date,
                               languages=languages)


@app.route("/debug")
@login_required
def debug():
    if cfg['dev_options']['debug'] or user_cache[current_user.id]['username'] == "vamting@gmail.com":
        with open('data/debug_data.json', 'w') as f:
            json.dump({"ShiftCache": shiftCache, "SiteCache": siteCache, "loading_state": loading_state,
                       "user_cache": user_cache}, f)
        return jsonify({"ShiftCache": shiftCache, "SiteCache": siteCache, "loading_state": loading_state,
                        "user_cache": user_cache})
    else:
        return abort(403)


@app.route("/saveall")
@login_required
def save_all():
    if cfg['dev_options']['debug'] or user_cache[current_user.id]['username'] == "vamting@gmail.com":
        save_data()
        return jsonify({"message": "Saved data"})
    else:
        return abort(403)


@app.route("/reload")
@login_required
def reload():
    if cfg['dev_options']['debug'] or user_cache[current_user.id]['username'] == "vamting@gmail.com":
        load(True)
        return jsonify({"message": "Reloaded application"})
    else:
        return abort(403)


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
