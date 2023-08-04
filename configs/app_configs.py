# These are program configs and are independent of which agent we wish to run.

# TODO: Improve generalization of these configs for downstream users.
# Steam games likely can have hard-coded paths, manually installed games
# however, will likely need user configuration.

import os

try:
    mc_command = open("minecraft_command.txt").read()
except FileNotFoundError:
    mc_command = ""

app_configs = \
    [{
        "conf_title": "Skyrogue",
        #Skyrogue tries launches steam if the working directory isn't the game's directory.,
        "directory": "~/.local/share/Steam/steamapps/common/Sky Rogue",
        "command": "./skyrogue.x86",
        "window_title": "Sky Rogue",
        "x_res": 640,
        "y_res": 480,
    }, {
        "conf_title": "Minecraft",
        "directory": "./",
        # minecraft_command.txt comes from
        # $ ps -eo args | grep inecraft; killall minecraft-launcher
        "command": mc_command,
        # One's version may differ here.
        "window_title": "Minecraft 1.16.5",
        # Pass in bmp to disable compression
        "extension": ".bmp",
        "x_res": 1280,
        "y_res": 720   ,
    }, {
        "conf_title": "Firefox",
        "directory": "./",
        "command": "firefox",
        "window_title": "Mozilla Firefox",
        "x_res": 960,
        "y_res": 540,
    }, {
        "conf_title": "Art of Rally",
        "directory": "~/.local/share/Steam/steamapps/common/artofrally",
        "command": "steam steam://rungameid/550320",
        "window_title": "art of rally",
        "x_res": 1920,
        "y_res": 1080,
    }, {
        "conf_title": "Art of Rally Demo",
        "directory": "~/",
        "command": "~/Downloads/Linux/artofrally_demo.x64",
        "window_title": "art of rally",
        "x_res": 1920,
        "y_res": 1080,
    }, {
        "conf_title": "Art of Rally (Multi)",
        "directory": "~/Games/art_of_rally$i/game",
        "command": "./artofrally.x64",
        "window_title": "art of rally",
        "x_res": 1920,
        "y_res": 1080,
    }, {
        "conf_title": "Factorio",
        "directory": "~/Downloads/factorio/bin/x64",
        "command": "./factorio",
        "window_title": "Factorio 1.1.53",
        "x_res": 960,
        "y_res": 540,
        "keys": ["W", "A", "S", "D", "E", "R", "T", "Shift", "Tab", "Ctrl", "LMB", "RMB"],
    }, {
        "conf_title": "Noita",
        "directory": "~/",
        "command": "steam steam://rungameid/881100",
        "window_title": "Noita.*",
        "init_cmd": "rm -r ~/.steam/steam/steamapps/compatdata/881100/pfx/drive_c/users/steamuser/AppData/LocalLow/Nolla_Games_Noita/save0*",
        "process_mode": "separate",
        "keyboard_config": {
            "sequence_keydown_time": .08,
            "mode": "FAKE_INPUT",
        }
    }]

def LoadAppConfig(config_title):
    app_conf = None
    for c in app_configs:
        if c["conf_title"] == config_title:
            app_conf = c
    if c is not None and 'directory' in c:
        c['directory'] = os.path.expanduser(c['directory'])
    return app_conf
