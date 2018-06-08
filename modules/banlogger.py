#!/usr/bin/env python3
'''Stuff about logging bans and kicks and mutes'''

import shlex
import re
import os
import argparse
import subprocess
import urllib
import requests
from sopel import module
from sopel.config.types import StaticSection, ListAttribute, ValidatedAttribute, FilenameAttribute
from pyshorteners import Shortener


class BanLoggerSection(StaticSection):
    admin_channels = ListAttribute('admin_channels')
    loggable_channels = ListAttribute('loggable_channels')
    log_dir_path = FilenameAttribute('log_dir_path', directory=True)
    base_form_url = ValidatedAttribute('base_url_form')


def configure(config):
    '''Invoked when in configuration building mode.'''
    config.define_section('banlogger', BanLoggerSection, validate=True)


parser = None

shortener = None

# Format them with the appropriate information!
VALID_NICK = r'[a-zA-Z0-9_\-\\\[\]\{\}\^\`\|]+'
ISO8601 = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+\d{2}:\d{2}'
OPT_DURATION_GROUP = r'(\+\d{1,3}[smhdy])? ?'

MUTE_REGEX = re.compile(ISO8601+r' --  Mode #?\w+ \(\+b m:(.*)\) by ('+VALID_NICK+r') \((.*)\)')
BAN_REGEX = re.compile(ISO8601+r' --  Mode #?\w+ \(\+b (.*)\) by ('+VALID_NICK+r') \((.*)\)')
KICK_REGEX = re.compile(ISO8601+r' <-- ('+VALID_NICK+r') \((.*)\) has kicked ('+VALID_NICK+r') \(?(.*)\)?')
REMOVED_REGEX = re.compile(ISO8601+r' <-- ('+VALID_NICK+r') \((.*)\) has left \(?Removed by ('+VALID_NICK+r').*\)?')
MSG_REGEX = re.compile(ISO8601+r'     ('+VALID_NICK+r') \((.*)\) (.*)')
SWITCH_REGEX = re.compile(ISO8601+r' --  ('+VALID_NICK+r') \((.*)\) is now known as ('+VALID_NICK+')')
JOIN_REGEX = re.compile(ISO8601+r' --> ('+VALID_NICK+r') \((.*)\) has joined .*')

KICK_MACRO_REGEX = re.compile(ISO8601+r'     ('+VALID_NICK+r') \(.*\) !ki?c?k? ('+VALID_NICK+r') ?(.*)')
MUTE_MACRO_REGEX = re.compile(ISO8601+r'     ('+VALID_NICK+r') \(.*\) !mu?t?e? ' +
                              OPT_DURATION_GROUP+r'('+VALID_NICK+r') ?(.*)')
BAN_MACRO_REGEX = re.compile(ISO8601+r'     ('+VALID_NICK+r') \(.*\) !k?i?c?k?ba?n? ' +
                             OPT_DURATION_GROUP+r'('+VALID_NICK+r') ?(.*)')


def setup(bot):
    '''Invoked when module is loaded.'''
    bot.config.define_section('banlogger', BanLoggerSection, validate=True)
    argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    global parser
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('mode', type=str, choices=['recent', 'auto'], default='auto',
                        help='the desired logging mode')
    parser.add_argument('--linenumber', '-l', type=int, choices=range(1, 4001), default=100,
                        metavar="[1-4000]", help='the number of lines to log in recent mode')
    parser.add_argument('--maxautolines', '-m', type=int, choices=range(1, 4001), default=4000,
                        metavar="[1-4000]",
                        help='the maximum number of lines to search in auto mode')
    parser.add_argument('--maxlogautolines', '-b', type=int, choices=range(1, 4001), default=400,
                        metavar="[1-4000]",
                        help='the maximum number of lines for the log in auto mode')
    parser.add_argument('--followinglines', '-f', type=int, choices=range(0, 101), default=2,
                        metavar="[0-100]",
                        help='the desired number of lines following the action in auto mode')
    parser.add_argument('--skip', '-s', type=int, choices=range(11), default=0, metavar="[0-10]",
                        help='the number of actions to skip in auto mode')
    parser.add_argument('--chan', '-c', type=str, choices=bot.config.banlogger.loggable_channels,
                        default='#casualconversation', help='the channel to log')

    global shortener
    shortener = Shortener('Tinyurl')

    bot.memory['last_log_information'] = {'nick': None, 'result': None, 'length': None, 'operator': None,
                                          'channel': None, 'reason': None, 'host': None, 'paste': None}


CHANNEL_FOR_LOG = {'#casualconversation': '#Casualconversation',
                   '#talk': '#Talk',
                   '#casualnsfw': '#CasualNSFW',
                   '#casualappeals': 'All'}

APPROPRIATE_BACKTRACK_NUMBER = 8  # The number of lines to analyze before an action


@module.commands('log')
def log(bot, trigger):

    extra_info = ''

    is_admin_channel = (trigger.sender in bot.config.banlogger.admin_channels)
    if not is_admin_channel:
        return

    arguments = trigger.groups()[1]
    if arguments is None:
        bot.reply('No arguments :(   To learn the command syntax, please use -h')
        return
    try:
        args = parser.parse_args(shlex.split(arguments))
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
            relevant_info = get_action_relevant_information(log_lines[action_index])
            deduce_last_nickname_or_hostmask(log_lines[:action_index], relevant_info)
            if relevant_info['operator'] == 'Casual_Ban_Bot':
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

        relevant_info = get_action_relevant_information(log_lines[action_index])
        deduce_last_nickname_or_hostmask(log_lines[:action_index], relevant_info)
        if relevant_info['operator'] == 'Casual_Ban_Bot':
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

    url_content = create_snoonet_paste(relevant_content)
    relevant_info['log_url'] = url_content
    relevant_info['channel'] = CHANNEL_FOR_LOG[args.chan]

    bot.memory['last_log_information'] = relevant_info
    bot.reply('Here is the log: {} {}'.format(url_content, extra_info))


ENTRY_INDEXES = {'nick': '1999262323', 'result': '1898835520', 'length': '1118037499',
                 'operator': '1103903875', 'operator2': '1469630831', 'channel': '729017272',
                 'reason': '956001950', 'host': '400563484', 'log_url': '958498595',
                 'additional_information': 'entry.1480742756'}


@module.commands('form')
def serve_filled_form(bot, trigger):
    '''Serves a filled form from the memorized information of the last log.'''
    is_admin_channel = (trigger.sender in bot.config.banlogger.admin_channels)
    if not is_admin_channel:
        return

    url = bot.config.banlogger.base_form_url
    for info_type, info_value in bot.memory['last_log_information'].items():
        if info_value is not None:
            url += '&entry.{}={}'.format(ENTRY_INDEXES[info_type],
                                         urllib.parse.quote_plus(info_value))

    bot.reply('\U0001F449\U0001F60E\U0001F449 ' + shortener.short(url))


@module.commands('helplog')
def helplog(bot, trigger):
    '''Serves the help information for the command.'''
    is_admin_channel = (trigger.sender in bot.config.banlogger.admin_channels)
    if not is_admin_channel:
        return
    help_content = parser.format_help()
    help_content = help_content.replace('sopel', ',log')
    url = create_snoonet_paste(help_content)
    bot.reply(url)


def prettify_lines(lines):
    '''Reformats parts of the log to make them more human-readable'''
    # remove the host on regular messages
    lines_filtered_1 = []
    lines_filtered_2 = []
    for line in lines:
        message_match = MSG_REGEX.match(line)
        if message_match:
            host_str = '(' + message_match.group(2) + ') '
            nick_str = message_match.group(1)
            new_nick_str = '<' + nick_str + '>'
            better_line = line.replace(host_str, '', 1).replace(nick_str, new_nick_str, 1)
            lines_filtered_1.append(better_line)
        else:
            lines_filtered_1.append(line)

    # reformat the timestamp a bit (remove the T, remove the timezone part)
    for line in lines_filtered_1:
        lines_filtered_2.append(line[0:10]+' '+line[11:19]+line[25:len(line)])

    new_lines = lines_filtered_2

    return new_lines


def create_snoonet_paste(paste_content):
    '''Creates a ghostpaste and returns the link to it'''
    paste_url_prefix = 'https://paste.snoonet.org/paste/'
    post_url = paste_url_prefix+'new'

    data = {'lang': 'irc',
            'text': paste_content,
            'expire': -1,
            'password': None,
            'title': None}

    # We create a post
    response = requests.post(post_url, data=data)

    return response.url


def read_log_file(bot, channel_name, lines_number):
    '''Reads a log file, returns the lines'''
    fixed_chan_name = channel_name.lstrip('#')
    filepath = os.path.join(bot.config.banlogger.log_dir_path, '{}.log'.format(fixed_chan_name))
    cmd = ('tail', '-n', '{}'.format(lines_number), filepath)
    output = None
    try:
        output = subprocess.check_output(list(cmd))

    log_content = output.decode('utf8')
    return log_content


VPN_MESSAGE_PART = 'You must register your nickname to use a VPN connection on this channel.'


def get_action_line_index(log_lines, action_number_to_skip):
    '''Gets the index of the relevant action'''
    for line_index, line_str in reversed(list(enumerate(log_lines))):
        mute_match = MUTE_REGEX.match(line_str)
        ban_match = BAN_REGEX.match(line_str)
        simple_kick_match = KICK_REGEX.match(line_str)
        removed_by_op_match = REMOVED_REGEX.match(line_str)
        ban_match = BAN_REGEX.match(line_str)
        isAnAction = mute_match or ban_match or simple_kick_match or removed_by_op_match
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
        if isAnAction and action_number_to_skip <= 0:
            return line_index
        elif isAnAction:
            action_number_to_skip -= 1


def get_action_relevant_information(line_str):
    '''Returns a dictionary of useful information from the action line'''
    relevant_information = dict()
    mute_match = MUTE_REGEX.match(line_str)
    ban_match = BAN_REGEX.match(line_str)
    simple_kick_match = KICK_REGEX.match(line_str)
    removed_by_op_match = REMOVED_REGEX.match(line_str)

    if mute_match:
        relevant_information['result'] = 'Permanent Mute'  # will be downgrater to timed during backtrack
        relevant_information['host'] = mute_match.group(1).split('@')[1]
        relevant_information['operator'] = mute_match.group(2)
    elif ban_match:
        relevant_information['result'] = 'Permanent Ban'  # will be downgraded to timed during backtrack
        relevant_information['host'] = ban_match.group(1).split('@')[1]
        relevant_information['operator'] = ban_match.group(2)
    elif simple_kick_match:
        relevant_information['result'] = 'Kick'
        relevant_information['operator'] = simple_kick_match.group(1)
        relevant_information['nick'] = simple_kick_match.group(3)
        relevant_information['reason'] = simple_kick_match.group(4)
    elif removed_by_op_match:
        relevant_information['result'] = 'Kick'  # for logging purposes, interpreted as kick
        relevant_information['nick'] = removed_by_op_match.group(1)
        relevant_information['host'] = removed_by_op_match.group(2).split('@')[1]
        relevant_information['operator'] = removed_by_op_match.group(3)

    return relevant_information


def deduce_last_nickname_or_hostmask(log_lines, relevant_information):
    '''Deduces the nickname from the hostmask or vice-versa'''
    if 'host' not in relevant_information:
        missing_info = 'host'
        provided_info = 'nick'
    elif 'nick' not in relevant_information:
        missing_info = 'nick'
        provided_info = 'host'
    else:
        # all the info is already available
        print('deducing failed')
        return

    for line_str in reversed(log_lines):
        # If they speak, we have their hostmask and nick
        # If they switch their nick, we get their hostmask and nick that way too
        # If we get their join line, that gives us their nick and hostmask
        a_message_match = MSG_REGEX.match(line_str)
        a_switch_match = SWITCH_REGEX.match(line_str)
        a_join_match = JOIN_REGEX.match(line_str)
        if missing_info == 'nick':
            if a_message_match and a_message_match.group(2).split('@')[1] == relevant_information[provided_info]:
                relevant_information[missing_info] = a_message_match.group(1)
                break
            if a_switch_match and a_switch_match.group(2).split('@')[1] == relevant_information[provided_info]:
                relevant_information[missing_info] = a_switch_match.group(3)
                break
            if a_join_match and a_join_match.group(2).split('@')[1] == relevant_information[provided_info]:
                relevant_information[missing_info] = a_join_match.group(1)
                break
        elif missing_info == 'host':
            if a_message_match and a_message_match.group(1) == relevant_information[provided_info]:
                relevant_information[missing_info] = a_message_match.group(2).split('@')[1]
                break
            if a_switch_match and a_switch_match.group(3) == relevant_information[provided_info]:
                relevant_information[missing_info] = a_switch_match.group(2).split('@')[1]
                break
            if a_join_match and a_join_match.group(1) == relevant_information[provided_info]:
                relevant_information[missing_info] = a_join_match.group(2).split('@')[1]
                break


def get_first_index(log_lines, relevant_information):
    '''Returns the first index (join) of the user, otherwise None is returned'''
    for line_index, line_str in reversed(list(enumerate(log_lines))):
        join_match = JOIN_REGEX.match(line_str)
        if join_match and join_match.group(2).split('@')[1] == relevant_information['host']:
            return line_index

    return None


def extract_macro_info(log_lines, relevant_information):
    '''Searches for macro information, if available, for example !k, then extracts relevant info'''
    if 'nick' not in relevant_information:
        return  # to detect the correct line

    for _, line_str in reversed(list(enumerate(log_lines))):
        kick_match = KICK_MACRO_REGEX.match(line_str)
        mute_match = MUTE_MACRO_REGEX.match(line_str)
        ban_match = BAN_MACRO_REGEX.match(line_str)
        if (kick_match and
                kick_match.group(2) == relevant_information['nick']):
            relevant_information['operator'] = kick_match.group(1)
            relevant_information['reason'] = kick_match.group(3)
            return
        elif (mute_match and
                mute_match.group(3) == relevant_information['nick'] and
                relevant_information['result'] == 'Permanent Mute'):
            relevant_information['operator'] = mute_match.group(1)
            if mute_match.group(2):
                relevant_information['length'] = format_time(mute_match.group(2))
                relevant_information['result'] = 'Timed Mute'
            relevant_information['reason'] = mute_match.group(4)
            return
        elif (ban_match and
                ban_match.group(3) == relevant_information['nick'] and
                relevant_information['result'] == 'Permanent Ban'):
            relevant_information['operator'] = ban_match.group(1)
            if ban_match.group(2):
                relevant_information['length'] = format_time(ban_match.group(2))
                relevant_information['result'] = 'Timed Ban'
            relevant_information['reason'] = ban_match.group(4)
            return


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
