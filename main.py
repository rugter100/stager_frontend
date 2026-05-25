import base64
import json
import os
import re
import yaml
import requests

from datetime import datetime
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, request, Response, render_template, abort, send_from_directory, url_for, redirect, session, \
    flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

import libraries.logger as logger
import libraries.ntfy as ntfy
import libraries.stagerApi as stagerApi
import libraries.neushoorn_scraper as scrp

log = logger.file_logger()
log.initialize('Main')
log.info("Logging Initialized!")

app = Flask(__name__)


def load(reload=False):
    global cfg, stager, default_language, languages, shiftCache, siteCache, scraper

    with open(r'config.json', encoding='utf-8') as config:
        cfg = json.load(config)

    stager = stagerApi.stagerApi(f"https://{cfg['webinterface_backend']['stager_subdomain']}.stager.co/mobile/")

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

    shiftCache = {}
    siteCache = {}

    scraper = scrp.Scraper()

    random_bytes = os.urandom(48)
    app.secret_key = base64.b64encode(random_bytes).decode('utf-8')  # Generates and encodes a random 24-byte secret key
    log.info(f"Generated Secret Key: {app.secret_key}")


# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = '/'

load()

push_note = ntfy.send()


# User class for Flask-Login
class User(UserMixin):
    def __init__(self, id, language=None):
        self.id = id
        self.language = language or cfg['gui']['language']

    def get_id(self):
        return self.id


def slugify(text):
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)  # remove punctuation except spaces and hyphens
    text = re.sub(r"\s+", "-", text)  # spaces → hyphens
    return text


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
    log.info(f"Recieving {request.method} to {request.full_path} from {request.remote_addr}")
    if current_user.is_authenticated:
        lang = current_user.language
    else:
        lang = cfg['gui']['language']
    return render_template('index.html', config=cfg, lang=languages[lang], hide_nav=True)


@app.route('/login', methods=['POST'])
def login():
    log.info(f"Recieving {request.method} to {request.full_path} from {request.remote_addr}")
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
        user = User(id=token)
        login_user(user)
        lang = languages[user.language]
        flash(lang['login']['login_successful'], 'success')
        log.info(f"User {username} logged in successfully")
        return redirect(url_for('home'))
    else:
        flash(languages[cfg['gui']['language']]['login']['invalid_creds'], 'danger')
        log.info(f"Invalid login attempt for user {username}")
        return redirect(url_for('index'))


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    log.info(f"Recieving {request.method} to {request.full_path} from {request.remote_addr}")
    logout_user()
    flash(languages[current_user.language]['login']['logged_out'], 'success')
    return redirect(url_for('index'))


@app.route('/home')
@login_required
def home():
    log.info(f"Recieving {request.method} to {request.full_path} from {request.remote_addr}")
    rawShiftsDict = stager.assignedShifts(current_user.id)['myShiftsByDate']
    shiftsDict = {}
    for shifts in rawShiftsDict:
        colleagues = stager.colleagues(current_user.id, shifts['date'])
        shiftsDict[shifts['date']] = {"shifts": shifts['groups'][0]['shifts'],
                                      "colleagues": colleagues['groupsByEvent'][0]['shiftsByTeam'][0]['shifts']}

        shiftCache[current_user.id] = shiftsDict

    for key in shiftsDict.keys():
        if key not in siteCache:
            siteCache[key] = scraper.get_program_data(key)
    return render_template('home.html',
                           config=cfg, lang=languages[current_user.language], active_page='home',
                           shifts=shiftsDict)


@app.route('/shifts/<date>')
@login_required
def shift_details(date):
    if date not in shiftCache[current_user.id]:
        flash(languages[current_user.language]['messages']['unknown_shift'], 'danger')
        return redirect(url_for('home'))
    else:
        order_lookup = {
            role: index
            for index, role in enumerate(cfg['gui']['function_order'])
        }
        sorted_shifts = sorted(
            shiftCache[current_user.id][date]['colleagues'],
            key=lambda shift: order_lookup.get(
                shift["role"],
                float("inf")  # unknown roles go to the end
            )
        )
        shiftCache[current_user.id][date]['colleagues'] = sorted_shifts

        if date not in siteCache:
            siteCache[date] = scraper.get_program_data(date)

        print(siteCache)

        return render_template('shift_details.html', config=cfg, lang=languages[current_user.language],
                               active_page='shift_details', details=shiftCache[current_user.id][date], siteCache=siteCache[date], date=date)


if cfg['dev_options']['devmode']:
    app.run(debug=True)

elif __name__ == "__main__":

    from waitress import serve

    log.info(F"Starting server on {cfg['webinterface_backend']['bind_ip']}:{cfg['webinterface_backend']['bind_port']}")
    serve(app, host=cfg['webinterface_backend']['bind_ip'], port=cfg['webinterface_backend']['bind_port'], threads=8)
