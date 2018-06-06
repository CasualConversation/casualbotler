# coding=utf8
"""
Adapted by Znuxor
Based off:
chanlogs.py - Sopel Channel Logger module
Copyright 2014, David Baumgold <david@davidbaumgold.com>

Licensed under the Eiffel Forum License 2

http://sopel.chat
"""
from __future__ import unicode_literals
import os
import os.path
import re
import threading
import sys
from datetime import datetime
from collections import defaultdict
try:
    from pytz import timezone
    import pytz
except ImportError:
    pytz = None
import sopel.module
import sopel.tools
from sopel.config.types import StaticSection, ValidatedAttribute, FilenameAttribute, NO_DEFAULT

MESSAGE_TPL = "{datetime}     {trigger.nick} ({trigger.hostmask}) {message}"
ACTION_TPL =  "{datetime}     {trigger.nick} ({trigger.hostmask}) * {message}"
MODE_TPL_2 =  "{datetime} --  Mode {trigger.sender} ({trigger.args[1]})  by {trigger.nick} ({trigger.hostmask})"
MODE_TPL_3 =  "{datetime} --  Mode {trigger.sender} ({trigger.args[1]} {trigger.args[2]}) by {trigger.nick} ({trigger.hostmask})"
KICK_TPL =    "{datetime} <-- {trigger.nick} ({trigger.hostmask}) has kicked {trigger.args[1]} ({trigger.args[2]})"
NICK_TPL =    "{datetime} --  {trigger.nick} ({trigger.hostmask}) is now known as {trigger.sender}"
JOIN_TPL =    "{datetime} --> {trigger.nick} ({trigger.hostmask}) has joined {trigger}"
PART_TPL =    "{datetime} <-- {trigger.nick} ({trigger.hostmask}) has left ({trigger})"
QUIT_TPL =    "{datetime} *** {trigger.nick} ({trigger.hostmask}) has quit IRC ({trigger.args[0]})"
# According to Wikipedia
BAD_CHARS = re.compile(r'[\/?%*:|"<>. ]')

class ChanlogsSection(StaticSection):
    dir = FilenameAttribute('dir', directory=True, default='~/chanlogs')
    """Path to channel log storage directory"""
    by_day = ValidatedAttribute('by_day', parse=bool, default=True)
    """Split log files by day"""
    privmsg = ValidatedAttribute('privmsg', parse=bool, default=True)
    """Record private messages"""
    microseconds = ValidatedAttribute('microseconds', parse=bool, default=False)
    """Microsecond precision"""
    localtime = ValidatedAttribute('localtime', parse=bool, default=False)
    """Attempt to use preferred timezone instead of UTC"""
    ## TODO: Allow configuration of templates, perhaps the user would like to use
    ##       parsers that support only specific formats.
    message_template = ValidatedAttribute('message_template', default=None)
    action_template = ValidatedAttribute('action_template', default=None)
    mode_template = ValidatedAttribute('mode_template', default=None)
    kick_template = ValidatedAttribute('kick_template', default=None)
    join_template = ValidatedAttribute('join_template', default=None)
    part_template = ValidatedAttribute('part_template', default=None)
    quit_template = ValidatedAttribute('quit_template', default=None)
    nick_template = ValidatedAttribute('nick_template', default=None)


def configure(config):
    config.define_section('chanlogs', ChanlogsSection, validate=False)
    config.chanlogs.configure_setting(
        'dir',
        'Path to channel log storage directory',
    )
    

def get_datetime(bot):
    """
    Returns a datetime object of the current time.
    """
    dt = datetime.utcnow()
    if pytz:
        dt = dt.replace(tzinfo=timezone('UTC'))
        if bot.config.chanlogs.localtime:
            dt = dt.astimezone(timezone(bot.config.clock.tz))
    if not bot.config.chanlogs.microseconds:
        dt = dt.replace(microsecond=0)
    return dt


def get_fpath(bot, trigger, channel=None):
    """
    Returns a string corresponding to the path to the file where the message
    currently being handled should be logged.
    """
    basedir = bot.config.chanlogs.dir
    channel = channel or trigger.sender
    channel = channel.lstrip("#")
    channel = BAD_CHARS.sub('__', channel)
    channel = sopel.tools.Identifier(channel).lower()

    dt = get_datetime(bot)
    if bot.config.chanlogs.by_day:
        fname = "{channel}-{date}.log".format(channel=channel, date=dt.date().isoformat())
    else:
        fname = "{channel}.log".format(channel=channel)
    return os.path.join(basedir, fname)


def _format_template(tpl, bot, trigger, **kwargs):
    dt = get_datetime(bot)

    formatted = tpl.format(
        trigger=trigger, datetime=dt.isoformat(),
        date=dt.date().isoformat(), time=dt.time().isoformat(),
        **kwargs
    ) + "\n"

    if sys.version_info.major < 3 and isinstance(formatted, unicode):
        formatted = formatted.encode('utf-8')
    return formatted


def setup(bot):
    bot.config.define_section('chanlogs', ChanlogsSection)

    # locks for log files
    if not bot.memory.contains('chanlog_locks'):
        bot.memory['chanlog_locks'] = sopel.tools.SopelMemoryWithDefault(threading.Lock)

    # to keep track of joins parts and quits of users to log QUIT events correctly
    if not bot.memory.contains('channels_of_user'):
        bot.memory['channels_of_user'] = defaultdict(list)



@sopel.module.rule('.*')
@sopel.module.unblockable
def log_message(bot, message):
    "Log every message in a channel"
    # if this is a private message and we're not logging those, return early
    if message.sender.is_nick() and not bot.config.chanlogs.privmsg:
        return

    # determine which template we want, message or action
    if message.tags.get('intent') == 'ACTION':
        tpl = bot.config.chanlogs.action_template or ACTION_TPL
        # strip off start and end
        #message = message[8:-1]
    else:
        tpl = bot.config.chanlogs.message_template or MESSAGE_TPL

    logline = _format_template(tpl, bot, message, message=message)
    fpath = get_fpath(bot, message)
    with bot.memory['chanlog_locks'][fpath]:
        with open(fpath, "ab") as f:
            f.write(logline.encode('utf8'))

    # user channels management
    if message.sender not in bot.memory['channels_of_user'][message.nick]:
        bot.memory['channels_of_user'][message.nick].append(message.sender)


@sopel.module.rule('.*')
@sopel.module.event("MODE")
@sopel.module.unblockable
def log_mode(bot, trigger):
    if len(trigger.args) == 3:
        tpl = bot.config.chanlogs.mode_template or MODE_TPL_3
    elif len(trigger.args) == 2:
        tpl = bot.config.chanlogs.mode_template or MODE_TPL_2
    else:
        return

    logline = _format_template(tpl, bot, trigger)
    fpath = get_fpath(bot, trigger, channel=trigger.sender)
    with bot.memory['chanlog_locks'][fpath]:
        with open(fpath, "ab") as f:
            f.write(logline.encode('utf8'))


@sopel.module.rule('.*')
@sopel.module.event("KICK")
@sopel.module.unblockable
def log_kick(bot, trigger):
    tpl = bot.config.chanlogs.mode_template or KICK_TPL
    logline = _format_template(tpl, bot, trigger)
    fpath = get_fpath(bot, trigger, channel=trigger.sender)
    with bot.memory['chanlog_locks'][fpath]:
        with open(fpath, "ab") as f:
            f.write(logline.encode('utf8'))
    # user channels management
    if trigger.sender in bot.memory['channels_of_user'][trigger.nick]:
        bot.memory['channels_of_user'][trigger.nick].remove(trigger.sender)


users = defaultdict(list)

@sopel.module.rule('.*')
@sopel.module.event("JOIN")
@sopel.module.unblockable
def log_join(bot, trigger):
    tpl = bot.config.chanlogs.join_template or JOIN_TPL
    logline = _format_template(tpl, bot, trigger)
    fpath = get_fpath(bot, trigger, channel=trigger.sender)
    with bot.memory['chanlog_locks'][fpath]:
        with open(fpath, "ab") as f:
            f.write(logline.encode('utf8'))
    # user channels management
    bot.memory['channels_of_user'][trigger.nick].append(trigger.sender)


@sopel.module.rule('.*')
@sopel.module.event("PART")
@sopel.module.unblockable
def log_part(bot, trigger):
    tpl = bot.config.chanlogs.part_template or PART_TPL
    logline = _format_template(tpl, bot, trigger=trigger)
    fpath = get_fpath(bot, trigger, channel=trigger.sender)
    with bot.memory['chanlog_locks'][fpath]:
        with open(fpath, "ab") as f:
            f.write(logline.encode('utf8'))
    # user channels management
    if trigger.sender in bot.memory['channels_of_user'][trigger.nick]:
        bot.memory['channels_of_user'][trigger.nick].remove(trigger.sender)


@sopel.module.rule('.*')
@sopel.module.event("QUIT")
@sopel.module.unblockable
@sopel.module.thread(False)
@sopel.module.priority('high')
def log_quit(bot, trigger):
    tpl = bot.config.chanlogs.quit_template or QUIT_TPL
    logline = _format_template(tpl, bot, trigger)
    # make a copy of bot.privileges that we can safely iterate over
    privcopy = list(bot.privileges.items())
    # write logline to *all* channels that the user was present in
    for channel, privileges in privcopy:
        #if trigger.nick in privileges:
        if channel in bot.memory['channels_of_user'][trigger.nick]:
            fpath = get_fpath(bot, trigger, channel)
            with bot.memory['chanlog_locks'][fpath]:
                with open(fpath, "ab") as f:
                    f.write(logline.encode('utf8'))
    # user channels management
    del(bot.memory['channels_of_user'][trigger.nick])


@sopel.module.rule('.*')
@sopel.module.event("NICK")
@sopel.module.unblockable
def log_nick_change(bot, trigger):
    tpl = bot.config.chanlogs.nick_template or NICK_TPL
    logline = _format_template(tpl, bot, trigger)
    old_nick = trigger.nick
    new_nick = trigger.sender
    # make a copy of bot.privileges that we can safely iterate over
    privcopy = list(bot.privileges.items())
    # write logline to *all* channels that the user is present in
    for channel, privileges in privcopy:
        if old_nick in privileges or new_nick in privileges:
            fpath = get_fpath(bot, trigger, channel)
            with bot.memory['chanlog_locks'][fpath]:
                with open(fpath, "ab") as f:
                    f.write(logline.encode('utf8'))
    # user channels management
    bot.memory['channels_of_user'][new_nick].extend(bot.memory['channels_of_user'][old_nick])
    del(bot.memory['channels_of_user'][old_nick])
