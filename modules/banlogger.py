#!/usr/bin/env python3
'''Stuff about logging bans and kicks and mutes'''

import json
import os
import re
import shlex
import sys
import argparse
import subprocess
import urllib
import requests
from sopel import module
from sopel.config.types import StaticSection, ListAttribute, ValidatedAttribute, FilenameAttribute
from pyshorteners import Shortener

# hack for relative import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import from_admin_channel_only, create_s3_paste


class BanLoggerSection(StaticSection):
    '''Defines the section of the configuration for this module.'''
    admin_channels = ListAttribute('admin_channels')
    loggable_channels = ListAttribute('loggable_channels')
    base_form_url = ValidatedAttribute('base_form_url')
    s3_bucket_name = ValidatedAttribute('s3_bucket_name')


def configure(config):
    '''Invoked when in configuration building mode.'''
    config.define_section('banlogger', BanLoggerSection, validate=True)


LOG_CMD_PARSER = None

URL_SHORTENER = None

# Format them with the appropriate information!
VALID_NICK = r'[a-zA-Z0-9_\-\\\[\]\{\}\^\`\|]+'
ISO8601 = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+\d{2}:\d{2}'
OPT_DURATION_GROUP = r'(\+\d{1,3}[smhdy])? ?'

MUTE_REGEX = re.compile(ISO8601+r' --  Mode #?\w+ \(\+b m:(.*)\) by ('+VALID_NICK+r') \((.*)\)')
BAN_REGEX = re.compile(ISO8601+r' --  Mode #?\w+ \(\+b (.*)\) by ('+VALID_NICK+r') \((.*)\)')
KICK_REGEX = re.compile(ISO8601+r' <-- ('+VALID_NICK+r') \((.*)\) has kicked (' +
                        VALID_NICK+r') \(?(.*)\)?')
REMOVED_REGEX = re.compile(ISO8601+r' <-- ('+VALID_NICK +
                           r') \((.*)\) has left \(?Removed by ('+VALID_NICK+r').*\)?')
MSG_REGEX = re.compile(ISO8601+r'     ('+VALID_NICK+r') \((.*)\) (.*)')
SWITCH_REGEX = re.compile(ISO8601+r' --  ('+VALID_NICK +
                          r') \((.*)\) is now known as ('+VALID_NICK+')')
JOIN_REGEX = re.compile(ISO8601+r' --> ('+VALID_NICK+r') \((.*)\) has joined .*')

KICK_MACRO_REGEX = re.compile(ISO8601+r'     ('+VALID_NICK +
                              r') \(.*\) !ki?c?k? ('+VALID_NICK+r') ?(.*)')
MUTE_MACRO_REGEX = re.compile(ISO8601+r'     ('+VALID_NICK+r') \(.*\) !mu?t?e? ' +
                              OPT_DURATION_GROUP+r'('+VALID_NICK+r') ?(.*)')
BAN_MACRO_REGEX = re.compile(ISO8601+r'     ('+VALID_NICK+r') \(.*\) !k?i?c?k?ba?n? ' +
                             OPT_DURATION_GROUP+r'('+VALID_NICK+r') ?(.*)')


def setup(bot):
    '''Invoked when module is loaded.'''
    bot.config.define_section('banlogger', BanLoggerSection, validate=True)

    argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    global LOG_CMD_PARSER
    LOG_CMD_PARSER = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    LOG_CMD_PARSER.add_argument('mode',
                                type=str,
                                choices=['recent', 'auto'],
                                default='auto',
                                help='the desired logging mode')
    LOG_CMD_PARSER.add_argument('--linenumber',
                                '-l',
                                type=int,
                                choices=range(1, 4001),
                                default=100,
                                metavar="[1-4000]",
                                help='the number of lines to log in recent mode')
    LOG_CMD_PARSER.add_argument('--maxautolines',
                                '-m',
                                type=int,
                                choices=range(1, 4001),
                                default=4000,
                                metavar="[1-4000]",
                                help='the maximum number of lines to search in auto mode')
    LOG_CMD_PARSER.add_argument('--maxlogautolines',
                                '-b',
                                type=int,
                                choices=range(1, 4001),
                                default=400,
                                metavar="[1-4000]",
                                help='the maximum number of lines for the log in auto mode')
    LOG_CMD_PARSER.add_argument('--followinglines',
                                '-f',
                                type=int,
                                choices=range(0, 101),
                                default=2,
                                metavar="[0-100]",
                                help='the desired number of lines after the action in auto mode')
    LOG_CMD_PARSER.add_argument('--skip',
                                '-s',
                                type=int,
                                choices=range(11),
                                default=0,
                                metavar="[0-10]",
                                help='the number of actions to skip in auto mode')
    LOG_CMD_PARSER.add_argument('--chan',
                                '-c',
                                type=str.lower,
                                choices=bot.config.banlogger.loggable_channels,
                                default='#casualconversation',
                                help='the channel to log')

    global URL_SHORTENER
    URL_SHORTENER = Shortener('Tinyurl', timeout=10)

    bot.memory['last_log_information'] = {'nick': None,
                                          'result': None,
                                          'length': None,
                                          'operator': None,
                                          'channel': None,
                                          'reason': None,
                                          'host': None,
                                          'paste': None}


CHANNEL_FOR_LOG = {'#casualconversation': '#Casualconversation',
                   '#talk': '#Talk',
                   '#casualnsfw': '#CasualNSFW',
                   '#casualappeals': 'All'}

APPROPRIATE_BACKTRACK_NUMBER = 8  # The number of lines to analyze before an action


@module.commands('log')
@from_admin_channel_only
def log(bot, trigger):
    '''Bot function to log a ban in a given channel, has multiple options.'''

    extra_info = ''

    arguments = trigger.groups()[1]
    if arguments is None:
        bot.reply('No arguments :(   To learn the command syntax, please use -h')
        return
    try:
        args = LOG_CMD_PARSER.parse_args(shlex.split(arguments))
    except SystemExit:
        if '-h' in arguments or '--help' in arguments or 'help' in arguments:
            helplog(bot, trigger)
        else:
            bot.reply('invalid arguments :(   To learn the command syntax, please use -h')
        return

    if args.mode == 'recent':
        log_content = read_log_file(bot, args.chan, args.linenumber)
        log_lines = log_content.split('\n')
        start_index = 0
        end_index = len(log_lines)
        action_index = get_action_line_index(log_lines, args.skip)
        if action_index is None:
            relevant_info = dict()
        else:  # duplicated, but I'm not comfortable because it's used below too
            relevant_info = get_action_relevant_info(log_lines[action_index])
            deduce_last_nickname_or_hostmask(log_lines[:action_index], relevant_info)
            if is_banner_bot(relevant_info['operator']):
                backtrack_index = max(0, action_index-APPROPRIATE_BACKTRACK_NUMBER)
                extract_macro_info(log_lines[backtrack_index:action_index], relevant_info)
    elif args.mode == 'auto':
        log_content = read_log_file(bot, args.chan, args.maxautolines)
        log_lines = log_content.split('\n')
        log_length = len(log_lines)

        action_index = get_action_line_index(log_lines, args.skip)
        if action_index is None:
            bot.reply('I did not find any action in the past {} lines :('.format(args.maxautolines))
            return
        end_index = min(log_length, action_index+args.followinglines+1)  # +1 to include the index

        relevant_info = get_action_relevant_info(log_lines[action_index])
        deduce_last_nickname_or_hostmask(log_lines[:action_index], relevant_info)
        if is_banner_bot(relevant_info['operator']):
            backtrack_index = max(0, action_index-APPROPRIATE_BACKTRACK_NUMBER)
            extract_macro_info(log_lines[backtrack_index:action_index], relevant_info)

        if 'host' not in relevant_info or 'nick' not in relevant_info:
            print(relevant_info)
            bot.reply('For some strange reason I do not have the hostmask yet, stopping search')
            return

        start_index = get_first_index(log_lines[:action_index], relevant_info)
        if start_index is None:
            extra_info = extra_info + '(could not find join of user, log may miss some context) '
            start_index = 0

        if end_index - start_index > args.maxlogautolines:
            extra_info += 'only using {} lines, use -b if needed '.format(args.maxlogautolines)
            start_index = end_index - args.maxlogautolines

    prettified_lines = prettify_lines(log_lines[start_index:end_index])
    relevant_content = '\n'.join(prettified_lines)
    try:
        url_content = create_s3_paste(bot.config.banlogger.s3_bucket_name, relevant_content)
    except json.decoder.JSONDecodeError as err:
        bot.reply('The paste service is down :(')
        raise Exception(err)
    relevant_info['log_url'] = url_content
    relevant_info['channel'] = CHANNEL_FOR_LOG[args.chan]

    bot.memory['last_log_information'] = relevant_info
    bot.reply('Logged here: {} {}'.format(url_content, extra_info))


def is_banner_bot(nickname):
    '''Returns true if the bot can ban people'''
    return nickname in ('Casual_Ban_Bot',
                        'NSA',
                        'ChanServ')


ENTRY_INDEXES = {'nick': '1999262323', 'result': '1898835520', 'length': '1118037499',
                 'operator': '1103903875', 'operator2': '1469630831', 'channel': '729017272',
                 'reason': '956001950', 'host': '400563484', 'log_url': '958498595',
                 'additional_information': 'entry.1480742756'}


@module.commands('form')
@from_admin_channel_only
def serve_filled_form(bot, trigger):
    '''Serves a filled form from the memorized information of the last log.'''
    form_url = bot.config.banlogger.base_form_url
    for info_type, info_value in bot.memory['last_log_information'].items():
        if info_value is not None:
            form_url += '&entry.{}={}'.format(ENTRY_INDEXES[info_type],
                                              urllib.parse.quote_plus(info_value))
    center_emoji = ';)'

    try:
        shortened_url = URL_SHORTENER.short(form_url)
    except requests.exceptions.ReadTimeout:
        bot.reply('TinyURL connection timeout.')
    else:
        bot.reply('\U0001F449'+center_emoji+'\U0001F449 ' + shortened_url)


@module.commands('helplog')
@from_admin_channel_only
def helplog(bot, trigger):
    '''Serves the help information for the command.'''
    help_content = LOG_CMD_PARSER.format_help()
    help_content = help_content.replace('sopel', ',log')
    try:
        url = create_s3_paste(bot.config.banlogger.s3_bucket_name,
                              help_content,
                              wanted_title="logcommandhelp")
    except json.decoder.JSONDecodeError as err:
        bot.reply("The paste service is down :(")
        raise Exception(err)
    bot.reply(url)


def prettify_lines(lines):
    '''Reformats parts of the log to make them more human-readable'''
    # remove the host on regular messages
    lines_reordered_fields = []
    lines_compact_time = []
    for line in lines:
        message_match = MSG_REGEX.match(line)
        if message_match:
            host_str = '(' + message_match.group(2) + ') '
            nick_str = message_match.group(1)
            new_nick_str = '<' + nick_str + '>'
            better_line = line.replace(host_str, '', 1).replace(nick_str, new_nick_str, 1)
            lines_reordered_fields.append(better_line)
        else:
            lines_reordered_fields.append(line)

    # reformat the timestamp a bit (remove the T, remove the timezone part)
    for line in lines_reordered_fields:
        lines_compact_time.append(line[0:10]+' '+line[11:19]+line[25:len(line)])

    new_lines = lines_compact_time

    return new_lines


def read_log_file(bot, channel_name, lines_number):
    '''Reads a log file, returns the lines'''
    fixed_chan_name = channel_name.lstrip('#')
    filepath = os.path.join(bot.config.chanlogs.dir, '{}.log'.format(fixed_chan_name))
    cmd = ('tail', '-n', '{}'.format(lines_number), filepath)
    output = None
    output = subprocess.check_output(list(cmd))

    log_content = output.decode('utf8')
    return log_content


VPN_MESSAGE_PART = 'You must register your nickname to use a VPN connection on this channel.'


def get_action_line_index(log_lines, action_number_to_skip):
    '''Gets the index of the action done by a mod'''

    index_to_return = None

    for line_index, line_str in reversed(list(enumerate(log_lines))):
        mute_match = MUTE_REGEX.match(line_str)
        ban_match = BAN_REGEX.match(line_str)
        simple_kick_match = KICK_REGEX.match(line_str)
        removed_by_op_match = REMOVED_REGEX.match(line_str)
        ban_match = BAN_REGEX.match(line_str)
        is_an_action = mute_match or ban_match or simple_kick_match or removed_by_op_match
        if (simple_kick_match and
                simple_kick_match.group(1) == 'gonzobot'):  # ugh duckhunt
            continue
        if (simple_kick_match and
                simple_kick_match.group(1) == 'StormBot' and
                VPN_MESSAGE_PART in simple_kick_match.group(3)):  # vpn timed ban part 1
            continue
        if (ban_match and
                ban_match.group(2) == 'StormBot' and
                'U:' in ban_match.group(1)):  # vpn timed ban part 2
            continue
        if (ban_match and
                ban_match.group(2) == 'StormBot' and
                'fix-your-connection' in ban_match.group(1)):  # connection fix timed ban
            continue
        if is_an_action and action_number_to_skip <= 0:
            index_to_return = line_index
            break
        elif is_an_action:
            action_number_to_skip -= 1

    return index_to_return


def get_action_relevant_info(line_str):
    '''Returns a dictionary of useful information from the action line'''
    relevant_info = dict()
    mute_match = MUTE_REGEX.match(line_str)
    ban_match = BAN_REGEX.match(line_str)
    simple_kick_match = KICK_REGEX.match(line_str)
    removed_by_op_match = REMOVED_REGEX.match(line_str)

    # permanent bans are downgraded to timed bans during backtrack
    if mute_match:
        relevant_info['result'] = 'Permanent Mute'
        relevant_info['host'] = mute_match.group(1).split('@')[1]
        relevant_info['operator'] = mute_match.group(2)
    elif ban_match:
        relevant_info['result'] = 'Permanent Ban'
        relevant_info['host'] = ban_match.group(1).split('@')[1]
        relevant_info['operator'] = ban_match.group(2)
    elif simple_kick_match:
        relevant_info['result'] = 'Kick'
        relevant_info['operator'] = simple_kick_match.group(1)
        relevant_info['nick'] = simple_kick_match.group(3)
        relevant_info['reason'] = simple_kick_match.group(4)
    elif removed_by_op_match:
        relevant_info['result'] = 'Kick'  # for logging purposes, interpreted as kick
        relevant_info['nick'] = removed_by_op_match.group(1)
        relevant_info['host'] = removed_by_op_match.group(2).split('@')[1]
        relevant_info['operator'] = removed_by_op_match.group(3)

    return relevant_info


def deduce_last_nickname_or_hostmask(log_lines, relevant_info):
    '''Deduces the nickname from the hostmask or vice-versa'''
    if 'host' not in relevant_info:
        missing_info = 'host'
        known_info = 'nick'
    elif 'nick' not in relevant_info:
        missing_info = 'nick'
        known_info = 'host'
    else:
        # all the info is already available
        print('deducing failed')
        return

    for line_str in reversed(log_lines):
        # If they speak, we have their hostmask and nick
        # If they switch their nick, we get their hostmask and nick that way too
        # If we get their join line, that gives us their nick and hostmask
        message_match = MSG_REGEX.match(line_str)
        switch_match = SWITCH_REGEX.match(line_str)
        join_match = JOIN_REGEX.match(line_str)
        if missing_info == 'nick':
            if message_match and message_match.group(2).split('@')[1] == relevant_info[known_info]:
                relevant_info[missing_info] = message_match.group(1)
                break
            if switch_match and switch_match.group(2).split('@')[1] == relevant_info[known_info]:
                relevant_info[missing_info] = switch_match.group(3)
                break
            if join_match and join_match.group(2).split('@')[1] == relevant_info[known_info]:
                relevant_info[missing_info] = join_match.group(1)
                break
        elif missing_info == 'host':
            if message_match and message_match.group(1) == relevant_info[known_info]:
                relevant_info[missing_info] = message_match.group(2).split('@')[1]
                break
            if switch_match and switch_match.group(3) == relevant_info[known_info]:
                relevant_info[missing_info] = switch_match.group(2).split('@')[1]
                break
            if join_match and join_match.group(1) == relevant_info[known_info]:
                relevant_info[missing_info] = join_match.group(2).split('@')[1]
                break


def get_first_index(log_lines, relevant_info):
    '''Returns the first index (join) of the user, otherwise None is returned'''
    for line_index, line_str in reversed(list(enumerate(log_lines))):
        join_match = JOIN_REGEX.match(line_str)
        if join_match and join_match.group(2).split('@')[1] == relevant_info['host']:
            return line_index

    return None


def extract_macro_info(log_lines, relevant_info):
    '''Searches for macro information, if available, for example !k, then extracts relevant info'''
    if 'nick' not in relevant_info:
        return  # to detect the correct line

    for _, line_str in reversed(list(enumerate(log_lines))):
        kick_match = KICK_MACRO_REGEX.match(line_str)
        mute_match = MUTE_MACRO_REGEX.match(line_str)
        ban_match = BAN_MACRO_REGEX.match(line_str)
        if (kick_match and
                kick_match.group(2) == relevant_info['nick']):
            relevant_info['operator'] = kick_match.group(1)
            relevant_info['reason'] = kick_match.group(3)
            break
        elif (mute_match and
              mute_match.group(3) == relevant_info['nick'] and
              relevant_info['result'] == 'Permanent Mute'):
            relevant_info['operator'] = mute_match.group(1)
            if mute_match.group(2):
                relevant_info['length'] = format_time(mute_match.group(2))
                relevant_info['result'] = 'Timed Mute'
            relevant_info['reason'] = mute_match.group(4)
            break
        elif (ban_match and
              ban_match.group(3) == relevant_info['nick'] and
              relevant_info['result'] == 'Permanent Ban'):
            relevant_info['operator'] = ban_match.group(1)
            if ban_match.group(2):
                relevant_info['length'] = format_time(ban_match.group(2))
                relevant_info['result'] = 'Timed Ban'
            relevant_info['reason'] = ban_match.group(4)
            break


def format_time(unformatted_time):
    '''Returns the time in a format fit for the spreadsheet'''
    time_unformatted = unformatted_time.strip('+')
    if 's' in time_unformatted:
        time_unformatted = time_unformatted.replace('y', ' years')
    elif 'm' in time_unformatted:
        time_unformatted = time_unformatted.replace('m', ' minutes')
    elif 'h' in time_unformatted:
        time_unformatted = time_unformatted.replace('h', ' hours')
    elif 'd' in time_unformatted:
        time_unformatted = time_unformatted.replace('d', ' days')
    elif 'y' in time_unformatted:
        time_unformatted = time_unformatted.replace('y', ' years')
    return time_unformatted
