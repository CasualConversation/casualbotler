#!/usr/bin/env python3
'''Stuff about making the spreadsheet information a bit more useful'''

import collections
import shlex
import argparse
import sys
import os
from apiclient.discovery import build
from fuzzywuzzy import fuzz
from sopel import module
from sopel.config.types import StaticSection, ListAttribute, ValidatedAttribute

# hack for relative import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import admin_only, create_s3_paste


class LogToolsSection(StaticSection):
    '''Data class containing the parameters for the module.'''
    google_api_key_password = ValidatedAttribute('google_api_key_password')
    admin_channels = ListAttribute('admin_channels')
    acceptable_fuzz_ratio = ValidatedAttribute('acceptable_fuzz_ratio', int, default=75)
    spreadsheet_id = ValidatedAttribute('spreadsheet_id')
    relevant_sheets = ListAttribute('relevant_sheets')
    relevant_range = ValidatedAttribute('relevant_range')


def configure(config):
    '''Invoked by the configuration building mode of sopel.'''
    config.define_section('logtools', LogToolsSection, validate=True)


SEARCH_CMD_PARSER = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
SEARCH_CMD_PARSER.add_argument('terms',
                               type=str,
                               nargs='+',
                               help='the nick or piece of mask to search')
SEARCH_CMD_PARSER.add_argument('-c', '--convert', action='store_true')


def setup(bot):
    '''Invoked when the module is loaded.'''
    bot.config.define_section('logtools', LogToolsSection, validate=True)
    bot.memory['google_sheets_service'] = \
        build('sheets', 'v4',
              developerKey=bot.config.logtools.google_api_key_password,
              cache_discovery=False)

    if 'sheet_content_2019' in bot.memory:
        del bot.memory['sheet_content_2019']
    if 'sheet_content_2018' in bot.memory:
        del bot.memory['sheet_content_2018']
    if 'sheet_content_old' in bot.memory:
        del bot.memory['sheet_content_old']


ACCEPTABLE_RATIO = 75


def search_for_indexes(bot, search_term):
    '''Searches the data in the sheets, returns the indexes.'''
    found_indexes_1 = []
    found_indexes_2 = []
    found_indexes_3 = []

    for index, line in enumerate(bot.memory['sheet_content_2019']):
        if line and (search_term in line[8] or fuzz.ratio(search_term.lower(),
                                                          line[1].lower()) >= ACCEPTABLE_RATIO):
            found_indexes_1.append(index)

    for index, line in enumerate(bot.memory['sheet_content_2018']):
        if line and (search_term in line[8] or fuzz.ratio(search_term.lower(),
                                                          line[1].lower()) >= ACCEPTABLE_RATIO):
            found_indexes_2.append(index)

    for index, line in enumerate(bot.memory['sheet_content_old']):
        if line and (search_term in line[8] or fuzz.ratio(search_term.lower(),
                                                          line[1].lower()) >= ACCEPTABLE_RATIO):
            found_indexes_3.append(index)
    return found_indexes_1, found_indexes_2, found_indexes_3


@module.commands('latest')
@admin_only
def latest(bot, trigger):
    '''Returns the latest logged items'''
    if 'sheet_content_2019' not in bot.memory:
        refresh_spreadsheet_content(bot)

    entry_number = len(bot.memory['sheet_content_2019'])

    sheet_1_instances = []

    for an_index in range(max(entry_number-3-1, 0), entry_number-1):
        relevant_row = bot.memory['sheet_content_2019'][an_index]
        if any(relevant_row):
            current_entry = create_entry_from_row(relevant_row, an_index)
            report_str = format_spreadsheet_line(current_entry, '2019')
            sheet_1_instances.append(report_str)
    for an_instance in sheet_1_instances:
        bot.say('\u25A0 ' + an_instance, max_messages=2)


LogEntry = collections.namedtuple("LogEntry", ["timestamp",
                                               "username",
                                               "result",
                                               "length",
                                               "op",
                                               "second_op",
                                               "channel",
                                               "reason",
                                               "host",
                                               "log",
                                               "additional_info",
                                               "index"])


@module.commands('search')
@admin_only
def search(bot, trigger):
    '''Searches for a nick (fuzzy) for a part of a netmask in the spreadsheets.'''
    arguments = trigger.groups()[1]
    if arguments is None:
        bot.reply('No arguments :(   To learn the command syntax, please use -h')
        return
    try:
        args = SEARCH_CMD_PARSER.parse_args(shlex.split(arguments))
    except SystemExit:
        if '-h' in arguments or '--help' in arguments:
            helpsearch(bot, trigger)
        else:
            bot.reply('invalid arguments :(   To learn the command syntax, please use -h')
        return

    if args.convert:
        search_terms = []
        for a_nick in args.terms:
            lowercase_users = dict()
            for user in bot.users:
                lowercase_users[user.lower()] = bot.users[user].host
            if a_nick.lower() in lowercase_users:
                host_term = lowercase_users[a_nick.lower()]
                search_terms.append(host_term)
                bot.say('Converted {} to {}, using it for the search...'.format(a_nick, host_term))
            else:
                search_terms.append(a_nick)
                warn_msg = 'Could not convert {} to a host (not in channels). '.format(a_nick) + \
                           'Searching without conversion...'
                bot.say(warn_msg)
    else:
        search_terms = args.terms

    if 'sheet_content_2019' not in bot.memory:
        refresh_spreadsheet_content(bot)

    found_indexes_2019 = []
    found_indexes_2018 = []
    found_indexes_old = []

    for a_term in search_terms:
        if a_term is None:
            continue
        s2019_temp, s2018_temp, sold_temp = search_for_indexes(bot, a_term)
        found_indexes_2019.extend(s2019_temp)
        found_indexes_2018.extend(s2018_temp)
        found_indexes_old.extend(sold_temp)

    found_indexes_2019 = list(sorted(set(found_indexes_2019)))
    found_indexes_2018 = list(sorted(set(found_indexes_2018)))
    found_indexes_old = list(sorted(set(found_indexes_old)))

    sheet_1_instances = []
    sheet_2_instances = []
    sheet_3_instances = []
    for an_index in found_indexes_2019:
        relevant_row = bot.memory['sheet_content_2019'][an_index]
        current_entry = create_entry_from_row(relevant_row, an_index)
        report_str = format_spreadsheet_line(current_entry, '2019')
        sheet_1_instances.append(report_str)

    for an_index in found_indexes_2018:
        relevant_row = bot.memory['sheet_content_2018'][an_index]
        current_entry = create_entry_from_row(relevant_row, an_index)
        report_str = format_spreadsheet_line(current_entry, '2018')
        sheet_2_instances.append(report_str)

    for an_index in found_indexes_old:
        relevant_row = bot.memory['sheet_content_old'][an_index]
        current_entry = create_entry_from_row(relevant_row, an_index)
        report_str = format_spreadsheet_line(current_entry, 'old')
        sheet_3_instances.append(report_str)

    instances = sheet_1_instances + sheet_2_instances + sheet_3_instances

    if len(instances) > 3:
        answer_string = '\U0001F914 ' + create_s3_paste(bot.config.banlogger.s3_bucket_name,
                                                        '\n'.join(instances))
        bot.say(answer_string, max_messages=3)
    elif not instances:
        bot.say('None found.')
    else:
        for an_instance in instances:
            answer_string = '\u25A0 ' + an_instance
            bot.say(answer_string, max_messages=2)


def create_entry_from_row(spreadsheet_row, row_index):
    '''Creates a namedtuple for a row as an indirection layer.'''
    log_entry = LogEntry(timestamp=spreadsheet_row[0],
                         username=spreadsheet_row[1],
                         result=spreadsheet_row[2],
                         length=spreadsheet_row[3],
                         op=spreadsheet_row[4],
                         second_op=spreadsheet_row[5],
                         channel=spreadsheet_row[6],
                         reason=spreadsheet_row[7],
                         host=spreadsheet_row[8],
                         log=spreadsheet_row[9],
                         additional_info=spreadsheet_row[10],
                         index=row_index+2)  # 0-index to 1-index, and top row
    return log_entry


def format_spreadsheet_line(entry, sheet_name):
    '''Returns a formatted spreadsheet line for report.'''
    report_str = '{} on {} ({}) '.format(entry.result, entry.username, entry.host)
    if entry.length:
        report_str += '(duration: {}) '.format(entry.length)
    report_str += 'in channel {} on {} because "{}" (see {}) (row {}) ({})'.format(entry.channel,
                                                                                   entry.timestamp,
                                                                                   entry.reason,
                                                                                   entry.log,
                                                                                   entry.index,
                                                                                   sheet_name)
    return report_str


RELEVANT_SHEETS = ('Operator Actions 2019', 'Operator Actions 2018', 'Pre-2018 Data')
RELEVANT_RANGE = 'a2:l'


@module.interval(300)
def refresh_spreadsheet_content(bot):
    '''Periodically refreshes the spreadsheet content.
    This is done this way to limits calls to the API.'''

    values_obj = bot.memory['google_sheets_service'].spreadsheets().values()

    range_1 = RELEVANT_SHEETS[0]+'!'+RELEVANT_RANGE
    range_2 = RELEVANT_SHEETS[1]+'!'+RELEVANT_RANGE
    range_3 = RELEVANT_SHEETS[2]+'!'+RELEVANT_RANGE
    bot.memory['sheet_content_2019'] = \
        values_obj.get(spreadsheetId=bot.config.logtools.spreadsheet_id,
                       range=range_1).execute().get('values', [])
    bot.memory['sheet_content_2018'] = \
        values_obj.get(spreadsheetId=bot.config.logtools.spreadsheet_id,
                       range=range_2).execute().get('values', [])
    bot.memory['sheet_content_old'] = \
        values_obj.get(spreadsheetId=bot.config.logtools.spreadsheet_id,
                       range=range_3).execute().get('values', [])


@module.commands('helpsearch')
@admin_only
def helpsearch(bot, trigger):
    '''Serves the help documentation.'''
    help_content = SEARCH_CMD_PARSER.format_help()
    help_content = help_content.replace('sopel', ',search')
    url = create_s3_paste(bot.config.banlogger.s3_bucket_name,
                          help_content,
                          wanted_title="searchcommandhelp")
    bot.reply(url)
