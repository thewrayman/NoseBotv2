import re
import sys
import time
import sched
import socket
import discord
import requests
import paramiko
import jmespath
import importlib

from discord import Intents
from datetime import datetime
from paramiko import AutoAddPolicy
from discord.ext.commands import Bot
from discord.ext import tasks, commands
from settings import SERVER_CONNECTIONS, PREFIX, SANITISATION_CHARACTERS, lgsm_filepath, hastebin_pattern


intents = Intents(messages=True)
bot = Bot(intents=intents, command_prefix=PREFIX)
CURRENT_SETTINGS = {}

scheduler = sched.scheduler(time.time, time.sleep)
eip_event = None
monitor_object = None


def format_output(message, success=True):
    colour = "diff"
    symbol = "+"
    if not success:
        symbol = "-"

    output = "```{}\n" \
             "{} {}\n" \
             "```".format(colour, symbol, message)

    return output


def send_command(servername, command):
    print("send_command({}, {})".format(servername, command))
    success = True

    ip_address = jmespath.search('servers[?name == `{}`].host | [0]'.format(servername), SERVER_CONNECTIONS)
    binary_name = jmespath.search('servers[?name == `{}`].binary_name | [0]'.format(servername), SERVER_CONNECTIONS)
    usr = jmespath.search('servers[?name == `{}`].usr | [0]'.format(servername), SERVER_CONNECTIONS)
    pwd = jmespath.search('servers[?name == `{}`].pwd | [0]'.format(servername), SERVER_CONNECTIONS)

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(AutoAddPolicy)
        ssh.connect(ip_address, username=usr, password=pwd)
        print("Connected to server")

        if command != "basic":
            final_command = 'su - {0} {1}/{0} {2}'.format(binary_name, lgsm_filepath, command)
        else:
            final_command = 'pgrep -c {}'.format(servername)

        print("Executing command: {}".format(final_command))
        ssh_stdin, ssh_stdout, ssh_stderr = ssh.exec_command(final_command)
        print("Executed command successfully")

        stdout = ssh_stdout.readlines()
        ssh_stdout.close()
        ssh.close()

        joined = ''.join(stdout)

        if command == "basic":
            if "0" not in joined and "fail" not in joined.lower():
                success = True
                output = "{} is currently running as expected".format(servername)
            else:
                success = False
                output = "{} IS NOT currently running".format(servername)

        else:
            if "error" not in joined.lower() and "fail" not in joined.lower():
                output = "Things are looking good!"
                if command.lower() == "postdetails":
                    output = re.findall(hastebin_pattern, joined)[0][0]
            else:
                success = False
                output = "Something doesn't look quite right, attempting to restart the server!\nHere's some debug logs:\n" \
                         "{}".format(joined)
    except Exception as e:
        success = False
        output = "Failed to connect and execute command"
        print("Exception in server connection:\n{}".format(e))

    return output, success


def check_server(servername, command):
    print("check server({}, {})".format(servername, command))

    valid_commands = jmespath.search("servers[?name==`{}`].valid_commands | [0]".format(servername), SERVER_CONNECTIONS)

    if command.lower() not in valid_commands:
        return "**Not a valid command** - please try {0}{1} followed by one of the following:" \
               "\n{2}" \
               "\ne.g: **{0}{1} restart**".format(PREFIX, servername, ', '.join(valid_commands))

    output, success = send_command(servername, command)

    if command == "postdetails":
        return output

    output = ''.join([c for c in output if c not in SANITISATION_CHARACTERS])

    return format_output(output, success)


def check_eip():
    ip_list = []
    ais = socket.getaddrinfo("plex.tiniedev.com", 0, 0, 0, 0)
    for result in ais:
        ip_list.append(result[-1][0])
    ip = list(set(ip_list))[0]

    prev_ip = CURRENT_SETTINGS.get("eip")
    if prev_ip != ip:
        CURRENT_SETTINGS["eip"] = ip
        print("New EIP found: {}".format(ip))
        ##send email notification

    else:
        print("Existing EIP")


class MovrMonitor:
    def __init__(self, ctx):
        self.startup = True
        self.context = ctx
        self.last_reward = 0
        self.last_total = 0
        self.total_usd = 0
        self.latest_usd = 0
        self.movr_price = 0

    def check_if_new(self, new_json):
        current_total = new_json["movr_total"]
        latest_reward = new_json["movr_reward"]
        print("checking if the json contains new data")

        if latest_reward > self.last_reward:
            self.last_total = current_total
            self.last_reward = latest_reward
            self.total_usd = new_json["usd_total"]
            self.latest_usd = new_json["usd_reward"]
            self.movr_price = new_json["movr_price"]
            print("Found new reward")
            return True

        else:
            return False


@tasks.loop(minutes=5)
async def monitor():

    r = requests.get("http://localhost:8000/staking/0x2b3a1c94c72f311a8275d3c01e39b51260c07938")
    print("got result from the monitor")
    print(r.json())
    global monitor_object
    new = monitor_object.check_if_new(r.json())
    if new:
        msg = f"Found new staking reward!\n" \
              f"Reward: {monitor_object.last_reward:.6f}MOVR ({monitor_object.latest_usd:.6f}USD)\n" \
              f"Total: {monitor_object.last_total:.6f}MOVR ({monitor_object.total_usd:.6f}USD)\n" \
              f"Current MOVR price: ${monitor_object.movr_price:.2f}"
        print("outputting new staking message")
        await monitor_object.context.send(format_output(msg))


@bot.event
async def on_ready():
    print("Logged in as {0.user}".format(bot))
    activity = discord.Activity(name='children', type=discord.ActivityType.watching)
    await bot.change_presence(activity=activity)


@bot.command(name='hello')
async def say_hello(context):
    await context.send('Hello!')


@bot.command(name='mip')
async def mip(context):
    print("Monitor external IP")
    if CURRENT_SETTINGS.get("mip"):
        print("Disabling external IP monitor")

        global eip_event
        scheduler.cancel(eip_event)
        CURRENT_SETTINGS["mip"] = False
        print("EIP check removed from scheduler")

        await context.send("EIP check removed from scheduler")

    else:
        print("Enabling external IP monitor")
        CURRENT_SETTINGS["mip"] = True

        eip_event = scheduler.enter(300, 1, check_eip)

        print("EIP check added to scheduler")

        await context.send("EIP check added to scheduler")


@bot.command(name='warmie')
async def warmie(context):
    await context.send(file=discord.File('warmie.gif'))


@bot.command(name='warmie2')
async def warmie2(context):
    await context.send(file=discord.File('warmie2.gif'))


@bot.command(name='nightrider')
async def nightrider(context):
    await context.send(file=discord.File('nightrider.gif'))


@bot.command(name='h')
async def help(context):
    print("Help command")
    valid_commands = jmespath.search("servers[?name==`{}`].valid_commands | [0]".format("valheim"), SERVER_CONNECTIONS)
    msg = "Commands:\n\n" \
          "**{0}valheim** - returns if the server is currently running\n" \
          "**{0}valheim <{1}>** - use one of the options listed to carry out the specific valheim function\n" \
          "e.g - `$valheim restart` will restart the server\n" \
          "\nValheim currently cannot not save on-demand through here\n" \
          "**Please use F5 in game and type \"save\" before doing a restart/stop/backup**".format(PREFIX, '|'.join(valid_commands))
    await context.send(msg)


@bot.command(name='valheim')
async def valheim(context):
    print("{} - Valheim command".format(datetime.now()))

    split_content = context.message.content.split(' ')
    game_name = split_content[0]

    try:
        game_command = split_content[1]
    except:
        game_command = "basic"

    msg = await context.send("Checking on status of {}\nHold on this will take a few seconds".format(game_name))
    result = check_server("valheim", game_command)
    print("result:\n{}".format(result))
    print("********\n")
    await msg.edit(content=result)


@bot.command(name='movrtrack')
async def movrtrack(context):
    print("{} - movrtack command".format(datetime.now()))
    split_content = context.message.content.split(' ')
    command_key = "movrtrack"

    track_command = split_content[1]

    current_status = CURRENT_SETTINGS.get(command_key)
    msg = "Empty"

    if track_command.lower() == "on":
        if current_status:
            msg = "The moonriver tracker is already running!"

        else:
            msg = "enabling the moonriver tracker"
            #enable tracking
            global monitor_object
            if not monitor_object:
                monitor_object = MovrMonitor(context)
            monitor.start()
            CURRENT_SETTINGS[command_key] = True
            print("Turned on the monitor")

    elif track_command.lower() == "off":
        #turn off
        monitor.cancel()
        CURRENT_SETTINGS[command_key] = False
        msg = "Turned off the monitor"
    else:
        msg = f"Invalid parameter for {command_key} - please use movrtracker on or off"

    await context.send(msg)



@bot.command(name='reload')
async def reload(context):
    print("Reloading..")
    importlib.import_module('')

bot.run(sys.argv[1])
